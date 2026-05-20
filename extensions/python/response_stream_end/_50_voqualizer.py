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
            from usr.plugins.a0_voqualizer.extensions.python.process_chain_end._50_voqualizer import (
                finalize_voqualizer_response_once,
            )

            await finalize_voqualizer_response_once(self.agent, loop_data)
        except Exception as exc:  # Do not break A0 streaming finalization.
            _PRINTER.print(f"[Voqualizer] Failed to finalize response_stream_end TTS: {exc}")
