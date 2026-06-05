"""Wyoming ASR adapter scaffold for Voqualizer.

W4 goal: accept Wyoming audio-start/audio-chunk/audio-stop events from any
Wyoming-compatible client connected to a fixed interface/ctxID, then emit Wyoming
transcript events. This module is provider-agnostic and deliberately avoids the
old custom Voqualizer websocket protocol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import time
from typing import Awaitable, Callable

from .wyoming_protocol import WyomingEvent, event
from .wyoming_server import WyomingSession


TranscriptProvider = Callable[[bytes, dict], Awaitable[str] | str]

_ASR_IGNORE_NORMALIZED = {
    "thank you",
    "thanks",
    "thank you for watching",
    "blank audio",
    "silence",
    "inaudible",
    "clears throat",
    "clear throat",
    "clearing throat",
    "throat clearing",
}


def normalize_asr_text(text: str) -> str:
    """Normalize ASR final text for duplicate/false-positive filtering."""
    value = (text or "").strip().lower()
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"[\[\](){}<>]", " ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def should_ignore_asr_text(text: str) -> bool:
    """Return True for common non-process ASR artifacts/fillers."""
    return normalize_asr_text(text) in _ASR_IGNORE_NORMALIZED


def asr_text_hash(text: str) -> str:
    return hashlib.sha1(normalize_asr_text(text).encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class WyomingAsrState:
    audio_format: dict = field(default_factory=dict)
    chunks: list[bytes] = field(default_factory=list)
    utterance_id: str = ""
    started_at_ms: int = 0
    recent_finals: dict[str, int] = field(default_factory=dict)
    ignored_count: int = 0
    duplicate_count: int = 0

    def reset_audio(self) -> None:
        self.audio_format = {}
        self.chunks.clear()
        self.utterance_id = ""
        self.started_at_ms = 0


class WyomingAsrAdapter:
    """Handle Wyoming audio events and emit transcript events.

    The adapter keeps all state scoped to one WyomingSession. The session already
    belongs to one Wyoming interface and therefore one fixed A0 ctxID.
    """

    def __init__(self, provider: TranscriptProvider | None = None, *, dedupe_window_ms: int = 4500) -> None:
        self.provider = provider or self._default_empty_provider
        self.dedupe_window_ms = dedupe_window_ms
        self.state_by_session: dict[str, WyomingAsrState] = {}

    def state_for(self, session: WyomingSession) -> WyomingAsrState:
        state = self.state_by_session.get(session.session_id)
        if state is None:
            state = WyomingAsrState()
            self.state_by_session[session.session_id] = state
        return state

    async def handle_event(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        if incoming.type == "audio-start":
            return self.handle_audio_start(session, incoming)
        if incoming.type == "audio-chunk":
            return self.handle_audio_chunk(session, incoming)
        if incoming.type == "audio-stop":
            return await self.handle_audio_stop(session, incoming)
        return [event("error", code="unsupported_asr_event", message=f"Unsupported ASR event: {incoming.type}")]

    def handle_audio_start(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        state = self.state_for(session)
        state.reset_audio()
        state.audio_format = dict(incoming.data or {})
        state.utterance_id = str(incoming.data.get("utterance_id") or incoming.data.get("id") or "")
        state.started_at_ms = int(time.time() * 1000)
        return []

    def handle_audio_chunk(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        state = self.state_for(session)
        if incoming.payload:
            state.chunks.append(incoming.payload)
        return []

    async def handle_audio_stop(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        state = self.state_for(session)
        audio = b"".join(state.chunks)
        metadata = {
            "interface_id": session.interface.id,
            "ctxid": session.ctxid,
            "session_id": session.session_id,
            "utterance_id": state.utterance_id or incoming.data.get("utterance_id") or "",
            "audio_format": dict(state.audio_format),
        }
        text = await self._transcribe(audio, metadata)
        return self._final_transcript_events(session, state, text, metadata)

    async def _transcribe(self, audio: bytes, metadata: dict) -> str:
        result = self.provider(audio, metadata)
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[assignment]
        return str(result or "").strip()

    def _final_transcript_events(self, session: WyomingSession, state: WyomingAsrState, text: str, metadata: dict) -> list[WyomingEvent]:
        now_ms = int(time.time() * 1000)
        normalized = normalize_asr_text(text)
        if not normalized or should_ignore_asr_text(text):
            state.ignored_count += 1
            session.metadata["last_ignored_asr_final_text"] = text
            session.metadata["last_ignored_asr_final_reason"] = "false_positive_silence_or_filler"
            return [
                event(
                    "voqualizer-asr-ignored",
                    interface_id=session.interface.id,
                    ctxid=session.ctxid,
                    utterance_id=metadata.get("utterance_id", ""),
                    reason="false_positive_silence_or_filler",
                    text=text,
                )
            ]
        dedupe_key = str(metadata.get("utterance_id") or "") or asr_text_hash(text)
        cutoff = now_ms - self.dedupe_window_ms
        state.recent_finals = {key: ts for key, ts in state.recent_finals.items() if ts >= cutoff}
        if dedupe_key in state.recent_finals:
            state.duplicate_count += 1
            session.metadata["last_duplicate_asr_final_text"] = text
            return [
                event(
                    "voqualizer-asr-duplicate",
                    interface_id=session.interface.id,
                    ctxid=session.ctxid,
                    utterance_id=metadata.get("utterance_id", ""),
                    text=text,
                )
            ]
        state.recent_finals[dedupe_key] = now_ms
        return [
            event(
                "transcript",
                text=text,
                final=True,
                interface_id=session.interface.id,
                ctxid=session.ctxid,
                session_id=session.session_id,
                utterance_id=metadata.get("utterance_id", ""),
            )
        ]

    @staticmethod
    async def _default_empty_provider(audio: bytes, metadata: dict) -> str:
        return ""
