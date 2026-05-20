/*
 * Shared Voqualizer audio helpers used by both the in-plugin tester and the
 * in-GUI Conversational/PTT button overrides extension.
 *
 * Pure ES module, no build step, no dependency beyond the browser DOM/Web
 * Audio API and (optionally) A0's /js/api.js helper for token fetches.
 */

export const FRAME_HEADER_BYTES = 4;
export const INPUT_CODEC = 'pcm16/16k';
export const OUTPUT_CODEC = 'pcm16/16k';
export const PCM_SAMPLE_RATE = 16000;
export const WORKLET_URL = '/plugins/a0_voqualizer/webui/audio-worklet.js';
export const WORKLET_PROCESSOR = 'voqualizer-mic-processor';
export const ADMIN_ENDPOINT = '/api/plugins/a0_voqualizer/voqualizer_admin';

export function bytesFromUnknownAudio(value) {
  if (!value) return new Uint8Array();
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  if (Array.isArray(value)) return Uint8Array.from(value);
  return new Uint8Array();
}


export function base64ToBytes(value) {
  const text = String(value || '');
  if (!text) return new Uint8Array();
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function bytesFromTtsPayload(payload) {
  const data = (payload && payload.data) || payload || {};
  if (data.audio_b64 || payload.audio_b64) return base64ToBytes(data.audio_b64 || payload.audio_b64);
  return bytesFromUnknownAudio(data.audio_bytes || data.audio || data.pcm16 || payload.audio || payload.pcm16);
}



export function concatAudioBytes(parts) {
  const clean = (parts || []).map(bytesFromUnknownAudio).filter((part) => part.byteLength);
  const total = clean.reduce((sum, part) => sum + part.byteLength, 0);
  const output = new Uint8Array(total);
  let offset = 0;
  for (const part of clean) {
    output.set(part, offset);
    offset += part.byteLength;
  }
  return output;
}

export function repairRiffWaveHeader(bytes) {
  const data = bytesFromUnknownAudio(bytes);
  if (data.byteLength < 12) return data;
  const riffWave = data[0] === 0x52 && data[1] === 0x49 && data[2] === 0x46 && data[3] === 0x46
    && data[4] === 0x57 && data[5] === 0x41 && data[6] === 0x56 && data[7] === 0x45;
  if (!riffWave) return data;
  const repaired = new Uint8Array(data.byteLength + 4);
  repaired.set(data.slice(0, 4), 0);
  const view = new DataView(repaired.buffer);
  view.setUint32(4, repaired.byteLength - 8, true);
  repaired.set(data.slice(4), 8);
  return repaired;
}

export function normalizeTtsCodec(data = {}, payload = {}) {
  let codec = String(data.codec || payload.codec || data.format || payload.format || '').toLowerCase();
  const sampleRate = Number(data.sample_rate || data.sampleRate || payload.sample_rate || payload.sampleRate || 0);
  if (codec === 'pcm') codec = sampleRate === 24000 ? 'pcm16/24k' : 'pcm16/16k';
  if (!codec && (data.audio_encoding || payload.audio_encoding)) {
    codec = String(data.audio_encoding || payload.audio_encoding).toLowerCase();
  }
  return codec || 'pcm16/16k';
}

export function ttsSampleRate(data = {}, payload = {}, codec = '') {
  return Number(data.sample_rate || data.sampleRate || payload.sample_rate || payload.sampleRate || (codec === 'pcm16/24k' ? 24000 : PCM_SAMPLE_RATE));
}

export function rememberPlaybackSource(tracker, utteranceId, source) {
  if (!tracker || !tracker.activePlaybackSources) return;
  const key = utteranceId || 'default';
  if (!tracker.activePlaybackSources.has(key)) tracker.activePlaybackSources.set(key, []);
  tracker.activePlaybackSources.get(key).push(source);
  source.addEventListener && source.addEventListener('ended', () => {
    const list = tracker.activePlaybackSources.get(key) || [];
    const idx = list.indexOf(source);
    if (idx >= 0) list.splice(idx, 1);
    if (!list.length) tracker.activePlaybackSources.delete(key);
  }, { once: true });
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

export function audioChunkPayload(seq, tsMs, pcm16, extra = {}) {
  const frame = framePcm16(seq, tsMs, pcm16);
  return {
    frame,
    frame_b64: bytesToBase64(frame),
    frame_encoding: 'base64',
    frame_bytes: frame.byteLength,
    seq: seq & 0xffff,
    ts_ms: tsMs & 0xffff,
    ...extra,
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

/**
 * Preserve odd trailing bytes per utterance so streamed PCM16 chunks split
 * mid-sample do not desynchronize playback into static. carryMap is a Map
 * keyed by utteranceId.
 */
export function alignPcm16Bytes(bytes, carryMap, utteranceId = 'default') {
  const key = utteranceId || 'default';
  const input = bytesFromUnknownAudio(bytes);
  const carry = carryMap.get(key);
  const merged = carry && carry.byteLength
    ? (() => { const m = new Uint8Array(carry.byteLength + input.byteLength); m.set(carry, 0); m.set(input, carry.byteLength); return m; })()
    : input;
  if (merged.byteLength % 2 === 0) {
    carryMap.delete(key);
    return merged;
  }
  const aligned = merged.subarray(0, merged.byteLength - 1);
  carryMap.set(key, merged.subarray(merged.byteLength - 1));
  return aligned;
}

export function clearPcm16Carry(carryMap, utteranceId) {
  if (!utteranceId) { carryMap.clear(); return; }
  carryMap.delete(utteranceId);
}

export function createPlaybackTracker() {
  const activePlaybackSources = new Map();
  const cancelledTtsUtterances = new Set();
  function stopPlaybackForUtterance(utteranceId) {
    const key = utteranceId || 'default';
    cancelledTtsUtterances.add(key);
    const sources = activePlaybackSources.get(key) || [];
    for (const src of sources) {
      try { src.stop(); } catch (_e) {}
      try { src.disconnect(); } catch (_e) {}
    }
    activePlaybackSources.delete(key);
  }
  function stopAllPlayback() {
    for (const key of Array.from(activePlaybackSources.keys())) {
      stopPlaybackForUtterance(key);
    }
    activePlaybackSources.clear();
  }
  return { activePlaybackSources, cancelledTtsUtterances, stopPlaybackForUtterance, stopAllPlayback };
}

/**
 * Local mic-VU barge-in: if live mic level is above the threshold, stop local
 * playback immediately so the user is not talking over their own agent.
 * Returns true if barge-in was applied.
 */
export function maybeLocalBargeInFromMic(vu, tracker, opts = {}) {
  const threshold = typeof opts.threshold === 'number' ? opts.threshold : 0.18;
  const level = (vu && (vu.level || vu.rms || 0)) || 0;
  const peak = (vu && vu.peak) || 0;
  if (level < threshold && peak < threshold) return false;
  if (!tracker || !tracker.activePlaybackSources || tracker.activePlaybackSources.size === 0) return false;
  tracker.stopAllPlayback();
  return true;
}

/**
 * Fetch a per-session bearer token via the existing admin endpoint. Uses
 * A0's callJsonApi when available so CSRF tokens are handled centrally;
 * falls back to a same-origin fetch otherwise.
 */
export async function fetchSessionToken(payload = {}) {
  try {
    const apiMod = await import('/js/api.js');
    const callJsonApi = apiMod.callJsonApi || apiMod.default;
    if (typeof callJsonApi === 'function') {
      return await callJsonApi('plugins/a0_voqualizer/voqualizer_admin', { action: 'token', ...payload });
    }
  } catch (_err) {}
  const response = await fetch(ADMIN_ENDPOINT, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'token', ...payload }),
  });
  const data = await response.json();
  return data && data.results && data.results[0] && data.results[0].data ? data.results[0].data : data;
}

/**
 * Wire up the audio worklet capture pipeline: getUserMedia -> AudioContext ->
 * MediaStreamSource -> AudioWorkletNode -> muted GainNode -> destination.
 * onAudio({frame, seq, tsMs, pcm16}) is called per audio frame.
 * onVu({level, peak, rms, clipped}) is called per VU sample.
 * Returns { stop, mediaStream, audioContext, workletNode }.
 */
export async function initMicWorklet({ onAudio, onVu, sampleRate = PCM_SAMPLE_RATE } = {}) {
  const mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const audioContext = new (globalThis.AudioContext || globalThis.webkitAudioContext)({ sampleRate });
  await audioContext.audioWorklet.addModule(WORKLET_URL);
  const mediaSource = audioContext.createMediaStreamSource(mediaStream);
  const workletNode = new AudioWorkletNode(audioContext, WORKLET_PROCESSOR);
  const monitorGain = audioContext.createGain();
  monitorGain.gain.value = 0; // muted monitor: prevents echo into ASR
  mediaSource.connect(workletNode);
  workletNode.connect(monitorGain).connect(audioContext.destination);
  workletNode.port.onmessage = (event) => {
    const msg = event.data || {};
    if (msg.type === 'audio' && typeof onAudio === 'function') {
      const pcm16 = bytesFromUnknownAudio(msg.pcm16 || msg.data || msg.buffer);
      onAudio({ pcm16, seq: msg.seq | 0, tsMs: msg.tsMs | 0 });
    } else if (msg.type === 'vu' && typeof onVu === 'function') {
      onVu({ level: msg.level || 0, peak: msg.peak || 0, rms: msg.rms || 0, clipped: !!msg.clipped });
    }
  };
  function stop() {
    try { workletNode.port.postMessage({ type: 'setEnabled', enabled: false }); } catch (_e) {}
    try { workletNode.disconnect(); } catch (_e) {}
    try { mediaSource.disconnect(); } catch (_e) {}
    try { monitorGain.disconnect(); } catch (_e) {}
    try { mediaStream.getTracks().forEach((t) => t.stop()); } catch (_e) {}
    try { audioContext.close(); } catch (_e) {}
  }
  return { stop, mediaStream, audioContext, workletNode };
}
