"""Finalize Voqualizer TTS at A0 response_stream_end as a live fallback.

The GUI voice session should receive TTS whenever an assistant response is
produced, regardless of whether ASR is enabled.  Some live A0 paths expose the
parsed response-tool text in the context log before ``process_chain_end``.  This
extension calls the same idempotent finalizer helper used by process_chain_end so
TTS can start even if the later hook lacks response text.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - live A0 import path
    from agent import LoopData
except Exception:  # pragma: no cover
    class LoopData:  # type: ignore[no-redef]
        pass

from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#A78BFA", padding=False)


class VoqualizerResponseStreamEnd(Extension):
    async def execute(self, loop_data: LoopData | None = None, **kwargs: Any) -> None:
        if not self.agent:
            return
        try:
            await finalize_cx_stream_for_agent(self.agent, loop_data)

            from usr.plugins.a0_voqualizer.extensions.python.process_chain_end._50_voqualizer import (
                finalize_voqualizer_response_once,
            )

            await finalize_voqualizer_response_once(self.agent, loop_data)
        except Exception as exc:  # Do not break A0 streaming finalization.
            _PRINTER.print(f"[Voqualizer] Failed to finalize response_stream_end TTS: {exc}")


# M7 protocol event emitted through helpers.cx_stream: voqualizer_cx_stream_final
async def finalize_cx_stream_for_agent(agent: Any, loop_data: LoopData | None = None) -> dict[str, Any]:
    """Emit the M7 context-token final event for the completed response stream."""

    if not agent:
        return {"status": "skipped", "reason": "missing_agent"}
    context = getattr(agent, "context", None)
    context_id = getattr(context, "id", "")
    if not context_id:
        return {"status": "skipped", "reason": "missing_context"}
    text = getattr(loop_data, "last_response", "") if loop_data is not None else ""
    if not isinstance(text, str) or not text:
        text = getattr(getattr(agent, "loop_data", None), "last_response", "") or ""
    if not isinstance(text, str):
        text = ""
    from usr.plugins.a0_voqualizer.helpers.cx_stream import get_default_cx_stream_hub

    return await get_default_cx_stream_hub().final(
        context_id=context_id,
        text=text,
        source="response_stream_end",
    )
