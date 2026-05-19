"""Assistant-final response bridge for Voqualizer (M5 / A5.3).

This helper is called by the Agent Zero ``process_chain_end`` extension after an
agent finishes a response on a context bound to one or more Voqualizer sessions.
It emits the completed text and then reuses the accepted M4 TTS provider/session
machinery to synthesize the final response for the voice client.
"""

from __future__ import annotations

import ast
import base64
import json
import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from usr.plugins.a0_voqualizer.helpers.context_bridge import get_default_context_bridge
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry
from usr.plugins.a0_voqualizer.helpers.session import BridgeSession, STATE_READY
from usr.plugins.a0_voqualizer.helpers.tts import AudioChunk as TTSAudioChunk
from usr.plugins.a0_voqualizer.helpers.tts import TTSProvider, TTSRequest, TTSError

ConfigLoader = Callable[[], dict[str, Any]]
TTSProviderFactory = Callable[[Mapping[str, Any]], TTSProvider]
UtteranceIdFactory = Callable[[], str]


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _extract_text_section(value: Any) -> str:
    """Extract the user-facing text section from A0/JSON-style responses.

    Handles real dicts, strict JSON strings, fenced JSON, Python-literal dict
    strings, and envelopes embedded in surrounding text.  This is deliberately
    defensive because A0 final response text can be captured at different points
    in the response pipeline.
    """

    if isinstance(value, Mapping):
        for path in (("tool_args", "text"), ("response", "text"), ("message",), ("text",), ("content",)):
            cur: Any = value
            ok = True
            for key in path:
                if isinstance(cur, Mapping) and key in cur:
                    cur = cur[key]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, str) and cur.strip():
                return cur.strip()
        return ""

    text = _clean_text(value)
    if not text:
        return ""

    candidates = [text]
    fence = re.fullmatch(r"\s*```(?:json|python)?\s*(.*?)\s*```\s*", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    first = text.find("{")
    last = text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first:last + 1].strip())

    for candidate in candidates:
        c = candidate.strip()
        if not (c.startswith("{") and c.endswith("}")):
            continue
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(c)
            except Exception:
                continue
            extracted = _extract_text_section(parsed)
            if extracted:
                return extracted

    return text


def _decode_partial_string_literal(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(value):
            break
        esc = value[i + 1]
        mapping = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"', "'": "'"}
        out.append(mapping.get(esc, esc))
        i += 2
    return "".join(out)


def _extract_streaming_text_section(value: Any) -> str:
    """Extract current tool/text field content from a partial structured stream.

    Unlike ``_extract_text_section``, this works before the surrounding JSON or
    Python-literal dict is complete.  It lets streaming TTS start once the
    response reaches ``tool_args.text`` while avoiding speech for envelope keys
    like thoughts/headline/tool_name.
    """

    text = _clean_text(value)
    if not text:
        return ""
    match = re.search(r"[\"']text[\"']\s*:\s*([\"'])", text)
    if not match:
        return ""
    quote = match.group(1)
    start = match.end()
    escaped = False
    end = len(text)
    for i in range(start, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == quote:
            end = i
            break
    return _decode_partial_string_literal(text[start:end])


def _looks_like_structured_response_stream(text: Any) -> bool:
    """Return true while a streamed response appears to be a JSON/tool envelope.

    Partial streamed JSON cannot be safely reduced to ``tool_args.text`` yet. If
    we synthesize it sentence-by-sentence before the finalizer sees the complete
    object, TTS reads keys like thoughts/tool_args and Markdown symbols.  The
    sentence chunker uses this to defer TTS until process_chain_end, where the
    complete response can be extracted and normalized.
    """

    if not isinstance(text, str):
        return False
    stripped = text.lstrip()
    if not stripped.startswith(("{", "```json", "```python")):
        return False
    markers = ("tool_args", "thoughts", "tool_name", "headline", "user_message", "tool_args")
    return any(marker in text for marker in markers)

def _markdown_to_speech_text(value: Any) -> str:
    """Convert Markdown-ish assistant text into TTS-friendly plain speech.

    This intentionally avoids heavy Markdown dependencies and focuses on the
    structures that sound bad when spoken raw: code fences, inline code ticks,
    links, images, headings, bullets, tables, blockquotes, emphasis markers and
    horizontal rules.
    """

    text = _clean_text(value)
    if not text:
        return ""

    # Remove fenced code blocks rather than reading symbols/code aloud.
    text = re.sub(r"```[\s\S]*?```", " Code block omitted. ", text)
    # Replace inline code with its readable content.
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Images: keep alt text if present.
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Links: speak visible label only.
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)

    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        if re.fullmatch(r"[-*_]{3,}", line):
            continue
        # Drop Markdown table separator rows.
        if "|" in line and re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", line):
            continue
        # Convert table cells to comma-separated phrases.
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|") if c.strip()]
            if cells:
                line = ", ".join(cells)
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^>+\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = re.sub(r"^\[[ xX]\]\s+", "", line)
        lines.append(line)

    text = "\n".join(lines)
    # Remove common emphasis/strikethrough markers while preserving words.
    text = re.sub(r"(\*\*|__|~~)", "", text)
    text = re.sub(r"(?<!\w)[*_](?!\s)(.*?)(?<!\s)[*_](?!\w)", r"\1", text)
    # Collapse excessive punctuation that TTS reads awkwardly.
    text = text.replace("→", " to ").replace("←", " from ").replace("=>", " to ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text.strip()


def _tts_speakable_text(value: Any) -> str:
    """Return only the assistant text section, normalized for speech."""

    return _markdown_to_speech_text(_extract_text_section(value))


def _default_config_loader() -> dict[str, Any]:
    from usr.plugins.a0_voqualizer.api.ws_voqualizer import _safe_load_config

    return _safe_load_config()


def _default_tts_provider_factory(spec: Mapping[str, Any]) -> TTSProvider:
    from usr.plugins.a0_voqualizer.api.ws_voqualizer import _build_tts_provider

    return _build_tts_provider(spec)


def _provider_config(config: dict[str, Any], provider_name: str) -> dict[str, Any] | None:
    for provider in config.get("tts", {}).get("providers", []):
        if provider.get("name") == provider_name:
            return dict(provider)
    return None


def _sample_rate_for_codec(codec: str) -> int:
    if codec == "pcm16/24k":
        return 24000
    if codec == "mulaw/8k":
        return 8000
    return 16000


def _codec_for_tts_spec(spec: Mapping[str, Any], fallback_codec: str) -> str:
    """Resolve final-response TTS codec from provider format/sample-rate.

    The direct ``voqualizer_user_text`` path already learned to honor provider
    ``format: pcm`` + ``sample_rate: 24000``.  The agent finalizer must mirror
    that behavior; otherwise context-driven assistant responses can be routed to
    TTS with stale session/protocol defaults such as ``pcm16/16k``.
    """

    fmt = str(spec.get("format") or spec.get("response_format") or "").strip().lower()
    if fmt in {"mp3", "opus", "wav"}:
        return fmt
    if fmt == "pcm":
        try:
            rate = int(spec.get("sample_rate") or 24000)
        except Exception:
            rate = 24000
        return "pcm16/24k" if rate >= 24000 else "pcm16/16k"
    return fallback_codec or "pcm16/16k"


def _request_metadata_for_tts_spec(spec: Mapping[str, Any], *, source: str, context_id: str) -> dict[str, Any]:
    metadata = {
        "source": source,
        "context_id": context_id,
    }
    fmt = str(spec.get("format") or spec.get("response_format") or "").strip().lower()
    if fmt:
        metadata["response_format"] = fmt
    return metadata


async def _emit_tts_chunk(
    session: BridgeSession,
    chunk: TTSAudioChunk,
    *,
    metadata_defaults: dict[str, Any] | None = None,
) -> None:
    payload = chunk.event_payload()
    event = payload.pop("event")
    payload["session_id"] = session.session_id
    payload["audio"] = chunk.data
    # Match the direct WS TTS path: A0/Socket.IO/browser dispatch may not always
    # preserve nested binary payloads, while the tester reliably decodes base64.
    payload["audio_b64"] = base64.b64encode(chunk.data).decode("ascii")
    payload["audio_encoding"] = "base64"
    if metadata_defaults:
        payload["metadata"] = {**metadata_defaults, **dict(payload.get("metadata") or {})}
    if session.sender is not None:
        await session.sender(event, payload)


async def _emit_tts_done(
    session: BridgeSession,
    *,
    utterance_id: str,
    cancelled: bool = False,
    chunks: int = 0,
    reason: str | None = None,
) -> None:
    if session.sender is None:
        return
    payload: dict[str, Any] = {
        "session_id": session.session_id,
        "utterance_id": utterance_id,
        "cancelled": cancelled,
        "chunks": chunks,
    }
    if reason:
        payload["reason"] = reason
    await session.sender("voqualizer_tts_done", payload)


async def _emit_tts_error(session: BridgeSession, err: TTSError) -> None:
    if session.sender is None:
        return
    payload = err.to_dict()
    payload["session_id"] = session.session_id
    await session.sender("voqualizer_error", payload)


async def synthesize_agent_response_tts(
    session: BridgeSession,
    text: str,
    *,
    context_id: str,
    utterance_id: str,
    config_loader: ConfigLoader | None = None,
    tts_provider_factory: TTSProviderFactory | None = None,
    reset_cancel: bool = True,
    metadata_source: str = "voqualizer_agent_response_final",
) -> dict[str, Any]:
    """Stream completed assistant text through the session's TTS provider.

    Returns a JSON-safe summary for tests/telemetry. Errors are emitted to the
    client as ``voqualizer_error`` and summarized instead of escaping into the
    Agent Zero process-chain hook.
    """

    original_text = _clean_text(text)
    text = _tts_speakable_text(text)
    if not text:
        return {"status": "skipped", "reason": "empty_text", "chunks": 0}
    if session.sender is None:
        return {"status": "skipped", "reason": "missing_sender", "chunks": 0}

    cfg_loader = config_loader or _default_config_loader
    provider_factory = tts_provider_factory or _default_tts_provider_factory
    chunks = 0
    try:
        cfg = cfg_loader()
        spec = _provider_config(cfg, session.tts_provider)
        if spec is None:
            raise TTSError(
                f"unknown TTS provider {session.tts_provider!r}",
                code="TTS_PROVIDER_NOT_FOUND",
                details={"provider": session.tts_provider},
            )

        cached = session.metadata.get("tts_provider_instance")
        if isinstance(cached, TTSProvider):
            provider = cached
        else:
            provider = provider_factory(spec)
            await provider.start()
            session.metadata["tts_provider_instance"] = provider

        fallback_codec = session.output_codec or cfg.get("protocol", {}).get("default_output_codec") or "pcm16/16k"
        codec = _codec_for_tts_spec(spec, fallback_codec)
        sample_rate = int(spec.get("sample_rate") or _sample_rate_for_codec(codec))
        spec_options = spec.get("options") if isinstance(spec.get("options"), Mapping) else {}
        speed = float(spec.get("speed") or spec_options.get("speed") or 1.0)
        if reset_cancel:
            session.reset_cancel()
        session.metadata["tts_chunks_emitted"] = 0
        if isinstance(session.metadata.get("tts_barge_in_notified"), set):
            session.metadata["tts_barge_in_notified"].clear()
        session.metadata["tts_active_utterance_id"] = utterance_id
        try:
            session.transition_to("speaking")
        except Exception:
            pass

        request_metadata = _request_metadata_for_tts_spec(
            spec,
            source=metadata_source,
            context_id=context_id,
        )
        if original_text != text:
            request_metadata["speech_normalized"] = True
        request = TTSRequest(
            text=text,
            utterance_id=utterance_id,
            codec=codec,
            sample_rate=sample_rate,
            speed=speed,
            voice=spec.get("voice"),
            language=session.language if session.language != "auto" else None,
            metadata=request_metadata,
        )
        async for chunk in provider.stream(request):
            if session.cancel_tts.is_set():
                await _emit_tts_done(
                    session,
                    utterance_id=utterance_id,
                    cancelled=True,
                    chunks=chunks,
                    reason="barge_in",
                )
                return {"status": "cancelled", "reason": "barge_in", "chunks": chunks}
            await _emit_tts_chunk(session, chunk, metadata_defaults=request_metadata)
            chunks += 1
            session.metadata["tts_chunks_emitted"] = chunks
            if session.cancel_tts.is_set():
                await _emit_tts_done(
                    session,
                    utterance_id=utterance_id,
                    cancelled=True,
                    chunks=chunks,
                    reason="barge_in",
                )
                return {"status": "cancelled", "reason": "barge_in", "chunks": chunks}
        await _emit_tts_done(session, utterance_id=utterance_id, cancelled=False, chunks=chunks)
        return {"status": "ok", "chunks": chunks, "utterance_id": utterance_id}
    except TTSError as exc:
        await _emit_tts_error(session, exc)
        return {"status": "error", "error": exc.to_dict(), "chunks": chunks}
    except Exception as exc:
        err = TTSError(str(exc), code="TTS_FINALIZATION_ERROR", recoverable=True)
        await _emit_tts_error(session, err)
        return {"status": "error", "error": err.to_dict(), "chunks": chunks}
    finally:
        session.metadata.pop("tts_active_utterance_id", None)
        try:
            session.transition_to(STATE_READY)
        except Exception:
            pass


async def finalize_agent_response_for_context(
    *,
    context_id: str,
    text: str,
    config_loader: ConfigLoader | None = None,
    tts_provider_factory: TTSProviderFactory | None = None,
    utterance_id_factory: UtteranceIdFactory | None = None,
) -> dict[str, Any]:
    """Emit final assistant text and trigger TTS for bound Voqualizer sessions."""

    context_id = _clean_text(context_id)
    text = _clean_text(text)
    speech_text = _tts_speakable_text(text)
    if not context_id or not text:
        return {"emitted": 0, "tts": [], "reason": "empty_context_or_text"}

    bridge = get_default_context_bridge()
    bindings = bridge.bindings_for_context(context_id)
    if not bindings:
        return {"emitted": 0, "tts": [], "reason": "no_bindings"}

    registry = BridgeRegistry.instance()
    utterance_ids = utterance_id_factory or (lambda: f"agent-{uuid.uuid4().hex}")
    emitted = 0
    tts_results: list[dict[str, Any]] = []
    for binding in bindings:
        session = registry.get(binding.session_id)
        if session is None or session.sender is None:
            continue
        utterance_id = utterance_ids()
        # Clear any stale cancellation before announcing the new final response.
        # If a client barges in in reaction to this event, preserve that fresh
        # cancellation into the TTS stream startup below.
        session.reset_cancel()
        await session.sender(
            "voqualizer_agent_response_final",
            {
                "session_id": session.session_id,
                "context_id": binding.context_id,
                "text": text,
                "speech_text": speech_text,
                "utterance_id": utterance_id,
            },
        )
        emitted += 1
        try:
            from usr.plugins.a0_voqualizer.helpers.sentence_chunker import get_default_sentence_tts_chunker

            chunker = get_default_sentence_tts_chunker()
            has_streaming_state = chunker.has_session_state(session.session_id)
        except Exception:
            chunker = None
            has_streaming_state = False

        if has_streaming_state and chunker is not None:
            tts_result = await chunker.finalize_session(
                session,
                context_id=binding.context_id,
                final_text=speech_text,
                config_loader=config_loader,
                tts_provider_factory=tts_provider_factory,
            )
        else:
            tts_result = await synthesize_agent_response_tts(
                session,
                speech_text,
                context_id=binding.context_id,
                utterance_id=utterance_id,
                config_loader=config_loader,
                tts_provider_factory=tts_provider_factory,
                reset_cancel=False,
            )
        tts_results.append({"session_id": session.session_id, **tts_result})
    return {"emitted": emitted, "tts": tts_results}


__all__ = [
    "finalize_agent_response_for_context",
    "synthesize_agent_response_tts",
    "_extract_text_section",
    "_markdown_to_speech_text",
    "_tts_speakable_text",
    "_looks_like_structured_response_stream",
    "_extract_streaming_text_section",
]
