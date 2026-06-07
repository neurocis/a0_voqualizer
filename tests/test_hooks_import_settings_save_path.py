"""Regression for A0 Settings modal Save path.

A0 loads plugin hooks.py via helpers.modules.import_module/spec_from_file_location,
not as usr.plugins.a0_voqualizer.hooks. That means hooks.py has no package
context. The Wyoming rewrite originally used relative imports in hooks.py, which
made helpers.plugins.get_plugin_config/save_plugin_config fail before the modal
could save settings such as wyoming.dom_integration.enabled.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks.py"
DOM_SETTINGS = ROOT / "helpers" / "wyoming_dom_settings.py"


def test_hooks_py_loads_via_spec_from_file_location_like_a0_settings_modal():
    spec = importlib.util.spec_from_file_location("hooks", str(HOOKS))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "wyoming_runtime_status")
    assert hasattr(module, "validate_wyoming_config")
    assert hasattr(module, "start_wyoming_runtime")


def test_hooks_import_block_documents_settings_save_reason():
    src = HOOKS.read_text()
    assert "spec_from_file_location" in src
    assert "get_plugin_config / save_plugin_config" in src
    assert "a0_voqualizer_helpers" in src
    assert "from .helpers.wyoming_runtime" not in src
    assert "from helpers.wyoming_runtime" not in src


def test_dom_toggle_helper_can_persist_like_admin_action(tmp_path):
    spec = importlib.util.spec_from_file_location("dom_settings", str(DOM_SETTINGS))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"asr": {}, "tts": {}}))
    off = module.set_dom_integration_enabled(False, config_path=cfg)
    assert off["enabled"] is False
    loaded = json.loads(cfg.read_text())
    assert loaded["wyoming"]["dom_integration"]["enabled"] is False
    on = module.set_dom_integration_enabled(True, config_path=cfg)
    assert on["enabled"] is True
