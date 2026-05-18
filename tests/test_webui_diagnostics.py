from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "webui" / "tester-store.js"
HTML = ROOT / "webui" / "tester.html"


def store_source() -> str:
    return STORE.read_text(encoding="utf-8")


def html_source() -> str:
    return HTML.read_text(encoding="utf-8")


def test_a64_confirmed_diagnostics_are_in_tester_artifacts():
    assert STORE.is_file()
    assert HTML.is_file()
    assert "diagnostics" in store_source()
    assert "diagnostics-overlay" in html_source()


def test_store_tracks_latency_timers():
    text = store_source()
    for marker in [
        "lastAudioSentAt",
        "lastAudioAckAt",
        "lastAsrPartialAt",
        "lastAsrFinalAt",
        "lastAgentDeltaAt",
        "lastTtsChunkAt",
        "firstTtsLatencyMs",
        "lastAckRttMs",
        "markAudioAck",
    ]:
        assert marker in text


def test_store_implements_frame_inspector_for_a2_frames():
    text = store_source()
    assert "frameInspector" in text
    assert "recordFrameInspection" in text
    assert "view.getUint16(0, false)" in text
    assert "view.getUint16(2, false)" in text
    assert "payloadBytes: bytes.byteLength - FRAME_HEADER_BYTES" in text
    assert "codec: INPUT_CODEC" in text


def test_store_records_audio_acks_and_can_clear_diagnostics():
    text = store_source()
    assert "recordFrameInspection(frame)" in text
    assert "then(() => markAudioAck(sentAt))" in text
    assert "clearDiagnostics" in text
    assert "state.frameInspector.splice(0)" in text
    assert "state.events.splice(0)" in text


def test_html_renders_latency_timers_frame_inspector_and_event_log():
    text = html_source()
    for dom_id in [
        'id="diag-audio-rtt"',
        'id="diag-first-tts"',
        'id="diag-asr-partial"',
        'id="diag-asr-final"',
        'id="diag-agent-delta"',
        'id="diag-tts-chunk"',
        'id="frame-inspector"',
        'id="events"',
        'id="clear-diagnostics"',
    ]:
        assert dom_id in text
    assert "Latency timers" in text
    assert "Frame inspector" in text
    assert "Event log" in text


def test_html_binds_diagnostics_state_to_overlay():
    text = html_source()
    assert "state.diagnostics.lastAckRttMs" in text
    assert "state.diagnostics.firstTtsLatencyMs" in text
    assert "state.frameInspector.map" in text
    assert "seq=${frame.seq}" in text
    assert "store.clearDiagnostics()" in text
