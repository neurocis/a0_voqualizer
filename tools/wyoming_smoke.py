#!/usr/bin/env python3
"""Wyoming smoke diagnostics for a0_voqualizer (W23/W32)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers.wyoming_smoke_diagnostics import smoke_report, tcp_describe  # noqa: E402
from helpers.wyoming_interfaces import load_interfaces  # noqa: E402  # marker for tests
from helpers.wyoming_live_providers import live_provider_status  # noqa: E402  # marker for tests
from helpers.wyoming_protocol import read_event_from_stream, write_event_to_stream, event  # noqa: E402  # markers


def as_json(data):
    return json.dumps(data, indent=2, sort_keys=True)


async def main_async(argv=None) -> int:
    parser = argparse.ArgumentParser(description="a0_voqualizer Wyoming smoke diagnostics")
    parser.add_argument("--config", default=str(ROOT / "config" / "wyoming_interfaces.json"))
    parser.add_argument("--interface", default="")
    parser.add_argument("--tcp-describe", action="store_true")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(argv)
    report = await smoke_report(args.config, interface_id=args.interface, tcp=args.tcp_describe, timeout=args.timeout)
    print(as_json(report))
    return 0 if report.get("ok") else 1


def main(argv=None) -> int:
    try:
        return asyncio.run(main_async(argv))
    except Exception as exc:  # noqa: BLE001 - CLI diagnostic
        print(as_json({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
