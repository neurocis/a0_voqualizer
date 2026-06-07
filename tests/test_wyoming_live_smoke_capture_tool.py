"""W51 live smoke capture tool tests."""
import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'wyoming_live_smoke_capture.py'
FIXTURE = ROOT / 'config' / 'wyoming_interfaces.smoke.example.json'


def _load_tool():
    spec = importlib.util.spec_from_file_location('wyoming_live_smoke_capture_tool', TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_live_smoke_capture_tool_markers_and_no_legacy_protocol():
    src = TOOL.read_text()
    for marker in (
        'capture_live_smoke',
        'readiness_snapshot',
        'smoke_report',
        'cli_capture_no_framework_hooks',
        '--tcp-describe',
    ):
        assert marker in src, marker
    retired = [
        'voqualizer' + '_init',
        'voqualizer' + '_user_text',
        'voqualizer' + '_audio_chunk',
        'voqualizer' + '_tts_chunk',
        'ack' + '_fallback',
    ]
    for forbidden in retired:
        assert forbidden not in src, forbidden


def test_live_smoke_capture_flags_placeholder_fixture():
    mod = _load_tool()
    data = asyncio.run(mod.capture_live_smoke(config=FIXTURE, include_smoke=False))
    assert data['ok'] is False
    assert data['validation']['ok'] is False
    assert data['validation']['placeholder_errors']
    assert data['readiness']['runtime_started'] is False


def test_live_smoke_capture_cli_returns_failure_for_placeholder_fixture(capsys):
    mod = _load_tool()
    code = mod.main(['--config', str(FIXTURE), '--no-smoke'])
    out = capsys.readouterr().out
    assert code == 1
    assert 'placeholder_errors' in out
    assert 'readiness' in out
