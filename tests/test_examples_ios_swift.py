from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "ios-swift"
CLIENT = EXAMPLE / "VoqualizerDemo" / "VoqualizerClient.swift"
VIEW = EXAMPLE / "VoqualizerDemo" / "ContentView.swift"
README = EXAMPLE / "README.md"
PLIST = EXAMPLE / "VoqualizerDemo" / "Info.plist"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_a71_ios_swift_example_ships_with_readme_and_sources():
    assert README.is_file()
    assert CLIENT.is_file()
    assert VIEW.is_file()
    assert PLIST.is_file()


def test_readme_documents_connection_full_duplex_and_bearer_auth():
    text = read(README)
    assert "plugins/a0_voqualizer/ws_voqualizer" in text
    assert "voqualizer_init" in text
    assert "bearer_token" in text
    assert "voqualizer_audio_chunk" in text
    assert "voqualizer_tts_chunk" in text
    assert "full duplex" in text.lower()
    assert "README" or text


def test_client_connects_to_handler_and_stores_session_bearer_token():
    text = read(CLIENT)
    assert 'voqualizerSocketHandler = "plugins/a0_voqualizer/ws_voqualizer"' in text
    assert "func connect(handler: String) async throws" in text
    assert 'transport.connect(handler: voqualizerSocketHandler)' in text
    assert 'transport.emitWithAck("voqualizer_init"' in text
    assert 'ready["bearer_token"] as? String' in text
    assert 'next["bearer_token"] = bearerToken' in text


def test_client_frames_pcm16_with_a2_network_byte_order_header():
    text = read(CLIENT)
    assert "public struct VoqualizerFrame" in text
    assert "data.append(UInt8((seq >> 8) & 0xff))" in text
    assert "data.append(UInt8(seq & 0xff))" in text
    assert "data.append(UInt8((tsMs >> 8) & 0xff))" in text
    assert "data.append(UInt8(tsMs & 0xff))" in text
    assert "data.append(pcm16)" in text


def test_client_captures_microphone_and_sends_audio_chunks():
    text = read(CLIENT)
    assert "AVAudioEngine" in text
    assert "installTap" in text
    assert "downmixAndResampleToPcm16" in text
    assert 'transport.emitWithAck("voqualizer_audio_chunk"' in text
    assert "voqualizerInputCodec = \"pcm16/16k\"" in text
    assert "voqualizerSampleRate: Double = 16_000" in text


def test_client_is_full_duplex_and_handles_tts_playback():
    text = read(CLIENT)
    assert "startFullDuplex" in text
    assert "startMicrophoneCapture" in text
    assert "configurePlayback" in text
    assert "playPcm16" in text
    assert "AVAudioPlayerNode" in text
    assert "scheduleBuffer" in text
    assert "voqualizer_tts_chunk" in text


def test_client_renders_protocol_events_for_demo_ui():
    text = read(CLIENT)
    for event in [
        "voqualizer_asr_partial",
        "voqualizer_asr_final",
        "voqualizer_agent_delta",
        "voqualizer_agent_response_final",
        "voqualizer_tts_done",
        "voqualizer_error",
    ]:
        assert event in text
    assert "partialText" in text
    assert "finalTranscripts" in text
    assert "agentText" in text
    assert "eventLog" in text


def test_swiftui_view_exposes_connect_full_duplex_text_and_event_ui():
    text = read(VIEW)
    assert "VoqualizerDemoView" in text
    assert "Button(\"Connect\")" in text
    assert "Button(\"Start full duplex\")" in text
    assert "Button(\"Barge-in\")" in text
    assert "client.sendText" in text
    assert "client.partialText" in text
    assert "client.finalTranscripts" in text
    assert "client.agentText" in text
    assert "client.eventLog" in text


def test_ios_example_declares_microphone_usage_description():
    text = read(PLIST)
    assert "NSMicrophoneUsageDescription" in text
    assert "A0 Voqualizer" in text
