"""Static markers test for W19 DOM main-UI Wyoming extension scaffold.

The legacy DOM extension `voqualizer-buttons.html` remains in-tree for
reference. The new Wyoming-based extension lives alongside it and only speaks
Wyoming events via the shared WS client adapter.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-wyoming-buttons.html'
LEGACY = ROOT / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-buttons.html'


def test_new_dom_extension_exists():
    assert NEW.exists()


def test_legacy_dom_extension_preserved():
    assert LEGACY.exists()


def test_new_dom_extension_uses_only_wyoming_protocol():
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
        'chat-input-box-end',
        'voqualizer-wyoming-mic-button',
        'voqualizer-wyoming-speaker-button',
        'voqualizerWyomingButtons',
        'event:transcript',
        'event:audio-start',
        'event:audio-chunk',
        'beginAudio',
        'sendAudioChunk',
        'endAudio',
        'cancelTts',
        'data-wyoming-interface',
    ):
        assert required in src, required


def test_dom_extension_exposes_debug_snapshot():
    src = NEW.read_text()
    assert 'window.voqualizerWyomingDomDebug' in src
    assert 'client.snapshot()' in src


def test_dom_extension_filters_stale_audio_generations():
    src = NEW.read_text()
    assert '_isCurrent(ev)' in src
    assert 'client.isCurrentGeneration' in src
    assert 'if (this._isCurrent(ev)) this._playPcmChunk' in src


def test_dom_extension_uses_shared_interface_selection_fallback():
    src = NEW.read_text()
    assert 'a0_voqualizer_wyoming_interface' in src
    assert "params.get('wyoming')" in src


def test_dom_extension_uses_w35_csrf_cache_bust():
    src = NEW.read_text()
    assert 'w35-csrf-dom-2026-06-05-1' in src


def test_dom_extension_exposes_smoke_diagnostics():
    src = NEW.read_text()
    assert 'window.voqualizerWyomingDomSmoke' in src
    assert 'async _smokeDiagnostics()' in src
    assert "action: 'smoke'" in src
    assert '/api/plugins/a0_voqualizer/wyoming_status' in src
    assert 'lastSmokeDiagnostics' in src
    assert 'w36-smoke-dom-2026-06-06-1' in src


def test_dom_extension_exposes_validate_and_start_diagnostics():
    src = NEW.read_text()
    for marker in (
        'window.voqualizerWyomingDomValidate',
        'window.voqualizerWyomingDomStart',
        'async _validateRuntimeConfig()',
        'async _startRuntime()',
        "action: 'validate'",
        "action: 'start'",
        'lastValidation',
        'lastStartStatus',
        'w40-dom-status-2026-06-06-1',
    ):
        assert marker in src, marker
