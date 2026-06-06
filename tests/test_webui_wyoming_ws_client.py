"""Static markers test for the shared browser-side Wyoming WS client (W17).

We don't run JS at CI time; this test asserts the W17 adapter only references
Wyoming protocol events and is wired against the W16 handler id, never the
retired voqualizer_* socket protocol.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'webui' / 'wyoming' / 'wyoming-ws-client.js'


def test_wyoming_ws_client_exists():
    assert CLIENT.exists(), 'webui/wyoming/wyoming-ws-client.js must exist'


def test_wyoming_ws_client_uses_wyoming_protocol_only():
    src = CLIENT.read_text()
    for forbidden in (
        'voqualizer_init',
        'voqualizer_user_text',
        'voqualizer_audio_chunk',
        'voqualizer_tts_chunk',
        'ack_fallback',
    ):
        assert forbidden not in src, forbidden
    for required in (
        "plugins/a0_voqualizer/ws_wyoming",
        "emitWithAck('wyoming_init'",
        "emitWithAck('wyoming_event'",
        "wyoming_close",
        "wyoming_event",
        "audio-start",
        "audio-chunk",
        "audio-stop",
        "voqualizer-text-prompt",
        "voqualizer-control",
        "WyomingWsClient",
        "createWyomingWsClient",
        "newGeneration",
        "isCurrentGeneration",
    ):
        assert required in src, required


def test_wyoming_ws_client_exposes_event_dispatcher():
    src = CLIENT.read_text()
    assert 'this._handlers' in src
    assert "_emitLocal('event'" in src
    assert "_emitLocal('event:'" in src


def test_wyoming_ws_client_exposes_debug_snapshot():
    src = CLIENT.read_text()
    for required in (
        'snapshot() {',
        '_recordError',
        'connect_attempts',
        'init_acks',
        'events_in',
        'events_out',
        'payload_bytes_in',
        'payload_bytes_out',
        'last_in_type',
        'last_out_type',
        'last_generation_id',
        'stale_generation_drops',
    ):
        assert required in src, required


def test_wyoming_ws_client_normalizes_framework_ack_shapes():
    src = CLIENT.read_text()
    assert 'function _extractAckData' in src
    assert "Object.prototype.hasOwnProperty.call(ack, 'ok')" in src
    assert 'Array.isArray(ack.results)' in src
    assert 'Array.isArray(ack.data.results)' in src
    assert 'const ackData = _extractAckData(ack)' in src
    assert 'return _extractAckData(ack)' in src


def test_wyoming_ws_client_fetches_csrf_for_socket_auth():
    src = CLIENT.read_text()
    assert 'async function _fetchCsrfTokenSafe' in src
    assert "/webui/js/api.js" in src
    assert 'getCsrfToken' in src
    assert "/api/csrf_token" in src
    assert 'auth: async (cb)' in src
    assert 'csrf_token: csrf || undefined' in src
