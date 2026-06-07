"""Settings helpers for the Wyoming DOM main-UI integration toggle.

This toggle is intentionally scoped to ONLY the DOM main UI ASR/TTS extension.
It does NOT disable:
- the standalone Wyoming page (`webui/voqualizer-wyoming.html`);
- the Wyoming TCP runtime / interfaces;
- ASR/prompt/TTS provider adapters;
- legacy reference assets (`api/ws_voqualizer.py`, legacy standalone UI, etc.).

Resolution order (highest priority first):
1. `config.json` `wyoming.dom_integration.enabled`
2. `config.json` `wyoming.dom_integration_enabled` (flat compat key)
3. `default_config.yaml` `wyoming.dom_integration.enabled`
4. fallback default: True (DOM integration enabled).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CONFIG_JSON = PLUGIN_ROOT / "config.json"
DEFAULT_CONFIG_YAML = PLUGIN_ROOT / "default_config.yaml"
DEFAULT_DOM_INTEGRATION_ENABLED = True


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _read_default_yaml_toggle(path: Path = DEFAULT_CONFIG_YAML) -> bool | None:
    """Tiny non-PyYAML reader for `wyoming.dom_integration.enabled` only."""
    if not path.exists():
        return None
    in_wyoming = False
    in_dom = False
    try:
        for raw_line in path.read_text().splitlines():
            line = raw_line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if indent == 0:
                in_wyoming = stripped.startswith("wyoming:")
                in_dom = False
                continue
            if in_wyoming and indent == 2 and stripped.startswith("dom_integration:"):
                in_dom = True
                continue
            if in_wyoming and in_dom and indent == 4 and stripped.startswith("enabled:"):
                value = stripped.split(":", 1)[1].strip().lower()
                if value in ("true", "yes", "on", "1"):
                    return True
                if value in ("false", "no", "off", "0"):
                    return False
                return None
            if in_wyoming and indent <= 2:
                in_dom = False
            if indent == 0:
                in_wyoming = False
                in_dom = False
    except Exception:
        return None
    return None


def dom_integration_enabled(config: dict[str, Any] | None = None) -> bool:
    """Return whether the DOM main UI ASR/TTS integration should be active."""
    cfg = dict(config) if config is not None else _read_json(CONFIG_JSON)
    wyoming = cfg.get("wyoming") if isinstance(cfg.get("wyoming"), dict) else {}
    dom = wyoming.get("dom_integration") if isinstance(wyoming.get("dom_integration"), dict) else {}
    if "enabled" in dom:
        return bool(dom.get("enabled"))
    if "dom_integration_enabled" in wyoming:
        return bool(wyoming.get("dom_integration_enabled"))
    default_yaml_value = _read_default_yaml_toggle()
    if default_yaml_value is not None:
        return default_yaml_value
    return DEFAULT_DOM_INTEGRATION_ENABLED


def set_dom_integration_enabled(enabled: bool, *, config_path: str | Path = CONFIG_JSON) -> dict[str, Any]:
    """Persist the DOM-only toggle into the plugin config.json."""
    path = Path(config_path)
    cfg = _read_json(path)
    wyoming = cfg.get("wyoming") if isinstance(cfg.get("wyoming"), dict) else {}
    dom = wyoming.get("dom_integration") if isinstance(wyoming.get("dom_integration"), dict) else {}
    dom["enabled"] = bool(enabled)
    wyoming["dom_integration"] = dom
    cfg["wyoming"] = wyoming
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, sort_keys=True))
    return dom_integration_status(config_path=path)


def dom_integration_status(config_path: str | Path = CONFIG_JSON) -> dict[str, Any]:
    cfg = _read_json(Path(config_path))
    return {
        "ok": True,
        "scope": "dom_asr_tts_only",
        "enabled": dom_integration_enabled(cfg),
        "config_path": str(config_path),
        "does_not_disable": [
            "standalone_wyoming_page",
            "wyoming_tcp_runtime",
            "provider_runtime",
            "legacy_reference_assets",
        ],
    }
