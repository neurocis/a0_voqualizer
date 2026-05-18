'use strict';

const VOQUALIZER_HANDLER = 'plugins/a0_voqualizer/ws_voqualizer';
const INPUT_CODEC = 'pcm16/16k';
const OUTPUT_CODEC = 'pcm16/16k';
const TWILIO_SAMPLE_RATE = 8000;
const VOQUALIZER_SAMPLE_RATE = 16000;
const FRAME_HEADER_BYTES = 4;

function clamp16(value) {
  return Math.max(-32768, Math.min(32767, Math.round(value)));
}

function mulawByteToSample(byte) {
  const mu = ~byte & 0xff;
  const sign = mu & 0x80;
  const exponent = (mu >> 4) & 0x07;
  const mantissa = mu & 0x0f;
  let sample = ((mantissa << 3) + 0x84) << exponent;
  sample -= 0x84;
  return sign ? -sample : sample;
}

function sampleToMulawByte(sample) {
  const BIAS = 0x84;
  const CLIP = 32635;
  let pcm = clamp16(sample);
  let sign = 0;
  if (pcm < 0) {
    sign = 0x80;
    pcm = -pcm;
  }
  pcm = Math.min(CLIP, pcm) + BIAS;
  let exponent = 7;
  for (let mask = 0x4000; (pcm & mask) === 0 && exponent > 0; mask >>= 1) {
    exponent -= 1;
  }
  const mantissa = (pcm >> (exponent + 3)) & 0x0f;
  return (~(sign | (exponent << 4) | mantissa)) & 0xff;
}

function mulawToPcm16(mulaw) {
  const input = Buffer.from(mulaw || []);
  const out = Buffer.alloc(input.length * 2);
  for (let i = 0; i < input.length; i += 1) {
    out.writeInt16LE(clamp16(mulawByteToSample(input[i])), i * 2);
  }
  return out;
}

function pcm16ToMulaw(pcm16) {
  const input = Buffer.from(pcm16 || []);
  const samples = Math.floor(input.length / 2);
  const out = Buffer.alloc(samples);
  for (let i = 0; i < samples; i += 1) {
    out[i] = sampleToMulawByte(input.readInt16LE(i * 2));
  }
  return out;
}

function resamplePcm16Linear(pcm16, srcRate, dstRate) {
  const input = Buffer.from(pcm16 || []);
  if (srcRate === dstRate || input.length === 0) {
    return Buffer.from(input);
  }
  const inSamples = Math.floor(input.length / 2);
  const outSamples = Math.max(1, Math.round(inSamples * dstRate / srcRate));
  const out = Buffer.alloc(outSamples * 2);
  const scale = srcRate / dstRate;
  for (let i = 0; i < outSamples; i += 1) {
    const pos = i * scale;
    const left = Math.min(inSamples - 1, Math.floor(pos));
    const right = Math.min(inSamples - 1, left + 1);
    const frac = pos - left;
    const a = input.readInt16LE(left * 2);
    const b = input.readInt16LE(right * 2);
    out.writeInt16LE(clamp16(a + (b - a) * frac), i * 2);
  }
  return out;
}

function encodeVoqualizerFrame(seq, tsMs, pcm16) {
  const payload = Buffer.from(pcm16 || []);
  const frame = Buffer.alloc(FRAME_HEADER_BYTES + payload.length);
  frame.writeUInt16BE(seq & 0xffff, 0);
  frame.writeUInt16BE(tsMs & 0xffff, 2);
  payload.copy(frame, FRAME_HEADER_BYTES);
  return frame;
}

function decodeTwilioMediaPayload(payloadBase64) {
  const mulaw8k = Buffer.from(payloadBase64 || '', 'base64');
  const pcm8k = mulawToPcm16(mulaw8k);
  return resamplePcm16Linear(pcm8k, TWILIO_SAMPLE_RATE, VOQUALIZER_SAMPLE_RATE);
}

function encodeTwilioMediaPayload(pcm16k) {
  const pcm8k = resamplePcm16Linear(pcm16k, VOQUALIZER_SAMPLE_RATE, TWILIO_SAMPLE_RATE);
  return pcm16ToMulaw(pcm8k).toString('base64');
}

class TwilioVoqualizerBridge {
  constructor({ voqualizerTransport, twilioSocket, sessionId = `twilio-${Date.now().toString(36)}` } = {}) {
    this.voqualizerTransport = voqualizerTransport;
    this.twilioSocket = twilioSocket;
    this.sessionId = sessionId;
    this.bearerToken = '';
    this.streamSid = '';
    this.seq = 0;
  }

  async connect() {
    await this.voqualizerTransport.connect(VOQUALIZER_HANDLER);
    const ready = await this.voqualizerTransport.emitWithAck('voqualizer_init', {
      session_id: this.sessionId,
      asr: { codec: INPUT_CODEC },
      tts: { codec: OUTPUT_CODEC },
      barge_in: true,
    });
    if (!ready || !ready.bearer_token) {
      throw new Error('voqualizer_ready did not issue bearer_token');
    }
    this.sessionId = ready.session_id || this.sessionId;
    this.bearerToken = ready.bearer_token;
    this.voqualizerTransport.on('voqualizer_tts_chunk', (payload) => this.forwardTtsToTwilio(payload));
    this.voqualizerTransport.on('voqualizer_tts_done', () => this.sendTwilioMark('voqualizer_tts_done'));
    return ready;
  }

  async handleTwilioMessage(raw) {
    const message = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!message || !message.event) return;
    if (message.event === 'start') {
      this.streamSid = message.start && message.start.streamSid ? message.start.streamSid : message.streamSid || '';
      return;
    }
    if (message.event === 'media') {
      await this.forwardMediaToVoqualizer(message.media || {}, message.streamSid || this.streamSid);
      return;
    }
    if (message.event === 'stop') {
      await this.voqualizerTransport.emitWithAck('voqualizer_control', this.sessionPayload({ action: 'end_session' }));
    }
  }

  async forwardMediaToVoqualizer(media, streamSid = '') {
    this.ensureBearerToken();
    const pcm16k = decodeTwilioMediaPayload(media.payload || '');
    const tsMs = Number.isFinite(Number(media.timestamp)) ? Number(media.timestamp) : this.seq * 20;
    const frame = encodeVoqualizerFrame(this.seq, tsMs, pcm16k);
    this.seq = (this.seq + 1) & 0xffff;
    await this.voqualizerTransport.emitWithAck('voqualizer_audio_chunk', this.sessionPayload({ frame, streamSid }));
  }

  forwardTtsToTwilio(payload = {}) {
    const audio = Buffer.from(payload.audio || payload.data || payload.pcm16 || []);
    const twilioPayload = encodeTwilioMediaPayload(audio);
    this.sendTwilioMedia(twilioPayload);
  }

  sendTwilioMedia(payload) {
    if (!this.twilioSocket || typeof this.twilioSocket.send !== 'function') return;
    this.twilioSocket.send(JSON.stringify({
      event: 'media',
      streamSid: this.streamSid,
      media: { payload },
    }));
  }

  sendTwilioMark(name) {
    if (!this.twilioSocket || typeof this.twilioSocket.send !== 'function') return;
    this.twilioSocket.send(JSON.stringify({
      event: 'mark',
      streamSid: this.streamSid,
      mark: { name },
    }));
  }

  sessionPayload(payload) {
    return { ...payload, bearer_token: this.bearerToken };
  }

  ensureBearerToken() {
    if (!this.bearerToken) {
      throw new Error('connect before forwarding Twilio media');
    }
  }
}

module.exports = {
  VOQUALIZER_HANDLER,
  INPUT_CODEC,
  OUTPUT_CODEC,
  TWILIO_SAMPLE_RATE,
  VOQUALIZER_SAMPLE_RATE,
  FRAME_HEADER_BYTES,
  mulawToPcm16,
  pcm16ToMulaw,
  resamplePcm16Linear,
  encodeVoqualizerFrame,
  decodeTwilioMediaPayload,
  encodeTwilioMediaPayload,
  TwilioVoqualizerBridge,
};

if (require.main === module) {
  console.log('A0 Voqualizer Twilio bridge reference module loaded. Wire ws + Socket.IO transports for production use.');
}
