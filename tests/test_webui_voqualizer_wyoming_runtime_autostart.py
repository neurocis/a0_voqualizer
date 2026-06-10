"""Standalone Voqualizer typed send must auto-start Wyoming runtime before WS events."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'webui' / 'voqualizer.js'
HTML = ROOT / 'webui' / 'voqualizer.html'


def test_standalone_send_autostarts_runtime_before_connect():
    src = JS.read_text()
    for marker in (
        'function wyomingRuntimeIsStarted(status)',
        'function wyomingRuntimeStartError(status)',
        'async function startWyomingRuntimeForWeb()',
        "action: 'start'",
        'interface_id: WYOMING_PRIMARY_INTERFACE_ID',
        'throw new Error(reason)',
        'lastWyomingStartResult',
        'await startWyomingRuntimeForWeb();',
        'lastWyomingInitRetryReason',
        'lastWyomingInitRetryAt',
        'Wyoming runtime did not start',
    ):
        assert marker in src, marker


def test_runtime_autostart_version_marker_bumped():
    assert "const PAGE_VERSION = 'w62-wyoming-runtime-autostart-2026-06-09-1'" in JS.read_text()
    assert 'w62-wyoming-runtime-autostart-2026-06-09-1' in HTML.read_text()


def test_runtime_start_helpers_exported_for_debugging():
    src = JS.read_text()
    for marker in ('startWyomingRuntimeForWeb,', 'wyomingRuntimeIsStarted,', 'wyomingRuntimeStartError,'):
        assert marker in src
