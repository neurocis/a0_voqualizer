/*
 * A0 Voqualizer in-plugin browser tester store.
 *
 * This module intentionally has no build step and no external dependency beyond
 * the A0 page-provided Socket.IO client (`window.io`). It connects to the
 * plugin WS handler, loads webui/audio-worklet.js for microphone capture,
 * frames PCM16 chunks with the A2 4-byte header, preserves the A5.5 per-session
 * bearer token on session-bound operations, renders protocol events into a
 * tiny observable state model, and plays streamed TTS audio.
 */

export const VOQUALIZER_HANDLER = 'plugins/a0_voqualizer/ws_voqualizer';
export const WORKLET_URL = './audio-worklet.js';
export const WORKLET_PROCESSOR = 'voqualizer-mic-processor';
export const INPUT_CODEC = 'pcm16/16k';
export const OUTPUT_CODEC = 'pcm16/16k';
export const PCM_SAMPLE_RATE = 16000;
export const FRAME_HEADER_BYTES = 4;

function nowMs() {
  return Date.now();
}

function makeSessionId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `voq-${nowMs().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function bytesFromUnknownAudio(value) {
  if (!value) {
    return new Uint8Array();
  }
  if (value instanceof Uint8Array) {
    return value;
  }
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value);
  }
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  if (Array.isArray(value)) {
    return Uint8Array.from(value);
  }
  return new Uint8Array();
}

export function bytesToBase64(bytes) {
  const data = bytesFromUnknownAudio(bytes);
  let binary = '';
  const chunkSize = 0x8000;
  for (let offset = 0; offset < data.byteLength; offset += chunkSize) {
    const chunk = data.subarray(offset, Math.min(offset + chunkSize, data.byteLength));
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}

export function framePcm16(seq, tsMs, pcm16) {
  const audio = bytesFromUnknownAudio(pcm16);
  const frame = new Uint8Array(FRAME_HEADER_BYTES + audio.byteLength);
  const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
  view.setUint16(0, seq & 0xffff, false);
  view.setUint16(2, tsMs & 0xffff, false);
  frame.set(audio, FRAME_HEADER_BYTES);
  return frame;
}

export function audioChunkPayload(seq, tsMs, pcm16) {
  const frame = framePcm16(seq, tsMs, pcm16);
  return {
    frame,
    frame_b64: bytesToBase64(frame),
    frame_encoding: 'base64',
    frame_bytes: frame.byteLength,
    seq: seq & 0xffff,
    ts_ms: tsMs & 0xffff,
  };
}

export function pcm16ToFloat32(pcm16) {
  const bytes = bytesFromUnknownAudio(pcm16);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const samples = new Float32Array(Math.floor(bytes.byteLength / 2));
  for (let i = 0; i < samples.length; i += 1) {
    const value = view.getInt16(i * 2, true);
    samples[i] = value < 0 ? value / 32768 : value / 32767;
  }
  return samples;
}

export function createVoqualizerTesterStore(options = {}) {
  const listeners = new Set();
  const state = {
    connected: false,
    connecting: false,
    capturing: false,
    muted: false,
    sessionId: options.sessionId || makeSessionId(),
    bearerToken: '',
    selectedContextId: options.contextId || options.context_id || '',
    contexts: [],
    contextsLoading: false,
    negotiated: null,
    capabilities: null,
    vu: { level: 0, peak: 0, rms: 0, clipped: false },
    partialText: '',
    finalTranscripts: [],
    agentText: '',
    ttsChunks: 0,
    events: [],
    error: null,
    diagnostics: {
      connectedAt: 0,
      captureStartedAt: 0,
      lastAudioSentAt: 0,
      lastAudioAckAt: 0,
      lastAsrPartialAt: 0,
      lastAsrFinalAt: 0,
      lastAgentDeltaAt: 0,
      lastTtsChunkAt: 0,
      firstTtsChunkAt: 0,
      audioFramesSent: 0,
      audioAcks: 0,
      ttsChunks: 0,
      lastSeq: null,
      lastTsMs: null,
      lastFrameBytes: 0,
      lastAckRttMs: null,
      firstTtsLatencyMs: null,
    },
    frameInspector: [],
  };

  let socket = null;
  let mediaStream = null;
  let audioContext = null;
  let workletNode = null;
  let mediaSource = null;
  let playbackContext = null;
  let playbackTail = 0;
  const activePlaybackSources = new Map();
  const cancelledTtsUtterances = new Set();
  let lastLocalBargeInAt = 0;
  const encodedTtsBuffers = new Map();
  const pcm16CarryBytes = new Map();

  function snapshot() {
    return {
      ...state,
      vu: { ...state.vu },
      finalTranscripts: [...state.finalTranscripts],
      events: [...state.events],
      diagnostics: { ...state.diagnostics },
      frameInspector: [...state.frameInspector],
    };
  }

  function notify() {
    const copy = snapshot();
    for (const listener of listeners) {
      listener(copy);
    }
  }

  function setState(patch) {
    Object.assign(state, patch);
    notify();
  }

  function appendEvent(event, payload = {}) {
    state.events.push({ event, ts: nowMs(), payload });
    if (state.events.length > 200) {
      state.events.splice(0, state.events.length - 200);
    }
    notify();
  }

  function recordFrameInspection(frame) {
    const bytes = bytesFromUnknownAudio(frame);
    if (bytes.byteLength < FRAME_HEADER_BYTES) {
      return;
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const item = {
      ts: nowMs(),
      seq: view.getUint16(0, false),
      tsMs: view.getUint16(2, false),
      bytes: bytes.byteLength,
      payloadBytes: bytes.byteLength - FRAME_HEADER_BYTES,
      codec: INPUT_CODEC,
    };
    state.frameInspector.push(item);
    if (state.frameInspector.length > 50) {
      state.frameInspector.splice(0, state.frameInspector.length - 50);
    }
    Object.assign(state.diagnostics, {
      audioFramesSent: state.diagnostics.audioFramesSent + 1,
      lastAudioSentAt: item.ts,
      lastSeq: item.seq,
      lastTsMs: item.tsMs,
      lastFrameBytes: item.bytes,
    });
    notify();
  }

  function markAudioAck(sentAt) {
    const ts = nowMs();
    Object.assign(state.diagnostics, {
      audioAcks: state.diagnostics.audioAcks + 1,
      lastAudioAckAt: ts,
      lastAckRttMs: sentAt ? ts - sentAt : null,
    });
    notify();
  }

  function setError(error) {
    const message = error && error.message ? error.message : String(error || 'unknown error');
    setState({ error: message });
    appendEvent('voqualizer_error', { message });
  }

  function emitWithAck(event, payload = {}) {
    if (!socket || !state.connected) {
      return Promise.reject(new Error('Voqualizer socket is not connected'));
    }
    return new Promise((resolve, reject) => {
      socket.emit(event, payload, (response) => {
        // A0 WS dispatcher wraps handler results in an envelope:
        //   { correlationId, results: [ { handlerId, ok, correlationId,
        //                                  data: {...} | error: {...} } ] }
        // (see /a0/helpers/ws_manager.py#process_client_event). Unwrap it so
        // callers see the original handler payload. Fall back to legacy/test
        // shapes (bare {error}|{data}|payload) to keep deterministic tests
        // passing.
        if (response && Array.isArray(response.results)) {
          const first = response.results[0];
          if (!first) {
            resolve({});
            return;
          }
          if (first.error) {
            const err = first.error;
            reject(new Error(err.message || err.code || 'Voqualizer request failed'));
            return;
          }
          if (first.ok === false) {
            reject(new Error(first.message || 'Voqualizer request failed'));
            return;
          }
          resolve(first.data || {});
          return;
        }
        if (response && response.error) {
          reject(new Error(response.error.message || response.error.code || 'Voqualizer request failed'));
          return;
        }
        resolve(response || {});
      });
    });
  }

  function sessionPayload(extra = {}) {
    return {
      ...extra,
      bearer_token: state.bearerToken,
    };
  }

  function eventData(payload) {
    // A0 Socket.IO emits plugin events in an envelope:
    // { handlerId, eventId, correlationId, ts, data: {...} }.
    // Older tests/raw helpers may pass the protocol payload directly.  Normalize
    // both shapes before updating visible tester state.
    if (payload && typeof payload === 'object' && payload.data && typeof payload.data === 'object') {
      return payload.data;
    }
    return payload || {};
  }

  function handleReady(payload) {
    setState({
      connected: true,
      connecting: false,
      sessionId: payload.session_id || state.sessionId,
      bearerToken: payload.bearer_token || state.bearerToken,
      negotiated: payload.negotiated || null,
      capabilities: payload.capabilities || null,
      error: null,
    });
    state.diagnostics.connectedAt = nowMs();
    notify();
    appendEvent('voqualizer_ready', payload);
  }

  function handleAsrPartial(payload) {
    const data = eventData(payload);
    state.diagnostics.lastAsrPartialAt = nowMs();
    setState({ partialText: data.text || '' });
    appendEvent('voqualizer_asr_partial', payload);
  }

  function handleAsrFinal(payload) {
    const data = eventData(payload);
    const text = data.text || '';
    if (text) {
      state.finalTranscripts.push({ text, payload: data, envelope: payload, ts: nowMs() });
      state.partialText = '';
      state.diagnostics.lastAsrFinalAt = nowMs();
      notify();
    }
    appendEvent('voqualizer_asr_final', payload);
  }

  function handleAgentDelta(payload) {
    const data = eventData(payload);
    const delta = data.delta || data.text || data.content || '';
    if (delta) {
      state.diagnostics.lastAgentDeltaAt = nowMs();
      setState({ agentText: state.agentText + delta });
    }
    appendEvent('voqualizer_agent_delta', payload);
  }

  function handleAgentFinal(payload) {
    const data = eventData(payload);
    const text = data.text || data.content || data.response || '';
    if (text) {
      setState({ agentText: text });
    }
    const utteranceId = data.utterance_id || payload.utterance_id || '';
    if (utteranceId) {
      cancelledTtsUtterances.delete(utteranceId);
    }
    appendEvent('voqualizer_agent_response_final', payload);
  }

  async function ensurePlaybackContext(sampleRate = PCM_SAMPLE_RATE) {
    if (!playbackContext) {
      const AudioContextCtor = globalThis.AudioContext || globalThis.webkitAudioContext;
      if (!AudioContextCtor) {
        throw new Error('Web Audio playback is unavailable in this browser');
      }
      playbackContext = new AudioContextCtor({ sampleRate });
    }
    if (playbackContext.state === 'suspended') {
      await playbackContext.resume();
    }
    return playbackContext;
  }

  function base64ToBytes(value) {
    const binary = atob(String(value || ''));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  function concatBytes(parts) {
    const total = parts.reduce((sum, part) => sum + part.length, 0);
    const output = new Uint8Array(total);
    let offset = 0;
    for (const part of parts) {
      output.set(part, offset);
      offset += part.length;
    }
    return output;
  }

  function repairRiffWaveHeader(bytes) {
    // Some OpenAI-compatible Kokoro gateways stream WAV-like bytes as
    // RIFFWAVEfmt... (missing the RIFF chunk-size field). Browsers expect
    // RIFF + uint32 file_size_minus_8 + WAVE. Repair only this exact shape.
    if (!(bytes instanceof Uint8Array) || bytes.length < 12) {
      return bytes;
    }
    const riffWave = bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46
      && bytes[4] === 0x57 && bytes[5] === 0x41 && bytes[6] === 0x56 && bytes[7] === 0x45;
    if (!riffWave) {
      return bytes;
    }
    const repaired = new Uint8Array(bytes.length + 4);
    repaired.set(bytes.slice(0, 4), 0);
    const view = new DataView(repaired.buffer);
    view.setUint32(4, repaired.length - 8, true);
    repaired.set(bytes.slice(4), 8);
    return repaired;
  }

  function bytesFromTtsPayload(payload) {
    const data = eventData(payload);
    if (data.audio_b64 || payload.audio_b64) {
      return base64ToBytes(data.audio_b64 || payload.audio_b64);
    }
    const value = data.audio || data.data || data.pcm16 || payload.audio || payload.data || payload.pcm16;
    return value instanceof Uint8Array ? value : new Uint8Array(value || []);
  }

  function alignPcm16Bytes(bytes, utteranceId = 'default') {
    let input = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []);
    const key = utteranceId || 'default';
    const carry = pcm16CarryBytes.get(key);
    if (carry !== undefined) {
      const merged = new Uint8Array(input.length + 1);
      merged[0] = carry;
      merged.set(input, 1);
      input = merged;
      pcm16CarryBytes.delete(key);
    }
    if (input.length % 2 === 1) {
      pcm16CarryBytes.set(key, input[input.length - 1]);
      input = input.slice(0, input.length - 1);
    }
    return input;
  }

  function clearPcm16Carry(utteranceId = 'default') {
    pcm16CarryBytes.delete(utteranceId || 'default');
  }

  function stopPlaybackForUtterance(utteranceId = 'default') {
    const key = utteranceId || 'default';
    cancelledTtsUtterances.add(key);
    const sources = activePlaybackSources.get(key) || [];
    for (const source of sources) {
      try {
        source.stop(0);
      } catch (_err) {
        // Already ended or not started yet.
      }
      try {
        source.disconnect();
      } catch (_err) {
        // Ignore disconnect races.
      }
    }
    activePlaybackSources.delete(key);
    encodedTtsBuffers.delete(key);
    clearPcm16Carry(key);
    if (playbackContext) {
      playbackTail = playbackContext.currentTime + 0.01;
    } else {
      playbackTail = 0;
    }
  }

  function stopAllPlayback() {
    for (const key of Array.from(activePlaybackSources.keys())) {
      stopPlaybackForUtterance(key);
    }
    activePlaybackSources.clear();
    encodedTtsBuffers.clear();
    pcm16CarryBytes.clear();
    if (playbackContext) {
      playbackTail = playbackContext.currentTime + 0.01;
    } else {
      playbackTail = 0;
    }
  }

  function hasActivePlayback() {
    if (activePlaybackSources.size > 0) {
      return true;
    }
    return !!(playbackContext && playbackTail > playbackContext.currentTime + 0.05);
  }

  function maybeLocalBargeInFromMic(vu = {}) {
    if (!state.bearerToken || state.muted || !hasActivePlayback()) {
      return;
    }
    const rms = Number(vu.rms || vu.level || 0);
    const peak = Number(vu.peak || 0);
    if (rms < BARGE_IN_RMS_THRESHOLD && peak < BARGE_IN_RMS_THRESHOLD * 2.5) {
      return;
    }
    const now = nowMs();
    if (now - lastLocalBargeInAt < BARGE_IN_COOLDOWN_MS) {
      return;
    }
    lastLocalBargeInAt = now;
    stopAllPlayback();
    appendEvent('local_barge_in_playback_stop', { rms, peak });
    // Tell the backend too, but do not wait for the control ACK before stopping
    // browser playback. This is the critical low-latency path.
    control('barge_in').catch((error) => appendEvent('local_barge_in_control_error', { message: error.message || String(error) }));
  }

  async function playEncodedAudio(bytes, mimeType) {
    const blob = new Blob([bytes], { type: mimeType || 'application/octet-stream' });
    const url = URL.createObjectURL(blob);
    try {
      const audio = new Audio(url);
      await audio.play();
      audio.addEventListener('ended', () => URL.revokeObjectURL(url), { once: true });
    } catch (error) {
      URL.revokeObjectURL(url);
      throw error;
    }
  }

  async function flushEncodedTts(utteranceId) {
    const buffered = encodedTtsBuffers.get(utteranceId || 'default');
    if (!buffered || !buffered.chunks.length) {
      return;
    }
    encodedTtsBuffers.delete(utteranceId || 'default');
    let bytes = concatBytes(buffered.chunks);
    if (buffered.codec === 'wav') {
      bytes = repairRiffWaveHeader(bytes);
    }
    const mime = buffered.codec === 'wav' ? 'audio/wav' : buffered.codec === 'mp3' ? 'audio/mpeg' : 'audio/ogg; codecs=opus';
    await playEncodedAudio(bytes, mime);
  }

  async function playPcm16Chunk(payload) {
    const data = eventData(payload);
    const audio = bytesFromTtsPayload(payload);
    let codec = String(data.codec || payload.codec || '').toLowerCase();
    if (codec === 'pcm') {
      codec = Number(data.sample_rate || payload.sample_rate || PCM_SAMPLE_RATE) === 24000 ? 'pcm16/24k' : 'pcm16/16k';
    }
    if (codec === 'wav' || codec === 'mp3' || codec === 'opus') {
      // Encoded formats are not independently playable per transport chunk.
      // Accumulate by utterance and play after voqualizer_tts_done or a final
      // chunk marker.
      const bytes = audio instanceof Uint8Array ? audio : new Uint8Array(audio || []);
      const utteranceId = data.utterance_id || payload.utterance_id || 'default';
      if (!encodedTtsBuffers.has(utteranceId)) {
        encodedTtsBuffers.set(utteranceId, { codec, chunks: [] });
      }
      if (bytes.length) {
        encodedTtsBuffers.get(utteranceId).chunks.push(bytes);
      }
      if (data.is_final || payload.is_final) {
        await flushEncodedTts(utteranceId);
      }
      return;
    }
    const utteranceId = data.utterance_id || payload.utterance_id || 'default';
    const alignedAudio = alignPcm16Bytes(audio, utteranceId);
    const samples = pcm16ToFloat32(alignedAudio);
    if (!samples.length) {
      return;
    }
    const sampleRate = Number(data.sample_rate || data.sampleRate || payload.sample_rate || payload.sampleRate || (codec === 'pcm16/24k' ? 24000 : PCM_SAMPLE_RATE));
    const ctx = await ensurePlaybackContext(sampleRate);
    const buffer = ctx.createBuffer(1, samples.length, sampleRate);
    buffer.copyToChannel(samples, 0);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    if (!activePlaybackSources.has(utteranceId)) {
      activePlaybackSources.set(utteranceId, []);
    }
    activePlaybackSources.get(utteranceId).push(source);
    source.addEventListener('ended', () => {
      const list = activePlaybackSources.get(utteranceId) || [];
      const idx = list.indexOf(source);
      if (idx >= 0) {
        list.splice(idx, 1);
      }
      if (!list.length) {
        activePlaybackSources.delete(utteranceId);
      }
    }, { once: true });
    const startAt = Math.max(ctx.currentTime + 0.01, playbackTail);
    source.start(startAt);
    playbackTail = startAt + buffer.duration;
  }

  function handleTtsChunk(payload) {
    const ts = nowMs();
    state.ttsChunks += 1;
    state.diagnostics.ttsChunks += 1;
    state.diagnostics.lastTtsChunkAt = ts;
    if (!state.diagnostics.firstTtsChunkAt) {
      state.diagnostics.firstTtsChunkAt = ts;
      state.diagnostics.firstTtsLatencyMs = state.diagnostics.lastAsrFinalAt ? ts - state.diagnostics.lastAsrFinalAt : null;
    }
    notify();
    const data = eventData(payload);
    const utteranceId = data.utterance_id || payload.utterance_id || 'default';
    appendEvent('voqualizer_tts_chunk', { ...data, audio: '[binary]' });
    if (cancelledTtsUtterances.has(utteranceId)) {
      appendEvent('voqualizer_tts_chunk_ignored_cancelled', { utterance_id: utteranceId });
      return;
    }
    playPcm16Chunk(payload).catch(setError);
  }

  function handleTtsDone(payload) {
    const data = eventData(payload);
    const utteranceId = data.utterance_id || payload.utterance_id || 'default';
    if (data.cancelled || payload.cancelled || data.reason === 'barge_in' || payload.reason === 'barge_in') {
      if (utteranceId && utteranceId !== 'default') {
        stopPlaybackForUtterance(utteranceId);
      } else {
        stopAllPlayback();
      }
    } else {
      cancelledTtsUtterances.delete(utteranceId);
      clearPcm16Carry(utteranceId);
      flushEncodedTts(utteranceId).catch(setError);
    }
    appendEvent('voqualizer_tts_done', payload);
  }

  function bindSocketEvents() {
    socket.on('connect', () => {
      state.connected = true;
      state.connecting = false;
      notify();
      appendEvent('socket_connect', { id: socket.id });
    });
    socket.on('disconnect', (reason) => {
      setState({ connected: false, connecting: false, capturing: false });
      appendEvent('socket_disconnect', { reason });
    });
    socket.on('connect_error', setError);
    socket.on('voqualizer_ready', handleReady);
    socket.on('voqualizer_asr_partial', handleAsrPartial);
    socket.on('voqualizer_asr_final', handleAsrFinal);
    socket.on('voqualizer_agent_delta', handleAgentDelta);
    socket.on('voqualizer_agent_response_final', handleAgentFinal);
    socket.on('voqualizer_tts_chunk', handleTtsChunk);
    socket.on('voqualizer_tts_done', handleTtsDone);
    socket.on('voqualizer_error', (payload) => {
      setState({ error: payload.message || payload.code || 'voqualizer_error' });
      appendEvent('voqualizer_error', payload);
    });
  }

  async function adminAction(payload) {
    // Prefer A0's own API helper so auth, CSRF, runtime envelopes, and route
    // prefixes match the rest of the WebUI. The previous raw fetch path could
    // fail silently in live UI sessions, leaving the Context dropdown with only
    // its default "New Voqualizer context" option.
    try {
      const apiMod = await import('/js/api.js');
      const callJsonApi = apiMod.callJsonApi || apiMod.default;
      if (typeof callJsonApi === 'function') {
        const result = await callJsonApi('plugins/a0_voqualizer/voqualizer_admin', payload || {});
        const unwrapped = result && result.results && result.results[0] && result.results[0].data
          ? result.results[0].data
          : result;
        if (!unwrapped || unwrapped.ok === false) {
          throw new Error((unwrapped && (unwrapped.message || unwrapped.code)) || 'admin request failed');
        }
        return unwrapped;
      }
    } catch (error) {
      // If the helper reached the server/plugin and returned an error, surface it.
      // Only continue to raw fetch if the helper is genuinely unavailable in a
      // stripped-down test harness.
      const message = String(error && error.message || error || '');
      if (!message.includes('callJsonApi') && !message.includes('is not a function')) {
        throw error;
      }
    }

    const body = JSON.stringify(payload || {});
    const headers = { 'Content-Type': 'application/json' };
    try {
      const apiMod = await import('/js/api.js');
      if (apiMod.getCsrfToken) {
        const token = await apiMod.getCsrfToken();
        if (token) {
          headers['X-CSRF-Token'] = token;
          headers['X-CSRFToken'] = token;
          headers['x-csrf-token'] = token;
        }
      }
    } catch (_err) {
      // Same-origin authenticated sessions may still succeed without the helper.
    }
    const response = await fetch(ADMIN_ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers,
      body,
    });
    const data = await response.json();
    const unwrapped = data && data.results && data.results[0] && data.results[0].data ? data.results[0].data : data;
    if (!response.ok || !unwrapped || unwrapped.ok === false) {
      throw new Error((unwrapped && (unwrapped.message || unwrapped.code)) || `admin request failed: ${response.status}`);
    }
    return unwrapped;
  }

  async function loadContexts() {
    setState({ contextsLoading: true, error: null });
    try {
      const result = await adminAction({ action: 'contexts' });
      const contexts = Array.isArray(result.contexts) ? result.contexts : [];
      setState({ contexts, contextsLoading: false });
      appendEvent('contexts_loaded', { count: contexts.length });
      return contexts;
    } catch (error) {
      setState({ contextsLoading: false, error: error && error.message ? error.message : String(error) });
      throw error;
    }
  }

  function setContextId(contextId) {
    const clean = String(contextId || '').trim();
    setState({ selectedContextId: clean });
    appendEvent('context_selected', { context_id: clean });
  }

  async function connect(init = {}) {
    // A0 ships Socket.IO as an ES module at /vendor/socket.io.esm.min.js and
    // performs CSRF-protected auth (csrf_token + handlers) — see /a0/webui/js/
    // websocket.js#initializeSocket. We mirror that contract here so the
    // plugin's WS handler accepts the connection. `options.io` and
    // `options.getCsrfToken` may be injected for tests.
    setState({ connecting: true, error: null });
    try {
      let ioFactory = options.io || globalThis.io;
      let getCsrfToken = options.getCsrfToken;
      if (!ioFactory) {
        try {
          const mod = await import('/vendor/socket.io.esm.min.js');
          ioFactory = mod.io || mod.default;
        } catch (error) {
          throw new Error(`Socket.IO client unavailable: ${error.message}`);
        }
      }
      if (!ioFactory) {
        throw new Error('Socket.IO client (io) is not available');
      }
      if (!getCsrfToken) {
        try {
          const apiMod = await import('/js/api.js');
          getCsrfToken = apiMod.getCsrfToken;
        } catch (_err) {
          getCsrfToken = null;
        }
      }
      if (!socket) {
        socket = ioFactory('/ws', {
          autoConnect: false,
          reconnection: true,
          transports: ['websocket', 'polling'],
          withCredentials: true,
          auth: (cb) => {
            const handlers = [VOQUALIZER_HANDLER];
            if (getCsrfToken) {
              Promise.resolve()
                .then(() => getCsrfToken())
                .then((token) => cb({ csrf_token: token, handlers }))
                .catch((error) => {
                  console.error('[a0_voqualizer tester] CSRF token fetch failed', error);
                  cb({ handlers });
                });
            } else {
              cb({ handlers });
            }
          },
        });
        bindSocketEvents();
      }
      if (!socket.connected) {
        await new Promise((resolve, reject) => {
          const onConnect = () => { cleanup(); resolve(); };
          const onError = (err) => { cleanup(); reject(err instanceof Error ? err : new Error(String(err && err.message || err || 'connect_error'))); };
          function cleanup() {
            socket.off('connect', onConnect);
            socket.off('connect_error', onError);
          }
          socket.once('connect', onConnect);
          socket.once('connect_error', onError);
          if (typeof socket.connect === 'function') {
            socket.connect();
          }
        });
      } else if (!state.connected) {
        // The tester may intentionally mark the logical session disconnected
        // after `end_session` while keeping the underlying Socket.IO transport
        // alive. A subsequent Connect should re-use the live transport and
        // establish a fresh Voqualizer session rather than appearing stuck.
        setState({ connected: true });
      }
      const requestedSessionId = init.session_id || init.sessionId || '';
      const sessionId = requestedSessionId || (state.bearerToken && state.sessionId ? state.sessionId : makeSessionId());
      if (sessionId !== state.sessionId || !state.bearerToken) {
        setState({ sessionId, bearerToken: '', negotiated: null, capabilities: null });
      }
      const ready = await emitWithAck('voqualizer_init', {
        session_id: sessionId,
        asr: { codec: INPUT_CODEC, ...(init.asr || {}) },
        tts: { codec: OUTPUT_CODEC, ...(init.tts || {}) },
        context_id: init.context_id || init.contextId || state.selectedContextId || '',
        barge_in: init.barge_in !== undefined ? init.barge_in : true,
      });
      handleReady(ready);
      return ready;
    } catch (error) {
      setState({ connecting: false, error: error && error.message ? error.message : String(error) });
      throw error;
    }
  }

  async function startCapture() {
    if (!state.bearerToken) {
      throw new Error('Connect before starting microphone capture');
    }
    const AudioContextCtor = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (!AudioContextCtor || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error('Microphone capture is unavailable in this browser');
    }
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    audioContext = new AudioContextCtor();
    await audioContext.audioWorklet.addModule(WORKLET_URL);
    workletNode = new AudioWorkletNode(audioContext, WORKLET_PROCESSOR, {
      processorOptions: { inputSampleRate: audioContext.sampleRate, frameMs: 20, vuIntervalMs: 50 },
    });
    mediaSource = audioContext.createMediaStreamSource(mediaStream);
    mediaSource.connect(workletNode);
    workletNode.connect(audioContext.destination);
    workletNode.port.onmessage = (event) => {
      const message = event.data || {};
      if (message.type === 'vu') {
        const vu = { level: message.level || 0, peak: message.peak || 0, rms: message.rms || 0, clipped: !!message.clipped };
        setState({ vu });
        maybeLocalBargeInFromMic(vu);
      } else if (message.type === 'audio' && !state.muted) {
        const audioPayload = audioChunkPayload(message.seq || 0, message.tsMs || 0, message.pcm16);
        recordFrameInspection(audioPayload.frame);
        const sentAt = nowMs();
        emitWithAck('voqualizer_audio_chunk', sessionPayload(audioPayload))
          .then((ack) => { markAudioAck(sentAt); appendEvent('voqualizer_audio_ack', ack); })
          .catch((error) => { appendEvent('voqualizer_audio_error', { message: error.message || String(error), code: 'BAD_AUDIO_CHUNK' }); setError(error); });
      }
    };
    state.diagnostics.captureStartedAt = nowMs();
    setState({ capturing: true });
  }

  async function stopCapture() {
    if (workletNode) {
      workletNode.port.postMessage({ type: 'setEnabled', enabled: false });
      workletNode.disconnect();
      workletNode = null;
    }
    if (mediaSource) {
      mediaSource.disconnect();
      mediaSource = null;
    }
    if (mediaStream) {
      for (const track of mediaStream.getTracks()) {
        track.stop();
      }
      mediaStream = null;
    }
    if (audioContext) {
      await audioContext.close();
      audioContext = null;
    }
    setState({ capturing: false });
  }

  async function sendText(text) {
    const clean = String(text || '').trim();
    if (!clean) {
      return null;
    }
    appendEvent('voqualizer_user_text', { text: clean });
    // Do not force the tester's default PCM_SAMPLE_RATE here.  The backend
    // now derives TTS codec/sample_rate from the selected provider config
    // (for example Kokoro PCM at 24 kHz).  Sending the tester's 16 kHz default
    // overrides that provider config and makes 24 kHz PCM play back slow.
    return emitWithAck('voqualizer_user_text', sessionPayload({ text: clean }));
  }

  async function control(action) {
    const result = await emitWithAck('voqualizer_control', sessionPayload({ action }));
    if (action === 'mute') {
      setState({ muted: true });
    } else if (action === 'unmute') {
      setState({ muted: false });
    } else if (action === 'end_session') {
      await stopCapture();
      setState({
        bearerToken: '',
        connected: false,
        connecting: false,
        sessionId: makeSessionId(),
        negotiated: null,
        capabilities: null,
      });
    }
    appendEvent('voqualizer_control_ack', result);
    return result;
  }

  function clearDiagnostics() {
    state.events.splice(0);
    state.frameInspector.splice(0);
    Object.assign(state.diagnostics, {
      lastAudioSentAt: 0,
      lastAudioAckAt: 0,
      lastAsrPartialAt: 0,
      lastAsrFinalAt: 0,
      lastAgentDeltaAt: 0,
      lastTtsChunkAt: 0,
      firstTtsChunkAt: 0,
      audioFramesSent: 0,
      audioAcks: 0,
      ttsChunks: 0,
      lastSeq: null,
      lastTsMs: null,
      lastFrameBytes: 0,
      lastAckRttMs: null,
      firstTtsLatencyMs: null,
    });
    notify();
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(snapshot());
    return () => listeners.delete(listener);
  }

  return {
    getState: snapshot,
    subscribe,
    connect,
    loadContexts,
    setContextId,
    startCapture,
    stopCapture,
    sendText,
    control,
    clearDiagnostics,
    framePcm16,
    audioChunkPayload,
    pcm16ToFloat32,
  };
}
