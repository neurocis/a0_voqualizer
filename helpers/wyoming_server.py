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
from .wyoming_protocol import WyomingEvent, WyomingProtocolError, event, read_event_from_stream, write_event_to_stream


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
        self.pipeline_handler: Handler | None = None

    def set_pipeline(self, handler: Handler) -> None:
        """Install a composed pipeline handler for all non-describe events.

        Older scaffolds registered per-event handlers in `handlers`; the Wyoming
        migration later introduced a composed pipeline object. Keep both paths:
        describe is still handled locally, explicit handlers remain supported,
        and otherwise the pipeline receives the event.
        """
        self.pipeline_handler = handler

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
        if handler:
            return await handler(session, incoming)
        if self.pipeline_handler:
            return await self.pipeline_handler(session, incoming)
        return [event("error", code="unsupported_event", message=f"Unsupported Wyoming event: {incoming.type}")]


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


class WyomingTcpServer:
    """Asyncio TCP binding for one Wyoming interface runtime.

    This is the first real server binding scaffold. It accepts generic Wyoming TCP
    clients, creates a session bound to the runtime interface/ctxID, reads Wyoming
    framed events, dispatches them through the runtime, and writes Wyoming framed
    replies. Browser bridges should adapt to this protocol rather than define a
    separate primary protocol.
    """

    def __init__(self, runtime: WyomingInterfaceRuntime) -> None:
        self.runtime = runtime
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> asyncio.AbstractServer:
        self.server = await asyncio.start_server(
            self.handle_client,
            self.runtime.interface.bind_host,
            self.runtime.interface.bind_port,
        )
        return self.server

    async def stop(self) -> None:
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()
        self.server = None

    async def handle_client(self, reader, writer) -> None:
        session = self.runtime.create_session()
        peer = None
        try:
            peer = writer.get_extra_info("peername") if hasattr(writer, "get_extra_info") else None
            session.metadata["peername"] = repr(peer)
            while True:
                incoming = await read_event_from_stream(reader)
                if incoming is None:
                    break
                replies = await self.runtime.handle_event(session, incoming)
                for outgoing in replies:
                    await write_event_to_stream(writer, outgoing)
        except WyomingProtocolError as exc:
            await write_event_to_stream(writer, event("error", code="protocol_error", message=str(exc)))
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            await write_event_to_stream(writer, event("error", code="handler_error", message=str(exc)))
        finally:
            self.runtime.close_session(session.session_id)
            close = getattr(writer, "close", None)
            if close is not None:
                close()
            wait_closed = getattr(writer, "wait_closed", None)
            if wait_closed is not None:
                try:
                    await wait_closed()
                except Exception:
                    pass


class WyomingTcpInterfaceManager:
    """Starts/stops TCP servers for all enabled Wyoming interface runtimes."""

    def __init__(self, manager: WyomingInterfaceManager) -> None:
        self.manager = manager
        self.servers: dict[str, WyomingTcpServer] = {
            interface_id: WyomingTcpServer(runtime)
            for interface_id, runtime in manager.runtimes.items()
        }

    async def start(self) -> None:
        for server in self.servers.values():
            await server.start()

    async def stop(self) -> None:
        for server in list(self.servers.values()):
            await server.stop()


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


def build_wyoming_pipeline_runtime(interface: WyomingInterface):
    """Create a runtime whose event handling is the composed Wyoming pipeline.

    The import is intentionally local to avoid forcing provider wiring during
    module import. Each runtime still maps exactly one interface to one ctxID.
    """
    from .wyoming_pipeline import WyomingVoqualizerPipeline

    runtime = WyomingInterfaceRuntime(interface)
    pipeline = WyomingVoqualizerPipeline()
    runtime.set_pipeline(pipeline.handle_event)
    return runtime


def build_wyoming_pipeline_manager(interfaces: list[WyomingInterface]) -> WyomingInterfaceManager:
    """Create a manager with composed Wyoming pipelines for every interface.

    `WyomingInterfaceManager` constructs default runtimes in its constructor and
    exposes the `runtimes` mapping directly. Replace each enabled interface's
    runtime with the composed pipeline runtime without relying on a non-existent
    `add_runtime()` helper.
    """
    manager = WyomingInterfaceManager(interfaces)
    for interface in interfaces:
        if not interface.enabled:
            continue
        manager.runtimes[interface.id] = build_wyoming_pipeline_runtime(interface)
    return manager
