from __future__ import annotations

from pathlib import Path

WORKLET = Path(__file__).resolve().parents[1] / "webui" / "audio-worklet.js"


def source() -> str:
    return WORKLET.read_text(encoding="utf-8")


def test_audio_worklet_artifact_exists():
    assert WORKLET.is_file()


def test_worklet_registers_voqualizer_processor():
    text = source()
    assert "class VoqualizerMicProcessor extends AudioWorkletProcessor" in text
    assert "registerProcessor('voqualizer-mic-processor', VoqualizerMicProcessor)" in text


def test_worklet_targets_pcm16_16khz_capture():
    text = source()
    assert "const TARGET_SAMPLE_RATE = 16000" in text
    assert "format: 'pcm16'" in text
    assert "codec: 'pcm16/16k'" in text
    assert "new Int16Array" in text
    assert "floatToPcm16" in text
    assert "sampleRate: this.targetSampleRate" in text


def test_worklet_downsamples_from_browser_sample_rate():
    text = source()
    assert "this.inputSampleRate = Number(processorOptions.inputSampleRate) || sampleRate" in text
    assert "this._resampleRatio = this.inputSampleRate / this.targetSampleRate" in text
    assert "this._sourceCursor += this._resampleRatio" in text


def test_worklet_posts_audio_buffers_with_sequence_and_timestamp():
    text = source()
    assert "type: 'audio'" in text
    assert "seq: this._seq" in text
    assert "tsMs" in text
    assert "pcm16: buffer" in text
    assert "[buffer]" in text
    assert "this._seq = (this._seq + 1) & 0xffff" in text


def test_worklet_emits_vu_level_events():
    text = source()
    assert "type: 'vu'" in text
    assert "level:" in text
    assert "peak:" in text
    assert "rms" in text
    assert "clipped:" in text
    assert "DEFAULT_VU_INTERVAL_MS" in text
