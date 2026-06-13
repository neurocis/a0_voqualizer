/**
 * Shared browser-side Wyoming WS client adapter (W17).
 *
 * Speaks the small Wyoming-only protocol exposed by `api/ws_wyoming.py`:
 *   - wyoming_init    -> bind to a configured Wyoming interface (ctxID fixed server-side)
 *   - wyoming_event   -> Wyoming event envelope {type, data, payload_length, payload_b64?}
 *   - wyoming_payload -> streamed binary chunk paired with previous wyoming_event
 *   - wyoming_close   -> explicit teardown
 *
 * This adapter is intentionally framework-agnostic so both the standalone
 * Voqualizer web UI (W18) and the DOM main-UI ASR/TTS extensions (W19) can
 * consume the exact same Wyoming envelopes as any external Wyoming client.
 *
 * The old custom voqualizer_* WebSocket protocol is NOT used here. Legacy
 * webui/conversation-mode.js and webui/voqualizer.js remain in-tree for
 * reference only.
 */
import { io } from '/vendor/socket.io.esm.min.js';

const HANDLER_ID = 'plugins/a0_voqualizer/ws_wyoming';

function _b64ToBytes(b64) {
  if (!b64) return new Uint8Array(0);
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}



async function _fetchCsrfTokenSafe() {
  try {
    const mod = await import('/js/api.js');
    if (mod && typeof mod.getCsrfToken === 'function') {
      return await mod.getCsrfToken();
    }
  } catch (_) {}
  try {
    const res = await fetch('/api/csrf_token', { method: 'POST', credentials: 'same-origin' });
    const json = await res.json();
    return json && (json.token || json.csrf_token || json.csrfToken) || '';
  } catch (_) {}
  return '';
}

function _extractAckData(ack) {
  if (!ack || typeof ack !== 'object') return ack || {};
  // WsManager canonical result item: { ok, data, error, handlerId, ... }
  if (Object.prototype.hasOwnProperty.call(ack, 'ok')) {
    if (ack.ok) return (ack.data && typeof ack.data === 'object') ? ack.data : {};
    const err = new Error((ack.error && (ack.error.error || ack.error.message || ack.error.code)) || 'Wyoming WS error');
    err.details = ack.error || ack;
    throw err;
  }
  // Aggregated route result: { results: [{ ok, data, error, ... }] }
  if (Array.isArray(ack.results) && ack.results.length) {
    return _extractAckData(ack.results[0]);
  }
  // Some framework paths return { data: { results: [...] } } or { data: {...} }.
  if (ack.data && typeof ack.data === 'object') {
    if (Array.isArray(ack.data.results) && ack.data.results.length) {
      return _extractAckData(ack.data.results[0]);
    }
    if (Object.prototype.hasOwnProperty.call(ack.data, 'ok')) {
      return _extractAckData(ack.data);
    }
    return ack.data;
  }
  return ack;
}

function _bytesToB64(bytes) {
  if (!bytes || !bytes.length) return '';
  let s = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(s);
}

export class WyomingWsClient {
  constructor({ interfaceId, url = '/ws', csrfToken = null, debug = false } = {}) {
    if (!interfaceId) throw new Error('WyomingWsClient requires interfaceId');
    this.interfaceId = String(interfaceId);
    this.url = url;
    this.csrfToken = csrfToken;
    this.debug = !!debug;
    this._socket = null;
    this._connected = false;
    this._initInfo = null;
    this._handlers = new Map(); // event_type -> Set<handler>
    this._activeGenerationId = null;
    this._closed = false;
    this._initPromise = null;
    // Per-session init ack gate. Recreated on every (re)connect so any
    // sendEvent() awaiting send is suspended until the server-side handler
    // for the current sid has bound a Wyoming bridge via wyoming_init.
    this._initReady = null;
    this._initReadyResolve = null;
    this._initReadyReject = null;
    this._sessionEpoch = 0;
    this._stats = {
      connect_attempts: 0,
      init_acks: 0,
      reinit_acks: 0,
      events_in: 0,
      events_out: 0,
      payload_bytes_in: 0,
      payload_bytes_out: 0,
      errors: 0,
      last_in_type: '',
      last_out_type: '',
      last_error: '',
      last_generation_id: '',
      stale_generation_drops: 0,
      last_disconnect_reason: '',
      reconnects: 0,
    };
  }

  _resetInitReady() {
    this._initReady = new Promise((resolve, reject) => {
      this._initReadyResolve = resolve;
      this._initReadyReject = reject;
    });
    // Swallow unhandled rejections; sendEvent surfaces them explicitly.
    this._initReady.catch(() => {});
  }

  on(eventType, handler) {
    if (typeof handler !== 'function') return () => {};
    let bucket = this._handlers.get(eventType);
    if (!bucket) {
      bucket = new Set();
      this._handlers.set(eventType, bucket);
    }
    bucket.add(handler);
    return () => bucket.delete(handler);
  }

  snapshot() {
    return {
      interface_id: this.interfaceId,
      connected: this._connected,
      closed: this._closed,
      active_generation_id: this._activeGenerationId,
      init_info: this._initInfo,
      session_epoch: this._sessionEpoch,
      init_ready: !!this._initReady,
      stats: { ...this._stats },
    };
  }

  _recordError(kind, err) {
    this._stats.errors += 1;
    this._stats.last_error = `${kind}: ${err && (err.message || String(err)) || 'unknown'}`;
  }

  _emitLocal(eventType, payload) {
    const bucket = this._handlers.get(eventType);
    if (!bucket) return;
    for (const handler of bucket) {
      try { handler(payload); }
      catch (err) { if (this.debug) console.error('[wyoming] handler error', err); }
    }
  }

  async connect() {
    if (this._initPromise) return this._initPromise;
    this._stats.connect_attempts += 1;
    // Open the per-session init gate before the socket is even created so any
    // sendEvent() called immediately after connect() will wait.
    this._resetInitReady();
    this._initPromise = new Promise((resolve, reject) => {
      const socket = io(this.url, {
        path: '/socket.io',
        transports: ['websocket'],
        auth: async (cb) => {
          const csrf = this.csrfToken || await _fetchCsrfTokenSafe();
          cb({
            handlers: [HANDLER_ID],
            csrf_token: csrf || undefined,
            interface_id: this.interfaceId,
          });
        },
      });
      this._socket = socket;
      let firstInitResolved = false;

      socket.on('connect', async () => {
        // A new sid is a fresh server-side handler with no bridge yet. Re-arm
        // the init gate and (re)send wyoming_init before any wyoming_event.
        this._sessionEpoch += 1;
        this._resetInitReady();
        const epoch = this._sessionEpoch;
        try {
          const ack = await socket.emitWithAck('wyoming_init', { interface_id: this.interfaceId });
          const ackData = _extractAckData(ack);
          const info = (ackData && ackData.info) || null;
          // Guard against late acks from a stale session.
          if (epoch !== this._sessionEpoch) return;
          this._initInfo = info;
          this._connected = true;
          if (firstInitResolved) {
            this._stats.reinit_acks += 1;
          } else {
            this._stats.init_acks += 1;
          }
          if (this._initReadyResolve) this._initReadyResolve(info);
          this._emitLocal('open', { info });
          if (!firstInitResolved) { firstInitResolved = true; resolve(info); }
        } catch (err) {
          this._recordError('init', err);
          if (this._initReadyReject) this._initReadyReject(err);
          if (!firstInitResolved) { firstInitResolved = true; reject(err); }
        }
      });

      socket.on('disconnect', (reason) => {
        this._connected = false;
        this._stats.last_disconnect_reason = String(reason || '');
        this._stats.reconnects += 1;
        // Re-close the init gate so any subsequent sendEvent waits for the next
        // wyoming_init ack on the next sid.
        this._resetInitReady();
        this._emitLocal('close', { reason });
      });

      socket.on('connect_error', (err) => {
        this._recordError('connect', err);
        this._emitLocal('error', { kind: 'connect', error: err && (err.message || String(err)) });
        if (!firstInitResolved) { firstInitResolved = true; reject(err); }
      });

      socket.on('wyoming_event', (envelope) => {
        if (!envelope || typeof envelope !== 'object') return;
        const ev = {
          type: String(envelope.type || ''),
          data: (envelope.data && typeof envelope.data === 'object') ? envelope.data : {},
          payload: envelope.payload_b64 ? _b64ToBytes(envelope.payload_b64) : new Uint8Array(0),
        };
        this._stats.events_in += 1;
        this._stats.payload_bytes_in += ev.payload.length || 0;
        this._stats.last_in_type = ev.type;
        const gen = ev.data && (ev.data.generation_id || ev.data.generationId);
        if (gen) this._stats.last_generation_id = String(gen);
        this._emitLocal('event', ev);
        this._emitLocal('event:' + ev.type, ev);
      });
    });
    return this._initPromise;
  }

  async sendEvent(type, data = {}, payload = null) {
    if (!this._socket) await this.connect();
    if (this._initReady) await this._initReady;
    let eventType = type;
    let eventData = data || {};
    if (type && typeof type === 'object') {
      if (typeof type.type === 'string' && type.type.trim()) {
        eventType = type.type;
        eventData = (type.data && typeof type.data === 'object') ? type.data : (type.event_data || {});
      } else if (typeof type.text === 'string') {
        eventType = 'voqualizer-text-prompt';
        eventData = type;
      }
    }
    eventType = String(eventType || '').trim();
    if (!eventType) throw new Error('Wyoming sendEvent requires non-empty type');
    // NOTE: We use `event_data` (not `data`) at the envelope top level because
    // the framework's WsManager auto-unwraps incoming.data into the handler
    // payload, which would strip our Wyoming `type` field. See ws.py L607-612.
    const envelope = {
      type: eventType,
      event_data: eventData || {},
      payload_length: payload ? payload.length : 0,
    };
    if (payload && payload.length) envelope.payload_b64 = _bytesToB64(payload);
    this._stats.events_out += 1;
    this._stats.payload_bytes_out += payload ? payload.length : 0;
    this._stats.last_out_type = envelope.type;
    const gen = envelope.event_data && (envelope.event_data.generation_id || envelope.event_data.generationId || envelope.event_data.utterance_id);
    if (gen) this._stats.last_generation_id = String(gen);
    try {
      const ack = await this._socket.emitWithAck('wyoming_event', envelope);
      return _extractAckData(ack);
    } catch (err) {
      this._recordError('send', err);
      throw err;
    }
  }

  newGeneration(prefix = 'gen') {
    const id = `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    this._activeGenerationId = id;
    this._stats.last_generation_id = id;
    return id;
  }

  get activeGenerationId() { return this._activeGenerationId; }

  isCurrentGeneration(eventData) {
    if (!this._activeGenerationId) return true;
    if (!eventData || typeof eventData !== 'object') return true;
    const id = eventData.generation_id || eventData.generationId || null;
    if (!id) return true;
    const ok = id === this._activeGenerationId;
    if (!ok) this._stats.stale_generation_drops += 1;
    return ok;
  }

  // ----- Convenience helpers used by W18/W19 ---------------------------------

  async submitText(text, { generationId = null } = {}) {
    const gen = generationId || this.newGeneration('text');
    return this.sendEvent('voqualizer-text-prompt', {
      text: String(text || ''),
      generation_id: gen,
    });
  }

  async beginAudio({ rate = 16000, width = 2, channels = 1, utteranceId = null } = {}) {
    const utt = utteranceId || this.newGeneration('utt');
    await this.sendEvent('audio-start', { rate, width, channels, utterance_id: utt });
    return utt;
  }

  async sendAudioChunk(pcmBytes) {
    return this.sendEvent('audio-chunk', {}, pcmBytes);
  }

  async endAudio({ utteranceId = null } = {}) {
    return this.sendEvent('audio-stop', { utterance_id: utteranceId });
  }

  async cancelTts() {
    return this.sendEvent('voqualizer-control', { action: 'cancel_tts' });
  }

  async close() {
    if (this._closed) return;
    this._closed = true;
    if (this._socket) {
      try { await this._socket.emitWithAck('wyoming_close', {}); } catch (_) {}
      try { this._socket.disconnect(); } catch (_) {}
    }
  }
}

export function createWyomingWsClient(opts) {
  return new WyomingWsClient(opts);
}

export default WyomingWsClient;
