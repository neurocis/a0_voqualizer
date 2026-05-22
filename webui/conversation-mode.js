/*
 * Voqualizer Conversational + PTT state machine, exposed as an Alpine store
 * named 'voqualizer'. Tap (< 250 ms) toggles conversational mode; click-hold
 * (>= 250 ms) is push-to-talk and sends an explicit end-of-utterance final on
 * release. Speaker icon toggles TTS per-context, persisted in sessionStorage.
 *
 * This module is intentionally tester-page-independent: it imports only the
 * shared helpers from ./lib/voqualizer-audio.js. The tester continues to use
 * its own larger store for diagnostics.
 */

import {
  audioChunkPayload,
  initMicWorklet,
  createPlaybackTracker,
  alignPcm16Bytes,
  clearPcm16Carry,
  pcm16ToFloat32,
  bytesFromUnknownAudio,
  bytesFromTtsPayload,
  normalizeTtsCodec, ttsSampleRate, rememberPlaybackSource,
  maybeLocalBargeInFromMic,
  PCM_SAMPLE_RATE,
  INPUT_CODEC,
  OUTPUT_CODEC,
} from '/plugins/a0_voqualizer/webui/lib/voqualizer-audio.js';

export const TAP_HOLD_THRESHOLD_MS = 250;
export const VOQUALIZER_HANDLER = 'plugins/a0_voqualizer/ws_voqualizer';
export const TTS_PREF_PREFIX = 'a0_voqualizer.tts_enabled.';
export const CONTEXT_CHANGE_DEBOUNCE_MS = 850;
export const MIC_SPEECH_ACTIVE_THRESHOLD = 0.035;
export const MIC_SPEECH_FINAL_COOLDOWN_MS = 900;
export const MIC_SPEECH_SILENCE_CLEAR_MS = 700;

export const STATE_IDLE = 'idle';
export const STATE_CONNECTING = 'connecting';
export const STATE_CONVERSATIONAL = 'conversational';
export const STATE_PTT_ACTIVE = 'ptt-active';
export const STATE_TTS_READY = 'tts-ready';
export const STATE_STOPPING = 'stopping';
export const STATE_ERROR = 'error';

export const DESIRED_IDLE = 'idle';
export const DESIRED_CONVERSATIONAL = 'conversational';
export const DESIRED_PTT = 'ptt';
export const DESIRED_TTS = 'tts';

function normalizeContextCandidate(value) {
  if (value == null) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    const text = String(value).trim();
    return text && text !== '[object Object]' ? text : '';
  }
  if (typeof value === 'object') {
    for (const key of ('id', 'ctxid', 'context_id', 'contextId', 'chat_id', 'chatId')) {
      if (value[key] != null) {
        const text = normalizeContextCandidate(value[key]);
        if (text) return text;
      }
    }
  }
  return '';
}

export function currentContextId() {
  try {
    if (typeof globalThis.getContext === 'function') {
      const ctx = normalizeContextCandidate(globalThis.getContext());
      if (ctx) return ctx;
    }
  } catch (_e) {}
  try {
    const chats = globalThis.Alpine && globalThis.Alpine.store && globalThis.Alpine.store('chats');
    if (chats) {
      const candidates = [
        chats.selectedContext,
        chats.selectedContext && chats.selectedContext.id,
        typeof chats.getSelectedChatId === 'function' ? chats.getSelectedChatId() : '',
        chats.selected,
      ];
      for (const candidate of candidates) {
        const id = normalizeContextCandidate(candidate);
        if (id) return id;
      }
    }
  } catch (_e) {}
  try {
    const raw = globalThis.sessionStorage && globalThis.sessionStorage.getItem('lastSelectedChat');
    const id = normalizeContextCandidate(raw);
    if (id) return id;
  } catch (_e) {}
  try {
    const params = new URLSearchParams(globalThis.location && globalThis.location.search || '');
    for (const key of ('ctxid', 'context_id', 'contextId')) {
      const id = normalizeContextCandidate(params.get(key));
      if (id) return id;
    }
  } catch (_e) {}
  return '';
}

function ttsPrefKey(ctxId) { return TTS_PREF_PREFIX + (ctxId || 'default'); }

export function readTtsEnabled(ctxId) {
  try {
    const raw = globalThis.sessionStorage && globalThis.sessionStorage.getItem(ttsPrefKey(ctxId));
    if (raw === '0' || raw === 'false') return false;
  } catch (_e) {}
  return true;
}

export function writeTtsEnabled(ctxId, enabled) {
  try {
    globalThis.sessionStorage && globalThis.sessionStorage.setItem(ttsPrefKey(ctxId), enabled ? '1' : '0');
  } catch (_e) {}
}

export function createVoqualizerStore(options = {}) {
  const tracker = createPlaybackTracker();
  const carryMap = new Map();
  const state = {
    state: STATE_IDLE,
    desiredMode: DESIRED_IDLE,
    contextId: currentContextId(),
    pendingContextId: '',
    pendingContextSince: 0,
    contextChangeDebounceMs: options.contextChangeDebounceMs || CONTEXT_CHANGE_DEBOUNCE_MS,
    connectionGeneration: 0,
    intentionalDisconnect: false,
    sessionId: '',
    bearerToken: '',
    capturing: false,
    pttActive: false,
    conversational: false,
    ttsEnabledByContext: {},
    lastError: '',
    lastTransitionReason: 'created',
    lastConnectPhase: '',
    lastDisconnectReason: '',
    lastSocketEvent: '',
    lastAnySocketEvent: '',
    lastAnySocketPayloadKeys: '',
    lastAnySocketEventAt: 0,
    lastTtsEnabledSent: null,
    lastTtsControlAck: null,
    lastTtsChunkAt: 0,
    lastTtsDoneAt: 0,
    lastTtsChunkBytes: 0,
    lastTtsChunkSource: '',
    lastTtsUtteranceId: '',
    pushedTtsChunkCount: 0,
    pushedTtsDoneCount: 0,
    ackTtsChunkCount: 0,
    ackTtsDoneCount: 0,
    lastPushedTtsUtteranceId: '',
    lastAckTtsUtteranceId: '',
    pushedTtsChunksByUtterance: {},
    lastTtsSkipReason: '',
    lastPlaybackStartAt: 0,
    lastRawTtsPushEvent: '',
    lastRawTtsPushAt: 0,
    lastRawTtsPushKeys: '',
    lastRawTtsPushDataKeys: '',
    lastAckTtsFallbackAt: 0,
    lastAckTtsFallbackChunks: 0,
    lastAckTtsFallbackReason: '',
    ttsChunkCount: 0,
    ttsDoneCount: 0,
    agentFinalCount: 0,
    asrFinalCount: 0,
    lastPlaybackStopReason: '',
    lastAgentFinalAt: 0,
    lastAgentFinalText: '',
    lastDirectTtsText: '',
    lastDirectTtsAt: 0,
    lastDirectTtsAck: null,
    lastDirectTtsError: '',
    directTtsCount: 0,
    lastFinalFrameSentAt: 0,
    lastFinalFrameReason: '',
    lastAudioSeqSent: 0,
    lastAudioAckAt: 0,
    lastAudioAck: null,
    lastAudioAckError: '',
    lastAsrPartialAt: 0,
    lastAsrPartialText: '',
    lastAsrFinalText: '',
    lastAckAsrFinalText: '',
    lastAsrFinalUtteranceId: '',
    asrPromptDraftOwned: false,
    lastAsrPartialPromptAt: 0,
    lastAsrFinalPromptAt: 0,
    lastPromptSubmitAt: 0,
    lastPromptSubmitText: '',
    lastPromptSubmitSkipReason: '',
    lastPromptElementSelector: '',
    lastAsrPromptSource: '',
    lastAsrPromptMirrorAt: 0,
    lastAsrPromptClearAt: 0,
    lastAsrPromptClearScheduledAt: 0,
    lastAsrPromptClearDueAt: 0,
    lastAsrPromptClearReason: '',
    micVuLevel: 0,
    micVuPeak: 0,
    micVuRms: 0,
    micVuClipped: false,
    micSpeechActive: false,
    micSpeechStartedAt: 0,
    micSpeechLastActiveAt: 0,
    micSpeechCooldownUntil: 0,
    lastMicVuAt: 0,
    audioFramesSent: 0,
    seq: 0,
    startTs: 0,
    holdStartedAt: 0,
    _initialized: false,
    _socket: null,
    _mic: null,
    _ctxPoll: null,
    _contextHandler: null,
    _pttOverlay: false,
    init() {
      if (this._initialized) return this;
      this._initialized = true;
      this._setReason('init');
      this.contextId = normalizeContextCandidate(this.contextId) || currentContextId();
      this.ttsEnabledByContext[this.contextId] = readTtsEnabled(this.contextId);
      this._contextHandler = () => this._observeContextChange(currentContextId(), 'event');
      try { globalThis.addEventListener && globalThis.addEventListener('a0:context-changed', this._contextHandler); } catch (_e) {}
      this._ctxPoll = setInterval(() => this._observeContextChange(currentContextId(), 'poll'), 500);
      this._publishDebug();
      if (this.isTtsEnabled()) this._ensurePassiveTtsSession('init_tts_passive_connect');
      return this;
    },
    destroy() {
      try { clearInterval(this._ctxPoll); } catch (_e) {}
      try { this._contextHandler && globalThis.removeEventListener && globalThis.removeEventListener('a0:context-changed', this._contextHandler); } catch (_e) {}
      this._stopMic('destroy');
      this._disconnect('destroy');
      this._initialized = false;
      this._publishDebug();
    },
    isTtsEnabled() { return !!this.ttsEnabledByContext[this.contextId]; },
    debugSnapshot() {
      return {
    _asrPromptClearTimer: null,
        state: this.state,
        desiredMode: this.desiredMode,
        contextId: this.contextId,
        pendingContextId: this.pendingContextId,
        connectionGeneration: this.connectionGeneration,
        intentionalDisconnect: this.intentionalDisconnect,
        sessionId: this.sessionId,
        bearerTokenIssued: !!this.bearerToken,
        capturing: this.capturing,
        conversational: this.conversational,
        pttActive: this.pttActive,
        ttsEnabled: this.isTtsEnabled(),
        micVuLevel: this.micVuLevel,
        micVuPeak: this.micVuPeak,
        micVuRms: this.micVuRms,
        micVuClipped: this.micVuClipped,
        micSpeechActive: this.micSpeechActive,
        micSpeechStartedAt: this.micSpeechStartedAt,
        micSpeechLastActiveAt: this.micSpeechLastActiveAt,
        micSpeechCooldownUntil: this.micSpeechCooldownUntil,
        lastMicVuAt: this.lastMicVuAt,
        lastError: this.lastError,
        lastTransitionReason: this.lastTransitionReason,
        lastConnectPhase: this.lastConnectPhase,
        lastDisconnectReason: this.lastDisconnectReason,
        lastSocketEvent: this.lastSocketEvent,
        lastAnySocketEvent: this.lastAnySocketEvent,
        lastAnySocketPayloadKeys: this.lastAnySocketPayloadKeys,
        lastAnySocketEventAt: this.lastAnySocketEventAt,
        ttsEnabled: this.isTtsEnabled(),
        lastTtsEnabledSent: this.lastTtsEnabledSent,
        lastTtsControlAck: this.lastTtsControlAck,
        lastTtsChunkAt: this.lastTtsChunkAt,
        lastTtsDoneAt: this.lastTtsDoneAt,
        lastTtsChunkBytes: this.lastTtsChunkBytes,
        lastTtsChunkSource: this.lastTtsChunkSource,
        lastTtsUtteranceId: this.lastTtsUtteranceId,
        pushedTtsChunkCount: this.pushedTtsChunkCount,
        pushedTtsDoneCount: this.pushedTtsDoneCount,
        ackTtsChunkCount: this.ackTtsChunkCount,
        ackTtsDoneCount: this.ackTtsDoneCount,
        lastPushedTtsUtteranceId: this.lastPushedTtsUtteranceId,
        lastAckTtsUtteranceId: this.lastAckTtsUtteranceId,
        pushedTtsChunksByUtterance: this.pushedTtsChunksByUtterance,
        lastTtsSkipReason: this.lastTtsSkipReason,
        lastPlaybackStartAt: this.lastPlaybackStartAt,
        lastRawTtsPushEvent: this.lastRawTtsPushEvent,
        lastRawTtsPushAt: this.lastRawTtsPushAt,
        lastRawTtsPushKeys: this.lastRawTtsPushKeys,
        lastRawTtsPushDataKeys: this.lastRawTtsPushDataKeys,
        lastAckTtsFallbackAt: this.lastAckTtsFallbackAt,
        lastAckTtsFallbackChunks: this.lastAckTtsFallbackChunks,
        lastAckTtsFallbackReason: this.lastAckTtsFallbackReason,
        ttsChunkCount: this.ttsChunkCount,
        ttsDoneCount: this.ttsDoneCount,
        agentFinalCount: this.agentFinalCount,
        asrFinalCount: this.asrFinalCount,
        lastPlaybackStopReason: this.lastPlaybackStopReason,
        lastAgentFinalAt: this.lastAgentFinalAt,
        lastAgentFinalText: this.lastAgentFinalText,
        lastDirectTtsText: this.lastDirectTtsText,
        lastDirectTtsAt: this.lastDirectTtsAt,
        lastDirectTtsAck: this.lastDirectTtsAck,
        lastDirectTtsError: this.lastDirectTtsError,
        directTtsCount: this.directTtsCount,
        lastFinalFrameSentAt: this.lastFinalFrameSentAt,
        lastFinalFrameReason: this.lastFinalFrameReason,
        lastAudioSeqSent: this.lastAudioSeqSent,
        lastAudioAckAt: this.lastAudioAckAt,
        lastAudioAck: this.lastAudioAck,
        lastAudioAckError: this.lastAudioAckError,
        lastAsrPartialAt: this.lastAsrPartialAt,
        lastAsrPartialText: this.lastAsrPartialText,
        lastAsrFinalText: this.lastAsrFinalText,
        lastAckAsrFinalText: this.lastAckAsrFinalText,
        lastAsrFinalUtteranceId: this.lastAsrFinalUtteranceId,
        asrPromptDraftOwned: this.asrPromptDraftOwned,
        lastAsrPartialPromptAt: this.lastAsrPartialPromptAt,
        lastAsrFinalPromptAt: this.lastAsrFinalPromptAt,
        lastPromptSubmitAt: this.lastPromptSubmitAt,
        lastPromptSubmitText: this.lastPromptSubmitText,
        lastPromptSubmitSkipReason: this.lastPromptSubmitSkipReason,
        lastPromptElementSelector: this.lastPromptElementSelector,
        lastAsrPromptSource: this.lastAsrPromptSource,
        lastAsrPromptMirrorAt: this.lastAsrPromptMirrorAt,
        lastAsrPromptClearAt: this.lastAsrPromptClearAt,
        lastAsrPromptClearScheduledAt: this.lastAsrPromptClearScheduledAt,
        lastAsrPromptClearDueAt: this.lastAsrPromptClearDueAt,
        lastAsrPromptClearReason: this.lastAsrPromptClearReason,
        lastAsrPromptGraceClearDelayMs: this.lastAsrPromptGraceClearDelayMs,
        audioFramesSent: this.audioFramesSent,
      };
    },
    _publishDebug() {
      try { globalThis.__voqualizer_conversation = this.debugSnapshot(); } catch (_e) {}
    },
    _setReason(reason, phase = '') {
      this.lastTransitionReason = reason || this.lastTransitionReason;
      if (phase) this.lastConnectPhase = phase;
      this._publishDebug();
    },
    _isGenerationCurrent(generation) {
      const current = generation === this.connectionGeneration;
      if (!current) this._setReason('stale_generation_ignored');
      return current;
    },
    _wantsPassiveTtsSession() {
      return this.isTtsEnabled() && !this.conversational && !this.pttActive && !this.capturing;
    },
    async _ensurePassiveTtsSession(reason = 'tts_passive_connect') {
      if (!this.isTtsEnabled()) return;
      if (this._socket && this.bearerToken) {
        if (this.desiredMode === DESIRED_IDLE) this.desiredMode = DESIRED_TTS;
        if (this.state === STATE_IDLE || this.state === STATE_CONNECTING) this.state = STATE_TTS_READY;
        this._setReason(reason);
        return;
      }
      const generation = this._beginLifecycle(DESIRED_TTS, reason);
      await this._ensureConnected(generation);
      if (!this._isGenerationCurrent(generation) || this.desiredMode !== DESIRED_TTS) return;
      this.conversational = false;
      this.pttActive = false;
      this.state = STATE_TTS_READY;
      this._setReason('tts_passive_ready');
    },
    _observeContextChange(rawId, source = 'poll') {
      const cur = normalizeContextCandidate(rawId);
      if (!cur) {
        // Ignore a single empty/transient context read; only act if it remains
        // stable long enough and we currently have no meaningful context.
        if (!this.pendingContextId) {
          this.pendingContextId = '';
          this.pendingContextSince = Date.now();
        }
        this._setReason(`context_empty_${source}`);
        return;
      }
      if (cur === this.contextId) {
        this.pendingContextId = '';
        this.pendingContextSince = 0;
        return;
      }
      const now = Date.now();
      if (cur !== this.pendingContextId) {
        this.pendingContextId = cur;
        this.pendingContextSince = now;
        this._setReason('context_change_pending');
        return;
      }
      if (now - this.pendingContextSince < this.contextChangeDebounceMs) return;
      this._applyContextChange(cur);
    },
    async _applyContextChange(newId) {
      const cur = normalizeContextCandidate(newId);
      if (!cur || cur === this.contextId) return;
      this._setReason('context_changed');
      this.pendingContextId = '';
      this.pendingContextSince = 0;
      this.desiredMode = DESIRED_IDLE;
      this.intentionalDisconnect = true;
      this.connectionGeneration += 1;
      await this._stopMic('context_changed');
      await this._disconnect('context_changed');
      this.contextId = cur;
      this.ttsEnabledByContext[this.contextId] = readTtsEnabled(this.contextId);
      this.conversational = false;
      this.pttActive = false;
      this.state = STATE_IDLE;
      this._publishDebug();
      if (this.isTtsEnabled()) await this._ensurePassiveTtsSession('context_changed_tts_passive_connect');
    },
    async toggleTts() {
      const enabled = !this.isTtsEnabled();
      this.ttsEnabledByContext[this.contextId] = enabled;
      writeTtsEnabled(this.contextId, enabled);
      this._setReason(enabled ? 'tts_enabled' : 'tts_muted');
      if (this._socket && this.bearerToken) {
        try {
          this.lastTtsEnabledSent = enabled;
          this._publishDebug();
          this._socket.emit('voqualizer_control', {
            action: 'set_tts_enabled',
            enabled,
            bearer_token: this.bearerToken,
          }, (ack) => {
            this.lastTtsControlAck = this._unwrapPayload(ack);
            this._publishDebug();
          });
        } catch (_e) {}
      }
      if (enabled) {
        await this._ensurePassiveTtsSession('tts_enabled_passive_connect');
      } else if (!this.conversational && !this.pttActive && !this.capturing) {
        this.desiredMode = DESIRED_IDLE;
        this.connectionGeneration += 1;
        await this._disconnect('tts_disabled_passive_disconnect');
        this.state = STATE_IDLE;
        this._publishDebug();
      }
    },
    async onTap() {
      if (this.state === STATE_CONNECTING || this.state === STATE_STOPPING) return;
      if (this.state === STATE_CONVERSATIONAL || this.desiredMode === DESIRED_CONVERSATIONAL) {
        this._setReason('manual_stop');
        this.state = STATE_STOPPING;
        await this._stopMic('manual_stop');
        this.conversational = false;
        this.pttActive = false;
        if (this.isTtsEnabled()) {
          this.desiredMode = DESIRED_TTS;
          this.intentionalDisconnect = false;
          this.state = STATE_TTS_READY;
          this._setReason('manual_stop_tts_passive');
        } else {
          this.desiredMode = DESIRED_IDLE;
          this.intentionalDisconnect = true;
          this.connectionGeneration += 1;
          await this._disconnect('manual_stop');
          this.state = STATE_IDLE;
        }
        this._publishDebug();
        return;
      }
      const generation = this._beginLifecycle(DESIRED_CONVERSATIONAL, 'connect_requested');
      await this._ensureConnected(generation);
      if (!this._isGenerationCurrent(generation) || this.desiredMode !== DESIRED_CONVERSATIONAL) return;
      await this._startMic(generation);
      if (!this._isGenerationCurrent(generation) || this.desiredMode !== DESIRED_CONVERSATIONAL) return;
      this.conversational = true;
      this.state = STATE_CONVERSATIONAL;
      this._setReason('mic_started');
    },
    async onHoldStart() {
      if (this.state === STATE_CONNECTING && this.desiredMode !== DESIRED_PTT) return;
      this.holdStartedAt = Date.now();
      this.pttActive = true;
      const wasConversational = this.state === STATE_CONVERSATIONAL || this.desiredMode === DESIRED_CONVERSATIONAL;
      this._pttOverlay = wasConversational;
      const generation = this._beginLifecycle(DESIRED_PTT, 'ptt_requested');
      await this._ensureConnected(generation);
      if (!this._isGenerationCurrent(generation) || this.desiredMode !== DESIRED_PTT) return;
      if (!this.capturing) await this._startMic(generation);
      if (!this._isGenerationCurrent(generation) || this.desiredMode !== DESIRED_PTT) return;
      this.state = STATE_PTT_ACTIVE;
      this._setReason('ptt_started');
    },
    async onHoldEnd() {
      if (!this.pttActive && this.desiredMode !== DESIRED_PTT) return;
      const generation = this.connectionGeneration;
      this.pttActive = false;
      await this._sendFinalFrame(generation);
      if (!this._isGenerationCurrent(generation)) return;
      if (this._pttOverlay) {
        this.desiredMode = DESIRED_CONVERSATIONAL;
        this.conversational = true;
        this.state = STATE_CONVERSATIONAL;
        this._setReason('ptt_final_overlay');
      } else {
        this.state = STATE_STOPPING;
        await this._stopMic('ptt_release');
        this.conversational = false;
        if (this.isTtsEnabled()) {
          this.desiredMode = DESIRED_TTS;
          this.intentionalDisconnect = false;
          this.state = STATE_TTS_READY;
          this._setReason('ptt_final_tts_passive');
        } else {
          this.desiredMode = DESIRED_IDLE;
          this.intentionalDisconnect = true;
          this.connectionGeneration += 1;
          await this._disconnect('ptt_release');
          this.state = STATE_IDLE;
          this._setReason('ptt_final_disconnect');
        }
      }
      this._pttOverlay = false;
      this._publishDebug();
    },
    _beginLifecycle(desiredMode, reason) {
      this.connectionGeneration += 1;
      this.intentionalDisconnect = false;
      this.desiredMode = desiredMode;
      this.lastError = '';
      this.state = STATE_CONNECTING;
      this._setReason(reason, 'begin');
      return this.connectionGeneration;
    },
    async _ensureConnected(generation = this.connectionGeneration) {
      if (!this._isGenerationCurrent(generation)) return;
      if (this._socket && this.bearerToken) return;
      if (this.desiredMode === DESIRED_IDLE) return;
      this.state = STATE_CONNECTING;
      try {
        this._setReason('connecting_socket', 'socket_import');
        const ioMod = await import('/vendor/socket.io.esm.min.js');
        if (!this._isGenerationCurrent(generation) || this.desiredMode === DESIRED_IDLE) return;
        const ioFactory = ioMod.io || ioMod.default;
        const apiMod = await import('/js/api.js');
        if (!this._isGenerationCurrent(generation) || this.desiredMode === DESIRED_IDLE) return;
        this._setReason('connecting_token', 'csrf');
        const csrf = apiMod.getCsrfToken ? await apiMod.getCsrfToken() : '';
        if (!this._isGenerationCurrent(generation) || this.desiredMode === DESIRED_IDLE) return;
        const socket = ioFactory('/ws', {
          autoConnect: false,
          reconnection: true,
          transports: ['websocket', 'polling'],
          withCredentials: true,
          auth: { csrf_token: csrf, handlers: [VOQUALIZER_HANDLER] },
        });
        this._socket = socket;
        this._bindSocket(socket, generation);
        this._setReason('connecting_socket', 'socket_connect');
        socket.connect();
        await new Promise((resolve) => {
          let done = false;
          const finish = () => { if (!done) { done = true; resolve(); } };
          try { socket.once && socket.once('connect', finish); } catch (_e) {}
          setTimeout(finish, 5000);
        });
        if (!this._isGenerationCurrent(generation) || this.desiredMode === DESIRED_IDLE) return;
        this._setReason('connecting_init', 'init');
        await new Promise((resolve) => {
          let done = false;
          const finish = () => { if (!done) { done = true; resolve(); } };
          try {
            this.lastTtsEnabledSent = this.isTtsEnabled();
            this._publishDebug();
            socket.emit('voqualizer_init', {
              context_id: this.contextId || '',
              input_codec: INPUT_CODEC,
              output_codec: OUTPUT_CODEC,
              tts: { enabled: this.isTtsEnabled() },
              asr_submit_mode: 'context_bridge',
            }, (response) => {
              if (!this._isGenerationCurrent(generation) || this.desiredMode === DESIRED_IDLE) { finish(); return; }
              const data = this._unwrapPayload(response);
              this.sessionId = data.session_id || this.sessionId;
              this.bearerToken = data.bearer_token || this.bearerToken;
              if (this.desiredMode === DESIRED_TTS) this.state = STATE_TTS_READY;
              this._setReason('ready', 'init_ack');
              finish();
            });
          } catch (_e) { finish(); }
          setTimeout(finish, 5000);
        });
      } catch (err) {
        if (!this._isGenerationCurrent(generation) || this.desiredMode === DESIRED_IDLE || this.intentionalDisconnect) return;
        this.lastError = err && err.message ? err.message : String(err);
        this.state = STATE_ERROR;
        this._setReason('connect_error');
      }
    },
    _bindSocket(socket, generation) {
      const guard = (fn) => (payload) => {
        if (socket !== this._socket || !this._isGenerationCurrent(generation)) return;
        fn(payload);
      };
      try {
        if (socket.onAny) {
          socket.onAny((eventName, payload) => {
            if (socket !== this._socket || !this._isGenerationCurrent(generation)) return;
            this.lastAnySocketEvent = String(eventName || '');
            this.lastAnySocketEventAt = Date.now();
            try {
              const data = this._unwrapPayload(payload);
              this.lastAnySocketPayloadKeys = data && typeof data === 'object' ? Object.keys(data).slice(0, 20).join(',') : '';
            } catch (_e) {
              this.lastAnySocketPayloadKeys = '';
            }
            this._publishDebug();
          });
        }
      } catch (_e) {}
      socket.on('connect', guard(() => {
        this.lastSocketEvent = 'connect';
        if (this.intentionalDisconnect || this.desiredMode === DESIRED_IDLE) {
          this._setReason('socket_reconnect_ignored');
          try { socket.disconnect(); } catch (_e) {
      this._cancelAsrPromptMirrorClear?.();}
          return;
        }
        this._setReason('connecting_socket', 'socket_connect');
      }));
      socket.on('reconnect', guard(() => {
        this.lastSocketEvent = 'reconnect';
        if (this.intentionalDisconnect || this.desiredMode === DESIRED_IDLE) {
          this._setReason('socket_reconnect_ignored');
          try { socket.disconnect(); } catch (_e) {}
          return;
        }
        this._setReason('socket_reconnect', 'socket_reconnect');
      }));
      socket.on('reconnect_attempt', guard(() => {
        this.lastSocketEvent = 'reconnect_attempt';
        if (this.intentionalDisconnect || this.desiredMode === DESIRED_IDLE) {
          this._setReason('socket_reconnect_ignored');
          return;
        }
        this._setReason('socket_reconnect_attempt');
      }));
      socket.on('disconnect', guard((reason) => {
        this.lastSocketEvent = 'disconnect';
        this.lastDisconnectReason = reason || this.lastDisconnectReason;
        if (this.intentionalDisconnect || this.desiredMode === DESIRED_IDLE) {
          this._setReason('intentional_disconnect');
          return;
        }
        this._setReason('socket_disconnect');
      }));
      socket.on('connect_error', guard((err) => {
        this.lastSocketEvent = 'connect_error';
        if (this.intentionalDisconnect || this.desiredMode === DESIRED_IDLE) return;
        this.lastError = err && err.message ? err.message : String(err);
        this.state = STATE_ERROR;
        this._setReason('connect_error');
      }));
      socket.on('voqualizer_ready', guard((payload) => {
        if (this.desiredMode === DESIRED_IDLE) return;
        const data = this._unwrapPayload(payload);
        this.sessionId = data.session_id || this.sessionId;
        this.bearerToken = data.bearer_token || this.bearerToken;
        if (this.desiredMode === DESIRED_TTS) this.state = STATE_TTS_READY;
        this._setReason('ready', 'ready_event');
      }));
      socket.on('voqualizer_agent_response_final', guard((payload) => this._handleAgentFinal(payload)));
      socket.on('voqualizer_asr_partial', guard((payload) => this._handleAsrPartial(payload)));
      socket.on('voqualizer_asr_final', guard((payload) => this._handleAsrFinal(payload)));
      socket.on('voqualizer_tts_chunk', guard((payload) => {
        this._recordRawTtsPush('voqualizer_tts_chunk', payload);
        this._handleTtsChunk(payload, 'push');
      }));
      socket.on('voqualizer_tts_done', guard((payload) => {
        this._recordRawTtsPush('voqualizer_tts_done', payload);
        this._handleTtsDone(payload, 'push');
      }));
      socket.on('voqualizer_error', guard((payload) => {
        if (this.intentionalDisconnect || this.desiredMode === DESIRED_IDLE) return;
        const data = this._unwrapPayload(payload);
        this.lastError = data.message || data.code || 'voqualizer_error';
        this.state = STATE_ERROR;
        this._setReason('voqualizer_error');
      }));
    },
    _unwrapPayload(payload) {
      if (payload && payload.results && payload.results[0] && payload.results[0].data) return payload.results[0].data;
      return (payload && payload.data) || payload || {};
    },
    async _disconnect(reason = 'manual_stop') {
      this.lastDisconnectReason = reason;
      this.intentionalDisconnect = true;
      const socket = this._socket;
      if (socket) {
        try {
          if (this.bearerToken) socket.emit('voqualizer_control', { action: 'end_session', bearer_token: this.bearerToken });
          socket.disconnect();
        } catch (_e) {}
      }
      if (socket === this._socket) this._socket = null;
      this.bearerToken = '';
      this.sessionId = '';
      this._setReason('intentional_disconnect');
    },
    async _startMic(generation = this.connectionGeneration) {
      if (!this._isGenerationCurrent(generation) || this.desiredMode === DESIRED_IDLE) return;
      if (this.capturing) return;
      this.lastConnectPhase = 'mic_init';
      this.startTs = Date.now();
      this.seq = 0;
      const mic = await initMicWorklet({
        onAudio: ({ pcm16, seq, tsMs }) => this._sendAudio(pcm16, seq, tsMs, generation),
        onVu: (vu) => this._handleMicVu(vu),
        sampleRate: PCM_SAMPLE_RATE,
      });
      if (!this._isGenerationCurrent(generation) || this.desiredMode === DESIRED_IDLE) {
        try { mic && mic.stop && mic.stop(); } catch (_e) {}
        return;
      }
      this._mic = mic;
      this.capturing = true;
      this._setReason('mic_started', 'mic_started');
    },
    _clearMicSpeech(reason = 'utterance_finalized', cooldownMs = MIC_SPEECH_FINAL_COOLDOWN_MS) {
      this.micSpeechActive = false;
      this.micSpeechStartedAt = 0;
      this.micSpeechLastActiveAt = 0;
      this.micSpeechCooldownUntil = cooldownMs > 0 ? Date.now() + cooldownMs : 0;
      if (reason) this._setReason(reason);
      this._publishDebug();
    },
    _handleMicVu(vu = {}) {
      const level = Math.max(0, Math.min(1, Number(vu.level || vu.rms || 0) || 0));
      const peak = Math.max(0, Math.min(1, Number(vu.peak || 0) || 0));
      const rms = Math.max(0, Math.min(1, Number(vu.rms || 0) || 0));
      this.micVuLevel = level;
      this.micVuPeak = peak;
      this.micVuRms = rms;
      this.micVuClipped = !!vu.clipped;
      this.lastMicVuAt = Date.now();
      const isAboveSpeechThreshold = level >= MIC_SPEECH_ACTIVE_THRESHOLD || peak >= MIC_SPEECH_ACTIVE_THRESHOLD;
      const canStartSpeech = Date.now() >= (this.micSpeechCooldownUntil || 0);
      if (isAboveSpeechThreshold) this.micSpeechLastActiveAt = this.lastMicVuAt;
      if (canStartSpeech && !this.micSpeechActive && isAboveSpeechThreshold) {
        this.micSpeechActive = true;
        this.micSpeechStartedAt = this.lastMicVuAt;
        this.micSpeechCooldownUntil = 0;
        this._setReason('mic_speech_detected');
      }
      if (this.micSpeechActive && !isAboveSpeechThreshold && this.micSpeechLastActiveAt && (this.lastMicVuAt - this.micSpeechLastActiveAt) >= MIC_SPEECH_SILENCE_CLEAR_MS) {
        this._clearMicSpeech('mic_silence_detected', 0);
      }
      maybeLocalBargeInFromMic(vu, tracker);
      this._publishDebug();
    },
    _resetMicVu(reason = 'mic_stopped') {
      this.micVuLevel = 0;
      this.micVuPeak = 0;
      this.micVuRms = 0;
      this.micVuClipped = false;
      this.micSpeechActive = false;
      this.micSpeechStartedAt = 0;
      this.micSpeechLastActiveAt = 0;
      this.micSpeechCooldownUntil = 0;
      if (reason) this.lastPlaybackStopReason = this.lastPlaybackStopReason || '';
      this._publishDebug();
    },
    async _stopMic(reason = 'manual_stop') {
      if (!this.capturing && !this._mic) return;
      try { this._mic && this._mic.stop(); } catch (_e) {}
      this._mic = null;
      this.capturing = false;
      this._resetMicVu(reason);
      this.lastDisconnectReason = reason;
      this._setReason('mic_stopped');
    },
    async speakText(text, options = {}) {
      const speechText = String(text || '').trim();
      if (!speechText) return { ok: false, reason: 'empty_text' };
      if (!this.isTtsEnabled()) {
        this.lastDirectTtsError = 'tts_disabled_ui';
        this._publishDebug();
        return { ok: false, reason: 'tts_disabled_ui' };
      }
      if (!this._socket || !this.bearerToken || !this.sessionId) {
        await this._ensurePassiveTtsSession('direct_tts_repair_passive_session');
      }
      if (!this._socket || !this.bearerToken || !this.sessionId) {
        const generation = this.connectionGeneration || this._beginLifecycle(DESIRED_TTS, 'direct_tts_requested');
        await this._ensureConnected(generation);
      }
      if (!this._socket || !this.bearerToken || !this.sessionId) {
        this.lastDirectTtsError = 'no_voqualizer_session';
        this._publishDebug();
        return { ok: false, reason: 'no_voqualizer_session' };
      }
      const utteranceId = String(options.utterance_id || options.utteranceId || `gui-response-${Date.now().toString(36)}`);
      const payload = {
        text: speechText,
        bearer_token: this.bearerToken,
        utterance_id: utteranceId,
        metadata: {
          source: 'webui_rendered_response_fallback',
          context_id: this.contextId || '',
          response_id: String(options.response_id || options.responseId || ''),
        },
      };
      this.lastDirectTtsText = speechText.slice(0, 160);
      this.lastDirectTtsAt = Date.now();
      this.lastDirectTtsError = '';
      this.directTtsCount += 1;
      this._publishDebug();
      return await new Promise((resolve) => {
        let settled = false;
        const finish = (value) => {
          if (settled) return;
          settled = true;
          this.lastDirectTtsAck = value;
          if (value && value.code) this.lastDirectTtsError = value.code;
          if (value && Array.isArray(value.tts_chunks) && value.tts_chunks.length) {
            this._handleAckTtsFallback(value);
          }
          this._publishDebug();
          resolve(value);
        };
        try {
          this._socket.emit('voqualizer_user_text', payload, (ack) => {
            finish(this._unwrapPayload(ack));
          });
        } catch (err) {
          finish({ ok: false, reason: 'emit_failed', message: err && err.message ? err.message : String(err) });
        }
        setTimeout(() => finish({ ok: false, reason: 'ack_timeout' }), 15000);
      });
    },
    _promptElement() {
      const selectors = [
        'textarea#chat-input',
        '#chat-input',
        'textarea[x-model="$store.chatInput.message"]',
        'textarea[name="message"]',
        'textarea#message',
        'textarea#prompt',
        'textarea[x-model*="message"]',
        'textarea[x-model*="prompt"]',
        'textarea',
        '[contenteditable="true"]',
      ];
      for (const selector of selectors) {
        try {
          const el = globalThis.document && globalThis.document.querySelector(selector);
          if (el) { this.lastPromptElementSelector = selector; return el; }
        } catch (_e) {}
      }
      return null;
    },
    _promptValue(el) {
      if (!el) return '';
      if (el.isContentEditable) return el.textContent || '';
      return el.value || '';
    },
    _setPromptValue(el, text) {
      if (!el) return false;
      const value = String(text || '');
      try {
        const chatInput = globalThis.Alpine && globalThis.Alpine.store && globalThis.Alpine.store('chatInput');
        if (chatInput) {
          chatInput.message = value;
        }
      } catch (_e) {}
      if (el.isContentEditable) {
        el.textContent = value;
      } else {
        el.value = value;
      }
      try { el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value })); } catch (_e) {
        try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (_e2) {}
      }
      try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch (_e) {}
      return true;
    },
    _canOwnPrompt(el) {
      const current = this._promptValue(el).trim();
      return !current || this.asrPromptDraftOwned || current === this.lastAsrPartialText || current === this.lastAsrFinalText;
    },
    _writeAsrPromptDraft(text, kind = 'partial') {
      const isClearDraft = String(kind || '').toLowerCase().includes('clear');
      const draft = isClearDraft ? '' : String(text || '').trim();
      if (!draft && !isClearDraft) return false;
      const el = this._promptElement();
      if (!el) { this.lastPromptSubmitSkipReason = 'prompt_missing'; this._publishDebug(); return false; }
      if (!this._canOwnPrompt(el)) { this.lastPromptSubmitSkipReason = 'prompt_not_owned'; this._publishDebug(); return false; }
      this._setPromptValue(el, draft);
      this.asrPromptDraftOwned = true;
      if (kind === 'partial') this.lastAsrPartialPromptAt = Date.now();
      if (kind === 'final') this.lastAsrFinalPromptAt = Date.now();
      this.lastPromptSubmitSkipReason = '';
      this._publishDebug();
      return true;
    },
    _submitPromptFromAsr(text) {
      const finalText = String(text || '').trim();
      if (!finalText) { this.lastPromptSubmitSkipReason = 'empty_final'; this._publishDebug(); return false; }
      const el = this._promptElement();
      if (!el) { this.lastPromptSubmitSkipReason = 'prompt_missing'; this._publishDebug(); return false; }
      if (!this._writeAsrPromptDraft(finalText, 'final')) return false;
      try {
        const chatInput = globalThis.Alpine && globalThis.Alpine.store && globalThis.Alpine.store('chatInput');
        if (chatInput && typeof chatInput.sendMessage === 'function') {
          chatInput.sendMessage();
          this.lastPromptSubmitAt = Date.now();
          this.lastPromptSubmitText = finalText.slice(0, 160);
          this.asrPromptDraftOwned = false;
          this.lastPromptSubmitSkipReason = '';
          this._publishDebug();
          return true;
        }
      } catch (_e) {}
      try {
        if (typeof globalThis.sendMessage === 'function') {
          globalThis.sendMessage();
          this.lastPromptSubmitAt = Date.now();
          this.lastPromptSubmitText = finalText.slice(0, 160);
          this.asrPromptDraftOwned = false;
          this.lastPromptSubmitSkipReason = '';
          this._publishDebug();
          return true;
        }
      } catch (_e) {}
      const selectors = [
        '#send-button',
        'button[aria-label="Send message"]',
        'button[type="submit"]',
        'button[aria-label="Send"]',
        'button[title="Send"]',
        '[data-testid="send-button"]',
      ];
      for (const selector of selectors) {
        try {
          const btn = globalThis.document && globalThis.document.querySelector(selector);
          if (btn && !btn.disabled) {
            btn.click();
            this.lastPromptSubmitAt = Date.now();
            this.lastPromptSubmitText = finalText.slice(0, 160);
            this.asrPromptDraftOwned = false;
            this.lastPromptSubmitSkipReason = '';
            this._publishDebug();
            return true;
          }
        } catch (_e) {}
      }
      try {
        const form = el.closest && el.closest('form');
        if (form && form.requestSubmit) {
          form.requestSubmit();
          this.lastPromptSubmitAt = Date.now();
          this.lastPromptSubmitText = finalText.slice(0, 160);
          this.asrPromptDraftOwned = false;
          this.lastPromptSubmitSkipReason = '';
          this._publishDebug();
          return true;
        }
      } catch (_e) {}
      this.lastPromptSubmitSkipReason = 'send_control_missing';
      this._publishDebug();
      return false;
    },
    _mirrorAsrTextToPrompt(text, kind = 'partial', source = 'event') {
      const value = String(text || '').trim();
      if (!value) return false;
      const ok = this._writeAsrPromptDraft(value, kind);
      if (ok) {
        this.lastAsrPromptSource = source;
        this.lastAsrPromptMirrorAt = Date.now();
      const mirrorKind = String(kind || '').toLowerCase();
      const mirrorSource = String(source || '').toLowerCase();
      const isFinalMirror = mirrorKind.includes('final') || mirrorSource.includes('final');
      const isPartialMirror = mirrorKind.includes('partial') || mirrorSource.includes('partial');
      if (isFinalMirror && this.asrPromptDraftOwned) {
        this._scheduleAsrPromptMirrorClear('context_bridge_final_blank_populate');
      } else if (isPartialMirror && !Number(this.lastAsrPromptClearDueAt || 0)) {
        this._cancelAsrPromptMirrorClear();
      }
        this._publishDebug();
      }
      return ok;
    },
    _cancelAsrPromptMirrorClear() {
      if (this._asrPromptClearTimer) {
        clearTimeout(this._asrPromptClearTimer);
        this._asrPromptClearTimer = null;
      }
    },

    _clearAsrPromptMirror(reason = 'context_bridge_final_blank_populate') {
      if (this._asrPromptClearTimer) {
        clearTimeout(this._asrPromptClearTimer);
        this._asrPromptClearTimer = null;
      }
      if (!this.asrPromptDraftOwned) {
        this.lastAsrPromptClearReason = 'prompt_not_owned';
        this._publishDebug();
        return false;
      }

      // Clearing reuses the same path that successfully mirrors ASR text into
      // A0's prompt, but with a blank value.  Ownership is the safety gate;
      // do not compare transcript text because ASR punctuation/casing can drift.
      const ok = this._writeAsrPromptDraft('', 'clear');
      if (!ok) {
        this.lastAsrPromptClearReason = 'blank_populate_failed';
        this._publishDebug();
        return false;
      }

      this.asrPromptDraftOwned = false;
      this.lastAsrPromptClearDueAt = 0;
      this.lastAsrPromptClearAt = Date.now();
      this.lastAsrPromptClearReason = reason;
      this._publishDebug();
      return true;
    },


    _maybeClearAsrPromptMirror(reason = 'ack_tick_due') {
      if (!this.asrPromptDraftOwned) return false;
      const dueAt = Number(this.lastAsrPromptClearDueAt || 0);
      if (!dueAt || Date.now() < dueAt) return false;
      return this._clearAsrPromptMirror(reason);
    },

    _scheduleFinalAsrPromptMirrorClear(reason = 'context_bridge_final_blank_populate') {
      if (!this.asrPromptDraftOwned) return false;
      this._scheduleAsrPromptMirrorClear(reason);
      this._publishDebug();
      return true;
    },

    _scheduleAsrPromptMirrorClear(reason = 'context_bridge_final_blank_populate') {
      this._cancelAsrPromptMirrorClear();
      this.lastAsrPromptClearScheduledAt = Date.now();
      const delay = Math.max(100, Number(this.lastAsrPromptGraceClearDelayMs || 900));
      this.lastAsrPromptClearDueAt = this.lastAsrPromptClearScheduledAt + delay;
      this._publishDebug?.();
      this._asrPromptClearTimer = setTimeout(() => {
        this._asrPromptClearTimer = null;
        this._maybeClearAsrPromptMirror(reason);
      }, delay);
    },


    _handleAudioAckForAsr(data) {
      if (!data || typeof data !== 'object') return;
      this._maybeClearAsrPromptMirror('ack_tick_due');
      const injectionCount = Number(data?.context_injections || 0);
      if (injectionCount > Number(this.lastContextInjectionCount || 0)) {
        this.lastContextInjectionCount = injectionCount;
        this.lastContextInjectionAckAt = Date.now();
        this._clearAsrPromptMirror('context_bridge_submitted');
      }

      const finalText = String(data.asr_last_final_text || '').trim();
      if (finalText && finalText === this.lastAckAsrFinalText && this.asrPromptDraftOwned) {
        this._clearAsrPromptMirror('ack_final_duplicate_blank_populate');
      }
      if (finalText && finalText !== this.lastAckAsrFinalText && finalText !== this.lastAsrFinalText) {
        this.lastAckAsrFinalText = finalText;
        this.asrFinalCount += 1;
        this.lastAsrFinalText = finalText.slice(0, 160);
        this._clearMicSpeech('asr_final_ack_received');
        // ACK fallback mirrors text into the visible prompt, but intentionally
        // does not click Send. The GUI remains in context_bridge mode so the
        // backend context injection is the canonical prompt submission path;
        // auto-submitting here would risk duplicate prompts.
        const mirrored = this._mirrorAsrTextToPrompt(finalText, 'final', 'audio_ack_final');
        if (mirrored) {
          this._clearAsrPromptMirror('audio_ack_final_blank_populate');
        }
        this._publishDebug();
        return;
      }
      const partialText = String(data.asr_last_partial_text || '').trim();
      if (partialText && partialText !== this.lastAsrPartialText) {
        this.lastAsrPartialAt = Date.now();
        this.lastAsrPartialText = partialText.slice(0, 160);
        this._mirrorAsrTextToPrompt(partialText, 'partial', 'audio_ack_partial');
        this._publishDebug();
      }
    },
    _sendAudio(pcm16, seq, tsMs, generation = this.connectionGeneration) {
      if (!this._isGenerationCurrent(generation)) return;
      if (!this._socket || !this.bearerToken || this.desiredMode === DESIRED_IDLE) return;
      this.seq = ((seq | 0) || (this.seq + 1)) & 0xffff;
      const tsRel = ((tsMs | 0) || (Date.now() - this.startTs)) & 0xffff;
      const payload = audioChunkPayload(this.seq, tsRel, pcm16, { bearer_token: this.bearerToken });
      try {
        this._socket.emit('voqualizer_audio_chunk', payload, (ack) => {
          const data = this._unwrapPayload(ack);
          this.lastAudioAckAt = Date.now();
          this.lastAudioAck = data;
          this.lastAudioAckError = data && (data.code || data.error || data.message) ? String(data.code || data.error || data.message) : '';
          this._handleAudioAckForAsr(data);
          this._publishDebug();
        });
        this.audioFramesSent += 1;
        this.lastAudioSeqSent = this.seq;
        this._publishDebug();
      } catch (err) {
        this.lastAudioAckError = err && err.message ? err.message : String(err || 'audio_emit_failed');
        this._publishDebug();
      }
    },
    async _sendFinalFrame(generation = this.connectionGeneration) {
      if (!this._isGenerationCurrent(generation)) return;
      if (!this._socket || !this.bearerToken) return;
      const payload = audioChunkPayload((this.seq + 1) & 0xffff, ((Date.now() - this.startTs) & 0xffff), new Uint8Array(0), { bearer_token: this.bearerToken, is_final: true });
      try {
        this._socket.emit('voqualizer_audio_chunk', payload, (ack) => {
          const data = this._unwrapPayload(ack);
          this.lastAudioAckAt = Date.now();
          this.lastAudioAck = data;
          this.lastAudioAckError = data && (data.code || data.error || data.message) ? String(data.code || data.error || data.message) : '';
          this._handleAudioAckForAsr(data);
          this._publishDebug();
        });
        this.lastFinalFrameSentAt = Date.now();
        this.lastFinalFrameReason = this._pttOverlay ? 'ptt_overlay_release' : 'ptt_release';
        this._clearMicSpeech('final_frame_sent');
      } catch (err) {
        this.lastAudioAckError = err && err.message ? err.message : String(err || 'final_audio_emit_failed');
        this._publishDebug();
      }
    },
    _handleAgentFinal(payload) {
      const data = this._unwrapPayload(payload);
      this.lastAgentFinalAt = Date.now();
      this.agentFinalCount += 1;
      this.lastAgentFinalText = String(data.speech_text || data.text || '').slice(0, 160);
      this._clearMicSpeech('agent_final_received');
      this._publishDebug();
    },
    _handleAsrPartial(payload) {
      const data = this._unwrapPayload(payload);
      const text = String(data.text || '').trim();
      this.lastAsrPartialAt = Date.now();
      this.lastAsrPartialText = text.slice(0, 160);
      if (text) this._mirrorAsrTextToPrompt(text, 'partial', 'asr_partial_event');
      this._publishDebug();
    },
    _handleAsrFinal(payload) {
      const data = this._unwrapPayload(payload);
      const text = String(data.text || '').trim();
      this.asrFinalCount += 1;
      this.lastAsrFinalText = text.slice(0, 160);
      this.lastAsrFinalUtteranceId = String((data.metadata && data.metadata.utterance_id) || data.utterance_id || '');
      this._clearMicSpeech('asr_final_received');
      if (text) {
        const mirrored = this._mirrorAsrTextToPrompt(text, 'final', 'asr_final_event');
        if (mirrored) {
          this._clearAsrPromptMirror('asr_final_event_blank_populate');
        }
      }
      this._publishDebug();
    },
    _handleAckTtsFallback(ack) {
      const data = this._unwrapPayload(ack);
      const chunks = Array.isArray(data.tts_chunks) ? data.tts_chunks : [];
      if (!chunks.length) return;
      const utteranceId = String(data.utterance_id || '');
      const pushedForUtterance = utteranceId ? Number(this.pushedTtsChunksByUtterance[utteranceId] || 0) : 0;
      // If server-pushed chunks for THIS utterance already arrived, avoid duplicate playback.
      // Do not use the aggregate ttsChunkCount here: ACK fallback chunks also increment it,
      // and previous utterances must never suppress the reliable fallback for a new one.
      if (pushedForUtterance > 0) {
        this.lastAckTtsFallbackReason = `push_already_received:${pushedForUtterance}`;
        this._publishDebug();
        return;
      }
      this.lastAckTtsFallbackAt = Date.now();
      this.lastAckTtsFallbackChunks = chunks.length;
      this.lastAckTtsFallbackReason = 'ack_chunks';
      this.lastAckTtsUtteranceId = utteranceId;
      for (const chunk of chunks) {
        this._handleTtsChunk(chunk, 'ack');
      }
      if (data.tts_done) {
        this._handleTtsDone(data.tts_done, 'ack');
      } else {
        this._handleTtsDone({
          session_id: data.session_id || this.sessionId || '',
          utterance_id: data.utterance_id || '',
          chunks: chunks.length,
          cancelled: false,
        }, 'ack');
      }
      this._publishDebug();
    },
    async _handleTtsChunk(payload, deliverySource = 'unknown') {
      const data = this._unwrapPayload(payload);
      const utteranceId = String(data.utterance_id || 'default');
      this.lastTtsChunkAt = Date.now();
      this.lastTtsUtteranceId = utteranceId;
      this.lastTtsChunkSource = deliverySource;
      this.ttsChunkCount += 1;
      if (deliverySource === 'push') {
        this.pushedTtsChunkCount += 1;
        this.lastPushedTtsUtteranceId = utteranceId;
        this.pushedTtsChunksByUtterance = {
          ...this.pushedTtsChunksByUtterance,
          [utteranceId]: Number(this.pushedTtsChunksByUtterance[utteranceId] || 0) + 1,
        };
      } else if (deliverySource === 'ack') {
        this.ackTtsChunkCount += 1;
        this.lastAckTtsUtteranceId = utteranceId;
      }
      if (!this.isTtsEnabled()) { this.lastTtsSkipReason = 'tts_disabled_ui'; this._publishDebug(); return; }
      const audio = bytesFromTtsPayload(payload);
      this.lastTtsChunkBytes = audio.byteLength || 0;
      if (tracker.cancelledTtsUtterances.has(utteranceId)) { this.lastTtsSkipReason = 'cancelled_utterance'; this._publishDebug(); return; }
      const codec = normalizeTtsCodec(data, payload);
      if (codec === 'wav' || codec === 'mp3' || codec === 'opus') {
        this.lastTtsSkipReason = `encoded_${codec}_not_streamed_in_gui`;
        this._publishDebug();
        return;
      }
      const aligned = alignPcm16Bytes(audio, carryMap, utteranceId);
      const samples = pcm16ToFloat32(aligned);
      if (!samples.length) { this.lastTtsSkipReason = 'empty_audio'; this._publishDebug(); return; }
      const sampleRate = ttsSampleRate(data, payload, codec);
      const ctx = this._ensurePlaybackContext(sampleRate);
      try { if (ctx.state === 'suspended' && ctx.resume) await ctx.resume(); } catch (_e) {}
      const buffer = ctx.createBuffer(1, samples.length, sampleRate);
      buffer.copyToChannel(samples, 0);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      const startAt = Math.max(ctx.currentTime + 0.01, this._playbackTail || 0);
      source.start(startAt);
      this._playbackTail = startAt + buffer.duration;
      this.lastPlaybackStartAt = Date.now();
      this.lastTtsSkipReason = '';
      rememberPlaybackSource(tracker, utteranceId, source);
      this._publishDebug();
    },
    _handleTtsDone(payload, deliverySource = 'unknown') {
      const data = this._unwrapPayload(payload);
      const utteranceId = String(data.utterance_id || 'default');
      this.lastTtsDoneAt = Date.now();
      this.ttsDoneCount += 1;
      if (deliverySource === 'push') {
        this.pushedTtsDoneCount += 1;
        this.lastPushedTtsUtteranceId = utteranceId;
      } else if (deliverySource === 'ack') {
        this.ackTtsDoneCount += 1;
        this.lastAckTtsUtteranceId = utteranceId;
      }
      this.lastTtsUtteranceId = utteranceId;
      if (data.reason) this.lastTtsSkipReason = data.reason;
      if (data.cancelled || data.reason === 'barge_in') { this.lastPlaybackStopReason = data.reason || 'cancelled'; tracker.stopPlaybackForUtterance(utteranceId); }
      clearPcm16Carry(carryMap, utteranceId);
      this._publishDebug();
    },
    _ensurePlaybackContext(sampleRate) {
      if (!this._playbackCtx) {
        this._playbackCtx = new (globalThis.AudioContext || globalThis.webkitAudioContext)({ sampleRate });
        this._playbackTail = 0;
      }
      return this._playbackCtx;
    },
  };
  return state;
}

export function registerVoqualizerStore() {
  if (globalThis.__a0VoqualizerConversationStore) {
    const existing = globalThis.__a0VoqualizerConversationStore;
    if (globalThis.Alpine && globalThis.Alpine.store) {
      try { if (!globalThis.Alpine.store('voqualizer')) globalThis.Alpine.store('voqualizer', existing); } catch (_e) {}
    }
    try { existing.init && existing.init(); } catch (_e) {}
    return existing;
  }
  if (!globalThis.Alpine || !globalThis.Alpine.store) return undefined;
  try {
    const alpineExisting = globalThis.Alpine.store('voqualizer');
    if (alpineExisting) {
      globalThis.__a0VoqualizerConversationStore = alpineExisting;
      try { alpineExisting.init && alpineExisting.init(); } catch (_e) {}
      return alpineExisting;
    }
  } catch (_e) {}
  const store = createVoqualizerStore();
  globalThis.__a0VoqualizerConversationStore = store;
  globalThis.Alpine.store('voqualizer', store);
  try { store.init && store.init(); } catch (_e) {}
  return store;
}

if (globalThis.document) {
  if (globalThis.Alpine && globalThis.Alpine.store) {
    registerVoqualizerStore();
  } else {
    globalThis.document.addEventListener('alpine:init', () => registerVoqualizerStore());
  }
}
