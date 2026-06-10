"""W57: preserve polished standalone UI while switching canonical transport to Wyoming."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / 'webui' / 'voqualizer.html'
JS = ROOT / 'webui' / 'voqualizer.js'
REF = ROOT / 'webui' / 'voqualizer-legacy-reference.html'


def test_canonical_html_preserves_existing_visual_layout():
    src = HTML.read_text()
    for marker in (
        'data-voqualizer-page="standalone"',
        'voq-topbar',
        'voq-brand-row',
        'voq-context-menu-button',
        'voq-chat',
        'voq-composer',
        'voq-send-button',
        'voq-mic-button',
        'voq-speaker-button',
        'Material Symbols',
        '/plugins/a0_voqualizer/webui/voqualizer.css',
        '/plugins/a0_voqualizer/webui/voqualizer.js',
        'w61-wyoming-init-gate-2026-06-09-1',
    ):
        assert marker in src, marker


def test_legacy_reference_kept_for_diff_not_served_as_canonical():
    assert REF.exists()
    assert HTML.read_text() != ''
    assert 'voqualizer-legacy-reference' not in HTML.read_text()


def test_voqualizer_js_imports_wyoming_client_and_primary_transport():
    src = JS.read_text()
    for marker in (
        'loadWyomingWsClientFactory',
        'WYOMING_STATUS_ENDPOINT',
        'WYOMING_TRANSPORT_PRIMARY = true',
        'configureWyomingWebInterface',
        'ensureWyomingSession',
        'submitPromptOverWyomingSession',
        "action: 'web_configure'",
        "promptSubmitTransport = 'wyoming'",
        'w61-wyoming-init-gate-2026-06-09-1',
    ):
        assert marker in src, marker


def test_wyoming_events_feed_existing_ui_handlers():
    src = JS.read_text()
    for marker in (
        "event:voqualizer-response-start",
        "event:voqualizer-response-chunk",
        "event:voqualizer-response-final",
        "event:audio-start",
        "event:audio-chunk",
        "event:audio-stop",
        'handleCxStreamStart',
        'handleCxToken',
        'handleCxStreamFinal',
        'handleTtsChunk',
        'handleTtsDone',
    ):
        assert marker in src, marker


def test_canonical_html_is_not_minimal_wyoming_scaffold():
    src = HTML.read_text()
    assert 'id="voq-wyoming-app"' not in src
    assert 'id="voq-wyoming-transcript"' not in src
    assert 'class="voqualizer-app"' not in src


def test_wyoming_runtime_helpers_match_shared_client_api():
    src = JS.read_text()
    assert 'function getPageStateRef()' in src
    assert 'function isWyomingClientConnected' in src
    assert '.isConnected?.()' not in src
    assert 'disconnectWyomingSession' in src
    assert 'client.snapshot?.().connected' in src
