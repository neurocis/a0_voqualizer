"""Tests for W52 Wyoming live admin capture tool."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'wyoming_live_admin_capture.py'


def _load_tool():
    spec = importlib.util.spec_from_file_location('wyoming_live_admin_capture', TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_tool_source_avoids_retired_protocol_tokens():
    src = TOOL.read_text()
    for token in ('voqualizer' + '_init', 'voqualizer' + '_user_text',
                  'voqualizer' + '_audio_chunk', 'voqualizer' + '_tts_chunk',
                  'ack' + '_fallback'):
        assert token not in src
    assert '/api/plugins/a0_voqualizer/wyoming_status' in src
    for action in ('"status"', '"dom_integration"', '"validate"', '"readiness"', '"smoke"', '"checklist"'):
        assert action in src


def test_capture_handles_connection_failure_cleanly():
    mod = _load_tool()
    bundle = mod.capture(
        host='127.0.0.1', port=1, scheme='http',
        cookie=None, csrf_token=None,
        interface_id='', tcp_describe=False, timeout=0.25,
    )
    assert bundle['ok'] is False
    assert any('framework_unreachable' in b for b in bundle['blockers'])
    assert bundle['actions']['status']['error'] == 'connection_failed'
    assert any('framework is running' in n for n in bundle['next_actions'])


def test_capture_handles_auth_redirect():
    mod = _load_tool()
    fake = {'http_status': 302, 'ok': False, 'error': 'http_error', 'reason': 'Found'}

    def _fake_post(*a, **kw):
        return dict(fake)

    with patch.object(mod, '_post_action', side_effect=_fake_post):
        bundle = mod.capture(
            host='127.0.0.1', port=80, scheme='http',
            cookie=None, csrf_token=None,
            interface_id='', tcp_describe=False, timeout=1.0,
        )
    assert bundle['ok'] is False
    assert any('auth_required' in b for b in bundle['blockers'])
    assert any('--cookie' in n for n in bundle['next_actions'])


def test_capture_success_bundles_all_actions():
    mod = _load_tool()
    ok = {'ok': True, 'http_status': 200, 'data': {'fake': True}}

    def _fake_post(base_url, payload, *, cookie, csrf_token, timeout):
        return {'ok': True, 'http_status': 200, 'echo': payload.get('action')}

    with patch.object(mod, '_post_action', side_effect=_fake_post):
        bundle = mod.capture(
            host='127.0.0.1', port=80, scheme='http',
            cookie='session=x', csrf_token='tok',
            interface_id='hero', tcp_describe=True, timeout=1.0,
        )
    assert bundle['ok'] is True
    assert bundle['authenticated'] is True
    for name in ('status', 'dom_integration', 'validate', 'readiness', 'smoke', 'checklist'):
        assert name in bundle['actions']
        assert bundle['actions'][name]['echo'] == name
    assert bundle['blockers'] == []


def test_main_returns_nonzero_on_connection_failure(capsys):
    mod = _load_tool()
    rc = mod.main(['--host', '127.0.0.1', '--port', '1', '--timeout', '0.25'])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 1
    assert payload['ok'] is False
    assert 'wyoming_live_admin_capture' == payload['tool']
