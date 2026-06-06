"""Wyoming WebSocket handler for Agent Zero (W16).

This handler exposes the Wyoming-over-WebSocket bridge under the framework's
unified ``/ws`` Socket.IO namespace. It is the runtime entry point that lets
browsers, DOM main-UI extensions, and any other Wyoming-compatible client/app
speak the Wyoming protocol against a configured Voqualizer interface.

Protocol surface (intentionally distinct from the retired ``voqualizer_*``
events kept in-tree for reference only):

* ``wyoming_init``  — client opens a Wyoming bridge session against an
                       ``interface_id``; server replies with the interface
                       ``info`` event payload (capabilities/version).
* ``wyoming_event`` — client sends or receives a Wyoming event envelope of
                       the form ``{type, data, payload_length}``. A binary
                       payload, when present, is sent in a paired
                       ``wyoming_payload`` event immediately after.
* ``wyoming_payload`` — raw binary payload (``bytes``) for the most recently
                       sent ``wyoming_event`` whose ``payload_length`` was
                       non-zero.
* ``wyoming_close`` — explicit teardown of the bridge session.

Client-supplied ``ctxid`` / ``interface_id`` inside event ``data`` are stripped
by :class:`WyomingWsBridge`; the interface boundary picked at ``wyoming_init``
is authoritative.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from helpers.ws import WsHandler
from helpers.ws_manager import WsResult
from helpers.print_style import PrintStyle

from usr.plugins.a0_voqualizer.helpers.wyoming_protocol import WyomingEvent
from usr.plugins.a0_voqualizer.helpers.wyoming_ws_bridge import WyomingWsBridge


def _get_runtime():
    """Resolve the plugin-level Wyoming runtime without importing admin internals."""
    from usr.plugins.a0_voqualizer import hooks

    return hooks.get_wyoming_runtime()


class WsWyoming(WsHandler):
    """Mount one :class:`WyomingWsBridge` session per WS connection."""

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_csrf(cls) -> bool:
        return True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._bridge: WyomingWsBridge | None = None
        self._interface_id: str | None = None
        self._pending_outbound_payload: bytes | None = None
        self._sid: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_disconnect(self, sid: str) -> None:
        if self._bridge is not None:
            try:
                self._bridge.close()
            except Exception as exc:  # noqa: BLE001 - defensive teardown
                PrintStyle.error(f"wyoming: bridge close failed: {exc}")
            self._bridge = None
        self._interface_id = None
        self._sid = None

    # ------------------------------------------------------------------
    # Event router
    # ------------------------------------------------------------------

    async def process(
        self,
        event: str,
        data: dict,
        sid: str,
    ) -> dict[str, Any] | WsResult | None:
        if not event.startswith("wyoming_"):
            return None
        self._sid = sid
        try:
            if event == "wyoming_init":
                return await self._handle_init(data or {}, sid)
            if event == "wyoming_event":
                return await self._handle_event(data or {}, sid)
            if event == "wyoming_payload":
                return await self._handle_payload(data or {}, sid)
            if event == "wyoming_close":
                return await self._handle_close(sid)
            return WsResult.error(
                code="WYOMING_UNKNOWN_EVENT",
                message=f"wyoming does not handle {event!r}",
            )
        except Exception as exc:  # noqa: BLE001 - never let exceptions escape
            return WsResult.error(
                code="WYOMING_HANDLER_ERROR",
                message=f"wyoming handler error: {type(exc).__name__}: {exc!r}",
            )

    # ------------------------------------------------------------------
    # wyoming_init
    # ------------------------------------------------------------------

    async def _handle_init(self, data: dict, sid: str) -> WsResult:
        interface_id = str(data.get("interface_id") or "").strip()
        if not interface_id:
            return WsResult.error(
                code="WYOMING_MISSING_INTERFACE_ID",
                message="wyoming_init requires interface_id",
            )
        runtime = _get_runtime()
        runtime_started = bool(getattr(runtime, "running", False) or getattr(runtime, "_started", False))
        if runtime is None or not runtime_started:
            return WsResult.error(
                code="WYOMING_RUNTIME_NOT_STARTED",
                message="Wyoming runtime is not started; configure interfaces first",
            )
        manager = getattr(runtime, "manager", None)
        runtimes = getattr(manager, "runtimes", {}) if manager is not None else {}
        interface_runtime = runtimes.get(interface_id)
        if interface_runtime is None:
            return WsResult.error(
                code="WYOMING_UNKNOWN_INTERFACE",
                message=f"unknown wyoming interface {interface_id!r}",
            )
        try:
            if self._bridge is not None:
                self._bridge.close()
            self._bridge = WyomingWsBridge(interface_runtime)
            info = self._bridge.describe()
        except Exception as exc:  # noqa: BLE001
            return WsResult.error(
                code="WYOMING_INIT_FAILED",
                message=f"wyoming bridge init failed: {type(exc).__name__}: {exc!r}",
            )
        self._interface_id = interface_id
        info_payload = {
            "type": info.type,
            "data": dict(info.data),
            "payload_length": len(info.payload or b""),
        }
        return WsResult.ok({
            "interface_id": interface_id,
            "info": info_payload,
        })

    # ------------------------------------------------------------------
    # wyoming_event
    # ------------------------------------------------------------------

    async def _handle_event(self, data: dict, sid: str) -> WsResult:
        bridge = self._bridge
        if bridge is None:
            return WsResult.error(
                code="WYOMING_NO_SESSION",
                message="send wyoming_init before wyoming_event",
            )
        event_type = str(data.get("type") or "").strip()
        if not event_type:
            return WsResult.error(
                code="WYOMING_BAD_EVENT",
                message="wyoming_event requires non-empty type",
            )
        event_data = data.get("data") or {}
        if not isinstance(event_data, dict):
            return WsResult.error(
                code="WYOMING_BAD_EVENT",
                message="wyoming_event data must be an object",
            )
        payload_length = int(data.get("payload_length") or 0)
        payload_inline = data.get("payload_b64")
        payload: bytes | None = None
        if isinstance(payload_inline, str) and payload_inline:
            try:
                payload = base64.b64decode(payload_inline)
            except Exception:  # noqa: BLE001
                return WsResult.error(
                    code="WYOMING_BAD_PAYLOAD",
                    message="payload_b64 must be base64 encoded bytes",
                )
            if payload_length and len(payload) != payload_length:
                return WsResult.error(
                    code="WYOMING_BAD_PAYLOAD",
                    message="payload_length does not match decoded payload",
                )
        elif payload_length:
            # Client will send a separate wyoming_payload binary frame next.
            self._pending_outbound_payload = b""
            self._pending_outbound_length = payload_length
            self._pending_outbound_event_type = event_type
            self._pending_outbound_event_data = dict(event_data)
            return WsResult.ok({"awaiting_payload": True, "payload_length": payload_length})
        replies = await bridge.handle_text_envelope(
            event_type=event_type,
            event_data=event_data,
            payload=payload,
        )
        await self._emit_replies(sid, replies)
        return WsResult.ok({"replies": len(replies)})

    # ------------------------------------------------------------------
    # wyoming_payload (binary)
    # ------------------------------------------------------------------

    async def _handle_payload(self, data: dict, sid: str) -> WsResult:
        bridge = self._bridge
        if bridge is None:
            return WsResult.error(
                code="WYOMING_NO_SESSION",
                message="send wyoming_init before wyoming_payload",
            )
        if not hasattr(self, "_pending_outbound_event_type"):
            return WsResult.error(
                code="WYOMING_UNEXPECTED_PAYLOAD",
                message="no wyoming_event is awaiting a payload",
            )
        chunk_b64 = data.get("chunk_b64") or ""
        try:
            chunk = base64.b64decode(chunk_b64) if chunk_b64 else b""
        except Exception:  # noqa: BLE001
            return WsResult.error(
                code="WYOMING_BAD_PAYLOAD",
                message="chunk_b64 must be base64 encoded bytes",
            )
        self._pending_outbound_payload = (self._pending_outbound_payload or b"") + chunk
        if len(self._pending_outbound_payload) < self._pending_outbound_length:
            return WsResult.ok({"awaiting_more": True, "received_bytes": len(self._pending_outbound_payload)})
        # Payload complete — dispatch.
        event_type = self._pending_outbound_event_type
        event_data = self._pending_outbound_event_data
        payload = self._pending_outbound_payload[: self._pending_outbound_length]
        # Reset pending state.
        self._pending_outbound_payload = None
        del self._pending_outbound_event_type
        del self._pending_outbound_event_data
        del self._pending_outbound_length
        replies = await bridge.handle_text_envelope(
            event_type=event_type,
            event_data=event_data,
            payload=payload,
        )
        await self._emit_replies(sid, replies)
        return WsResult.ok({"replies": len(replies)})

    # ------------------------------------------------------------------
    # wyoming_close
    # ------------------------------------------------------------------

    async def _handle_close(self, sid: str) -> WsResult:
        if self._bridge is not None:
            try:
                self._bridge.close()
            except Exception:  # noqa: BLE001
                pass
            self._bridge = None
        self._interface_id = None
        return WsResult.ok({"closed": True})

    # ------------------------------------------------------------------
    # Reply emission
    # ------------------------------------------------------------------

    async def _emit_replies(self, sid: str, replies: list[WyomingEvent]) -> None:
        for ev in replies or []:
            envelope = {
                "type": ev.type,
                "data": dict(ev.data),
                "payload_length": len(ev.payload or b""),
            }
            if ev.payload:
                envelope["payload_b64"] = base64.b64encode(ev.payload).decode("ascii")
            try:
                await self.emit_to(sid, "wyoming_event", envelope)
            except Exception as exc:  # noqa: BLE001
                PrintStyle.error(f"wyoming: emit_to failed: {exc}")
