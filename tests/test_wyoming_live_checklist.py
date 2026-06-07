"""W46 shared live checklist/admin action tests."""
import asyncio
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'helpers' / 'wyoming_live_checklist.py'
API = ROOT / 'api' / 'wyoming_status.py'
TOOL = ROOT / 'tools' / 'wyoming_live_checklist.py'
FIXTURE = ROOT / 'config' / 'wyoming_interfaces.smoke.example.json'


def test_shared_live_checklist_helper_exists_and_avoids_legacy_protocol():
    src = HELPER.read_text()
    for marker in ('run_live_checklist', 'real_ctxid_configured', 'tcp_describe_info', 'next_actions'):
        assert marker in src, marker
    for forbidden in ('voqualizer_init', 'voqualizer_user_text', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'ack_fallback'):
        assert forbidden not in src, forbidden


def test_shared_live_checklist_flags_placeholder_fixture():
    mod = importlib.import_module('helpers.wyoming_live_checklist')
    data = asyncio.run(mod.run_live_checklist(FIXTURE))
    steps = {step['name']: step for step in data['steps']}
    assert data['ok'] is False
    assert steps['config_load']['ok'] is True
    assert steps['real_ctxid_configured']['ok'] is False
    assert steps['tcp_describe_info']['skipped'] is True


def test_shared_live_checklist_accepts_real_ctxid_without_tcp(tmp_path):
    mod = importlib.import_module('helpers.wyoming_live_checklist')
    config = tmp_path / 'wyoming_interfaces.json'
    config.write_text(json.dumps({'interfaces': [{
        'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-real', 'enabled': True,
        'bind_host': '127.0.0.1', 'bind_port': 10701,
    }]}))
    data = asyncio.run(mod.run_live_checklist(config, interface_id='hero'))
    assert data['ok'] is True
    steps = {step['name']: step for step in data['steps']}
    assert steps['real_ctxid_configured']['ok'] is True
    assert steps['tcp_describe_info']['ok'] is None


def test_admin_endpoint_supports_checklist_action():
    src = API.read_text()
    assert 'action == "checklist"' in src
    assert 'run_live_checklist' in src
    assert '"checklist"' in src


def test_cli_delegates_to_shared_helper():
    src = TOOL.read_text()
    assert 'from helpers.wyoming_live_checklist import run_live_checklist' in src
    assert 'return await run_live_checklist' in src
