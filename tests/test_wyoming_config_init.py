"""W38 safe Wyoming config initialization tests."""
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'helpers' / 'wyoming_config_init.py'
API = ROOT / 'api' / 'wyoming_status.py'


def test_config_init_helper_markers_and_legacy_protocol_avoidance():
    src = HELPER.read_text()
    for required in ('init_wyoming_config', 'build_single_interface_config', 'placeholder ctxid is not allowed', 'overwrite'):
        assert required in src
    for forbidden in ('voqualizer_init', 'voqualizer_user_text', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'ack_fallback'):
        assert forbidden not in src, forbidden


def test_build_single_interface_config_rejects_placeholder_ctxid():
    mod = importlib.import_module('helpers.wyoming_config_init')
    try:
        mod.build_single_interface_config(ctxid='REPLACE_WITH_REAL_CTXID')
    except ValueError as exc:
        assert 'placeholder ctxid' in str(exc)
    else:
        raise AssertionError('placeholder ctxid should be rejected')


def test_init_writes_valid_config_and_refuses_overwrite(tmp_path):
    mod = importlib.import_module('helpers.wyoming_config_init')
    path = tmp_path / 'wyoming_interfaces.json'
    report = mod.init_wyoming_config(ctxid='ctx-real', interface_id='hero smoke', config_path=path)
    assert report['ok'] is True
    assert report['created'] is True
    data = json.loads(path.read_text())
    assert data['interfaces'][0]['id'] == 'hero-smoke'
    assert data['interfaces'][0]['ctxid'] == 'ctx-real'
    again = mod.init_wyoming_config(ctxid='ctx-other', config_path=path)
    assert again['ok'] is False
    assert again['error'] == 'config_exists'


def test_init_overwrite_replaces_existing_config(tmp_path):
    mod = importlib.import_module('helpers.wyoming_config_init')
    path = tmp_path / 'wyoming_interfaces.json'
    mod.init_wyoming_config(ctxid='ctx-one', config_path=path)
    report = mod.init_wyoming_config(ctxid='ctx-two', interface_id='two', config_path=path, overwrite=True)
    assert report['ok'] is True
    data = json.loads(path.read_text())
    assert data['interfaces'][0]['id'] == 'two'
    assert data['interfaces'][0]['ctxid'] == 'ctx-two'


def test_status_endpoint_supports_init_config_action():
    src = API.read_text()
    assert 'action == "init_config"' in src
    assert 'init_wyoming_config' in src
    assert '"init_config"' in src
