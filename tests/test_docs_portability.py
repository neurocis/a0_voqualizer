from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "clients" / "portability.md"


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_a75_portability_doc_exists_at_plan_path():
    assert DOC.is_file()


def test_doc_lists_all_m7_artifacts_and_example_paths():
    content = text()
    for marker in [
        "A7.1",
        "A7.2",
        "A7.3",
        "A7.4",
        "examples/ios-swift/",
        "examples/android-kotlin/",
        "examples/twilio-media-streams/",
        "examples/asterisk-audiofork/",
    ]:
        assert marker in content


def test_doc_covers_common_voqualizer_protocol_contract():
    content = text()
    assert "plugins/a0_voqualizer/ws_voqualizer" in content
    assert "voqualizer_init" in content
    assert "voqualizer_ready" in content
    assert "bearer_token" in content
    for event in [
        "voqualizer_audio_chunk",
        "voqualizer_user_text",
        "voqualizer_control",
        "voqualizer_asr_partial",
        "voqualizer_asr_final",
        "voqualizer_agent_delta",
        "voqualizer_agent_response_final",
        "voqualizer_tts_chunk",
        "voqualizer_tts_done",
        "voqualizer_error",
    ]:
        assert event in content


def test_doc_defines_a2_frame_header_portability_contract():
    content = text()
    assert "uint16 seq" in content or "uint16 sequence" in content
    assert "uint16 tsMs" in content or "uint16 timestamp" in content
    assert "network byte order" in content
    assert "byte 0..1" in content
    assert "byte 2..3" in content
    assert "byte 4..n" in content
    for encoder in [
        "framePcm16()",
        "VoqualizerFrame.encoded()",
        "VoqualizerFrame.encode()",
        "encodeVoqualizerFrame()",
        "encode_voqualizer_frame()",
    ]:
        assert encoder in content


def test_doc_has_portability_matrix_for_browser_mobile_and_telephony():
    content = text()
    assert "## Portability matrix" in content
    for client in ["Browser WebUI", "iOS Swift", "Android Kotlin", "Twilio Media Streams", "Asterisk audio-fork"]:
        assert client in content
    for capability in [
        "Transport abstraction",
        "Handler auth",
        "Bearer-token enforcement",
        "Input audio source",
        "Input conversion",
        "A2 frame header",
        "TTS playback/return",
    ]:
        assert capability in content


def test_doc_covers_codec_and_sample_rate_adaptation():
    content = text()
    for marker in [
        "PCM16/16k",
        "µ-law/8k",
        "signed-linear PCM16/8k",
        "Float32 Web Audio mic",
        "AVAudioEngine",
        "AudioRecord",
        "linear resampling",
    ]:
        assert marker in content


def test_doc_includes_bearer_token_checklist_and_pytest_constraints():
    content = text()
    assert "A5.5 bearer-token checklist" in content
    assert "Tokens are not shared between sessions" in content
    assert "Normal pytest" in content
    for forbidden_requirement in [
        "Xcode",
        "Android SDK",
        "Gradle",
        "Twilio",
        "Asterisk",
        "live A0 backend",
    ]:
        assert forbidden_requirement in content
