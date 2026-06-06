"""Wyoming-over-WebSocket bridge API endpoint scaffold.

This endpoint accepts a browser/mobile WebSocket-style request, resolves the
Wyoming interface by `interface_id`, and runs a `WyomingWsBridge` session that
relays Wyoming events between the client and the configured Wyoming runtime.

The endpoint deliberately keeps wiring details (Flask vs Quart vs Sanic, raw
WebSocket vs Sock-style) opaque: it exposes a transport-agnostic helper that
takes async `recv`/`send` callables, plus a JSON status action for diagnostics.

Old custom websocket endpoints in `api/ws_voqualizer.py` remain available for
reference and gradual migration; this new endpoint does NOT share their event
names or transport.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from python.helpers.api import ApiHandler, Request, Response


WsRecv = Callable[[], Awaitable[bytes | str | None]]
WsSend = Callable[[bytes | str], Awaitable[None]]


async def run_wyoming_ws_bridge_session(interface_id: str, recv: WsRecv, send: WsSend) -> dict[str, Any]:
    """Run one Wyoming-over-WebSocket session bound to the named interface.

    Returns the final bridge snapshot for diagnostics. Raises `KeyError` if the
    interface is not configured/running.
    """
    from usr.plugins.a0_voqualizer import hooks
    from usr.plugins.a0_voqualizer.helpers.wyoming_ws_bridge import WyomingWsBridge

    runtime = hooks.get_wyoming_runtime()
    if runtime is None:
        raise RuntimeError("Wyoming runtime has not been started")
    try:
        interface_runtime = runtime.manager.get(interface_id)
    except KeyError as exc:
        raise KeyError(f"Unknown Wyoming interface: {interface_id!r}") from exc
    bridge = WyomingWsBridge(interface_runtime)
    await bridge.run(recv, send)
    return bridge.snapshot()


def _interface_payloads(runtime) -> list[dict[str, Any]]:
    """Return browser-safe interface descriptors for configured Wyoming clients."""
    payloads: list[dict[str, Any]] = []
    interfaces = getattr(runtime, "interfaces", []) or []
    running_ids = set((getattr(getattr(runtime, "manager", None), "runtimes", {}) or {}).keys())
    for interface in interfaces:
        payloads.append({
            "id": interface.id,
            "name": interface.name,
            "enabled": bool(interface.enabled),
            "running": interface.id in running_ids,
            "bind_host": interface.bind_host,
            "bind_port": interface.bind_port,
            "capabilities": dict(interface.capabilities or {}),
        })
    return payloads


def _default_interface_id(runtime) -> str:
    for item in _interface_payloads(runtime):
        if item.get("enabled") and item.get("running"):
            return str(item.get("id") or "")
    for item in _interface_payloads(runtime):
        if item.get("enabled"):
            return str(item.get("id") or "")
    return ""


class WyomingWs(ApiHandler):
    """JSON status/diagnostics endpoint for the Wyoming WS bridge.

    Real WebSocket attachment is wired by the host framework's WS layer using
    `run_wyoming_ws_bridge_session(...)`. This handler exposes:

    - `action=list` -> list configured Wyoming interfaces and their bind ports.
    - `action=status` (default) -> per-interface status including running flag.
    - `action=describe` with `interface_id` -> info event payload for one interface.
    """

    async def process(self, input: dict, request: Request) -> dict | Response:
        from usr.plugins.a0_voqualizer import hooks

        action = str((input or {}).get("action") or "status").strip().lower()
        runtime = hooks.get_wyoming_runtime()

        if runtime is None:
            return {
                "action": action,
                "running": False,
                "message": "Wyoming runtime has not been started",
                "config_path": str(hooks.wyoming_config_path()),
            }

        if action in {"list", "interfaces"}:
            interfaces = _interface_payloads(runtime)
            return {
                "action": action,
                "running": True,
                "interfaces": interfaces,
                "default_interface_id": _default_interface_id(runtime),
            }

        if action == "describe":
            interface_id = str((input or {}).get("interface_id") or "")
            if not interface_id:
                return {
                    "error": "missing_interface_id",
                    "message": "action=describe requires interface_id",
                }
            try:
                interface_runtime = runtime.manager.get(interface_id)
            except KeyError:
                return {
                    "error": "unknown_interface",
                    "message": f"Unknown Wyoming interface: {interface_id!r}",
                }
            session = interface_runtime.create_session()
            info_event = session.info_event()
            interface_runtime.close_session(session.session_id)
            return {
                "action": action,
                "interface_id": interface_id,
                "info": {"type": info_event.type, "data": info_event.data},
            }

        if action == "status":
            return {
                "action": action,
                "running": runtime.status().running,
                "interfaces": _interface_payloads(runtime),
                "default_interface_id": _default_interface_id(runtime),
                "config_path": str(hooks.wyoming_config_path()),
            }

        return {
            "error": "unsupported_action",
            "message": f"Unsupported Wyoming WS action: {action}",
            "supported_actions": ["status", "list", "interfaces", "describe"],
        }
