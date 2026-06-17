"""W76 standalone UI finalization race regression checks."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'webui' / 'voqualizer.js'
HTML = ROOT / 'webui' / 'voqualizer.html'


def test_standalone_has_wyoming_finalization_helper_and_ack_safety():
    src = JS.read_text()
    for marker in (
        'function finalizeWyomingSubmission',
        'function scheduleWyomingAckFinalizer',
        'wyoming_ack_reply_safety_final',
        'lastWyomingFinalReason',
        'lastWyomingFinalGenerationId',
        'Do not overwrite a completed lifecycle back to an awaiting/running state',
    ):
        assert marker in src, marker


def test_response_final_uses_shared_finalize_helper():
    src = JS.read_text()
    assert "finalizeWyomingSubmission(state, 'wyoming_response_final', data)" in src
    # Avoid reintroducing direct partial cleanup inside the response-final handler.
    final_handler = src.split("client.on('event:voqualizer-response-final'", 1)[1].split("client.on('event:audio-start'", 1)[0]
    assert 'state.isSubmitting = false' not in final_handler
    assert "setPageStatus('Awaiting Wyoming response…', 'loading');\n    if (!WYOMING_TRANSPORT_PRIMARY)" not in src


def test_cache_marker_bumped_for_mobile_refresh():
    marker = 'w76-wyoming-finalize-race-2026-06-16-1'
    assert marker in JS.read_text()
    assert marker in HTML.read_text()


if __name__ == '__main__':
    test_standalone_has_wyoming_finalization_helper_and_ack_safety()
    test_response_final_uses_shared_finalize_helper()
    test_cache_marker_bumped_for_mobile_refresh()
    print('OK')
