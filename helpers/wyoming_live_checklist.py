"""Shared live Wyoming validation checklist (W46).

Used by both the CLI runner and admin status endpoint. It is intentionally
transport-agnostic except for the optional Wyoming TCP describe/info probe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .wyoming_runtime import DEFAULT_INTERFACE_CONFIG
from .wyoming_smoke_diagnostics import smoke_report

_PLACEHOLDER_PREFIXES = ("REPLACE_WITH_", "PLACEHOLDER", "TODO", "CTXID_HERE")


async def run_live_checklist(
    config: str | Path = DEFAULT_INTERFACE_CONFIG,
    *,
    interface_id: str = "",
    tcp_describe: bool = False,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Run local live-validation checks and return JSON-safe results."""
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
        if not ctxid or ctxid.upper().startswith(_PLACEHOLDER_PREFIXES):
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
            "details": {"reason": "pass tcp_describe=true after runtime is started"},
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
            "Re-run with tcp_describe=true and verify type=info.",
            "Then test browser wyoming_init and a text prompt from webui/voqualizer-wyoming.html.",
        ],
    }
