/*
 * A0 Voqualizer browser microphone AudioWorklet.
 *
 * Captures browser-provided Float32 microphone samples, converts them to
 * mono PCM16 at 16 kHz, and posts deterministic messages to the main thread:
 *   { type: "audio", sampleRate: 16000, seq, tsMs, pcm16: ArrayBuffer }
 *   { type: "vu", level, peak, rms, clipped, inputSampleRate, sampleRate }
 *
 * The main thread is responsible for forwarding audio buffers over the
 * voqualizer_* WebSocket protocol, including the server-side frame header.
 */

const TARGET_SAMPLE_RATE = 16000;
const DEFAULT_FRAME_MS = 20;
const DEFAULT_VU_INTERVAL_MS = 50;
const INT16_MAX = 32767;
const INT16_MIN = -32768;

function clampSample(value) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  if (value > 1) {
    return 1;
  }
  if (value < -1) {
    return -1;
  }
  return value;
}

function floatToPcm16(sample) {
  const clamped = clampSample(sample);
  // Keep positive and negative full scale symmetric for browser clients.
  return clamped < 0 ? Math.round(clamped * -INT16_MIN) : Math.round(clamped * INT16_MAX);
}

function readMonoSample(inputs, frameIndex) {
  const input = inputs[0];
  if (!input || input.length === 0) {
    return 0;
  }

  if (input.length === 1) {
    return input[0][frameIndex] || 0;
  }

  let sum = 0;
  for (let channel = 0; channel < input.length; channel += 1) {
    sum += input[channel][frameIndex] || 0;
  }
  return sum / input.length;
}

class VoqualizerMicProcessor extends AudioWorkletProcessor {
  constructor(options = {}) {
    super();

    const processorOptions = options.processorOptions || {};
    this.targetSampleRate = TARGET_SAMPLE_RATE;
    this.inputSampleRate = Number(processorOptions.inputSampleRate) || sampleRate;
    this.frameSamples = Math.max(
      1,
      Math.round((Number(processorOptions.frameMs) || DEFAULT_FRAME_MS) * this.targetSampleRate / 1000),
    );
    this.vuIntervalSamples = Math.max(
      1,
      Math.round((Number(processorOptions.vuIntervalMs) || DEFAULT_VU_INTERVAL_MS) * this.inputSampleRate / 1000),
    );

    this._resampleRatio = this.inputSampleRate / this.targetSampleRate;
    this._sourceCursor = 0;
    this._inputSamplesSeen = 0;
    this._outputSamplesSeen = 0;
    this._seq = 0;
    this._pending = [];

    this._vuSumSquares = 0;
    this._vuPeak = 0;
    this._vuCount = 0;
    this._vuClipped = false;

    this._enabled = true;
    this.port.onmessage = (event) => {
      const data = event.data || {};
      if (data.type === 'setEnabled') {
        this._enabled = data.enabled !== false;
      } else if (data.type === 'reset') {
        this._resetCounters();
      }
    };
  }

  _resetCounters() {
    this._sourceCursor = 0;
    this._inputSamplesSeen = 0;
    this._outputSamplesSeen = 0;
    this._seq = 0;
    this._pending = [];
    this._vuSumSquares = 0;
    this._vuPeak = 0;
    this._vuCount = 0;
    this._vuClipped = false;
  }

  _recordVu(sample) {
    const abs = Math.abs(sample);
    this._vuPeak = Math.max(this._vuPeak, Math.min(abs, 1));
    this._vuSumSquares += sample * sample;
    this._vuCount += 1;
    if (abs >= 1) {
      this._vuClipped = true;
    }

    if (this._vuCount >= this.vuIntervalSamples) {
      const rms = Math.sqrt(this._vuSumSquares / this._vuCount);
      this.port.postMessage({
        type: 'vu',
        level: Math.max(0, Math.min(1, rms)),
        peak: this._vuPeak,
        rms,
        clipped: this._vuClipped,
        inputSampleRate: this.inputSampleRate,
        sampleRate: this.targetSampleRate,
      });
      this._vuSumSquares = 0;
      this._vuPeak = 0;
      this._vuCount = 0;
      this._vuClipped = false;
    }
  }

  _emitAudioFrame() {
    if (this._pending.length < this.frameSamples) {
      return;
    }

    const frame = this._pending.splice(0, this.frameSamples);
    const pcm = new Int16Array(frame.length);
    for (let i = 0; i < frame.length; i += 1) {
      pcm[i] = floatToPcm16(frame[i]);
    }

    const tsMs = Math.round((this._outputSamplesSeen / this.targetSampleRate) * 1000) & 0xffff;
    this._outputSamplesSeen += pcm.length;

    const buffer = pcm.buffer;
    this.port.postMessage(
      {
        type: 'audio',
        sampleRate: this.targetSampleRate,
        channels: 1,
        format: 'pcm16',
        codec: 'pcm16/16k',
        seq: this._seq,
        tsMs,
        pcm16: buffer,
      },
      [buffer],
    );
    this._seq = (this._seq + 1) & 0xffff;
  }

  process(inputs) {
    const input = inputs[0];
    const blockLength = input && input[0] ? input[0].length : 0;

    if (!this._enabled || blockLength === 0) {
      return true;
    }

    for (let i = 0; i < blockLength; i += 1) {
      this._recordVu(readMonoSample(inputs, i));
    }

    const blockStart = this._inputSamplesSeen;
    const blockEnd = blockStart + blockLength;

    while (this._sourceCursor < blockEnd) {
      const relative = this._sourceCursor - blockStart;
      if (relative >= 0 && relative < blockLength) {
        this._pending.push(clampSample(readMonoSample(inputs, Math.floor(relative))));
        this._emitAudioFrame();
      }
      this._sourceCursor += this._resampleRatio;
    }

    this._inputSamplesSeen = blockEnd;
    return true;
  }
}

registerProcessor('voqualizer-mic-processor', VoqualizerMicProcessor);
