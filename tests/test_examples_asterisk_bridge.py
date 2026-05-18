from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "asterisk-audiofork"
README = EXAMPLE / "README.md"
BRIDGE = EXAMPLE / "bridge.py"
EXTENSIONS = EXAMPLE / "config" / "extensions.conf"
AUDIOSOCKET = EXAMPLE / "config" / "audiosocket.conf"
PJSIP = EXAMPLE / "config" / "pjsip.conf"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_a74_asterisk_bridge_ships_with_readme_bridge_and_config():
    assert README.is_file()
    assert BRIDGE.is_file()
    assert EXTENSIONS.is_file()
    assert AUDIOSOCKET.is_file()
    assert PJSIP.is_file()


def test_readme_documents_working_audiofork_sample_and_voqualizer_forwarding():
    text = read(README)
    assert "Asterisk" in text
    assert "AudioSocket" in text
    assert "PCM16 16 kHz" in text
    assert "voqualizer_audio_chunk" in text
    assert "voqualizer_tts_chunk" in text
    assert "bearer_token" in text
    assert "plugins/a0_voqualizer/ws_voqualizer" in text
    assert "extensions.conf" in text


def test_dialplan_and_config_include_working_sample_entries():
    extensions = read(EXTENSIONS)
    audiosocket = read(AUDIOSOCKET)
    pjsip = read(PJSIP)
    assert "[voqualizer-demo]" in extensions
    assert "AudioSocket(${VOQ_CALL_ID},127.0.0.1:9092)" in extensions
    assert "Answer()" in extensions
    assert "Hangup()" in extensions
    assert "[voqualizer-bridge]" in audiosocket
    assert "host = 127.0.0.1" in audiosocket
    assert "port = 9092" in audiosocket
    assert "format = slin" in audiosocket
    assert "context=voqualizer-demo" in pjsip
    assert "allow=slin,ulaw,alaw" in pjsip


def test_bridge_exports_resampling_codec_frame_and_bridge_helpers():
    text = read(BRIDGE)
    for symbol in [
        "resample_pcm16_linear",
        "asterisk_slin8_to_voqualizer_pcm16",
        "voqualizer_pcm16_to_asterisk_slin8",
        "ulaw8_to_voqualizer_pcm16",
        "voqualizer_pcm16_to_ulaw8",
        "encode_voqualizer_frame",
        "AsteriskVoqualizerBridge",
        "JsonLineAudioSocketSink",
    ]:
        assert symbol in text
    assert "__all__" in text


def test_bridge_transcodes_asterisk_slin8_to_voqualizer_pcm16_16k():
    text = read(BRIDGE)
    assert "ASTERISK_SAMPLE_RATE = 8000" in text
    assert "VOQUALIZER_SAMPLE_RATE = 16000" in text
    assert "resample_pcm16_linear(slin8, ASTERISK_SAMPLE_RATE, VOQUALIZER_SAMPLE_RATE)" in text
    assert "resample_pcm16_linear(pcm16k, VOQUALIZER_SAMPLE_RATE, ASTERISK_SAMPLE_RATE)" in text


def test_bridge_frames_audio_with_a2_network_byte_order_header():
    text = read(BRIDGE)
    assert "FRAME_HEADER_BYTES = 4" in text
    assert 'struct.pack("!HH", seq & 0xFFFF, ts_ms & 0xFFFF)' in text
    assert "+ bytes(pcm16)" in text


def test_bridge_connects_to_voqualizer_and_preserves_bearer_token_semantics():
    text = read(BRIDGE)
    assert "plugins/a0_voqualizer/ws_voqualizer" in text
    assert "await self.voqualizer.connect(VOQUALIZER_HANDLER)" in text
    assert "voqualizer_init" in text
    assert 'ready.get("bearer_token")' in text
    assert "self.bearer_token = str(token)" in text
    assert '"bearer_token": self.bearer_token' in text


def test_bridge_forwards_audio_and_tts_between_asterisk_and_voqualizer():
    text = read(BRIDGE)
    assert "forward_asterisk_audio" in text
    assert "asterisk_slin8_to_voqualizer_pcm16(slin8)" in text
    assert "encode_voqualizer_frame(self.seq, ts_ms, pcm16k)" in text
    assert "voqualizer_audio_chunk" in text
    assert "handle_tts_chunk" in text
    assert "voqualizer_pcm16_to_asterisk_slin8" in text
    assert "write_audio(pcm8k)" in text
    assert "voqualizer_tts_done" in text
