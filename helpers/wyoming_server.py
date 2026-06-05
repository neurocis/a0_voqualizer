"""Wyoming interface server scaffolding for Voqualizer.

This W2 module intentionally avoids the old custom Voqualizer websocket events.
It provides interface-bound session primitives that can later be wired to a real
async TCP server. Each interface is fixed to exactly one A0 ctxID.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable

from .wyoming_interfaces import WyomingInterface
from .wyoming_protocol import WyomingEvent, event


Handler = Callable[["WyomingSession", WyomingEvent], Awaitable[list[WyomingEvent]]]


@dataclass(slots=True)
class WyomingSession:
    """A client session connected to one Wyoming interface."""

    interface: WyomingInterface
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    connected_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    active_generation_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ctxid(self) -> str:
        return self.interface.ctxid

    def new_generation(self) -> str:
        self.active_generation_id = str(uuid.uuid4())
        self.metadata["last_generation_started_at_ms"] = int(time.time() * 1000)
        return self.active_generation_id

    def info_event(self) -> WyomingEvent:
        """Return a Wyoming info event for this interface/session."""
        return event(
            "info",
            voqualizer={
                "interface_id": self.interface.id,
                "name": self.interface.name,
                "ctxid": self.interface.ctxid,
                "session_id": self.session_id,
                "capabilities": dict(self.interface.capabilities),
            },
        )


class WyomingInterfaceRuntime:
    """Runtime state for a single configured Wyoming interface."""

    def __init__(self, interface: WyomingInterface) -> None:
        interface.validate()
        self.interface = interface
        self.sessions: dict[str, WyomingSession] = {}
        self.handlers: dict[str, Handler] = {}

    def create_session(self) -> WyomingSession:
        session = WyomingSession(interface=self.interface)
        self.sessions[session.session_id] = session
        return session

    def close_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def on(self, event_type: str, handler: Handler) -> None:
        self.handlers[event_type] = handler

    async def handle_event(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        if incoming.type == "describe":
            return [session.info_event()]
        handler = self.handlers.get(incoming.type)
        if not handler:
            return [event("error", code="unsupported_event", message=f"Unsupported Wyoming event: {incoming.type}")]
        return await handler(session, incoming)


class WyomingInterfaceManager:
    """Owns multiple concurrently active 1:1 interface-to-ctxID runtimes."""

    def __init__(self, interfaces: list[WyomingInterface]) -> None:
        self.runtimes: dict[str, WyomingInterfaceRuntime] = {}
        for interface in interfaces:
            if not interface.enabled:
                continue
            if interface.id in self.runtimes:
                raise ValueError(f"Duplicate Wyoming interface runtime id {interface.id!r}")
            self.runtimes[interface.id] = WyomingInterfaceRuntime(interface)

    def get(self, interface_id: str) -> WyomingInterfaceRuntime:
        try:
            return self.runtimes[interface_id]
        except KeyError as exc:
            raise KeyError(f"Unknown Wyoming interface {interface_id!r}") from exc

    def list_info(self) -> list[dict[str, Any]]:
        return [
            {
                "id": runtime.interface.id,
                "name": runtime.interface.name,
                "ctxid": runtime.interface.ctxid,
                "bind_host": runtime.interface.bind_host,
                "bind_port": runtime.interface.bind_port,
                "capabilities": dict(runtime.interface.capabilities),
                "active_sessions": len(runtime.sessions),
            }
            for runtime in self.runtimes.values()
        ]


async def echo_text_prompt_handler(session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
    """Temporary W2 handler proving ctx-bound generation routing.

    W3 will replace this with real A0 context submission. This handler deliberately
    preserves the interface ctxID in metadata and does not accept a client-supplied
    context override.
    """
    generation_id = session.new_generation()
    text = str(incoming.data.get("text") or "")
    return [
        event(
            "voqualizer-response-start",
            generation_id=generation_id,
            ctxid=session.ctxid,
            interface_id=session.interface.id,
        ),
        event(
            "voqualizer-response-final",
            generation_id=generation_id,
            ctxid=session.ctxid,
            interface_id=session.interface.id,
            text=text,
        ),
    ]


async def run_noop_server_forever() -> None:  # pragma: no cover - placeholder for W2 TCP binding
    """Placeholder hook for future asyncio TCP Wyoming server binding."""
    await asyncio.Event().wait()
