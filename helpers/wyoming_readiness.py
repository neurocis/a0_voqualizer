"""Consolidated Wyoming readiness snapshot (W49).

This helper combines runtime status, config validation, smoke/checklist results,
and live provider status into one JSON-safe diagnostic payload suitable for admin
APIs and browser debug surfaces.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Awaitable

from .wyoming_live_checklist import run_live_checklist


StatusProvider = Callable[[], dict[str, Any]]
ValidateProvider = Callable[[str | Path | None], dict[str, Any]]
LiveProviderStatus = Callable[[], dict[str, Any]]


async def readiness_snapshot(
    *,
    config_path: str | Path,
    interface_id: str = "",
    tcp_describe: bool = False,
    timeout: float = 3.0,
    runtime_status_provider: StatusProvider | None = None,
    validate_provider: ValidateProvider | None = None,
    live_provider_status: LiveProviderStatus | None = None,
) -> dict[str, Any]:
    """Return a consolidated Wyoming operational readiness snapshot."""
    runtime_status: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    live_status: dict[str, Any] = {}
    errors: list[str] = []

    if runtime_status_provider is not None:
        try:
            runtime_status = runtime_status_provider() or {}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"runtime_status: {exc}")
            runtime_status = {"error": str(exc)}

    if validate_provider is not None:
        try:
            validation = validate_provider(config_path) or {}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"validation: {exc}")
            validation = {"ok": False, "error": str(exc)}

    if live_provider_status is not None:
        try:
            live_status = live_provider_status() or {}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"live_providers: {exc}")
            live_status = {"mode": "live_providers", "error": str(exc)}

    checklist = await run_live_checklist(
        config_path,
        interface_id=interface_id,
        tcp_describe=tcp_describe,
        timeout=timeout,
    )
    steps = {step.get("name"): step for step in checklist.get("steps", [])}
    runtime_started = bool(runtime_status.get("started") or runtime_status.get("running") or runtime_status.get("_started"))
    validation_ok = validation.get("ok") if validation else steps.get("config_load", {}).get("ok")
    checklist_ok = bool(checklist.get("ok"))
    provider_ok = (live_status or checklist.get("report", {}).get("live_providers", {})).get("mode") == "live_providers"
    tcp_step = steps.get("tcp_describe_info", {})
    tcp_ok = tcp_step.get("ok")
    if tcp_step.get("skipped"):
        tcp_state = "skipped"
    elif tcp_ok:
        tcp_state = "ok"
    else:
        tcp_state = "failed"

    blockers: list[str] = []
    if validation_ok is False:
        blockers.append("config_validation_failed")
    if steps.get("enabled_interface_present", {}).get("ok") is False:
        blockers.append("no_enabled_interface")
    if steps.get("real_ctxid_configured", {}).get("ok") is False:
        blockers.append("placeholder_or_missing_ctxid")
    if tcp_describe and tcp_ok is False:
        blockers.append("tcp_describe_failed")
    if errors:
        blockers.append("snapshot_errors")

    return {
        "ok": not blockers and checklist_ok,
        "ready_for_browser": not blockers and runtime_started,
        "config_path": str(config_path),
        "interface_id": interface_id,
        "runtime_started": runtime_started,
        "validation_ok": validation_ok,
        "provider_ok": provider_ok,
        "tcp_describe": tcp_state,
        "blockers": blockers,
        "errors": errors,
        "runtime": runtime_status,
        "validation": validation,
        "live_providers": live_status,
        "checklist": checklist,
        "next_actions": checklist.get("next_actions", []),
    }
