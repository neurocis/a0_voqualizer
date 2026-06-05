"""Wyoming-over-WebSocket bridge scaffold for browser/mobile clients.

This is a thin transport adapter that lets browsers participate in the Wyoming
rewrite. The Wyoming TCP server remains authoritative; the bridge accepts the
same `WyomingEvent` envelopes over WebSocket text/binary frames, dispatches them
through the configured interface runtime, and emits Wyoming replies back over
the same WebSocket.

This intentionally does NOT revive the retired custom websocket protocol. Each
bridge session is bound 1:1 to a configured Wyoming interface (and therefore a
fixed Agent Zero ctxID). Clients cannot override the bound ctxID.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Awaitable, Callable

from .wyoming_protocol import WyomingEvent, WyomingProtocolError, event
from .wyoming_server import WyomingInterfaceRuntime, WyomingSession


WsSend = Callable[[bytes | str], Awaitable[None]]
WsRecv = Callable[[], Awaitable[bytes | str | None]]


@dataclass(slots=True)
class WyomingWsBridgeDebug:
    text_events_in: int = 0
    binary_events_in: int = 0
    text_events_out: int = 0
    binary_events_out: int = 0
    bad_frames: int = 0
    closed: bool = False
    last_event_type: str = ""
    last_outgoing_type: str = ""


@dataclass(slots=True)
class WyomingWsBridge:
    runtime: WyomingInterfaceRuntime
    session: WyomingSession = field(init=False)
    debug: WyomingWsBridgeDebug = field(default_factory=WyomingWsBridgeDebug)

    def __post_init__(self) -> None:
        self.session = self.runtime.create_session()

    @property
    def interface_id(self) -> str:
        return self.runtime.interface.id

    @property
    def ctxid(self) -> str:
        return self.runtime.interface.ctxid

    def encode_for_browser(self, outgoing: WyomingEvent) -> tuple[str, bytes | None]:
        """Encode a Wyoming event for a browser WebSocket transport.

        Returns ``(text_envelope, optional_binary_payload)``. The text envelope is
        canonical JSON describing the event; if the event has a binary payload,
        the caller should also send a binary frame containing those bytes.
        """
        envelope = {
            "type": outgoing.type,
            "data": dict(outgoing.data),
            "payload_length": len(outgoing.payload),
        }
        return json.dumps(envelope, separators=(",", ":")), (outgoing.payload or None)

    def decode_text_frame(self, text: str) -> WyomingEvent:
        try:
            payload = json.loads(text)
        except Exception as exc:
            raise WyomingProtocolError(f"Invalid Wyoming WS text frame: {exc}")
        if not isinstance(payload, dict):
            raise WyomingProtocolError("Wyoming WS text frame must be an object")
        event_type = str(payload.get("type") or "")
        if not event_type:
            raise WyomingProtocolError("Wyoming WS text frame missing 'type'")
        data = dict(payload.get("data") or {})
        # Defensive: ignore any client attempt to set ctxid/interface_id; the
        # bridge enforces interface-bound ctxID at the Wyoming runtime layer.
        data.pop("ctxid", None)
        data.pop("interface_id", None)
        return WyomingEvent(type=event_type, data=data, payload=b"")

    def attach_binary_payload(self, incoming: WyomingEvent, payload: bytes) -> WyomingEvent:
        return WyomingEvent(type=incoming.type, data=dict(incoming.data), payload=bytes(payload or b""))

    async def handle_incoming(self, incoming: WyomingEvent) -> list[WyomingEvent]:
        self.debug.last_event_type = incoming.type
        replies = await self.runtime.handle_event(self.session, incoming)
        return list(replies)

    async def send_event(self, send: WsSend, outgoing: WyomingEvent) -> None:
        text, binary = self.encode_for_browser(outgoing)
        await send(text)
        self.debug.text_events_out += 1
        self.debug.last_outgoing_type = outgoing.type
        if binary is not None:
            await send(binary)
            self.debug.binary_events_out += 1

    async def run(self, recv: WsRecv, send: WsSend) -> None:
        """Drive a browser WebSocket session until the client disconnects."""
        try:
            pending_event: WyomingEvent | None = None
            while True:
                frame = await recv()
                if frame is None:
                    break
                if isinstance(frame, str):
                    self.debug.text_events_in += 1
                    try:
                        pending_event = self.decode_text_frame(frame)
                    except WyomingProtocolError as exc:
                        self.debug.bad_frames += 1
                        await send(json.dumps({"type": "error", "data": {"code": "bad_frame", "message": str(exc)}}))
                        continue
                    if pending_event.data.get("payload_length"):
                        # Wait for the binary payload frame before dispatching.
                        continue
                    incoming = pending_event
                    pending_event = None
                    replies = await self.handle_incoming(incoming)
                    for reply in replies:
                        await self.send_event(send, reply)
                elif isinstance(frame, (bytes, bytearray)):
                    self.debug.binary_events_in += 1
                    if pending_event is None:
                        self.debug.bad_frames += 1
                        await send(json.dumps({"type": "error", "data": {"code": "unexpected_binary", "message": "Binary frame without preceding text envelope"}}))
                        continue
                    incoming = self.attach_binary_payload(pending_event, bytes(frame))
                    pending_event = None
                    replies = await self.handle_incoming(incoming)
                    for reply in replies:
                        await self.send_event(send, reply)
                else:
                    self.debug.bad_frames += 1
                    await send(json.dumps({"type": "error", "data": {"code": "unsupported_frame", "message": f"Unsupported WS frame type: {type(frame).__name__}"}}))
        finally:
            self.debug.closed = True
            self.runtime.close_session(self.session.session_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            "interface_id": self.interface_id,
            "ctxid": self.ctxid,
            "session_id": self.session.session_id,
            "text_events_in": self.debug.text_events_in,
            "binary_events_in": self.debug.binary_events_in,
            "text_events_out": self.debug.text_events_out,
            "binary_events_out": self.debug.binary_events_out,
            "bad_frames": self.debug.bad_frames,
            "closed": self.debug.closed,
            "last_event_type": self.debug.last_event_type,
            "last_outgoing_type": self.debug.last_outgoing_type,
        }

    async def handle_text_envelope(self, *, event_type: str, event_data: dict, payload: bytes | None = None):
        """Process a Wyoming event envelope already split from its transport.

        Strips client-supplied ``ctxid`` / ``interface_id`` overrides and
        forwards the event to the bound :class:`WyomingInterfaceRuntime`. The
        return value is the list of reply :class:`WyomingEvent` instances the
        caller should send back to its client.
        """
        from .wyoming_protocol import WyomingEvent
        safe_data = {k: v for k, v in (event_data or {}).items() if k not in ("ctxid", "interface_id")}
        ev = WyomingEvent(type=str(event_type), data=safe_data, payload=payload or b"")
        replies = await self._runtime.handle_event(self._session, ev)
        for reply in replies or []:
            try:
                self._snapshot["text_out"] = int(self._snapshot.get("text_out", 0)) + 1
                self._snapshot["last_outgoing_event"] = reply.type
            except Exception:
                pass
        return list(replies or [])
