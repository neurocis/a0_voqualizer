"""Agent Zero context prompt submitter for the Wyoming rewrite (W24).

Bridges Wyoming prompt events into the fixed Agent Zero ctxID bound to a
Wyoming interface.  Avoids the retired custom websocket protocol.
"""
from __future__ import annotations

import inspect
import uuid
from typing import Any, Callable


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _extract_response_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "response", "message", "content", "final_text", "result"):
            if isinstance(value.get(key), str):
                return value[key]
        return " ".join(str(v) for v in value.values() if isinstance(v, str)).strip()
    for attr in ("text", "response", "message", "content", "final_text"):
        got = getattr(value, attr, None)
        if isinstance(got, str):
            return got
    return str(value)


async def submit_to_agent_context(text: str, metadata: dict[str, Any]) -> str:
    """Submit text to the fixed ctxID from Wyoming metadata."""
    ctxid = str(metadata.get("ctxid") or metadata.get("ctxID") or "").strip()
    if not ctxid:
        raise ValueError("Wyoming prompt submitter requires metadata.ctxid")
    clean = str(text or "").strip()
    if not clean:
        return ""
    try:
        from agent import AgentContext, UserMessage  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"Agent Zero context API unavailable: {exc}") from exc

    context = None
    for factory_name in ("get", "get_context", "by_id", "load"):
        factory = getattr(AgentContext, factory_name, None)
        if factory is None:
            continue
        try:
            context = await _maybe_await(factory(ctxid))
            if context is not None:
                break
        except Exception:
            continue
    if context is None:
        try:
            context = AgentContext(ctxid)
        except Exception as exc:
            raise RuntimeError(f"Could not resolve AgentContext for ctxid={ctxid!r}: {exc}") from exc

    user_message: Any = clean
    try:
        user_message = UserMessage(content=clean)
    except Exception:
        try:
            user_message = UserMessage(clean)
        except Exception:
            user_message = clean

    call_metadata = {
        "ctxid": ctxid,
        "interface_id": metadata.get("interface_id"),
        "generation_id": metadata.get("generation_id"),
        "message_id": metadata.get("message_id") or uuid.uuid4().hex,
        "source": "wyoming",
    }
    for method_name in ("communicate", "message_async", "submit", "ask", "run"):
        method = getattr(context, method_name, None)
        if method is None:
            continue
        for args, kwargs in (
            ((user_message,), {"metadata": call_metadata}),
            ((clean,), {"metadata": call_metadata}),
            ((user_message,), {}),
            ((clean,), {}),
        ):
            try:
                result = await _maybe_await(method(*args, **kwargs))
                return _extract_response_text(result)
            except TypeError:
                continue
    raise RuntimeError(f"AgentContext for ctxid={ctxid!r} has no supported prompt method")


async def safe_echo_submitter(text: str, metadata: dict[str, Any]) -> str:
    """Safe deterministic fallback when live framework is unavailable."""
    return f"[ctxid={metadata.get('ctxid', '')}] {text}"


def build_agent_context_submitter(*, allow_echo_fallback: bool = True) -> Callable[[str, dict[str, Any]], Any]:
    """Return the prompt submitter callable used by live Wyoming runtimes."""
    async def submitter(text: str, metadata: dict[str, Any]) -> str:
        try:
            result = await submit_to_agent_context(text, metadata)
            if result:
                return result
        except Exception:
            if not allow_echo_fallback:
                raise
        return await safe_echo_submitter(text, metadata)
    return submitter
