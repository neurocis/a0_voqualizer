"""Sentence-boundary TTS chunking for Voqualizer (M5 / A5.4).

The Agent Zero token stream arrives as small response deltas. This module buffers
those deltas per Voqualizer session and starts TTS when either:

* a sentence boundary is observed, or
* the first buffered text has waited longer than the configured latency budget.

The latency path gives the first audio a deterministic sub-1s trigger without
waiting for long assistant paragraphs to finish. The process_chain_end finalizer
flushes any remaining buffered text so already-spoken streaming chunks are not
synthesized again as one duplicate full response.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from collections.abc import Mapping

from usr.plugins.a0_voqualizer.helpers.context_bridge import get_default_context_bridge
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry
from usr.plugins.a0_voqualizer.helpers.session import BridgeSession

ClockFn = Callable[[], float]
UtteranceIdFactory = Callable[[], str]
ConfigLoader = Callable[[], dict[str, Any]]
TTSProviderFactory = Callable[[Mapping[str, Any]], Any]

_SENTENCE_RE = re.compile(r"(.+?[.!?]+(?:[\"'”’\)]*)?)(?=\s+|$)", re.DOTALL)


def _clean_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


@dataclass
class SentenceChunkerConfig:
    """Runtime knobs for token→TTS buffering."""

    first_audio_latency_ms: int = 750
    min_latency_flush_chars: int = 24
    max_buffer_chars: int = 360


@dataclass
class SentenceChunkState:
    session_id: str
    context_id: str
    buffer: str = ""
    started_at: float | None = None
    sequence: int = 0
    spoken_text: str = ""
    pending_tasks: list[Any] = field(default_factory=list)


class SentenceTTSChunker:
    """Buffer streamed LLM tokens and synthesize TTS at sentence boundaries."""

    def __init__(
        self,
        *,
        config: SentenceChunkerConfig | None = None,
        clock: ClockFn | None = None,
        utterance_id_factory: UtteranceIdFactory | None = None,
        config_loader: ConfigLoader | None = None,
        tts_provider_factory: TTSProviderFactory | None = None,
    ) -> None:
        self.config = config or SentenceChunkerConfig()
        self.clock = clock or time.monotonic
        self.utterance_id_factory = utterance_id_factory or (lambda: f"agent-sentence-{uuid.uuid4().hex}")
        self.config_loader = config_loader
        self.tts_provider_factory = tts_provider_factory
        self._states: dict[str, SentenceChunkState] = {}

    def reset(self) -> None:
        self._states.clear()

    def has_session_state(self, session_id: str) -> bool:
        state = self._states.get(session_id)
        return bool(state and (state.buffer or state.spoken_text or state.pending_tasks))

    def state_for(self, session_id: str) -> SentenceChunkState | None:
        return self._states.get(session_id)

    def _state(self, session: BridgeSession, context_id: str) -> SentenceChunkState:
        state = self._states.get(session.session_id)
        if state is None:
            state = SentenceChunkState(session_id=session.session_id, context_id=context_id)
            self._states[session.session_id] = state
        else:
            state.context_id = context_id
        return state

    def extract_ready_text(self, state: SentenceChunkState, *, final: bool = False) -> str:
        """Return text ready for TTS and remove it from the buffer."""

        if not state.buffer:
            return ""

        match = _SENTENCE_RE.match(state.buffer)
        if match:
            ready = match.group(1).strip()
            state.buffer = state.buffer[match.end() :].lstrip()
            return ready

        now = self.clock()
        elapsed_ms = 0 if state.started_at is None else (now - state.started_at) * 1000.0
        if final or len(state.buffer) >= self.config.max_buffer_chars:
            ready = state.buffer.strip()
            state.buffer = ""
            return ready
        if (
            elapsed_ms >= self.config.first_audio_latency_ms
            and len(state.buffer.strip()) >= self.config.min_latency_flush_chars
        ):
            ready = state.buffer.strip()
            state.buffer = ""
            return ready
        return ""

    async def process_session_delta(
        self,
        session: BridgeSession,
        *,
        context_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Buffer one token delta for one session and synthesize if ready."""

        text = _clean_text(text)
        if not text or session.sender is None:
            return {"status": "skipped", "reason": "empty_or_no_sender"}
        state = self._state(session, context_id)
        if not state.buffer:
            state.started_at = self.clock()
        state.buffer += text
        try:
            from usr.plugins.a0_voqualizer.helpers.agent_finalizer import _looks_like_structured_response_stream

            if _looks_like_structured_response_stream(state.buffer):
                return {"status": "buffered", "buffer_chars": len(state.buffer), "deferred_structured_response": True}
        except Exception:
            pass
        ready = self.extract_ready_text(state)
        if not ready:
            return {"status": "buffered", "buffer_chars": len(state.buffer)}
        return await self._synthesize_ready(session, state, ready, final=False)

    async def process_context_delta(self, *, context_id: str, text: str) -> dict[str, Any]:
        """Buffer one token delta for every Voqualizer session bound to context."""

        text = _clean_text(text)
        if not context_id or not text:
            return {"sessions": 0, "results": [], "reason": "empty_context_or_text"}
        bridge = get_default_context_bridge()
        bindings = bridge.bindings_for_context(context_id)
        if not bindings:
            return {"sessions": 0, "results": [], "reason": "no_bindings"}
        registry = BridgeRegistry.instance()
        results: list[dict[str, Any]] = []
        sessions = 0
        for binding in bindings:
            session = registry.get(binding.session_id)
            if session is None or session.sender is None:
                continue
            sessions += 1
            result = await self.process_session_delta(session, context_id=binding.context_id, text=text)
            results.append({"session_id": session.session_id, **result})
        return {"sessions": sessions, "results": results}

    async def finalize_session(
        self,
        session: BridgeSession,
        *,
        context_id: str,
        final_text: str = "",
        config_loader: ConfigLoader | None = None,
        tts_provider_factory: TTSProviderFactory | None = None,
    ) -> dict[str, Any]:
        """Flush remaining buffered text at process_chain_end.

        If no streaming text has been spoken for this session, callers should use
        the A5.3 full-response TTS path instead. This method is for sessions that
        have A5.4 state from token streaming.
        """

        state = self._state(session, context_id)
        final_text = _clean_text(final_text)
        if final_text and not state.spoken_text:
            # If streaming was deferred for a structured JSON/tool response, use
            # the finalizer-supplied clean speech text instead of the raw stream
            # buffer so TTS does not read response-envelope keys or Markdown.
            state.buffer = final_text
        if not state.buffer:
            return {"status": "ok", "chunks": 0, "final_flush": False, "spoken_text": state.spoken_text}
        ready = self.extract_ready_text(state, final=True)
        if not ready:
            return {"status": "ok", "chunks": 0, "final_flush": False, "spoken_text": state.spoken_text}
        result = await self._synthesize_ready(
            session,
            state,
            ready,
            final=True,
            config_loader=config_loader,
            tts_provider_factory=tts_provider_factory,
        )
        result["final_flush"] = True
        return result

    async def _synthesize_ready(
        self,
        session: BridgeSession,
        state: SentenceChunkState,
        text: str,
        *,
        final: bool,
        config_loader: ConfigLoader | None = None,
        tts_provider_factory: TTSProviderFactory | None = None,
    ) -> dict[str, Any]:
        from usr.plugins.a0_voqualizer.helpers.agent_finalizer import synthesize_agent_response_tts

        text = text.strip()
        if not text:
            return {"status": "skipped", "reason": "empty_ready_text"}
        utterance_id = self.utterance_id_factory()
        result = await synthesize_agent_response_tts(
            session,
            text,
            context_id=state.context_id,
            utterance_id=utterance_id,
            reset_cancel=False,
            metadata_source="voqualizer_agent_sentence",
            config_loader=config_loader or self.config_loader,
            tts_provider_factory=tts_provider_factory or self.tts_provider_factory,
        )
        state.sequence += 1
        state.started_at = self.clock() if state.buffer else None
        if result.get("status") in {"ok", "cancelled"}:
            state.spoken_text += text
        result.update({
            "utterance_id": utterance_id,
            "text": text,
            "sequence": state.sequence,
            "final": final,
        })
        return result


_default_sentence_tts_chunker: SentenceTTSChunker | None = None


def get_default_sentence_tts_chunker() -> SentenceTTSChunker:
    global _default_sentence_tts_chunker
    if _default_sentence_tts_chunker is None:
        _default_sentence_tts_chunker = SentenceTTSChunker()
    return _default_sentence_tts_chunker


__all__ = [
    "SentenceChunkerConfig",
    "SentenceChunkState",
    "SentenceTTSChunker",
    "get_default_sentence_tts_chunker",
]
