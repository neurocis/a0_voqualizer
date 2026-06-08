"""Browser-facing Wyoming web setup helpers.

W55 makes the web interface functional without requiring users to run manual CLI
setup. The browser supplies the current Agent Zero ctxID; this helper creates a
single Wyoming interface bound 1:1 to that ctxID. Runtime routing still ignores
client-supplied ctxIDs after binding.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .wyoming_config_init import init_wyoming_config

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PLUGIN_ROOT / "config" / "wyoming_interfaces.json"
_PLACEHOLDER_MARKERS = ("REPLACE_WITH", "PLACEHOLDER", "TODO", "CTXID_HERE")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def is_real_ctxid(ctxid: str) -> bool:
    value = _clean(ctxid)
    if not value:
        return False
    upper = value.upper()
    return not any(marker in upper for marker in _PLACEHOLDER_MARKERS)


def bind_current_context_interface(
    *,
    ctxid: str,
    interface_id: str = "web",
    name: str = "Voqualizer Web",
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    bind_host: str = "127.0.0.1",
    bind_port: int = 10701,
    overwrite: bool = True,
) -> dict[str, Any]:
    clean_ctxid = _clean(ctxid)
    clean_interface = _clean(interface_id) or "web"
    if not is_real_ctxid(clean_ctxid):
        return {
            "ok": False,
            "error": "real_ctxid_required",
            "message": "Open Voqualizer from an active Agent Zero chat so the current ctxID can be bound.",
            "ctxid": clean_ctxid,
        }
    result = init_wyoming_config(
        ctxid=clean_ctxid,
        interface_id=clean_interface,
        name=name or clean_interface,
        config_path=config_path,
        bind_host=bind_host,
        bind_port=int(bind_port),
        overwrite=overwrite,
    )
    result.update({
        "web_ready": bool(result.get("ok")),
        "interface_id": clean_interface,
        "ctxid": clean_ctxid,
        "one_to_one_binding": True,
    })
    return result


def load_browser_interfaces(config_path: str | Path = DEFAULT_CONFIG_PATH) -> list[dict[str, Any]]:
    p = Path(config_path)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text())
    except Exception:
        return []
    items = raw.get("interfaces", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append({
            "id": _clean(item.get("id")),
            "name": _clean(item.get("name") or item.get("id")),
            "enabled": bool(item.get("enabled", True)),
            "ctxid": _clean(item.get("ctxid") or item.get("ctxID")),
            "bind_host": _clean(item.get("bind_host") or item.get("host")),
            "bind_port": item.get("bind_port") or item.get("port"),
        })
    return out
