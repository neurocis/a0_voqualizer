"""In-framework Wyoming live admin capture helper (W53).

This helper builds the same support-style diagnostic bundle as the W52 HTTP CLI,
but from inside the authenticated Agent Zero framework process. It avoids HTTP,
cookies, and CSRF because the caller is already inside the admin API handler.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from .wyoming_live_checklist import run_live_checklist
from .wyoming_readiness import readiness_snapshot
from .wyoming_smoke_diagnostics import smoke_report

StatusProvider = Callable[[], dict[str, Any]]
ValidateProvider = Callable[[str | Path | None], dict[str, Any]]
LiveProviderStatus = Callable[[], dict[str, Any]]
DomStatusProvider = Callable[[], dict[str, Any]]


async def live_admin_capture(
    *,
    config_path: str | Path,
    interface_id: str = "",
    tcp_describe: bool = False,
    timeout: float = 3.0,
    runtime_status_provider: StatusProvider | None = None,
    validate_provider: ValidateProvider | None = None,
    live_provider_status: LiveProviderStatus | None = None,
    dom_integration_status_provider: DomStatusProvider | None = None,
) -> dict[str, Any]:
    """Return one pasteable JSON-safe live admin diagnostic bundle."""
    actions: dict[str, Any] = {}
    blockers: list[str] = []

    async def record(name: str, fn):
        try:
            value = fn()
            if hasattr(value, "__await__"):
                value = await value
            actions[name] = value if isinstance(value, dict) else {"ok": True, "value": value}
        except Exception as exc:  # noqa: BLE001 - diagnostics must not crash caller
            actions[name] = {"ok": False, "error": str(exc)}
            blockers.append(f"{name}: exception")

    await record("status", lambda: runtime_status_provider() if runtime_status_provider else {})
    await record("dom_integration", lambda: dom_integration_status_provider() if dom_integration_status_provider else {})
    await record("validate", lambda: validate_provider(config_path) if validate_provider else {})
    await record(
        "readiness",
        lambda: readiness_snapshot(
            config_path=config_path,
            interface_id=interface_id,
            tcp_describe=tcp_describe,
            timeout=timeout,
            runtime_status_provider=runtime_status_provider,
            validate_provider=validate_provider,
            live_provider_status=live_provider_status,
        ),
    )
    await record("smoke", lambda: smoke_report(config_path, interface_id=interface_id, tcp=tcp_describe, timeout=timeout))
    await record("checklist", lambda: run_live_checklist(config_path, interface_id=interface_id, tcp_describe=tcp_describe, timeout=timeout))

    for name, result in actions.items():
        if isinstance(result, dict) and result.get("ok") is False:
            blockers.append(f"{name}: action_failed")

    readiness = actions.get("readiness", {}) if isinstance(actions.get("readiness"), dict) else {}
    for blocker in readiness.get("blockers", []) or []:
        blockers.append(f"readiness: {blocker}")

    # Preserve order while deduping.
    blockers = list(dict.fromkeys(blockers))
    next_actions: list[str] = []
    if blockers:
        next_actions.append("Review blockers and individual action payloads in this capture JSON.")
        next_actions.append("If runtime is not started, use wyoming_status action=start, then capture again.")
        next_actions.append("If TCP describe failed, verify interface bind host/port and firewall settings.")
    else:
        next_actions.append("Live admin capture succeeded. Paste this JSON when reporting Wyoming runtime issues.")

    return {
        "ok": not blockers,
        "tool": "wyoming_live_admin_capture_in_framework",
        "interface_id": interface_id or None,
        "tcp_describe": tcp_describe,
        "config_path": str(config_path),
        "actions": actions,
        "blockers": blockers,
        "next_actions": next_actions,
    }
