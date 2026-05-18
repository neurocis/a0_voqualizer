from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "twilio-media-streams"
BRIDGE = EXAMPLE / "bridge.js"
README = EXAMPLE / "README.md"
PACKAGE = EXAMPLE / "package.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_a73_twilio_bridge_ships_with_readme_package_and_bridge():
    assert README.is_file()
    assert PACKAGE.is_file()
    assert BRIDGE.is_file()


def test_readme_documents_twilio_mulaw_to_voqualizer_pcm16_forwarding():
    text = read(README)
    assert "Twilio Media Streams" in text
    assert "μ-law" in text or "mulaw" in text.lower()
    assert "8 kHz" in text
    assert "PCM16 16 kHz" in text
    assert "voqualizer_audio_chunk" in text
    assert "voqualizer_tts_chunk" in text
    assert "bearer_token" in text
    assert "plugins/a0_voqualizer/ws_voqualizer" in text


def test_bridge_exports_codec_resampling_frame_and_bridge_helpers():
    text = read(BRIDGE)
    for symbol in [
        "mulawToPcm16",
        "pcm16ToMulaw",
        "resamplePcm16Linear",
        "encodeVoqualizerFrame",
        "decodeTwilioMediaPayload",
        "encodeTwilioMediaPayload",
        "TwilioVoqualizerBridge",
    ]:
        assert symbol in text
    assert "module.exports" in text


def test_bridge_transcodes_mulaw_8k_to_pcm16_16k():
    text = read(BRIDGE)
    assert "const TWILIO_SAMPLE_RATE = 8000" in text
    assert "const VOQUALIZER_SAMPLE_RATE = 16000" in text
    assert "Buffer.from(payloadBase64 || '', 'base64')" in text
    assert "mulawToPcm16(mulaw8k)" in text
    assert "resamplePcm16Linear(pcm8k, TWILIO_SAMPLE_RATE, VOQUALIZER_SAMPLE_RATE)" in text


def test_bridge_transcodes_pcm16_16k_to_mulaw_8k_for_twilio():
    text = read(BRIDGE)
    assert "resamplePcm16Linear(pcm16k, VOQUALIZER_SAMPLE_RATE, TWILIO_SAMPLE_RATE)" in text
    assert "pcm16ToMulaw(pcm8k).toString('base64')" in text
    assert "forwardTtsToTwilio" in text
    assert "sendTwilioMedia" in text
    assert "event: 'media'" in text


def test_bridge_frames_voqualizer_audio_with_a2_network_byte_order_header():
    text = read(BRIDGE)
    assert "const FRAME_HEADER_BYTES = 4" in text
    assert "frame.writeUInt16BE(seq & 0xffff, 0)" in text
    assert "frame.writeUInt16BE(tsMs & 0xffff, 2)" in text
    assert "payload.copy(frame, FRAME_HEADER_BYTES)" in text


def test_bridge_connects_to_voqualizer_and_preserves_bearer_token_semantics():
    text = read(BRIDGE)
    assert "plugins/a0_voqualizer/ws_voqualizer" in text
    assert "voqualizerTransport.connect(VOQUALIZER_HANDLER)" in text
    assert "voqualizer_init" in text
    assert "ready.bearer_token" in text
    assert "this.bearerToken = ready.bearer_token" in text
    assert "bearer_token: this.bearerToken" in text


def test_bridge_forwards_twilio_media_to_voqualizer_audio_chunk():
    text = read(BRIDGE)
    assert "handleTwilioMessage" in text
    assert "forwardMediaToVoqualizer" in text
    assert "decodeTwilioMediaPayload(media.payload" in text
    assert "encodeVoqualizerFrame(this.seq, tsMs, pcm16k)" in text
    assert "voqualizer_audio_chunk" in text
    assert "streamSid" in text


def test_package_declares_optional_runtime_transports():
    text = read(PACKAGE)
    assert "@socket.io/client" in text
    assert '"ws"' in text
    assert '"start": "node bridge.js"' in text
