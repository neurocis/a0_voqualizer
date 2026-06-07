"""Admin/status endpoint for the Voqualizer Wyoming runtime scaffold."""
from __future__ import annotations

from python.helpers.api import ApiHandler, Request, Response

from usr.plugins.a0_voqualizer.helpers.wyoming_live_providers import live_provider_status  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.wyoming_smoke_diagnostics import smoke_report  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.wyoming_config_init import init_wyoming_config  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.wyoming_live_checklist import run_live_checklist  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.wyoming_readiness import readiness_snapshot  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.wyoming_dom_settings import (  # noqa: E402
    dom_integration_status,
    set_dom_integration_enabled,
)


def _attach_live_provider_status(status: dict) -> dict:
    try:
        status["live_providers"] = live_provider_status()
    except Exception as exc:  # noqa: BLE001 - status endpoint must remain diagnostic-safe
        status["live_providers"] = {"mode": "live_providers", "error": str(exc)}
    return status


class WyomingStatus(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        from usr.plugins.a0_voqualizer import hooks

        action = str((input or {}).get("action") or "status").strip().lower()
        if action == "status":
            return _attach_live_provider_status(hooks.wyoming_runtime_status())
        if action == "bootstrap":
            bootstrap_status = hooks.ensure_dependency_bootstrap()
            runtime_status = _attach_live_provider_status(hooks.wyoming_runtime_status())
            runtime_status["bootstrap"] = bootstrap_status
            return runtime_status
        if action == "validate":
            config_path = (input or {}).get("config_path") or hooks.wyoming_config_path()
            return _attach_live_provider_status(hooks.validate_wyoming_config(config_path))
        if action == "init_config":
            payload = input or {}
            return _attach_live_provider_status(init_wyoming_config(
                ctxid=str(payload.get("ctxid") or ""),
                interface_id=str(payload.get("interface_id") or "default"),
                name=str(payload.get("name") or "Voqualizer Wyoming"),
                bind_host=str(payload.get("bind_host") or "127.0.0.1"),
                bind_port=int(payload.get("bind_port") or 10701),
                enabled=bool(payload.get("enabled", True)),
                config_path=payload.get("config_path") or hooks.wyoming_config_path(),
                overwrite=bool(payload.get("overwrite") or False),
            ))
        if action == "start":
            runtime = await hooks.start_wyoming_runtime()
            status = _attach_live_provider_status(hooks.wyoming_runtime_status())
            status["started"] = runtime is not None
            return status
        if action == "stop":
            await hooks.stop_wyoming_runtime()
            status = _attach_live_provider_status(hooks.wyoming_runtime_status())
            status["stopped"] = True
            return status
        if action == "smoke":
            config_path = (input or {}).get("config_path") or hooks.wyoming_config_path()
            interface_id = str((input or {}).get("interface_id") or "")
            tcp = bool((input or {}).get("tcp_describe") or False)
            timeout = float((input or {}).get("timeout") or 3.0)
            return await smoke_report(config_path, interface_id=interface_id, tcp=tcp, timeout=timeout)
        if action == "checklist":
            config_path = (input or {}).get("config_path") or hooks.wyoming_config_path()
            interface_id = str((input or {}).get("interface_id") or "")
            tcp = bool((input or {}).get("tcp_describe") or False)
            timeout = float((input or {}).get("timeout") or 3.0)
            return await run_live_checklist(config_path, interface_id=interface_id, tcp_describe=tcp, timeout=timeout)
        if action == "readiness":
            config_path = (input or {}).get("config_path") or hooks.wyoming_config_path()
            interface_id = str((input or {}).get("interface_id") or "")
            tcp = bool((input or {}).get("tcp_describe") or False)
            timeout = float((input or {}).get("timeout") or 3.0)
            return await readiness_snapshot(
                config_path=config_path,
                interface_id=interface_id,
                tcp_describe=tcp,
                timeout=timeout,
                runtime_status_provider=hooks.wyoming_runtime_status,
                validate_provider=hooks.validate_wyoming_config,
                live_provider_status=live_provider_status,
            )
        if action == "dom_integration":
            payload = input or {}
            if "enabled" in payload:
                return set_dom_integration_enabled(bool(payload.get("enabled")))
            return dom_integration_status()
        return {
            "error": "unsupported_action",
            "message": f"Unsupported Wyoming status action: {action}",
            "supported_actions": ["status", "bootstrap", "validate", "init_config", "start", "stop", "smoke", "checklist", "readiness", "dom_integration"],
        }
