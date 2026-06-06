"""Static markers test for the W18 Wyoming-based standalone page.

The new page lives alongside the legacy webui/voqualizer.html, which remains
in-tree for reference per the breaking-rewrite plan.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / 'webui' / 'voqualizer-wyoming.html'
LEGACY = ROOT / 'webui' / 'voqualizer.html'


def test_new_wyoming_page_exists():
    assert NEW.exists()


def test_legacy_standalone_page_preserved():
    assert LEGACY.exists()


def test_new_page_uses_only_wyoming_protocol():
    src = NEW.read_text()
    for forbidden in (
        'voqualizer_init',
        'voqualizer_user_text',
        'voqualizer_audio_chunk',
        'voqualizer_tts_chunk',
        'ack_fallback',
        'conversation-mode.js',
    ):
        assert forbidden not in src, forbidden
    for required in (
        '/plugins/a0_voqualizer/webui/wyoming/wyoming-ws-client.js',
        'createWyomingWsClient',
        "event:transcript",
        "event:voqualizer-response-start",
        "event:voqualizer-response-chunk",
        "event:voqualizer-response-final",
        "event:audio-start",
        "event:audio-chunk",
        "event:audio-stop",
        "submitText",
        "beginAudio",
        "sendAudioChunk",
        "endAudio",
        "cancelTts",
    ):
        assert required in src, required


def test_new_page_exposes_debug_snapshot():
    src = NEW.read_text()
    assert 'window.voqualizerWyomingDebug' in src
    assert 'client.snapshot()' in src
    assert 'beforeunload' in src


def test_new_page_filters_stale_generations():
    src = NEW.read_text()
    assert 'function isCurrent(ev)' in src
    assert 'client.isCurrentGeneration' in src
    assert 'if (!isCurrent(ev)) return;' in src


def test_new_page_has_interface_selector_and_discovery():
    src = NEW.read_text()
    assert 'voq-wyoming-interface' in src
    assert "action: 'interfaces'" in src
    assert '/api/plugins/a0_voqualizer/wyoming_ws' in src
    assert 'a0_voqualizer_wyoming_interface' in src
