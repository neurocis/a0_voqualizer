"""W49 consolidated readiness snapshot tests."""
import asyncio
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'helpers' / 'wyoming_readiness.py'
API = ROOT / 'api' / 'wyoming_status.py'
FIXTURE = ROOT / 'config' / 'wyoming_interfaces.smoke.example.json'


def test_readiness_helper_source_markers_and_no_legacy_protocol():
    src = HELPER.read_text()
    for marker in ('readiness_snapshot', 'ready_for_browser', 'blockers', 'run_live_checklist'):
        assert marker in src, marker
    for forbidden in ('voqualizer_init', 'voqualizer_user_text', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'ack_fallback'):
        assert forbidden not in src, forbidden


def test_readiness_snapshot_flags_placeholder_fixture_blocker():
    mod = importlib.import_module('helpers.wyoming_readiness')
    data = asyncio.run(mod.readiness_snapshot(
        config_path=FIXTURE,
        runtime_status_provider=lambda: {'started': False},
        validate_provider=lambda path: {'ok': False, 'errors': ['placeholder']},
        live_provider_status=lambda: {'mode': 'live_providers'},
    ))
    assert data['ok'] is False
    assert data['ready_for_browser'] is False
    assert 'config_validation_failed' in data['blockers']
    assert 'placeholder_or_missing_ctxid' in data['blockers']


def test_readiness_snapshot_real_config_not_ready_until_runtime_started(tmp_path):
    mod = importlib.import_module('helpers.wyoming_readiness')
    config = tmp_path / 'wyoming_interfaces.json'
    config.write_text(json.dumps({'interfaces': [{
        'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-real', 'enabled': True,
        'bind_host': '127.0.0.1', 'bind_port': 10701,
    }]}))
    data = asyncio.run(mod.readiness_snapshot(
        config_path=config,
        interface_id='hero',
        runtime_status_provider=lambda: {'started': False},
        validate_provider=lambda path: {'ok': True},
        live_provider_status=lambda: {'mode': 'live_providers'},
    ))
    assert data['ok'] is True
    assert data['ready_for_browser'] is False
    assert data['tcp_describe'] == 'skipped'
    assert data['provider_ok'] is True


def test_admin_endpoint_supports_readiness_action():
    src = API.read_text()
    assert 'action == "readiness"' in src
    assert 'readiness_snapshot' in src
    assert '"readiness"' in src
