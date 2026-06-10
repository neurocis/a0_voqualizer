"""Wyoming interface configuration model for Voqualizer.

Each interface maps 1:1 to an A0 ctxID. This module is W1/W2 scaffolding and is
kept independent of the old custom websocket runtime.
"""
from __future__ import annotations

from pathlib import Path

import json
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(slots=True)
class WyomingInterface:
    id: str
    name: str
    ctxid: str
    enabled: bool = True
    bind_host: str = "0.0.0.0"
    bind_port: int = 10700
    capabilities: dict[str, bool] = field(default_factory=lambda: {
        "asr": True,
        "tts": True,
        "assistant_text": True,
        "barge_in": True,
    })

    def validate(self) -> None:
        if not self.id:
            raise ValueError("Wyoming interface id is required")
        if not self.ctxid:
            raise ValueError(f"Wyoming interface {self.id!r} missing ctxID")
        if not isinstance(self.bind_port, int) or self.bind_port <= 0:
            raise ValueError(f"Wyoming interface {self.id!r} has invalid bind_port")


def load_interfaces(raw: Iterable[dict[str, Any]]) -> list[WyomingInterface]:
    interfaces: list[WyomingInterface] = []
    seen_ids: set[str] = set()
    seen_ports: set[tuple[str, int]] = set()
    for item in raw:
        iface = WyomingInterface(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or item.get("id") or ""),
            ctxid=str(item.get("ctxid") or item.get("ctxID") or ""),
            enabled=bool(item.get("enabled", True)),
            bind_host=str(item.get("bind_host") or "0.0.0.0"),
            bind_port=int(item.get("bind_port") or 10700),
            capabilities=dict(item.get("capabilities") or {}),
        )
        iface.validate()
        if iface.id in seen_ids:
            raise ValueError(f"Duplicate Wyoming interface id {iface.id!r}")
        port_key = (iface.bind_host, iface.bind_port)
        if iface.enabled and port_key in seen_ports:
            raise ValueError(f"Duplicate enabled Wyoming bind {port_key!r}")
        seen_ids.add(iface.id)
        if iface.enabled:
            seen_ports.add(port_key)
        interfaces.append(iface)
    return interfaces


def load_interfaces_from_file(path: str | Path) -> list[WyomingInterface]:
    """Load Wyoming interface records from a JSON config file.

    Compatibility helper used by runtime/bootstrap code. The config may be either
    a raw list of interface records or an object with an `interfaces` list.
    """
    raw = json.loads(Path(path).read_text())
    records = raw.get("interfaces", raw) if isinstance(raw, dict) else raw
    return load_interfaces(records)
