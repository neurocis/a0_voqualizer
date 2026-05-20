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
export const STATE_STOPPING = 'stopping';
export const STATE_ERROR = 'error';

export const DESIRED_IDLE = 'idle';
export const DESIRED_CONVERSATIONAL = 'conversational';
export const DESIRED_PTT = 'ptt';

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
    lastTtsEnabledSent: null,
    lastTtsControlAck: null,
    lastTtsChunkAt: 0,
    lastTtsDoneAt: 0,
    lastTtsChunkBytes: 0,
    lastTtsUtteranceId: '',
    lastTtsSkipReason: '',
    lastPlaybackStartAt: 0,
    ttsChunkCount: 0,
    ttsDoneCount: 0,
    agentFinalCount: 0,
    asrFinalCount: 0,
    lastPlaybackStopReason: '',
    lastAgentFinalAt: 0,
    lastAgentFinalText: '',
    lastFinalFrameSentAt: 0,
    lastFinalFrameReason: '',
    lastAudioSeqSent: 0,
    lastAsrFinalText: '',
    lastAsrFinalUtteranceId: '',
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
        ttsEnabled: this.isTtsEnabled(),
        lastTtsEnabledSent: this.lastTtsEnabledSent,
        lastTtsControlAck: this.lastTtsControlAck,
        lastTtsChunkAt: this.lastTtsChunkAt,
        lastTtsDoneAt: this.lastTtsDoneAt,
        lastTtsChunkBytes: this.lastTtsChunkBytes,
        lastTtsUtteranceId: this.lastTtsUtteranceId,
        lastTtsSkipReason: this.lastTtsSkipReason,
        lastPlaybackStartAt: this.lastPlaybackStartAt,
        ttsChunkCount: this.ttsChunkCount,
        ttsDoneCount: this.ttsDoneCount,
        agentFinalCount: this.agentFinalCount,
        asrFinalCount: this.asrFinalCount,
        lastPlaybackStopReason: this.lastPlaybackStopReason,
        lastAgentFinalAt: this.lastAgentFinalAt,
        lastAgentFinalText: this.lastAgentFinalText,
        lastFinalFrameSentAt: this.lastFinalFrameSentAt,
        lastFinalFrameReason: this.lastFinalFrameReason,
        lastAudioSeqSent: this.lastAudioSeqSent,
        lastAsrFinalText: this.lastAsrFinalText,
        lastAsrFinalUtteranceId: this.lastAsrFinalUtteranceId,
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
    },
    toggleTts() {
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
    },
    async onTap() {
      if (this.state === STATE_CONNECTING || this.state === STATE_STOPPING) return;
      if (this.state === STATE_CONVERSATIONAL || this.desiredMode === DESIRED_CONVERSATIONAL) {
        this._setReason('manual_stop');
        this.desiredMode = DESIRED_IDLE;
        this.intentionalDisconnect = true;
        this.connectionGeneration += 1;
        this.state = STATE_STOPPING;
        await this._stopMic('manual_stop');
        await this._disconnect('manual_stop');
        this.conversational = false;
        this.pttActive = false;
        this.state = STATE_IDLE;
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
        this.desiredMode = DESIRED_IDLE;
        this.intentionalDisconnect = true;
        this.connectionGeneration += 1;
        this.state = STATE_STOPPING;
        await this._stopMic('ptt_release');
        await this._disconnect('ptt_release');
        this.conversational = false;
        this.state = STATE_IDLE;
        this._setReason('ptt_final_disconnect');
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
            }, (response) => {
              if (!this._isGenerationCurrent(generation) || this.desiredMode === DESIRED_IDLE) { finish(); return; }
              const data = this._unwrapPayload(response);
              this.sessionId = data.session_id || this.sessionId;
              this.bearerToken = data.bearer_token || this.bearerToken;
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
      socket.on('connect', guard(() => {
        this.lastSocketEvent = 'connect';
        if (this.intentionalDisconnect || this.desiredMode === DESIRED_IDLE) {
          this._setReason('socket_reconnect_ignored');
          try { socket.disconnect(); } catch (_e) {}
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
        this._setReason('ready', 'ready_event');
      }));
      socket.on('voqualizer_agent_response_final', guard((payload) => this._handleAgentFinal(payload)));
      socket.on('voqualizer_asr_final', guard((payload) => this._handleAsrFinal(payload)));
      socket.on('voqualizer_tts_chunk', guard((payload) => this._handleTtsChunk(payload)));
      socket.on('voqualizer_tts_done', guard((payload) => this._handleTtsDone(payload)));
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
    _sendAudio(pcm16, seq, tsMs, generation = this.connectionGeneration) {
      if (!this._isGenerationCurrent(generation)) return;
      if (!this._socket || !this.bearerToken || this.desiredMode === DESIRED_IDLE) return;
      this.seq = ((seq | 0) || (this.seq + 1)) & 0xffff;
      const tsRel = ((tsMs | 0) || (Date.now() - this.startTs)) & 0xffff;
      const payload = audioChunkPayload(this.seq, tsRel, pcm16, { bearer_token: this.bearerToken });
      try { this._socket.emit('voqualizer_audio_chunk', payload); this.audioFramesSent += 1; this.lastAudioSeqSent = this.seq; this._publishDebug(); } catch (_e) {}
    },
    async _sendFinalFrame(generation = this.connectionGeneration) {
      if (!this._isGenerationCurrent(generation)) return;
      if (!this._socket || !this.bearerToken) return;
      const payload = audioChunkPayload((this.seq + 1) & 0xffff, ((Date.now() - this.startTs) & 0xffff), new Uint8Array(0), { bearer_token: this.bearerToken, is_final: true });
      try { this._socket.emit('voqualizer_audio_chunk', payload); this.lastFinalFrameSentAt = Date.now(); this.lastFinalFrameReason = this._pttOverlay ? 'ptt_overlay_release' : 'ptt_release'; this._clearMicSpeech('final_frame_sent'); } catch (_e) {}
    },
    _handleAgentFinal(payload) {
      const data = this._unwrapPayload(payload);
      this.lastAgentFinalAt = Date.now();
      this.agentFinalCount += 1;
      this.lastAgentFinalText = String(data.speech_text || data.text || '').slice(0, 160);
      this._clearMicSpeech('agent_final_received');
      this._publishDebug();
    },
    _handleAsrFinal(payload) {
      const data = this._unwrapPayload(payload);
      this.asrFinalCount += 1;
      this.lastAsrFinalText = String(data.text || '').slice(0, 160);
      this.lastAsrFinalUtteranceId = String((data.metadata && data.metadata.utterance_id) || data.utterance_id || '');
      this._clearMicSpeech('asr_final_received');
      this._publishDebug();
    },
    async _handleTtsChunk(payload) {
      const data = this._unwrapPayload(payload);
      const utteranceId = data.utterance_id || 'default';
      this.lastTtsChunkAt = Date.now();
      this.lastTtsUtteranceId = utteranceId;
      this.ttsChunkCount += 1;
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
    _handleTtsDone(payload) {
      const data = this._unwrapPayload(payload);
      const utteranceId = data.utterance_id || 'default';
      this.lastTtsDoneAt = Date.now();
      this.ttsDoneCount += 1;
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
