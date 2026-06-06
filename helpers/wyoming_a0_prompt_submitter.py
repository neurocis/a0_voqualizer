"""Agent Zero context prompt submitter for the Wyoming rewrite (W24/W25).

Bridges Wyoming prompt events into the fixed Agent Zero ctxID bound to a
Wyoming interface. Avoids the retired custom websocket protocol.

W25 adds optional streaming: if the live Agent Zero context exposes a streaming
method, chunks are yielded into Wyoming voqualizer-response-chunk events before
the final assistant response/TTS path.
"""
from __future__ import annotations

import inspect
import uuid
from typing import Any, AsyncIterable, Callable


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
        for key in ("text", "response", "message", "content", "final_text", "result", "chunk", "delta"):
            if isinstance(value.get(key), str):
                return value[key]
        return " ".join(str(v) for v in value.values() if isinstance(v, str)).strip()
    for attr in ("text", "response", "message", "content", "final_text", "chunk", "delta"):
        got = getattr(value, attr, None)
        if isinstance(got, str):
            return got
    return str(value)


async def _resolve_agent_context(ctxid: str) -> tuple[Any, Any]:
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
    return context, UserMessage


def _build_user_message(clean: str, UserMessage: Any) -> Any:
    try:
        return UserMessage(content=clean)
    except Exception:
        try:
            return UserMessage(clean)
        except Exception:
            return clean


def _call_metadata(metadata: dict[str, Any], ctxid: str) -> dict[str, Any]:
    return {
        "ctxid": ctxid,
        "interface_id": metadata.get("interface_id"),
        "generation_id": metadata.get("generation_id"),
        "message_id": metadata.get("message_id") or uuid.uuid4().hex,
        "source": "wyoming",
    }


def _ctxid_from_metadata(metadata: dict[str, Any]) -> str:
    ctxid = str(metadata.get("ctxid") or metadata.get("ctxID") or "").strip()
    if not ctxid:
        raise ValueError("Wyoming prompt submitter requires metadata.ctxid")
    return ctxid


async def submit_to_agent_context(text: str, metadata: dict[str, Any]) -> str:
    """Submit text to the fixed ctxID from Wyoming metadata and return final text."""
    ctxid = _ctxid_from_metadata(metadata)
    clean = str(text or "").strip()
    if not clean:
        return ""
    context, UserMessage = await _resolve_agent_context(ctxid)
    user_message = _build_user_message(clean, UserMessage)
    call_metadata = _call_metadata(metadata, ctxid)
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


async def stream_to_agent_context(text: str, metadata: dict[str, Any]) -> AsyncIterable[str]:
    """Yield assistant chunks from a live Agent Zero context when supported.

    Supported method names are intentionally broad to tolerate framework/API
    evolution. If no streaming method exists, this function falls back to a
    single final chunk from submit_to_agent_context(...).
    """
    ctxid = _ctxid_from_metadata(metadata)
    clean = str(text or "").strip()
    if not clean:
        return
    context, UserMessage = await _resolve_agent_context(ctxid)
    user_message = _build_user_message(clean, UserMessage)
    call_metadata = _call_metadata(metadata, ctxid)
    for method_name in ("stream", "stream_async", "communicate_stream", "message_stream", "submit_stream", "ask_stream"):
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
                result = method(*args, **kwargs)
                result = await _maybe_await(result)
                if hasattr(result, "__aiter__"):
                    async for chunk in result:
                        piece = _extract_response_text(chunk)
                        if piece:
                            yield piece
                    return
                if isinstance(result, (list, tuple)) or hasattr(result, "__iter__") and not isinstance(result, (str, bytes, dict)):
                    for chunk in result:
                        piece = _extract_response_text(chunk)
                        if piece:
                            yield piece
                    return
                piece = _extract_response_text(result)
                if piece:
                    yield piece
                return
            except TypeError:
                continue
    final = await submit_to_agent_context(clean, metadata)
    if final:
        yield final


async def safe_echo_submitter(text: str, metadata: dict[str, Any]) -> str:
    """Safe deterministic fallback when live framework is unavailable."""
    return f"[ctxid={metadata.get('ctxid', '')}] {text}"


async def safe_echo_stream_submitter(text: str, metadata: dict[str, Any]) -> AsyncIterable[str]:
    yield await safe_echo_submitter(text, metadata)


def build_agent_context_submitter(*, allow_echo_fallback: bool = True, stream: bool = True) -> Callable[[str, dict[str, Any]], Any]:
    """Return the prompt submitter callable used by live Wyoming runtimes.

    With stream=True, the callable returns an async iterable when live streaming
    is available, allowing WyomingPromptAdapter to emit response chunks in real
    time before final/TTS handling.
    """
    if stream:
        async def stream_submitter(text: str, metadata: dict[str, Any]):
            try:
                return stream_to_agent_context(text, metadata)
            except Exception:
                if not allow_echo_fallback:
                    raise
                return safe_echo_stream_submitter(text, metadata)
        return stream_submitter

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
