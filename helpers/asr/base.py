"""Base ASR adapter interface for a0_voqualizer.

This module defines the stable provider contract used by local Whisper,
OpenAI Whisper, OpenAI-compatible/LocalAI, and the WebSocket audio ingress
pipeline.  Concrete providers may be true-streaming or batch/final-only, but
all expose the same async ``stream()`` and ``transcribe()`` surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterable, AsyncIterator, Iterable, Mapping, Sequence


class ASRError(Exception):
    """JSON-safe ASR provider error.

    ``code`` is intended to map directly into ``voqualizer_error.code``.
    ``recoverable`` lets the WS layer decide whether a session can continue.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "ASR_ERROR",
        recoverable: bool = True,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.recoverable = bool(recoverable)
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
            "details": dict(self.details),
        }


class ASRUnavailableError(ASRError):
    """Raised when a provider cannot be used in the current environment."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="ASR_UNAVAILABLE", recoverable=True, details=details)


class TranscriptKind(str, Enum):
    """Transcript event kind emitted by ASR providers."""

    PARTIAL = "partial"
    FINAL = "final"


@dataclass(frozen=True)
class ASRProviderSpec:
    """Configuration/specification for one ASR provider."""

    name: str
    type: str
    model: str | None = None
    language: str = "auto"
    streaming: bool = False
    endpoint: str | None = None
    api_key_env: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "ASRProviderSpec":
        if not isinstance(config, Mapping):
            raise ASRError("provider config must be a mapping", code="BAD_ASR_CONFIG", recoverable=False)
        name = config.get("name")
        provider_type = config.get("type")
        if not isinstance(name, str) or not name.strip():
            raise ASRError("provider config requires non-empty name", code="BAD_ASR_CONFIG", recoverable=False)
        if not isinstance(provider_type, str) or not provider_type.strip():
            raise ASRError("provider config requires non-empty type", code="BAD_ASR_CONFIG", recoverable=False)
        known = {"name", "type", "model", "language", "streaming", "endpoint", "api_key_env"}
        options = {k: v for k, v in config.items() if k not in known}
        return cls(
            name=name,
            type=provider_type,
            model=config.get("model"),
            language=str(config.get("language", "auto") or "auto"),
            streaming=bool(config.get("streaming", False)),
            endpoint=config.get("endpoint"),
            api_key_env=config.get("api_key_env"),
            options=options,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "language": self.language,
            "streaming": self.streaming,
        }
        if self.model is not None:
            data["model"] = self.model
        if self.endpoint is not None:
            data["endpoint"] = self.endpoint
        if self.api_key_env is not None:
            data["api_key_env"] = self.api_key_env
        data.update(dict(self.options))
        return data


@dataclass(frozen=True)
class ASRCapabilities:
    """Provider capability metadata surfaced to admin/WS layers."""

    provider: str
    streaming: bool
    languages: Sequence[str] = ("auto",)
    input_sample_rates: Sequence[int] = (16000,)
    input_codecs: Sequence[str] = ("pcm16/16k",)
    partials: bool = False
    finals: bool = True
    word_timestamps: bool = False
    confidence: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "streaming": self.streaming,
            "languages": list(self.languages),
            "input_sample_rates": list(self.input_sample_rates),
            "input_codecs": list(self.input_codecs),
            "partials": self.partials,
            "finals": self.finals,
            "word_timestamps": self.word_timestamps,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class AudioChunk:
    """Normalized ASR input audio chunk.

    Audio bytes are PCM16 little-endian mono unless a concrete provider explicitly
    documents otherwise.  The M2 codec layer is responsible for normalizing
    protocol ingress before it reaches ASR providers.
    """

    pcm16: bytes
    sample_rate: int = 16000
    seq: int | None = None
    ts_ms: int | None = None
    is_final: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.pcm16, (bytes, bytearray, memoryview)):
            raise TypeError("pcm16 must be bytes-like")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if len(bytes(self.pcm16)) % 2 != 0:
            raise ValueError("pcm16 byte length must be even")
        object.__setattr__(self, "pcm16", bytes(self.pcm16))


@dataclass(frozen=True)
class TranscriptResult:
    """ASR transcript result/event shape.

    ``to_protocol_event()`` maps directly to the v1 WS event taxonomy:
    ``voqualizer_asr_partial`` or ``voqualizer_asr_final``.
    """

    text: str
    kind: TranscriptKind = TranscriptKind.FINAL
    confidence: float | None = None
    t_start: float | None = None
    t_end: float | None = None
    language: str | None = None
    provider: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not isinstance(self.kind, TranscriptKind):
            object.__setattr__(self, "kind", TranscriptKind(self.kind))
        if self.confidence is not None:
            conf = float(self.confidence)
            if conf < 0.0 or conf > 1.0:
                raise ValueError("confidence must be between 0.0 and 1.0")
            object.__setattr__(self, "confidence", conf)

    @property
    def is_partial(self) -> bool:
        return self.kind is TranscriptKind.PARTIAL

    @property
    def is_final(self) -> bool:
        return self.kind is TranscriptKind.FINAL

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": self.text,
            "conf": self.confidence,
            "t_start": self.t_start,
            "t_end": self.t_end,
        }
        if self.is_final:
            payload["lang"] = self.language
        if self.provider is not None:
            payload["provider"] = self.provider
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    def to_protocol_event(self) -> dict[str, Any]:
        return {
            "event": "voqualizer_asr_partial" if self.is_partial else "voqualizer_asr_final",
            **self.to_payload(),
        }


class ASRProvider(ABC):
    """Abstract ASR adapter contract."""

    def __init__(self, spec: ASRProviderSpec | Mapping[str, Any]) -> None:
        self.spec = spec if isinstance(spec, ASRProviderSpec) else ASRProviderSpec.from_config(spec)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def provider_type(self) -> str:
        return self.spec.type

    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(
            provider=self.name,
            streaming=self.spec.streaming,
            languages=(self.spec.language,) if self.spec.language != "auto" else ("auto",),
            partials=self.spec.streaming,
        )

    async def start(self) -> None:
        """Optional provider lifecycle hook."""

    async def close(self) -> None:
        """Optional provider lifecycle hook."""

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes | AudioChunk | Sequence[AudioChunk],
        *,
        language: str | None = None,
        sample_rate: int = 16000,
        metadata: Mapping[str, Any] | None = None,
    ) -> TranscriptResult:
        """Return one final transcript for the given audio."""

    @abstractmethod
    async def stream(
        self,
        chunks: AsyncIterable[AudioChunk] | Iterable[AudioChunk],
        *,
        language: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[TranscriptResult]:
        """Yield partial/final transcript results for incoming chunks."""


async def iter_audio_chunks(chunks: AsyncIterable[AudioChunk] | Iterable[AudioChunk]) -> AsyncIterator[AudioChunk]:
    """Normalize sync or async chunk iterables into an async iterator."""

    if hasattr(chunks, "__aiter__"):
        async for chunk in chunks:  # type: ignore[union-attr]
            if not isinstance(chunk, AudioChunk):
                raise TypeError("ASR stream chunks must be AudioChunk instances")
            yield chunk
    else:
        for chunk in chunks:  # type: ignore[union-attr]
            if not isinstance(chunk, AudioChunk):
                raise TypeError("ASR stream chunks must be AudioChunk instances")
            yield chunk


class MockASRProvider(ASRProvider):
    """Deterministic mock provider used by unit/integration tests."""

    def __init__(
        self,
        spec: ASRProviderSpec | Mapping[str, Any] | None = None,
        *,
        final_text: str = "mock transcript",
        partial_prefix: str = "partial",
    ) -> None:
        super().__init__(spec or {"name": "mock-asr", "type": "mock", "streaming": True, "language": "auto"})
        self.final_text = final_text
        self.partial_prefix = partial_prefix
        self.started = False
        self.closed = False
        self.feed_count = 0

    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(
            provider=self.name,
            streaming=True,
            languages=("auto", "en"),
            input_sample_rates=(8000, 16000, 24000),
            input_codecs=("pcm16/8k", "pcm16/16k", "pcm16/24k"),
            partials=True,
            finals=True,
        )

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def transcribe(
        self,
        audio: bytes | AudioChunk | Sequence[AudioChunk],
        *,
        language: str | None = None,
        sample_rate: int = 16000,
        metadata: Mapping[str, Any] | None = None,
    ) -> TranscriptResult:
        if isinstance(audio, AudioChunk):
            duration_ms = len(audio.pcm16) / 2 / audio.sample_rate * 1000.0
        elif isinstance(audio, Sequence) and not isinstance(audio, (bytes, bytearray, memoryview)):
            duration_ms = sum(len(chunk.pcm16) / 2 / chunk.sample_rate * 1000.0 for chunk in audio)
        else:
            duration_ms = len(bytes(audio)) / 2 / sample_rate * 1000.0
        return TranscriptResult(
            text=self.final_text,
            kind=TranscriptKind.FINAL,
            confidence=1.0,
            t_start=0.0,
            t_end=round(duration_ms / 1000.0, 3),
            language=language or self.spec.language or "auto",
            provider=self.name,
            metadata=dict(metadata or {}),
        )

    async def stream(
        self,
        chunks: AsyncIterable[AudioChunk] | Iterable[AudioChunk],
        *,
        language: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[TranscriptResult]:
        buffered: list[AudioChunk] = []
        async for chunk in iter_audio_chunks(chunks):
            self.feed_count += 1
            buffered.append(chunk)
            yield TranscriptResult(
                text=f"{self.partial_prefix} {self.feed_count}",
                kind=TranscriptKind.PARTIAL,
                confidence=0.5,
                t_start=0.0,
                t_end=(chunk.ts_ms / 1000.0) if chunk.ts_ms is not None else None,
                language=None,
                provider=self.name,
                metadata={"seq": chunk.seq} if chunk.seq is not None else {},
            )
        yield await self.transcribe(buffered, language=language, metadata=metadata)


__all__ = [
    "ASRCapabilities",
    "ASRError",
    "ASRProvider",
    "ASRProviderSpec",
    "ASRUnavailableError",
    "AudioChunk",
    "MockASRProvider",
    "TranscriptKind",
    "TranscriptResult",
    "iter_audio_chunks",
]
