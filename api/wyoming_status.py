"""Admin/status endpoint for the Voqualizer Wyoming runtime scaffold."""
from __future__ import annotations

from python.helpers.api import ApiHandler, Request, Response


class WyomingStatus(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        from usr.plugins.a0_voqualizer import hooks

        action = str((input or {}).get("action") or "status").strip().lower()
        if action == "status":
            return hooks.wyoming_runtime_status()
        if action == "bootstrap":
            status = hooks.ensure_dependency_bootstrap()
            runtime_status = hooks.wyoming_runtime_status()
            runtime_status["bootstrap"] = status
            return runtime_status
        if action == "start":
            runtime = await hooks.start_wyoming_runtime()
            status = hooks.wyoming_runtime_status()
            status["started"] = runtime is not None
            return status
        if action == "stop":
            await hooks.stop_wyoming_runtime()
            status = hooks.wyoming_runtime_status()
            status["stopped"] = True
            return status
        return {
            "error": "unsupported_action",
            "message": f"Unsupported Wyoming status action: {action}",
            "supported_actions": ["status", "bootstrap", "start", "stop"],
        }
