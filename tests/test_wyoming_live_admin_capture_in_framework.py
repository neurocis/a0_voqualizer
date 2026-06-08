"""Tests for W53 in-framework live admin capture."""
import asyncio
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'helpers' / 'wyoming_live_admin_capture.py'
API = ROOT / 'api' / 'wyoming_status.py'
PAGE = ROOT / 'webui' / 'voqualizer-wyoming.html'
CLI = ROOT / 'tools' / 'wyoming_live_admin_capture.py'


def test_helper_avoids_retired_protocol_tokens():
    src = HELPER.read_text()
    for token in ('voqualizer' + '_init', 'voqualizer' + '_user_text', 'voqualizer' + '_audio_chunk', 'voqualizer' + '_tts_chunk', 'ack' + '_fallback'):
        assert token not in src
    assert 'live_admin_capture' in src
    assert 'wyoming_live_admin_capture_in_framework' in src


def test_capture_bundles_actions_with_mocked_providers(tmp_path):
    mod = importlib.import_module('helpers.wyoming_live_admin_capture')
    cfg = tmp_path / 'wyoming_interfaces.json'
    cfg.write_text('{"interfaces": []}')
    result = asyncio.run(mod.live_admin_capture(
        config_path=cfg,
        interface_id='hero',
        runtime_status_provider=lambda: {'ok': True, 'started': True},
        validate_provider=lambda path: {'ok': True, 'config_path': str(path)},
        live_provider_status=lambda: {'mode': 'live_providers'},
        dom_integration_status_provider=lambda: {'ok': True, 'enabled': False},
    ))
    assert result['tool'] == 'wyoming_live_admin_capture_in_framework'
    for action in ('status', 'dom_integration', 'validate', 'readiness', 'smoke', 'checklist'):
        assert action in result['actions']
    assert result['interface_id'] == 'hero'


def test_api_and_standalone_page_expose_live_capture():
    api = API.read_text()
    assert 'action == "live_admin_capture"' in api
    assert 'live_admin_capture' in api
    page = PAGE.read_text()
    for marker in (
        'id="voq-wyoming-capture"',
        "action: 'live_admin_capture'",
        'window.voqualizerWyomingCapture',
        'captureLiveAdminDiagnostics',
        'w53-capture-2026-06-07-1',
    ):
        assert marker in page, marker


def test_cli_save_flag_markers():
    src = CLI.read_text()
    assert 'p.add_argument("--save"' in src
    assert 'Path(args.save).write_text' in src
