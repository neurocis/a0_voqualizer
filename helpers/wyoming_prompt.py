"""Wyoming prompt/assistant text adapter scaffold for Voqualizer.

This module defines the context-bound prompt side of the Wyoming rewrite. It is
provider/runtime agnostic for now: callers supply an async/sync response provider
that receives the fixed interface ctxID and returns assistant text or chunks.

No old custom websocket protocol is used or preserved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import inspect
import json
import time
import uuid
from typing import Any, AsyncIterable, Awaitable, Callable, Iterable

from .wyoming_protocol import WyomingEvent, event
from .wyoming_server import WyomingSession


PromptProvider = Callable[[str, dict[str, Any]], Awaitable[str | Iterable[str] | AsyncIterable[str]] | str | Iterable[str] | AsyncIterable[str]]


def stable_text_hash(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]


def collapse_response_tool_json(text: str) -> dict[str, str]:
    """Collapse response-tool JSON envelopes to headline/body display text.

    Keeps generic Wyoming clients from receiving raw Agent Zero response-tool JSON
    when the payload is a response envelope.
    """
    raw = (text or "").strip()
    if not raw.startswith("{"):
        return {"headline": "", "text": text or "", "kind": "plain"}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"headline": "", "text": text or "", "kind": "plain"}
    if not isinstance(parsed, dict):
        return {"headline": "", "text": text or "", "kind": "plain"}
    tool_args = parsed.get("tool_args") if isinstance(parsed.get("tool_args"), dict) else {}
    headline = str(parsed.get("headline") or "").strip()
    body = str(tool_args.get("text") or parsed.get("text") or "").strip()
    if parsed.get("tool_name") == "response" and (headline or body):
        return {"headline": headline, "text": body or headline, "kind": "response_tool"}
    return {"headline": headline, "text": body or text or "", "kind": "json" if headline or body else "plain"}


@dataclass(slots=True)
class WyomingGenerationState:
    generation_id: str
    prompt_text: str
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    final_text: str = ""
    cancelled: bool = False
    chunk_count: int = 0


class WyomingPromptAdapter:
    """Submit prompt text to the fixed interface ctxID and emit response events."""

    def __init__(self, provider: PromptProvider | None = None) -> None:
        self.provider = provider or self._default_echo_provider
        self.generations_by_session: dict[str, WyomingGenerationState] = {}

    async def handle_event(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        if incoming.type == "voqualizer-text-prompt":
            return await self.handle_text_prompt(session, incoming)
        if incoming.type == "transcript":
            # Wyoming-compatible clients may send transcript text to be handled by
            # the assistant pipeline. The interface ctxID remains authoritative.
            return await self.handle_transcript_prompt(session, incoming)
        if incoming.type in {"cancel", "voqualizer-cancel", "pause-satellite"}:
            return self.handle_cancel(session, incoming)
        return [event("error", code="unsupported_prompt_event", message=f"Unsupported prompt event: {incoming.type}")]

    async def handle_transcript_prompt(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        text = str(incoming.data.get("text") or "").strip()
        if not text:
            return [event("error", code="empty_transcript", message="Transcript text is empty")]
        return await self._submit_prompt(session, text, source="transcript", input_event=incoming)

    async def handle_text_prompt(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        text = str(incoming.data.get("text") or "").strip()
        if not text:
            return [event("error", code="empty_prompt", message="Prompt text is empty")]
        return await self._submit_prompt(session, text, source="text", input_event=incoming)

    def handle_cancel(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        state = self.generations_by_session.get(session.session_id)
        if state:
            state.cancelled = True
        generation_id = session.new_generation()
        return [
            event(
                "voqualizer-generation-cancelled",
                interface_id=session.interface.id,
                ctxid=session.ctxid,
                session_id=session.session_id,
                generation_id=generation_id,
                reason=str(incoming.data.get("reason") or "client_cancel"),
            )
        ]

    async def _submit_prompt(self, session: WyomingSession, text: str, *, source: str, input_event: WyomingEvent) -> list[WyomingEvent]:
        # Respect client-supplied generation_id when present so browser/UI
        # event-correlation filters (e.g. isCurrentWyomingGeneration) match the
        # response-start/chunk/final events back to the original submission.
        client_generation = str((input_event.data or {}).get("generation_id") or (input_event.data or {}).get("generationId") or "").strip()
        if client_generation:
            generation_id = client_generation
            session.active_generation_id = client_generation
            session.metadata["last_generation_started_at_ms"] = int(__import__("time").time() * 1000)
        else:
            generation_id = session.new_generation()
        state = WyomingGenerationState(generation_id=generation_id, prompt_text=text)
        self.generations_by_session[session.session_id] = state
        metadata = {
            "interface_id": session.interface.id,
            "ctxid": session.ctxid,
            "session_id": session.session_id,
            "generation_id": generation_id,
            "source": source,
            "input_event_type": input_event.type,
            "prompt_hash": stable_text_hash(text),
        }
        replies: list[WyomingEvent] = [
            event("voqualizer-response-start", **metadata),
        ]
        final_parts: list[str] = []
        provider_error: str = ""
        try:
            provider_result = self.provider(text, metadata)
            if inspect.isawaitable(provider_result):
                provider_result = await provider_result
            if hasattr(provider_result, "__aiter__"):
                async for chunk in provider_result:  # type: ignore[union-attr]
                    if state.cancelled:
                        break
                    piece = str(chunk)
                    final_parts.append(piece)
                    state.chunk_count += 1
                    replies.append(event("voqualizer-response-chunk", text=piece, chunk_index=state.chunk_count - 1, **metadata))
            elif isinstance(provider_result, str):
                final_parts.append(provider_result)
                if provider_result:
                    state.chunk_count += 1
                    replies.append(event("voqualizer-response-chunk", text=provider_result, chunk_index=0, **metadata))
            else:
                for chunk in provider_result or []:  # type: ignore[union-attr]
                    if state.cancelled:
                        break
                    piece = str(chunk)
                    final_parts.append(piece)
                    state.chunk_count += 1
                    replies.append(event("voqualizer-response-chunk", text=piece, chunk_index=state.chunk_count - 1, **metadata))
        except Exception as exc:  # noqa: BLE001
            # Do not let late provider/framework errors turn an already-visible
            # response into a failed ACK that leaves the browser in processing.
            # Emit a Wyoming error event for diagnostics, then finalize with any
            # accumulated text, or a compact error message if no text arrived.
            provider_error = f"{type(exc).__name__}: {exc}"
            replies.append(event("error", code="prompt_provider_error", message=provider_error, **metadata))
            if not final_parts:
                final_parts.append(f"Prompt provider error: {provider_error}")
                state.chunk_count += 1
                replies.append(event("voqualizer-response-chunk", text=final_parts[-1], chunk_index=0, **metadata))
        collapsed = collapse_response_tool_json("".join(final_parts))
        state.final_text = collapsed["text"]
        replies.append(
            event(
                "voqualizer-response-final",
                text=collapsed["text"],
                headline=collapsed["headline"],
                display_kind=collapsed["kind"],
                chunk_count=state.chunk_count,
                provider_error=provider_error,
                ok=not bool(provider_error),
                **metadata,
            )
        )
        return replies

    @staticmethod
    async def _default_echo_provider(text: str, metadata: dict[str, Any]) -> str:
        return text
