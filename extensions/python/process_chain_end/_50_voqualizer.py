"""Finalize Voqualizer assistant responses at Agent Zero process_chain_end.

A5.3 acceptance: emit ``voqualizer_agent_response_final`` and trigger TTS
finalization for voice sessions bound to the completed AgentContext.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - live A0 import path
    from agent import LoopData
except Exception:  # pragma: no cover - deterministic tests can stub this
    class LoopData:  # type: ignore[no-redef]
        pass

from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#A78BFA", padding=False)


def _final_response_text(agent: Any, loop_data: Any) -> str:
    text = getattr(loop_data, "last_response", "") or ""
    if isinstance(text, str) and text.strip():
        return text.strip()
    text = getattr(getattr(agent, "loop_data", None), "last_response", "") or ""
    if isinstance(text, str) and text.strip():
        return text.strip()
    return ""


class VoqualizerProcessChainEnd(Extension):
    async def execute(self, loop_data: LoopData | None = None, **kwargs: Any) -> None:
        if not self.agent:
            return
        context = getattr(self.agent, "context", None)
        context_id = getattr(context, "id", "")
        if not context_id:
            return
        text = _final_response_text(self.agent, loop_data)
        if not text:
            return
        try:
            from usr.plugins.a0_voqualizer.helpers.agent_finalizer import finalize_agent_response_for_context

            await finalize_agent_response_for_context(context_id=context_id, text=text)
        except Exception as exc:  # Never break Agent Zero process finalization.
            _PRINTER.print(f"[Voqualizer] Failed to finalize agent response: {exc}")
