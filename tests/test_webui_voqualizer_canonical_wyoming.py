"""Canonical standalone Voqualizer page preserves UI and loads Wyoming-backed JS."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / 'webui' / 'voqualizer.html'
JS = ROOT / 'webui' / 'voqualizer.js'
LEGACY_REF = ROOT / 'webui' / 'voqualizer-legacy-reference.html'


def test_canonical_voqualizer_page_preserves_polished_layout():
    src = CANONICAL.read_text()
    for marker in ('voq-topbar', 'voq-brand-row', 'voq-chat', 'voq-composer', 'voq-send-button', 'voq-mic-button', 'voq-speaker-button'):
        assert marker in src, marker
    assert 'id="voq-wyoming-app"' not in src


def test_canonical_page_loads_wyoming_backed_js_cache_marker():
    src = CANONICAL.read_text()
    assert '/plugins/a0_voqualizer/webui/voqualizer.js?v=w62-wyoming-runtime-autostart-2026-06-09-1' in src
    js = JS.read_text()
    for marker in ('loadWyomingWsClientFactory', 'submitPromptOverWyomingSession', 'WYOMING_TRANSPORT_PRIMARY = true'):
        assert marker in js, marker


def test_legacy_standalone_preserved_only_as_reference():
    assert LEGACY_REF.exists()
