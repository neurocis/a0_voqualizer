"""Shared Wyoming smoke diagnostics for CLI/admin API (W32)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .wyoming_interfaces import load_interfaces_from_file
from .wyoming_live_providers import live_provider_status
from .wyoming_protocol import event, read_event_from_stream, write_event_to_stream


async def tcp_describe(host: str, port: int, *, timeout: float = 3.0) -> dict[str, Any]:
    """Run one Wyoming describe/info TCP round-trip."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        try:
            await write_event_to_stream(writer, event("describe"))
            reply = await asyncio.wait_for(read_event_from_stream(reader), timeout=timeout)
            if reply is None:
                return {"ok": False, "error": "eof"}
            return {
                "ok": reply.type == "info",
                "type": reply.type,
                "data": reply.data,
                "payload_length": len(reply.payload or b""),
            }
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001 - diagnostic helper
        return {"ok": False, "error": str(exc)}


def interface_report(config_path: str | Path) -> dict[str, Any]:
    """Load interface config and return a JSON-safe smoke report."""
    path = Path(config_path)
    interfaces = load_interfaces_from_file(path)
    enabled = [iface for iface in interfaces if iface.enabled]
    return {
        "ok": True,
        "config_path": str(path),
        "configured_interfaces": len(interfaces),
        "enabled_interfaces": len(enabled),
        "interfaces": [
            {
                "id": iface.id,
                "name": iface.name,
                "ctxid": iface.ctxid,
                "enabled": bool(iface.enabled),
                "bind_host": iface.bind_host,
                "bind_port": iface.bind_port,
                "capabilities": dict(iface.capabilities or {}),
            }
            for iface in interfaces
        ],
        "live_providers": live_provider_status(),
    }


async def smoke_report(
    config_path: str | Path,
    *,
    interface_id: str = "",
    tcp: bool = False,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Build a complete smoke report with optional TCP describe probe."""
    try:
        report = interface_report(config_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "config_path": str(config_path), "error": str(exc)}
    if tcp:
        candidates = [i for i in report["interfaces"] if i.get("enabled")]
        if interface_id:
            candidates = [i for i in candidates if i.get("id") == interface_id]
        if not candidates:
            report["tcp_describe"] = {"ok": False, "error": "no enabled matching interface"}
        else:
            iface = candidates[0]
            report["tcp_describe"] = await tcp_describe(
                str(iface.get("bind_host") or "127.0.0.1"),
                int(iface.get("bind_port") or 0),
                timeout=timeout,
            )
    return report
