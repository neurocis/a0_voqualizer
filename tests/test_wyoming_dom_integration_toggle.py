"""Side quest: DOM-only Wyoming ASR/TTS integration toggle tests."""
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'helpers' / 'wyoming_dom_settings.py'
API = ROOT / 'api' / 'wyoming_status.py'
DOM = ROOT / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-wyoming-buttons.html'
DEFAULT_YAML = ROOT / 'default_config.yaml'
CONFIG_HTML = ROOT / 'webui' / 'config.html'


def test_dom_settings_helper_defaults_and_overrides():
    mod = importlib.import_module('helpers.wyoming_dom_settings')
    # Empty config: falls back through yaml default which is true
    assert mod.dom_integration_enabled({}) is True
    # Explicit disable
    assert mod.dom_integration_enabled({'wyoming': {'dom_integration': {'enabled': False}}}) is False
    # Flat compat key
    assert mod.dom_integration_enabled({'wyoming': {'dom_integration_enabled': False}}) is False


def test_dom_settings_status_scope_and_does_not_disable():
    mod = importlib.import_module('helpers.wyoming_dom_settings')
    status = mod.dom_integration_status()
    assert status['scope'] == 'dom_asr_tts_only'
    assert status['ok'] is True
    for marker in ('standalone_wyoming_page', 'wyoming_tcp_runtime', 'provider_runtime', 'legacy_reference_assets'):
        assert marker in status['does_not_disable']


def test_set_dom_integration_enabled_roundtrip(tmp_path):
    mod = importlib.import_module('helpers.wyoming_dom_settings')
    cfg = tmp_path / 'config.json'
    result = mod.set_dom_integration_enabled(False, config_path=cfg)
    assert result['enabled'] is False
    raw = json.loads(cfg.read_text())
    assert raw['wyoming']['dom_integration']['enabled'] is False
    result2 = mod.set_dom_integration_enabled(True, config_path=cfg)
    assert result2['enabled'] is True


def test_default_yaml_includes_dom_integration_toggle():
    src = DEFAULT_YAML.read_text()
    assert 'wyoming:' in src
    assert 'dom_integration:' in src
    assert 'enabled: true' in src


def test_admin_endpoint_supports_dom_integration_action():
    src = API.read_text()
    assert 'action == "dom_integration"' in src
    assert 'dom_integration_status' in src
    assert 'set_dom_integration_enabled' in src
    assert '"dom_integration"' in src


def test_settings_panel_exposes_dom_toggle():
    src = CONFIG_HTML.read_text()
    assert 'Wyoming DOM integration' in src
    assert 'config.wyoming.dom_integration.enabled' in src
    assert 'DOM main UI ASR/TTS integration' in src


def test_dom_extension_checks_toggle_before_connecting():
    src = DOM.read_text()
    for marker in (
        "action: 'dom_integration'",
        '_fetchDomIntegrationStatus',
        'DOM ASR/TTS disabled',
        'data-wyoming-dom-disabled',
        'voqualizerWyomingDomIntegrationStatus',
        'side-dom-toggle-2026-06-08-4',
    ):
        assert marker in src, marker


def test_dom_settings_helper_avoids_retired_protocol_literals():
    src = HELPER.read_text()
    retired = ['voqualizer' + '_init', 'voqualizer' + '_user_text',
               'voqualizer' + '_audio_chunk', 'voqualizer' + '_tts_chunk',
               'ack' + '_fallback']
    for token in retired:
        assert token not in src
