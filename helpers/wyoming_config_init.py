"""Safe Wyoming interface config initializer (W38).

Creates a concrete `config/wyoming_interfaces.json` from explicit admin input so
users do not have to copy placeholder example files by hand. It refuses
placeholder ctxIDs and never overwrites an existing config unless explicitly
requested.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .wyoming_runtime import DEFAULT_INTERFACE_CONFIG, validate_runtime_interfaces
from .wyoming_interfaces import load_interfaces

_PLACEHOLDER_PREFIXES = ("REPLACE_WITH_", "PLACEHOLDER", "TODO", "CTXID_HERE")


def _clean_id(value: str, fallback: str = "default") -> str:
    text = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(value or "").strip())
    return text.strip("-") or fallback


def _validate_ctxid(ctxid: str) -> list[str]:
    text = str(ctxid or "").strip()
    if not text:
        return ["ctxid is required"]
    if any(text.upper().startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES):
        return [f"placeholder ctxid is not allowed: {text}"]
    return []


def build_single_interface_config(
    *,
    ctxid: str,
    interface_id: str = "default",
    name: str = "Voqualizer Wyoming",
    bind_host: str = "127.0.0.1",
    bind_port: int = 10701,
    enabled: bool = True,
) -> dict[str, Any]:
    """Return a validated one-interface config payload."""
    iid = _clean_id(interface_id)
    record = {
        "id": iid,
        "name": str(name or iid),
        "ctxid": str(ctxid or "").strip(),
        "enabled": bool(enabled),
        "bind_host": str(bind_host or "127.0.0.1"),
        "bind_port": int(bind_port or 10701),
        "capabilities": {
            "asr": True,
            "tts": True,
            "prompt": True,
            "assistant_text": True,
            "authoritative_tts": True,
        },
    }
    errors = _validate_ctxid(record["ctxid"])
    if not errors:
        errors = validate_runtime_interfaces(load_interfaces([record]))
    if errors:
        raise ValueError("; ".join(errors))
    return {"interfaces": [record]}


def init_wyoming_config(
    *,
    ctxid: str,
    interface_id: str = "default",
    name: str = "Voqualizer Wyoming",
    bind_host: str = "127.0.0.1",
    bind_port: int = 10701,
    enabled: bool = True,
    config_path: str | Path = DEFAULT_INTERFACE_CONFIG,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a concrete Wyoming interface config and return a JSON-safe report."""
    path = Path(config_path)
    if path.exists() and not overwrite:
        return {
            "ok": False,
            "created": False,
            "config_path": str(path),
            "error": "config_exists",
            "message": "Wyoming interface config already exists; pass overwrite=true to replace it.",
        }
    try:
        payload = build_single_interface_config(
            ctxid=ctxid,
            interface_id=interface_id,
            name=name,
            bind_host=bind_host,
            bind_port=bind_port,
            enabled=enabled,
        )
    except Exception as exc:  # noqa: BLE001 - report to admin caller
        return {"ok": False, "created": False, "config_path": str(path), "error": str(exc)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return {
        "ok": True,
        "created": True,
        "config_path": str(path),
        "interface_id": payload["interfaces"][0]["id"],
        "ctxid": payload["interfaces"][0]["ctxid"],
        "bind_host": payload["interfaces"][0]["bind_host"],
        "bind_port": payload["interfaces"][0]["bind_port"],
    }
