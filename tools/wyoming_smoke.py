#!/usr/bin/env python3
"""Wyoming smoke diagnostics for a0_voqualizer (W23).

This script is intentionally lightweight and safe:

- validates a Wyoming interface config file;
- prints interface -> ctxID bindings;
- reports configured live ASR/TTS provider status;
- optionally performs a TCP describe/info round-trip against one interface;
- does not depend on the retired custom voqualizer_* websocket protocol.

Usage examples:

  python3 tools/wyoming_smoke.py --config config/wyoming_interfaces.json
  python3 tools/wyoming_smoke.py --config config/wyoming_interfaces.json --interface hero-smoke --tcp-describe
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from helpers.wyoming_interfaces import load_interfaces  # noqa: E402
from helpers.wyoming_live_providers import live_provider_status  # noqa: E402
from helpers.wyoming_protocol import event, read_event_from_stream, write_event_to_stream  # noqa: E402


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


async def tcp_describe(host: str, port: int, timeout: float = 3.0) -> dict[str, Any]:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    try:
        await write_event_to_stream(writer, event("describe"))
        reply = await asyncio.wait_for(read_event_from_stream(reader), timeout=timeout)
        return {
            "ok": True,
            "type": reply.type,
            "data": dict(reply.data),
            "payload_length": len(reply.payload or b""),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def build_report(config_path: Path) -> dict[str, Any]:
    raw = json.loads(config_path.read_text())
    records = raw.get("interfaces", raw) if isinstance(raw, dict) else raw
    interfaces = load_interfaces(records)
    enabled = [i for i in interfaces if i.enabled]
    return {
        "config_path": str(config_path),
        "configured_interfaces": len(interfaces),
        "enabled_interfaces": len(enabled),
        "interfaces": [
            {
                "id": i.id,
                "name": i.name,
                "ctxid": i.ctxid,
                "enabled": i.enabled,
                "bind_host": i.bind_host,
                "bind_port": i.bind_port,
            }
            for i in interfaces
        ],
        "live_providers": live_provider_status(),
    }


async def amain() -> int:
    parser = argparse.ArgumentParser(description="a0_voqualizer Wyoming smoke diagnostics")
    parser.add_argument("--config", default=str(PLUGIN_ROOT / "config" / "wyoming_interfaces.json"))
    parser.add_argument("--interface", default="", help="interface id to use for TCP describe")
    parser.add_argument("--tcp-describe", action="store_true", help="perform TCP describe/info round-trip")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()

    config_path = Path(args.config)
    report = build_report(config_path)

    if args.tcp_describe:
        target = None
        for iface in report["interfaces"]:
            if args.interface and iface["id"] != args.interface:
                continue
            if iface["enabled"]:
                target = iface
                break
        if target is None:
            report["tcp_describe"] = {"ok": False, "error": "no enabled matching interface"}
        else:
            report["tcp_describe"] = await tcp_describe(
                str(target["bind_host"] or "127.0.0.1"),
                int(target["bind_port"]),
                timeout=float(args.timeout),
            )

    print(_json(report))
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except Exception as exc:  # noqa: BLE001 - CLI diagnostic
        print(_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
