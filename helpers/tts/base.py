from __future__ import annotations

import abc
import asyncio
import dataclasses
import hashlib
import inspect
import time
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from typing import Any, Literal


TTSAudioFormat = Literal["pcm16/16k", "pcm16/24k", "opus", "mp3", "mulaw/8k"]


@dataclasses.dataclass(frozen=True)
class TTSProviderSpec:
    """Normalized provider configuration used by concrete TTS adapters."""

    name: str
    type: str = "mock"
    model: str | None = None
    voice: str | None = None
    endpoint: str | None = None
    api_key_env: str | None = None
    sample_rate: int = 16000
    streaming: bool = True
    options: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None, *, default_name: str = "mock-tts") -> "TTSProviderSpec":
        raw = dict(config or {})
        options = dict(raw.pop("options", {}) or {})
        known = {field.name for field in dataclasses.fields(cls)}
        extra = {key: raw.pop(key) for key in list(raw.keys()) if key not in known}
        options.update(extra)
        return cls(
            name=str(raw.pop("name", default_name)),
            type=str(raw.pop("type", "mock")),
            model=raw.pop("model", None),
            voice=raw.pop("voice", None),
            endpoint=raw.pop("endpoint", None),
            api_key_env=raw.pop("api_key_env", None),
            sample_rate=int(raw.pop("sample_rate", 16000)),
            streaming=bool(raw.pop("streaming", True)),
            options=options,
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class TTSCapabilities:
    """JSON-safe provider capability metadata for voqualizer_ready/admin output."""

    provider: str
    streaming: bool
    output_codecs: tuple[str, ...] = ("pcm16/16k",)
    sample_rates: tuple[int, ...] = (16000,)
    voices: tuple[str, ...] = ("mock",)
    languages: tuple[str, ...] = ("en",)
    max_text_chars: int = 4000
    supports_cancellation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "streaming": self.streaming,
            "output_codecs": list(self.output_codecs),
            "sample_rates": list(self.sample_rates),
            "voices": list(self.voices),
            "languages": list(self.languages),
            "max_text_chars": self.max_text_chars,
            "supports_cancellation": self.supports_cancellation,
        }


@dataclasses.dataclass(frozen=True)
class TTSRequest:
    """A single text-to-speech synthesis request."""

    text: str
    utterance_id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex)
    voice: str | None = None
    codec: str = "pcm16/16k"
    sample_rate: int = 16000
    speed: float = 1.0
    language: str | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not self.text:
            raise ValueError("text must not be empty")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.speed <= 0:
            raise ValueError("speed must be positive")

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class AudioChunk:
    """A streaming TTS audio chunk destined for voqualizer_tts_chunk."""

    data: bytes
    seq: int
    utterance_id: str
    codec: str = "pcm16/16k"
    sample_rate: int = 16000
    is_final: bool = False
    duration_ms: int | None = None
    created_at: float = dataclasses.field(default_factory=time.time)
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        object.__setattr__(self, "data", bytes(self.data))
        if self.seq < 0:
            raise ValueError("seq must be non-negative")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")

    def event_payload(self) -> dict[str, Any]:
        """JSON-safe metadata paired with the binary chunk frame."""
        return {
            "event": "voqualizer_tts_chunk",
            "seq": self.seq,
            "utterance_id": self.utterance_id,
            "codec": self.codec,
            "sample_rate": self.sample_rate,
            "is_final": self.is_final,
            "duration_ms": self.duration_ms,
            "metadata": dict(self.metadata),
        }


class TTSError(Exception):
    """Base TTS error with a JSON-safe error representation."""

    def __init__(self, message: str, *, code: str = "TTS_ERROR", recoverable: bool = True, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.recoverable = recoverable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
            "details": dict(self.details),
        }


class TTSUnavailableError(TTSError):
    """Raised when a provider dependency/model/credential is unavailable."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message, code="TTS_UNAVAILABLE", recoverable=True, details=details)


class TTSCancelledError(TTSError):
    """Raised when synthesis is cancelled by barge-in or caller cancellation."""

    def __init__(self, message: str = "TTS synthesis cancelled", *, details: dict[str, Any] | None = None):
        super().__init__(message, code="TTS_CANCELLED", recoverable=True, details=details)


async def iter_text_chunks(chunks: str | Iterable[str] | AsyncIterable[str]) -> AsyncIterator[str]:
    """Normalize sync/async text inputs into an async text iterator."""
    if isinstance(chunks, str):
        yield chunks
        return
    if hasattr(chunks, "__aiter__"):
        async for chunk in chunks:  # type: ignore[union-attr]
            if chunk:
                yield str(chunk)
        return
    for chunk in chunks:  # type: ignore[union-attr]
        if inspect.isawaitable(chunk):
            chunk = await chunk
        if chunk:
            yield str(chunk)


class TTSProvider(abc.ABC):
    """Stable async provider contract for all Voqualizer TTS adapters."""

    def __init__(self, spec: TTSProviderSpec | dict[str, Any] | None = None):
        self.spec = spec if isinstance(spec, TTSProviderSpec) else TTSProviderSpec.from_config(spec)
        self.started = False

    @property
    @abc.abstractmethod
    def capabilities(self) -> TTSCapabilities:
        """Return provider capabilities."""

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    @abc.abstractmethod
    async def stream(self, request: TTSRequest) -> AsyncIterator[AudioChunk]:
        """Stream synthesized audio chunks for a request."""
        if False:  # pragma: no cover - makes this an async generator for typing
            yield AudioChunk(data=b"", seq=0, utterance_id=request.utterance_id)

    async def synthesize(self, request: TTSRequest) -> list[AudioChunk]:
        """Collect the streaming output into a deterministic list."""
        return [chunk async for chunk in self.stream(request)]


class MockTTSProvider(TTSProvider):
    """Deterministic TTS provider for unit/integration tests; no secrets/network/models."""

    def __init__(self, spec: TTSProviderSpec | dict[str, Any] | None = None, *, chunk_size: int = 8):
        super().__init__(spec or {"name": "mock-tts", "type": "mock", "voice": "mock", "streaming": True})
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size

    @property
    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(
            provider=self.spec.name,
            streaming=True,
            output_codecs=("pcm16/16k", "pcm16/24k", "mp3", "mulaw/8k"),
            sample_rates=(16000, 24000, 8000),
            voices=(self.spec.voice or "mock", "mock-alt"),
            languages=("en",),
            supports_cancellation=True,
        )

    def _audio_for_text(self, request: TTSRequest) -> bytes:
        seed = f"{request.text}|{request.voice or self.spec.voice or 'mock'}|{request.codec}|{request.sample_rate}|{request.speed}".encode("utf-8")
        digest = hashlib.sha256(seed).digest()
        # Keep deterministic output size text-dependent but bounded for small fast tests.
        repeats = max(2, min(64, len(request.text.encode("utf-8"))))
        return (digest * ((repeats // len(digest)) + 1))[:repeats]

    async def stream(self, request: TTSRequest) -> AsyncIterator[AudioChunk]:
        audio = self._audio_for_text(request)
        total = (len(audio) + self.chunk_size - 1) // self.chunk_size
        for seq, offset in enumerate(range(0, len(audio), self.chunk_size)):
            await asyncio.sleep(0)
            yield AudioChunk(
                data=audio[offset : offset + self.chunk_size],
                seq=seq,
                utterance_id=request.utterance_id,
                codec=request.codec,
                sample_rate=request.sample_rate,
                is_final=seq == total - 1,
                duration_ms=20,
                metadata={"provider": self.spec.name, "voice": request.voice or self.spec.voice or "mock"},
            )
