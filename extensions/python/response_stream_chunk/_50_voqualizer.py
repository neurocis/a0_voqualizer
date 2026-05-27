"""Stream A0 assistant response token deltas to Voqualizer sessions (A5.2).

Agent Zero calls the ``response_stream_chunk`` extension point for each
assistant response chunk with ``stream_data={"chunk": ..., "full": ...}``.
For contexts bound to a Voqualizer voice session, relay the chunk to the live WS
client as ``voqualizer_agent_delta``.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - import exercised in live A0, tests can stub it
    from agent import LoopData
except Exception:  # pragma: no cover
    class LoopData:  # type: ignore[no-redef]
        pass

from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#A78BFA", padding=False)


class VoqualizerResponseStreamChunk(Extension):
    async def execute(self, loop_data: LoopData | None = None, stream_data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        if not self.agent or not stream_data:
            return

        context = getattr(self.agent, "context", None)
        context_id = getattr(context, "id", "")
        if not context_id:
            return

        chunk = stream_data.get("chunk", "")
        if not isinstance(chunk, str) or not chunk:
            return

        full = stream_data.get("full", "")
        if not isinstance(full, str):
            full = ""

        try:
            await emit_cx_token_for_context(context_id=context_id, text=chunk, full=full)
            await emit_agent_delta_for_context(context_id=context_id, text=chunk)
            await buffer_agent_delta_for_tts(context_id=context_id, text=chunk)
        except Exception as exc:  # Keep streaming robust; never break A0 response generation.
            _PRINTER.print(f"[Voqualizer] Failed to stream agent delta: {exc}")

# M7 protocol event emitted through helpers.cx_stream: voqualizer_cx_token

async def emit_cx_token_for_context(*, context_id: str, text: str, full: str = "") -> dict[str, Any]:
    """Emit one M7 context-token delta using the additive protocol stream."""

    if not isinstance(context_id, str) or not context_id.strip():
        return {"status": "skipped", "reason": "empty_context", "sessions": 0}
    if not isinstance(text, str) or not text:
        return {"status": "skipped", "reason": "empty_text", "sessions": 0}
    from usr.plugins.a0_voqualizer.helpers.cx_stream import get_default_cx_stream_hub

    return await get_default_cx_stream_hub().token(
        context_id=context_id.strip(),
        delta=text,
        full=full,
        source="response_stream_chunk",
    )


async def emit_agent_delta_for_context(*, context_id: str, text: str) -> int:
    """Emit one assistant delta to all active Voqualizer sessions bound to a context.

    Returns the number of sessions that received the delta. This helper is kept
    separate from the Extension class so deterministic tests can exercise it
    without constructing framework extension loader state.
    """

    if not isinstance(context_id, str) or not context_id.strip():
        return 0
    if not isinstance(text, str) or not text:
        return 0

    from usr.plugins.a0_voqualizer.helpers.context_bridge import get_default_context_bridge
    from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry

    bridge = get_default_context_bridge()
    bindings = bridge.bindings_for_context(context_id.strip())
    if not bindings:
        return 0

    registry = BridgeRegistry.instance()
    emitted = 0
    for binding in bindings:
        session = registry.get(binding.session_id)
        if session is None or session.sender is None:
            continue
        payload = {
            "session_id": session.session_id,
            "context_id": binding.context_id,
            "text": text,
        }
        await session.sender("voqualizer_agent_delta", payload)
        emitted += 1
    return emitted


async def buffer_agent_delta_for_tts(*, context_id: str, text: str) -> dict[str, Any]:
    """Feed streamed assistant text into the A5.4 sentence TTS chunker."""

    if not isinstance(context_id, str) or not context_id.strip():
        return {"sessions": 0, "results": [], "reason": "empty_context"}
    if not isinstance(text, str) or not text:
        return {"sessions": 0, "results": [], "reason": "empty_text"}
    from usr.plugins.a0_voqualizer.helpers.sentence_chunker import get_default_sentence_tts_chunker

    return await get_default_sentence_tts_chunker().process_context_delta(
        context_id=context_id.strip(),
        text=text,
    )
