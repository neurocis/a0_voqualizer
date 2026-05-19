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
  fetchSessionToken,
  initMicWorklet,
  createPlaybackTracker,
  alignPcm16Bytes,
  pcm16ToFloat32,
  bytesFromUnknownAudio,
  maybeLocalBargeInFromMic,
  PCM_SAMPLE_RATE,
  INPUT_CODEC,
  OUTPUT_CODEC,
} from '/plugins/a0_voqualizer/webui/lib/voqualizer-audio.js';

export const TAP_HOLD_THRESHOLD_MS = 250;
export const VOQUALIZER_HANDLER = 'plugins/a0_voqualizer/ws_voqualizer';
export const TTS_PREF_PREFIX = 'a0_voqualizer.tts_enabled.';

export const STATE_IDLE = 'idle';
export const STATE_CONNECTING = 'connecting';
export const STATE_CONVERSATIONAL = 'conversational';
export const STATE_PTT_ACTIVE = 'ptt-active';
export const STATE_ERROR = 'error';

export function currentContextId() {
  try {
    if (typeof globalThis.getContext === 'function') {
      const ctx = globalThis.getContext();
      if (ctx) return String(ctx);
    }
  } catch (_e) {}
  try {
    const chats = globalThis.Alpine && globalThis.Alpine.store && globalThis.Alpine.store('chats');
    if (chats) {
      if (chats.selectedContext && chats.selectedContext.id) return String(chats.selectedContext.id);
      if (typeof chats.getSelectedChatId === 'function') {
        const id = chats.getSelectedChatId();
        if (id) return String(id);
      }
      if (chats.selected) return String(chats.selected);
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
    contextId: currentContextId(),
    sessionId: '',
    bearerToken: '',
    capturing: false,
    pttActive: false,
    conversational: false,
    ttsEnabledByContext: {},
    lastError: '',
    audioFramesSent: 0,
    seq: 0,
    startTs: 0,
    holdStartedAt: 0,
    init() {
      this.ttsEnabledByContext[this.contextId] = readTtsEnabled(this.contextId);
      const onContextChange = () => this._onContextChanged();
      try { globalThis.addEventListener && globalThis.addEventListener('a0:context-changed', onContextChange); } catch (_e) {}
      // Best-effort polling fallback because A0 does not always emit a context-change event.
      this._ctxPoll = setInterval(() => {
        const cur = currentContextId();
        if (cur && cur !== this.contextId) this._onContextChanged(cur);
      }, 500);
    },
    destroy() {
      try { clearInterval(this._ctxPoll); } catch (_e) {}
      this._stopMic();
    },
    isTtsEnabled() { return !!this.ttsEnabledByContext[this.contextId]; },
    _onContextChanged(newId) {
      const cur = newId || currentContextId();
      if (cur === this.contextId) return;
      // Context switch: stop, disconnect, reset.
      this._stopMic();
      this._disconnect();
      this.contextId = cur;
      this.ttsEnabledByContext[this.contextId] = readTtsEnabled(this.contextId);
      this.state = STATE_IDLE;
      this.conversational = false;
    },
    toggleTts() {
      const enabled = !this.isTtsEnabled();
      this.ttsEnabledByContext[this.contextId] = enabled;
      writeTtsEnabled(this.contextId, enabled);
      if (this._socket && this.bearerToken) {
        try {
          this._socket.emit('voqualizer_control', {
            action: 'set_tts_enabled',
            enabled,
            bearer_token: this.bearerToken,
          });
        } catch (_e) {}
      }
    },
    // --- Mic gesture entry points (called by extension capture-phase handler) ---
    async onTap() {
      // Quick tap: toggle conversational mode
      if (this.state === STATE_CONVERSATIONAL) {
        await this._stopMic();
        await this._disconnect();
        this.conversational = false;
        this.state = STATE_IDLE;
        return;
      }
      this.conversational = true;
      await this._ensureConnected();
      await this._startMic();
      this.state = STATE_CONVERSATIONAL;
    },
    async onHoldStart() {
      this.holdStartedAt = Date.now();
      this.pttActive = true;
      const wasConversational = this.state === STATE_CONVERSATIONAL;
      this._pttOverlay = wasConversational;
      await this._ensureConnected();
      if (!this.capturing) await this._startMic();
      this.state = STATE_PTT_ACTIVE;
    },
    async onHoldEnd() {
      if (!this.pttActive) return;
      this.pttActive = false;
      // Send explicit end-of-utterance final frame so backend finalizes immediately.
      await this._sendFinalFrame();
      if (this._pttOverlay) {
        // Stay connected in conversational mode.
        this.state = STATE_CONVERSATIONAL;
      } else {
        await this._stopMic();
        await this._disconnect();
        this.conversational = false;
        this.state = STATE_IDLE;
      }
    },
    // --- Internals ---
    async _ensureConnected() {
      if (this._socket && this.bearerToken) return;
      this.state = STATE_CONNECTING;
      try {
        const ioMod = await import('/vendor/socket.io.esm.min.js');
        const ioFactory = ioMod.io || ioMod.default;
        const apiMod = await import('/js/api.js');
        const csrf = apiMod.getCsrfToken ? await apiMod.getCsrfToken() : '';
        const socket = ioFactory({ path: '/socket.io', auth: { csrf_token: csrf, handlers: [VOQUALIZER_HANDLER] } });
        this._socket = socket;
        socket.on('voqualizer_ready', (payload) => {
          const data = (payload && payload.data) || payload || {};
          this.sessionId = data.session_id || this.sessionId;
          this.bearerToken = data.bearer_token || this.bearerToken;
        });
        socket.on('voqualizer_tts_chunk', (payload) => this._handleTtsChunk(payload));
        socket.on('voqualizer_tts_done', (payload) => this._handleTtsDone(payload));
        socket.on('voqualizer_error', (payload) => {
          const data = (payload && payload.data) || payload || {};
          this.lastError = data.message || data.code || 'voqualizer_error';
          this.state = STATE_ERROR;
        });
        await new Promise((resolve, reject) => {
          socket.emit('voqualizer_init', {
            context_id: this.contextId || '',
            input_codec: INPUT_CODEC,
            output_codec: OUTPUT_CODEC,
            tts: { enabled: this.isTtsEnabled() },
          }, (response) => {
            if (response && response.results && response.results[0] && response.results[0].data) {
              const data = response.results[0].data;
              this.sessionId = data.session_id || this.sessionId;
              this.bearerToken = data.bearer_token || this.bearerToken;
            }
            resolve();
          });
          setTimeout(() => resolve(), 5000);
        });
      } catch (err) {
        this.lastError = err && err.message ? err.message : String(err);
        this.state = STATE_ERROR;
      }
    },
    async _disconnect() {
      if (this._socket) {
        try {
          this._socket.emit('voqualizer_control', { action: 'end_session', bearer_token: this.bearerToken });
          this._socket.disconnect();
        } catch (_e) {}
      }
      this._socket = null;
      this.bearerToken = '';
      this.sessionId = '';
    },
    async _startMic() {
      if (this.capturing) return;
      this.startTs = Date.now();
      this.seq = 0;
      const mic = await initMicWorklet({
        onAudio: ({ pcm16, seq, tsMs }) => this._sendAudio(pcm16, seq, tsMs),
        onVu: (vu) => maybeLocalBargeInFromMic(vu, tracker),
        sampleRate: PCM_SAMPLE_RATE,
      });
      this._mic = mic;
      this.capturing = true;
    },
    async _stopMic() {
      if (!this.capturing) return;
      try { this._mic && this._mic.stop(); } catch (_e) {}
      this._mic = null;
      this.capturing = false;
    },
    _sendAudio(pcm16, seq, tsMs) {
      if (!this._socket || !this.bearerToken) return;
      this.seq = (seq | 0) || (this.seq + 1) & 0xffff;
      const tsRel = (tsMs | 0) || ((Date.now() - this.startTs) & 0xffff);
      const payload = audioChunkPayload(this.seq, tsRel, pcm16, { bearer_token: this.bearerToken });
      try { this._socket.emit('voqualizer_audio_chunk', payload); this.audioFramesSent += 1; } catch (_e) {}
    },
    async _sendFinalFrame() {
      if (!this._socket || !this.bearerToken) return;
      const payload = audioChunkPayload((this.seq + 1) & 0xffff, ((Date.now() - this.startTs) & 0xffff), new Uint8Array(0), { bearer_token: this.bearerToken, is_final: true });
      try { this._socket.emit('voqualizer_audio_chunk', payload); } catch (_e) {}
    },
    _handleTtsChunk(payload) {
      if (!this.isTtsEnabled()) return; // hard-mute when speaker is off
      const data = (payload && payload.data) || payload || {};
      const audio = bytesFromUnknownAudio(data.audio_bytes || data.audio || data.pcm16);
      const utteranceId = data.utterance_id || 'default';
      if (tracker.cancelledTtsUtterances.has(utteranceId)) return;
      const aligned = alignPcm16Bytes(audio, carryMap, utteranceId);
      const samples = pcm16ToFloat32(aligned);
      if (!samples.length) return;
      const sampleRate = data.sample_rate || PCM_SAMPLE_RATE;
      const ctx = this._ensurePlaybackContext(sampleRate);
      const buffer = ctx.createBuffer(1, samples.length, sampleRate);
      buffer.copyToChannel(samples, 0);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      source.start();
      if (!tracker.activePlaybackSources.has(utteranceId)) tracker.activePlaybackSources.set(utteranceId, []);
      tracker.activePlaybackSources.get(utteranceId).push(source);
    },
    _handleTtsDone(payload) {
      const data = (payload && payload.data) || payload || {};
      const utteranceId = data.utterance_id || 'default';
      if (data.cancelled || data.reason === 'barge_in') {
        tracker.stopPlaybackForUtterance(utteranceId);
      }
    },
    _ensurePlaybackContext(sampleRate) {
      if (!this._playbackCtx) this._playbackCtx = new (globalThis.AudioContext || globalThis.webkitAudioContext)({ sampleRate });
      return this._playbackCtx;
    },
  };
  return state;
}

export function registerVoqualizerStore() {
  if (!globalThis.Alpine || globalThis.Alpine.store('voqualizer')) return;
  const store = createVoqualizerStore();
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
