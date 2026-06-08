"""W55 functional Wyoming web UI auto setup tests."""
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'helpers' / 'wyoming_web_context.py'
API = ROOT / 'api' / 'wyoming_status.py'
PAGE = ROOT / 'webui' / 'voqualizer-wyoming.html'


def test_web_context_helper_creates_real_single_interface(tmp_path):
    mod = importlib.import_module('helpers.wyoming_web_context')
    path = tmp_path / 'wyoming_interfaces.json'
    result = mod.bind_current_context_interface(ctxid='ctx-live-123', interface_id='web', config_path=path)
    assert result['ok'] is True
    assert result['one_to_one_binding'] is True
    raw = json.loads(path.read_text())
    assert raw['interfaces'][0]['id'] == 'web'
    assert raw['interfaces'][0]['ctxid'] == 'ctx-live-123'


def test_web_context_helper_rejects_missing_or_placeholder_ctxid(tmp_path):
    mod = importlib.import_module('helpers.wyoming_web_context')
    for bad in ('', 'REPLACE_WITH_REAL_CTXID', 'CTXID_HERE'):
        result = mod.bind_current_context_interface(ctxid=bad, config_path=tmp_path / 'x.json')
        assert result['ok'] is False
        assert result['error'] == 'real_ctxid_required'


def test_status_endpoint_exposes_web_configure_action():
    src = API.read_text()
    assert 'bind_current_context_interface' in src
    assert 'action == "web_configure"' in src
    assert 'web_configure' in src


def test_standalone_page_auto_configures_current_chat_for_web_functionality():
    src = PAGE.read_text()
    for marker in (
        'getCurrentA0ContextId',
        "action: 'web_configure'",
        'configureWebInterfaceFromCurrentContext',
        'window.voqualizerWyomingConfigureWeb',
        'connectWithAutoSetup',
        'current_context_unavailable',
        'w55-functional-web-2026-06-08-1',
    ):
        assert marker in src, marker
    retired = ['voqualizer' + '_init', 'voqualizer' + '_user_text', 'voqualizer' + '_audio_chunk', 'voqualizer' + '_tts_chunk', 'ack' + '_fallback']
    for token in retired:
        assert token not in src
        assert token not in HELPER.read_text()
