"""Finalize Voqualizer assistant responses at Agent Zero process_chain_end.

A5.3 acceptance: emit ``voqualizer_agent_response_final`` and trigger TTS
finalization for voice sessions bound to the completed AgentContext.
"""

from __future__ import annotations

from typing import Any

VOQUALIZER_FINALIZED_RESPONSE_KEY = "a0_voqualizer_finalized_response_id"

try:  # pragma: no cover - live A0 import path
    from agent import LoopData
except Exception:  # pragma: no cover - deterministic tests can stub this
    class LoopData:  # type: ignore[no-redef]
        pass

from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#A78BFA", padding=False)


def _response_from_context_log(context: Any) -> tuple[str, str]:
    """Return the latest visible response text/id from the A0 context log.

    In live A0, the user-facing response tool text is written to a ``response``
    log item by the core ``response_stream`` extension before ``process_chain_end``.
    The raw ``LoopData.last_response`` may instead be the full JSON/tool envelope
    or may be unavailable on some hook paths.  Reading the context log mirrors
    the Telegram/Email integrations and makes Voqualizer TTS independent of ASR.
    """

    log = getattr(context, "log", None)
    if log is None:
        return "", ""
    try:
        lock = getattr(log, "_lock", None)
        if lock is not None:
            with lock:
                items = list(getattr(log, "logs", []) or [])
        else:
            items = list(getattr(log, "logs", []) or [])
    except Exception:
        return "", ""
    for item in reversed(items):
        if getattr(item, "type", "") != "response":
            continue
        content = getattr(item, "content", "") or ""
        if isinstance(content, str) and content.strip():
            return content.strip(), str(getattr(item, "id", "") or "")
    return "", ""


def _final_response_text(agent: Any, loop_data: Any) -> tuple[str, str, str]:
    """Resolve final speakable response text from live A0 hook state.

    Preference order intentionally starts with the context response log because
    that contains the already-parsed response-tool text shown to the user.
    Fallbacks preserve older tests and nonstandard runtimes.
    """

    context = getattr(agent, "context", None)
    text, response_id = _response_from_context_log(context)
    if text:
        return text, response_id, "context_log_response"
    text = getattr(loop_data, "last_response", "") or ""
    if isinstance(text, str) and text.strip():
        return text.strip(), "", "loop_data.last_response"
    text = getattr(getattr(agent, "loop_data", None), "last_response", "") or ""
    if isinstance(text, str) and text.strip():
        return text.strip(), "", "agent.loop_data.last_response"
    return "", "", "empty"


async def finalize_voqualizer_response_once(agent: Any, loop_data: Any = None) -> dict[str, Any]:
    """Finalize TTS once per A0 response log item/context response.

    Both ``response_stream_end`` and ``process_chain_end`` can call this helper.
    A context-data marker prevents duplicate TTS while giving live sessions a
    fallback when one lifecycle hook does not carry the expected final text.
    """

    if not agent:
        return {"status": "skipped", "reason": "missing_agent"}
    context = getattr(agent, "context", None)
    context_id = getattr(context, "id", "")
    if not context_id:
        return {"status": "skipped", "reason": "missing_context"}
    text, response_id, source = _final_response_text(agent, loop_data)
    if not text:
        return {"status": "skipped", "reason": "empty_response", "source": source}
    data = getattr(context, "data", None)
    marker = response_id or f"{source}:{hash(text)}"
    if isinstance(data, dict) and data.get(VOQUALIZER_FINALIZED_RESPONSE_KEY) == marker:
        return {"status": "skipped", "reason": "already_finalized", "source": source}
    from usr.plugins.a0_voqualizer.helpers.agent_finalizer import finalize_agent_response_for_context

    result = await finalize_agent_response_for_context(context_id=context_id, text=text)
    if isinstance(data, dict):
        data[VOQUALIZER_FINALIZED_RESPONSE_KEY] = marker
        data["a0_voqualizer_last_tts_finalize_source"] = source
        data["a0_voqualizer_last_tts_finalize_result"] = result
    return {"status": "ok", "source": source, "result": result}


class VoqualizerProcessChainEnd(Extension):
    async def execute(self, loop_data: LoopData | None = None, **kwargs: Any) -> None:
        if not self.agent:
            return
        context = getattr(self.agent, "context", None)
        context_id = getattr(context, "id", "")
        if not context_id:
            return
        try:
            await finalize_voqualizer_response_once(self.agent, loop_data)
        except Exception as exc:  # Never break Agent Zero process finalization.
            _PRINTER.print(f"[Voqualizer] Failed to finalize agent response: {exc}")
