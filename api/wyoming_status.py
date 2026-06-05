"""Admin/status endpoint for the Voqualizer Wyoming runtime scaffold."""
from __future__ import annotations

from python.helpers.api import ApiHandler, Request, Response

from usr.plugins.a0_voqualizer.helpers.wyoming_live_providers import live_provider_status  # noqa: E402


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
        return {
            "error": "unsupported_action",
            "message": f"Unsupported Wyoming status action: {action}",
            "supported_actions": ["status", "bootstrap", "start", "stop"],
        }
