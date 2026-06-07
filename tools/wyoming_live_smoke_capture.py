#!/usr/bin/env python3
"""Capture a live Wyoming readiness/smoke bundle for Voqualizer (W51).

This tool is meant to be run inside the deployed plugin checkout after a real
`config/wyoming_interfaces.json` has been created. It does not require browser
auth and does not speak the retired custom Voqualizer websocket protocol.

It captures:
- readiness snapshot without TCP describe by default;
- optional readiness snapshot with TCP describe;
- optional raw smoke report;
- exact next actions for browser/DOM/external validation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers.wyoming_readiness import readiness_snapshot  # noqa: E402
from helpers.wyoming_runtime import DEFAULT_INTERFACE_CONFIG  # noqa: E402
from helpers.wyoming_smoke_diagnostics import smoke_report  # noqa: E402
from helpers.wyoming_live_providers import live_provider_status  # noqa: E402


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _runtime_status_unavailable() -> dict[str, Any]:
    return {
        "started": False,
        "running": False,
        "source": "cli_capture_no_framework_hooks",
        "message": "CLI capture cannot inspect in-memory framework runtime; use admin action=readiness for live runtime state.",
    }


def _validate_config_file(config: str | Path) -> dict[str, Any]:
    path = Path(config)
    if not path.exists():
        return {"ok": False, "error": "config_not_found", "config_path": str(path)}
    try:
        raw = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"invalid_json: {exc}", "config_path": str(path)}
    records = raw.get("interfaces", raw) if isinstance(raw, dict) else raw
    if not isinstance(records, list) or not records:
        return {"ok": False, "error": "no_interfaces", "config_path": str(path)}
    placeholders: list[dict[str, str]] = []
    for rec in records:
        if not isinstance(rec, dict) or not rec.get("enabled", True):
            continue
        ctxid = str(rec.get("ctxid") or "")
        if not ctxid or ctxid.upper().startswith(("REPLACE_WITH_", "PLACEHOLDER", "TODO", "CTXID_HERE")):
            placeholders.append({"interface_id": str(rec.get("id") or ""), "ctxid": ctxid})
    return {"ok": not placeholders, "config_path": str(path), "placeholder_errors": placeholders}


async def capture_live_smoke(
    *,
    config: str | Path = DEFAULT_INTERFACE_CONFIG,
    interface_id: str = "",
    tcp_describe: bool = False,
    include_smoke: bool = True,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Capture a JSON-safe live smoke bundle."""
    validation = _validate_config_file(config)
    readiness = await readiness_snapshot(
        config_path=config,
        interface_id=interface_id,
        tcp_describe=tcp_describe,
        timeout=timeout,
        runtime_status_provider=_runtime_status_unavailable,
        validate_provider=lambda _path: validation,
        live_provider_status=live_provider_status,
    )
    bundle: dict[str, Any] = {
        "ok": bool(readiness.get("ok")) and bool(validation.get("ok")),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config),
        "interface_id": interface_id,
        "tcp_describe_requested": bool(tcp_describe),
        "validation": validation,
        "readiness": readiness,
        "next_actions": [
            "If this CLI shows runtime_started=false, run admin {action:'readiness'} inside the framework for authoritative runtime state.",
            "If config is valid, start runtime via admin {action:'start'} or plugin startup.",
            "After runtime start, rerun with --tcp-describe and expect tcp_describe=ok.",
            "Hard refresh browser/DOM clients and run their readiness debug helpers.",
        ],
    }
    if include_smoke:
        bundle["smoke"] = await smoke_report(config, interface_id=interface_id, tcp=tcp_describe, timeout=timeout)
    return bundle


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture Voqualizer Wyoming live smoke diagnostics")
    parser.add_argument("--config", default=str(DEFAULT_INTERFACE_CONFIG))
    parser.add_argument("--interface", default="")
    parser.add_argument("--tcp-describe", action="store_true")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--no-smoke", action="store_true")
    args = parser.parse_args(argv)
    result = await capture_live_smoke(
        config=args.config,
        interface_id=args.interface,
        tcp_describe=args.tcp_describe,
        timeout=args.timeout,
        include_smoke=not args.no_smoke,
    )
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
