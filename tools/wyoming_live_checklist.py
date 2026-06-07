#!/usr/bin/env python3
"""Local live Wyoming validation checklist runner (W45).

This script intentionally avoids framework-authenticated browser/admin APIs. It
validates config and can optionally probe the configured TCP Wyoming interface
with describe/info. Use it after starting the plugin runtime through Agent Zero
or the admin API.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers.wyoming_live_checklist import run_live_checklist  # noqa: E402
from helpers.wyoming_runtime import DEFAULT_INTERFACE_CONFIG  # noqa: E402

def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


async def run_checklist(config: str, interface_id: str = "", tcp_describe: bool = False, timeout: float = 3.0) -> dict[str, Any]:
    return await run_live_checklist(config, interface_id=interface_id, tcp_describe=tcp_describe, timeout=timeout)


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Voqualizer Wyoming live validation checklist")
    parser.add_argument("--config", default=str(DEFAULT_INTERFACE_CONFIG))
    parser.add_argument("--interface", default="")
    parser.add_argument("--tcp-describe", action="store_true")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(argv)
    result = await run_checklist(args.config, interface_id=args.interface, tcp_describe=args.tcp_describe, timeout=args.timeout)
    print(_json(result))
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(main_async(argv))
    except Exception as exc:  # noqa: BLE001
        print(_json({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
