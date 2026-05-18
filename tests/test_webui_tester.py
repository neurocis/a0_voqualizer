from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "webui" / "tester-store.js"
HTML = ROOT / "webui" / "tester.html"


def store_source() -> str:
    return STORE.read_text(encoding="utf-8")


def html_source() -> str:
    return HTML.read_text(encoding="utf-8")


def test_a62_artifacts_exist():
    assert STORE.is_file()
    assert HTML.is_file()


def test_store_connects_to_voqualizer_ws_handler_and_init_event():
    text = store_source()
    assert "plugins/a0_voqualizer/ws_voqualizer" in text
    # A0 ships Socket.IO as an ES module and uses CSRF-protected auth
    # (csrf_token + handlers). The tester store must mirror that contract.
    assert "/vendor/socket.io.esm.min.js" in text
    assert "/js/api.js" in text
    assert "csrf_token" in text
    assert "VOQUALIZER_HANDLER" in text
    assert "voqualizer_init" in text
    assert "voqualizer_ready" in text
    assert "bearer_token" in text
    # A0 registers WS handlers on namespace '/ws' (see /a0/helpers/ws.py).
    # Connecting to '/' makes the handshake succeed but no handler ever
    # receives voqualizer_init, so the client never gets voqualizer_ready.
    assert "ioFactory('/ws'" in text
    assert "ioFactory('/'," not in text

def test_store_loads_a61_worklet_and_starts_microphone_capture():
    text = store_source()
    assert "./audio-worklet.js" in text
    assert "voqualizer-mic-processor" in text
    assert "navigator.mediaDevices.getUserMedia" in text
    assert "audioContext.audioWorklet.addModule(WORKLET_URL)" in text
    assert "new AudioWorkletNode" in text


def test_store_frames_pcm16_audio_as_a2_binary_header_and_sends_with_token():
    text = store_source()
    assert "export function framePcm16" in text
    assert "view.setUint16(0, seq & 0xffff, false)" in text
    assert "view.setUint16(2, tsMs & 0xffff, false)" in text
    assert "export function audioChunkPayload" in text
    assert "return frame;" in text
    assert "frame_bytes: frame.byteLength" in text
    assert "voqualizer_audio_chunk" in text
    assert "sessionPayload(audioPayload)" in text
    assert "bearer_token: state.bearerToken" in text
    assert "voqualizer_audio_error" in text
    assert "appendEvent('voqualizer_audio_ack'" in text


def test_store_renders_asr_and_agent_events():
    text = store_source()
    for event in [
        "voqualizer_asr_partial",
        "voqualizer_asr_final",
        "voqualizer_agent_delta",
        "voqualizer_agent_response_final",
    ]:
        assert event in text
    assert "partialText" in text
    assert "finalTranscripts" in text
    assert "agentText" in text


def test_store_plays_streamed_tts_chunks():
    text = store_source()
    assert "voqualizer_tts_chunk" in text
    assert "voqualizer_tts_done" in text
    assert "pcm16ToFloat32" in text
    assert "createBuffer(1, samples.length, sampleRate)" in text
    assert "copyToChannel(samples, 0)" in text
    assert "source.start(startAt)" in text


def test_html_binds_controls_transcripts_agent_and_event_log():
    text = html_source()
    for dom_id in [
        'id="connect"',
        'id="start"',
        'id="partial"',
        'id="finals"',
        'id="agent"',
        'id="events"',
        'id="vu-bar"',
    ]:
        assert dom_id in text
    assert "createVoqualizerTesterStore" in text
    assert "store.startCapture()" in text
    assert "store.sendText(text)" in text
    assert "store.control('barge_in')" in text


def test_store_reconnect_after_end_session_creates_fresh_session():
    text = STORE.read_text(encoding="utf-8")
    assert "The tester may intentionally mark the logical session disconnected" in text
    assert "const requestedSessionId = init.session_id || init.sessionId || '';" in text
    assert "const sessionId = requestedSessionId || (state.bearerToken && state.sessionId ? state.sessionId : makeSessionId());" in text
    assert "setState({ sessionId, bearerToken: '', negotiated: null, capabilities: null });" in text
    assert "session_id: sessionId" in text
    assert "sessionId: makeSessionId()" in text
    assert "bearerToken: ''," in text
    assert "connected: false," in text
