"""Authoritative Wyoming TTS adapter scaffold for Voqualizer.

W6 goal: emit TTS only through Wyoming audio events for the active generation.
No ACK fallback, no direct final-response path, and no custom old websocket TTS
stream are preserved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import time
from typing import Any, AsyncIterable, Awaitable, Callable, Iterable

from .wyoming_protocol import WyomingEvent, event
from .wyoming_server import WyomingSession


TtsProvider = Callable[[str, dict[str, Any]], Awaitable[bytes | Iterable[bytes] | AsyncIterable[bytes]] | bytes | Iterable[bytes] | AsyncIterable[bytes]]


@dataclass(slots=True)
class WyomingTtsGeneration:
    generation_id: str
    text: str
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    chunk_seq: int = 0
    cancelled: bool = False
    final: bool = False


class WyomingTtsAdapter:
    """Emit one authoritative Wyoming TTS audio stream per active generation."""

    def __init__(self, provider: TtsProvider | None = None, *, sample_rate: int = 24000, width: int = 2, channels: int = 1) -> None:
        self.provider = provider or self._default_silent_provider
        self.sample_rate = sample_rate
        self.width = width
        self.channels = channels
        self.generations_by_session: dict[str, WyomingTtsGeneration] = {}
        self.seen_chunks_by_session: dict[str, set[tuple[str, int]]] = {}

    async def handle_event(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        if incoming.type == "synthesize":
            return await self.handle_synthesize(session, incoming)
        if incoming.type == "voqualizer-response-final":
            return await self.handle_response_final(session, incoming)
        if incoming.type in {"cancel", "voqualizer-cancel", "pause-satellite"}:
            return self.handle_cancel(session, incoming)
        return [event("error", code="unsupported_tts_event", message=f"Unsupported TTS event: {incoming.type}")]

    async def handle_response_final(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        text = str(incoming.data.get("text") or "").strip()
        generation_id = str(incoming.data.get("generation_id") or session.active_generation_id or session.new_generation())
        return await self._synthesize_generation(session, text, generation_id, source="assistant_response")

    async def handle_synthesize(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        text = str(incoming.data.get("text") or "").strip()
        generation_id = str(incoming.data.get("generation_id") or session.new_generation())
        return await self._synthesize_generation(session, text, generation_id, source="synthesize")

    def handle_cancel(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        current = self.generations_by_session.get(session.session_id)
        if current:
            current.cancelled = True
        new_generation = session.new_generation()
        self.seen_chunks_by_session[session.session_id] = set()
        return [
            event(
                "audio-stop",
                interface_id=session.interface.id,
                ctxid=session.ctxid,
                session_id=session.session_id,
                generation_id=new_generation,
                reason=str(incoming.data.get("reason") or "client_cancel"),
            )
        ]

    async def _synthesize_generation(self, session: WyomingSession, text: str, generation_id: str, *, source: str) -> list[WyomingEvent]:
        if not text:
            return [event("error", code="empty_tts_text", message="TTS text is empty")]
        session.active_generation_id = generation_id
        state = WyomingTtsGeneration(generation_id=generation_id, text=text)
        self.generations_by_session[session.session_id] = state
        self.seen_chunks_by_session[session.session_id] = set()
        metadata = {
            "interface_id": session.interface.id,
            "ctxid": session.ctxid,
            "session_id": session.session_id,
            "generation_id": generation_id,
            "source": source,
        }
        replies: list[WyomingEvent] = [
            event("audio-start", rate=self.sample_rate, width=self.width, channels=self.channels, **metadata),
        ]
        provider_result = self.provider(text, metadata)
        if inspect.isawaitable(provider_result):
            provider_result = await provider_result
        async for chunk in self._iter_chunks(provider_result):
            if state.cancelled or session.active_generation_id != generation_id:
                break
            key = (generation_id, state.chunk_seq)
            seen = self.seen_chunks_by_session.setdefault(session.session_id, set())
            if key in seen:
                continue
            seen.add(key)
            replies.append(
                WyomingEvent(
                    "audio-chunk",
                    data={**metadata, "chunk_seq": state.chunk_seq, "rate": self.sample_rate, "width": self.width, "channels": self.channels},
                    payload=bytes(chunk or b""),
                )
            )
            state.chunk_seq += 1
        state.final = True
        replies.append(event("audio-stop", chunk_count=state.chunk_seq, **metadata))
        return replies

    async def _iter_chunks(self, value):
        if value is None:
            return
        if isinstance(value, bytes):
            yield value
            return
        if hasattr(value, "__aiter__"):
            async for chunk in value:
                yield bytes(chunk or b"")
            return
        for chunk in value or []:
            yield bytes(chunk or b"")

    @staticmethod
    async def _default_silent_provider(text: str, metadata: dict[str, Any]) -> bytes:
        return b""
