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

from helpers.wyoming_smoke_diagnostics import smoke_report  # noqa: E402
from helpers.wyoming_runtime import DEFAULT_INTERFACE_CONFIG  # noqa: E402


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


async def run_checklist(config: str, interface_id: str = "", tcp_describe: bool = False, timeout: float = 3.0) -> dict[str, Any]:
    report = await smoke_report(config, interface_id=interface_id, tcp=tcp_describe, timeout=timeout)
    steps: list[dict[str, Any]] = []
    steps.append({
        "name": "config_load",
        "ok": bool(report.get("ok")),
        "details": {"config_path": report.get("config_path"), "error": report.get("error")},
    })
    interfaces = report.get("interfaces") or []
    enabled = [iface for iface in interfaces if iface.get("enabled")]
    steps.append({
        "name": "enabled_interface_present",
        "ok": bool(enabled),
        "details": {"enabled_interfaces": [iface.get("id") for iface in enabled]},
    })
    placeholder_errors = []
    for iface in enabled:
        ctxid = str(iface.get("ctxid") or "")
        if not ctxid or ctxid.upper().startswith(("REPLACE_WITH_", "PLACEHOLDER", "TODO", "CTXID_HERE")):
            placeholder_errors.append({"interface_id": iface.get("id"), "ctxid": ctxid})
    steps.append({
        "name": "real_ctxid_configured",
        "ok": not placeholder_errors and bool(enabled),
        "details": {"placeholder_errors": placeholder_errors},
    })
    if tcp_describe:
        tcp = report.get("tcp_describe") or {}
        steps.append({
            "name": "tcp_describe_info",
            "ok": bool(tcp.get("ok") and tcp.get("type") == "info"),
            "details": tcp,
        })
    else:
        steps.append({
            "name": "tcp_describe_info",
            "ok": None,
            "skipped": True,
            "details": {"reason": "pass --tcp-describe after runtime is started"},
        })
    provider = report.get("live_providers") or {}
    steps.append({
        "name": "live_provider_status_available",
        "ok": provider.get("mode") == "live_providers",
        "details": provider,
    })
    return {
        "ok": all(step.get("ok") is not False for step in steps),
        "config_path": str(config),
        "interface_id": interface_id,
        "tcp_describe_requested": bool(tcp_describe),
        "steps": steps,
        "report": report,
        "next_actions": [
            "If config is invalid, run tools/wyoming_init_config.py with a real ctxID.",
            "Start runtime through /api/plugins/a0_voqualizer/wyoming_status action=start or plugin startup.",
            "Re-run with --tcp-describe and verify type=info.",
            "Then test browser wyoming_init and a text prompt from webui/voqualizer-wyoming.html.",
        ],
    }


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
