"""Context/assistant token stream hub for Voqualizer (M7.2).

This module exposes an additive protocol-level stream for generated assistant
text.  It is intentionally independent of the standalone web page DOM so web,
VoIP, native, and telephony bridge clients can subscribe to the same socket
protocol.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from usr.plugins.a0_voqualizer.helpers.context_bridge import get_default_context_bridge
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry
from usr.plugins.a0_voqualizer.helpers.session import BridgeSession

ClockFn = Callable[[], float]


def server_time_ms() -> int:
    return int(time.time() * 1000)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def stream_id_for(context_id: str, message_id: str = "", attempt: int = 1) -> str:
    mid = message_id or "unknown"
    return f"cx-{context_id}-{mid}-{attempt}"


@dataclass
class CxStreamState:
    context_id: str
    message_id: str = ""
    stream_id: str = ""
    seq: int = 0
    text: str = ""
    channel: str = "assistant"
    role: str = "assistant"
    source: str = "response_stream_chunk"
    started: bool = False
    final: bool = False
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)


class CxStreamHub:
    """Small in-process stream hub keyed by A0 context id.

    The hub emits Socket.IO events through each active ``BridgeSession.sender``.
    It also keeps short-lived state so sequence numbers and accumulated text are
    deterministic for tests and client dedupe.
    """

    def __init__(self, *, clock: ClockFn | None = None) -> None:
        self.clock = clock or time.monotonic
        self._states: dict[str, CxStreamState] = {}

    def reset(self) -> None:
        self._states.clear()

    def state_for(self, context_id: str) -> CxStreamState | None:
        return self._states.get(context_id)

    def _state(self, context_id: str, *, message_id: str = "", source: str = "response_stream_chunk") -> CxStreamState:
        state = self._states.get(context_id)
        if state is None or state.final:
            state = CxStreamState(
                context_id=context_id,
                message_id=message_id,
                stream_id=stream_id_for(context_id, message_id),
                source=source,
                created_at=self.clock(),
                updated_at=self.clock(),
            )
            self._states[context_id] = state
        elif message_id and not state.message_id:
            state.message_id = message_id
            state.stream_id = stream_id_for(context_id, message_id)
        state.source = source
        return state

    def _sessions_for_context(self, context_id: str) -> list[BridgeSession]:
        sessions: list[BridgeSession] = []
        seen: set[str] = set()
        try:
            bridge = get_default_context_bridge()
            bindings = list(bridge.bindings_for_context(context_id))
        except Exception:
            bindings = []
        registry = BridgeRegistry.instance()
        for binding in bindings:
            session = registry.get(binding.session_id)
            if session is None or session.sender is None:
                continue
            sessions.append(session)
            seen.add(session.session_id)
        # Standalone GUI sessions are often bound directly by session.context_id
        # rather than through an ASR ContextBridge binding.  Include them too.
        for session in registry.iter_active():
            if session.session_id in seen or session.sender is None:
                continue
            if str(getattr(session, "context_id", "") or "") == context_id:
                sessions.append(session)
                seen.add(session.session_id)
        return sessions

    async def _emit(self, sessions: Iterable[BridgeSession], event: str, payload: dict[str, Any]) -> int:
        emitted = 0
        for session in sessions:
            if session.sender is None:
                continue
            per_session = dict(payload)
            per_session["session_id"] = session.session_id
            await session.sender(event, per_session)
            emitted += 1
        return emitted

    def _base_payload(self, state: CxStreamState) -> dict[str, Any]:
        return {
            "context_id": state.context_id,
            "message_id": state.message_id,
            "stream_id": state.stream_id,
            "seq": state.seq,
            "server_time": server_time_ms(),
            "source": state.source,
            "channel": state.channel,
            "role": state.role,
        }

    async def start(self, *, context_id: str, message_id: str = "", source: str = "response_stream") -> dict[str, Any]:
        context_id = _clean(context_id)
        if not context_id:
            return {"status": "skipped", "reason": "missing_context", "sessions": 0}
        state = self._state(context_id, message_id=message_id, source=source)
        if state.started:
            return {"status": "skipped", "reason": "already_started", "sessions": 0, "stream_id": state.stream_id}
        state.started = True
        state.seq = 0
        state.updated_at = self.clock()
        payload = self._base_payload(state)
        payload.update({"event": "voqualizer_cx_stream_start", "mode": "token_stream", "resumed": False})
        sessions = self._sessions_for_context(context_id)
        emitted = await self._emit(sessions, "voqualizer_cx_stream_start", payload)
        return {"status": "ok", "event": "voqualizer_cx_stream_start", "sessions": emitted, "stream_id": state.stream_id}

    async def token(self, *, context_id: str, delta: str, full: str = "", message_id: str = "", source: str = "response_stream_chunk") -> dict[str, Any]:
        context_id = _clean(context_id)
        if not context_id:
            return {"status": "skipped", "reason": "missing_context", "sessions": 0}
        if not isinstance(delta, str) or not delta:
            return {"status": "skipped", "reason": "empty_delta", "sessions": 0}
        state = self._state(context_id, message_id=message_id, source=source)
        sessions = self._sessions_for_context(context_id)
        emitted = 0
        if not state.started:
            state.started = True
            start_payload = self._base_payload(state)
            start_payload.update({"event": "voqualizer_cx_stream_start", "mode": "token_stream", "resumed": False})
            emitted += await self._emit(sessions, "voqualizer_cx_stream_start", start_payload)
        char_start = len(state.text)
        state.text = full if isinstance(full, str) and full else state.text + delta
        char_end = len(state.text)
        state.seq += 1
        state.updated_at = self.clock()
        payload = self._base_payload(state)
        payload.update({
            "event": "voqualizer_cx_token",
            "delta": delta,
            "text": state.text,
            "char_start": char_start,
            "char_end": char_end,
            "is_final": False,
        })
        emitted += await self._emit(sessions, "voqualizer_cx_token", payload)
        return {"status": "ok", "event": "voqualizer_cx_token", "sessions": emitted, "stream_id": state.stream_id, "seq": state.seq}

    async def final(self, *, context_id: str, text: str = "", message_id: str = "", source: str = "response_stream_end", finish_reason: str = "stop") -> dict[str, Any]:
        context_id = _clean(context_id)
        if not context_id:
            return {"status": "skipped", "reason": "missing_context", "sessions": 0}
        state = self._state(context_id, message_id=message_id, source=source)
        if isinstance(text, str) and text:
            state.text = text
        state.seq += 1
        state.final = True
        state.updated_at = self.clock()
        payload = self._base_payload(state)
        payload.update({
            "event": "voqualizer_cx_stream_final",
            "text": state.text,
            "is_final": True,
            "finish_reason": finish_reason,
        })
        emitted = await self._emit(self._sessions_for_context(context_id), "voqualizer_cx_stream_final", payload)
        return {"status": "ok", "event": "voqualizer_cx_stream_final", "sessions": emitted, "stream_id": state.stream_id, "seq": state.seq}

    async def error(self, *, context_id: str, message: str, code: str = "CX_STREAM_ERROR", recoverable: bool = True) -> dict[str, Any]:
        context_id = _clean(context_id)
        if not context_id:
            return {"status": "skipped", "reason": "missing_context", "sessions": 0}
        state = self._state(context_id, source="cx_stream_error")
        state.seq += 1
        payload = self._base_payload(state)
        payload.update({"event": "voqualizer_cx_stream_error", "code": code, "message": message, "recoverable": recoverable})
        emitted = await self._emit(self._sessions_for_context(context_id), "voqualizer_cx_stream_error", payload)
        return {"status": "ok", "event": "voqualizer_cx_stream_error", "sessions": emitted, "stream_id": state.stream_id, "seq": state.seq}


_default_cx_stream_hub: CxStreamHub | None = None


def get_default_cx_stream_hub() -> CxStreamHub:
    global _default_cx_stream_hub
    if _default_cx_stream_hub is None:
        _default_cx_stream_hub = CxStreamHub()
    return _default_cx_stream_hub


__all__ = ["CxStreamHub", "CxStreamState", "get_default_cx_stream_hub", "stream_id_for"]
