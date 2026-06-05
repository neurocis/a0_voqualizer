"""A0 provider adapters for the Wyoming Voqualizer rewrite.

Old websocket files and web UI assets remain in-tree for reference, but this
module bridges real/scaffold A0 providers into the Wyoming ASR/prompt/TTS
pipeline without depending on the retired custom websocket transport.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
import uuid
from typing import Any, AsyncIterable, Awaitable, Callable, Iterable

from .wyoming_asr import WyomingAsrAdapter
from .wyoming_prompt import WyomingPromptAdapter
from .wyoming_tts import WyomingTtsAdapter

ProviderFactory = Callable[[], Any | Awaitable[Any]]
PromptSubmitter = Callable[[str, dict[str, Any]], str | Iterable[str] | AsyncIterable[str] | Awaitable[str | Iterable[str] | AsyncIterable[str]]]


@dataclass(slots=True)
class WyomingA0AdapterStatus:
    asr_provider_factory: bool = False
    tts_provider_factory: bool = False
    prompt_submitter: bool = False
    mode: str = "provider_scaffold"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _collect_text_result(value: Any) -> str:
    value = await _maybe_await(value)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    transcript = getattr(value, "transcript", None)
    if isinstance(transcript, str):
        return transcript
    if isinstance(value, dict):
        for key in ("text", "transcript", "final_text", "result"):
            if isinstance(value.get(key), str):
                return str(value[key])
    return str(value)


async def _iter_any_chunks(value: Any):
    value = await _maybe_await(value)
    if value is None:
        return
    if isinstance(value, bytes):
        yield value
        return
    if hasattr(value, "__aiter__"):
        async for chunk in value:
            yield chunk
        return
    for chunk in value or []:
        yield chunk


def _chunk_to_bytes(chunk: Any) -> bytes:
    if chunk is None:
        return b""
    if isinstance(chunk, bytes):
        return chunk
    data = getattr(chunk, "data", None)
    if isinstance(data, bytes):
        return data
    audio = getattr(chunk, "audio", None)
    if isinstance(audio, bytes):
        return audio
    if isinstance(chunk, dict):
        for key in ("data", "audio", "payload", "bytes"):
            if isinstance(chunk.get(key), bytes):
                return chunk[key]
    return bytes(chunk)


async def transcribe_with_a0_provider(audio: bytes, metadata: dict[str, Any], provider_factory: ProviderFactory) -> str:
    """Transcribe Wyoming PCM bytes with a pluggable A0 ASR provider factory."""
    provider = await _maybe_await(provider_factory())
    if provider is None:
        return ""
    request = {
        "audio": audio,
        "audio_bytes": audio,
        "rate": int(metadata.get("rate") or metadata.get("sample_rate") or 16000),
        "width": int(metadata.get("width") or 2),
        "channels": int(metadata.get("channels") or 1),
        "codec": str(metadata.get("codec") or "pcm16"),
        "utterance_id": str(metadata.get("utterance_id") or uuid.uuid4()),
        "ctxid": str(metadata.get("ctxid") or ""),
        "interface_id": str(metadata.get("interface_id") or ""),
    }
    for method_name in ("transcribe_bytes", "transcribe_audio", "transcribe", "recognize"):
        method = getattr(provider, method_name, None)
        if method is None:
            continue
        try:
            return await _collect_text_result(method(**request))
        except TypeError:
            try:
                return await _collect_text_result(method(audio, request))
            except TypeError:
                return await _collect_text_result(method(audio))
    raise RuntimeError(f"ASR provider {provider!r} has no supported transcribe method")


async def synthesize_with_a0_provider(text: str, metadata: dict[str, Any], provider_factory: ProviderFactory):
    """Yield Wyoming audio bytes from a pluggable A0 TTS provider factory."""
    provider = await _maybe_await(provider_factory())
    if provider is None:
        return
    request = {
        "text": text,
        "utterance_id": str(metadata.get("generation_id") or uuid.uuid4()),
        "ctxid": str(metadata.get("ctxid") or ""),
        "interface_id": str(metadata.get("interface_id") or ""),
        "codec": str(metadata.get("codec") or "pcm16"),
    }
    for method_name in ("synthesize_stream", "synthesize", "speak"):
        method = getattr(provider, method_name, None)
        if method is None:
            continue
        try:
            result = method(**request)
        except TypeError:
            try:
                result = method(text, request)
            except TypeError:
                result = method(text)
        async for chunk in _iter_any_chunks(result):
            yield _chunk_to_bytes(chunk)
        return
    raise RuntimeError(f"TTS provider {provider!r} has no supported synthesize method")


async def submit_prompt_with_a0_context(text: str, metadata: dict[str, Any], submitter: PromptSubmitter):
    """Submit prompt text to a fixed ctxID through a pluggable A0 submitter."""
    return await _maybe_await(submitter(text, metadata))


def build_a0_asr_adapter(provider_factory: ProviderFactory) -> WyomingAsrAdapter:
    async def provider(audio: bytes, metadata: dict[str, Any]) -> str:
        return await transcribe_with_a0_provider(audio, metadata, provider_factory)
    return WyomingAsrAdapter(provider)


def build_a0_prompt_adapter(submitter: PromptSubmitter) -> WyomingPromptAdapter:
    async def provider(text: str, metadata: dict[str, Any]):
        return await submit_prompt_with_a0_context(text, metadata, submitter)
    return WyomingPromptAdapter(provider)


def build_a0_tts_adapter(provider_factory: ProviderFactory) -> WyomingTtsAdapter:
    async def provider(text: str, metadata: dict[str, Any]):
        async for chunk in synthesize_with_a0_provider(text, metadata, provider_factory):
            yield chunk
    return WyomingTtsAdapter(provider)


def adapter_status(*, asr_provider_factory: ProviderFactory | None = None, tts_provider_factory: ProviderFactory | None = None, prompt_submitter: PromptSubmitter | None = None) -> dict[str, Any]:
    status = WyomingA0AdapterStatus(
        asr_provider_factory=asr_provider_factory is not None,
        tts_provider_factory=tts_provider_factory is not None,
        prompt_submitter=prompt_submitter is not None,
    )
    return {
        "mode": status.mode,
        "asr_provider_factory": status.asr_provider_factory,
        "tts_provider_factory": status.tts_provider_factory,
        "prompt_submitter": status.prompt_submitter,
    }
