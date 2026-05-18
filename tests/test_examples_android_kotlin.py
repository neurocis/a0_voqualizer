from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "android-kotlin"
CLIENT = EXAMPLE / "app" / "src" / "main" / "java" / "com" / "a0" / "voqualizerdemo" / "VoqualizerClient.kt"
ACTIVITY = EXAMPLE / "app" / "src" / "main" / "java" / "com" / "a0" / "voqualizerdemo" / "MainActivity.kt"
README = EXAMPLE / "README.md"
MANIFEST = EXAMPLE / "app" / "src" / "main" / "AndroidManifest.xml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_a72_android_kotlin_example_ships_with_readme_and_sources():
    assert README.is_file()
    assert CLIENT.is_file()
    assert ACTIVITY.is_file()
    assert MANIFEST.is_file()


def test_readme_documents_connection_full_duplex_and_bearer_auth():
    text = read(README)
    assert "plugins/a0_voqualizer/ws_voqualizer" in text
    assert "voqualizer_init" in text
    assert "bearer_token" in text
    assert "voqualizer_audio_chunk" in text
    assert "voqualizer_tts_chunk" in text
    assert "full duplex" in text.lower()
    assert "examples/android-kotlin" or text


def test_client_connects_to_handler_and_stores_session_bearer_token():
    text = read(CLIENT)
    assert 'VOQUALIZER_SOCKET_HANDLER = "plugins/a0_voqualizer/ws_voqualizer"' in text
    assert "suspend fun connect(handler: String)" in text
    assert "transport.connect(VOQUALIZER_SOCKET_HANDLER)" in text
    assert 'transport.emitWithAck(\n            "voqualizer_init"' in text
    assert 'ready["bearer_token"] as? String' in text
    assert '"bearer_token" to _state.value.bearerToken' in text


def test_client_frames_pcm16_with_a2_network_byte_order_header():
    text = read(CLIENT)
    assert "data class VoqualizerFrame" in text
    assert "ByteOrder.BIG_ENDIAN" in text
    assert "buffer.putShort((seq and 0xffff).toShort())" in text
    assert "buffer.putShort((tsMs and 0xffff).toShort())" in text
    assert "buffer.put(pcm16)" in text


def test_client_captures_microphone_and_sends_audio_chunks():
    text = read(CLIENT)
    assert "AudioRecord" in text
    assert "MediaRecorder.AudioSource.VOICE_RECOGNITION" in text
    assert "ENCODING_PCM_16BIT" in text
    assert "VOQUALIZER_SAMPLE_RATE = 16_000" in text
    assert 'transport.emitWithAck("voqualizer_audio_chunk"' in text
    assert "VOQUALIZER_INPUT_CODEC = \"pcm16/16k\"" in text


def test_client_is_full_duplex_and_handles_tts_playback():
    text = read(CLIENT)
    assert "startFullDuplex" in text
    assert "startMicrophoneCapture" in text
    assert "configurePlayback" in text
    assert "playPcm16" in text
    assert "AudioTrack" in text
    assert "audioTrack?.write(audio, 0, audio.size)" in text
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


def test_compose_activity_exposes_connect_full_duplex_text_and_event_ui():
    text = read(ACTIVITY)
    assert "VoqualizerDemoScreen" in text
    assert "Button(onClick = { scope.launch { client.connect" in text
    assert "Button(onClick = { scope.launch { client.startFullDuplex()" in text
    assert "client.control(\"barge_in\")" in text
    assert "client.sendText(text)" in text
    assert "state.partialText" in text
    assert "state.finalTranscripts" in text
    assert "state.agentText" in text
    assert "state.eventLog" in text


def test_android_example_declares_network_and_microphone_permissions():
    text = read(MANIFEST)
    assert "android.permission.INTERNET" in text
    assert "android.permission.RECORD_AUDIO" in text
    assert "MainActivity" in text
