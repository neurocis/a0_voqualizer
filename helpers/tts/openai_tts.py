"""Hosted OpenAI TTS adapter for a0_voqualizer.

Targets OpenAI's ``/v1/audio/speech`` endpoint while keeping tests fully
offline through injectable HTTP session factories/fake sessions.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import AsyncIterator, Mapping
from typing import Any, Callable

from .base import AudioChunk, TTSCapabilities, TTSError, TTSProvider, TTSProviderSpec, TTSRequest, TTSUnavailableError


DEFAULT_OPENAI_TTS_ENDPOINT = "https://api.openai.com/v1/audio/speech"
DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_TTS_VOICE = "alloy"

SessionFactory = Callable[..., Any]


_CODEC_TO_OPENAI_FORMAT = {
    "mp3": "mp3",
    "opus": "opus",
    "pcm16/16k": "pcm",
    "pcm16/24k": "pcm",
    "mulaw/8k": "pcm",
}

_FORMAT_TO_CODEC = {
    "mp3": "mp3",
    "opus": "opus",
    "pcm": "pcm16/24k",
    "wav": "pcm16/24k",
}


class OpenAITTSProvider(TTSProvider):
    """Hosted OpenAI text-to-speech provider.

    The adapter uses JSON POST requests and normalizes returned bytes/streamed
    bytes into A4.1 ``AudioChunk`` instances. It does not require network/API
    credentials in tests because callers can inject ``api_key`` and
    ``session_factory``.
    """

    def __init__(
        self,
        spec: TTSProviderSpec | Mapping[str, Any] | None = None,
        *,
        api_key: str | None = None,
        session_factory: SessionFactory | None = None,
        chunk_size: int = 4096,
    ) -> None:
        super().__init__(
            spec
            or {
                "name": "openai-tts",
                "type": "openai",
                "endpoint": DEFAULT_OPENAI_TTS_ENDPOINT,
                "model": DEFAULT_OPENAI_TTS_MODEL,
                "voice": DEFAULT_OPENAI_TTS_VOICE,
                "api_key_env": "OPENAI_API_KEY",
                "sample_rate": 24000,
                "streaming": True,
            }
        )
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self._explicit_api_key = api_key
        self._session_factory = session_factory
        self._session: Any | None = None
        self._owns_session = False
        self._start_lock = asyncio.Lock()
        self.chunk_size = chunk_size
        self.last_request: dict[str, Any] | None = None

    @property
    def endpoint(self) -> str:
        return self.spec.endpoint or DEFAULT_OPENAI_TTS_ENDPOINT

    @property
    def model_name(self) -> str:
        return self.spec.model or DEFAULT_OPENAI_TTS_MODEL

    @property
    def voice_name(self) -> str:
        return self.spec.voice or DEFAULT_OPENAI_TTS_VOICE

    @property
    def api_key_env(self) -> str:
        return self.spec.api_key_env or "OPENAI_API_KEY"

    @property
    def timeout(self) -> float | None:
        value = self.spec.options.get("timeout")
        return None if value is None else float(value)

    @property
    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(
            provider=self.spec.name,
            streaming=True,
            output_codecs=("pcm16/24k", "pcm16/16k", "mp3", "opus"),
            sample_rates=(24000, 16000),
            voices=(self.voice_name, "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"),
            languages=("auto",),
            max_text_chars=int(self.spec.options.get("max_text_chars", 4096)),
            supports_cancellation=True,
        )

    def _api_key(self) -> str:
        key = self._explicit_api_key or os.environ.get(self.api_key_env)
        if not key:
            raise TTSUnavailableError(
                "OpenAI TTS API key is not configured",
                details={"provider": self.spec.name, "api_key_env": self.api_key_env},
            )
        return key

    async def start(self) -> None:
        async with self._start_lock:
            if self._session is not None:
                self.started = True
                return
            if self._session_factory is not None:
                self._session = self._session_factory()
                self._owns_session = True
                self.started = True
                return
            try:
                import aiohttp  # type: ignore
            except Exception as exc:  # pragma: no cover - dependency is optional at test time
                raise TTSUnavailableError(
                    "aiohttp is required for OpenAI TTS",
                    details={"provider": self.spec.name, "dependency": "aiohttp"},
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.timeout is not None:
                kwargs["timeout"] = self.timeout
            self._session = aiohttp.ClientSession(**kwargs)
            self._owns_session = True
            self.started = True

    async def stop(self) -> None:
        session = self._session
        self._session = None
        self.started = False
        if self._owns_session and session is not None:
            close = getattr(session, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
        self._owns_session = False

    def _response_format_for_request(self, request: TTSRequest) -> str:
        explicit = request.metadata.get("response_format") if isinstance(request.metadata, Mapping) else None
        explicit = explicit or self.spec.options.get("response_format") or self.spec.options.get("format")
        if explicit:
            return str(explicit)
        try:
            return _CODEC_TO_OPENAI_FORMAT[request.codec]
        except KeyError as exc:
            raise TTSError(
                "OpenAI TTS output codec is not supported",
                code="TTS_UNSUPPORTED_CODEC",
                recoverable=True,
                details={"provider": self.spec.name, "codec": request.codec},
            ) from exc

    def _codec_for_response_format(self, fmt: str, request: TTSRequest) -> str:
        if request.codec in _CODEC_TO_OPENAI_FORMAT:
            return request.codec
        return _FORMAT_TO_CODEC.get(fmt, request.codec)

    def _build_payload(self, request: TTSRequest) -> dict[str, Any]:
        fmt = self._response_format_for_request(request)
        payload: dict[str, Any] = {
            "model": self.model_name,
            "voice": request.voice or self.voice_name,
            "input": request.text,
            "response_format": fmt,
        }
        if request.speed and request.speed != 1.0:
            payload["speed"] = request.speed
        instructions = request.metadata.get("instructions") if isinstance(request.metadata, Mapping) else None
        if instructions:
            payload["instructions"] = str(instructions)
        extra = self.spec.options.get("extra_body")
        if isinstance(extra, Mapping):
            payload.update(dict(extra))
        return payload

    async def stream(self, request: TTSRequest) -> AsyncIterator[AudioChunk]:
        api_key = self._api_key()
        if not self.started:
            await self.start()
        payload = self._build_payload(request)
        fmt = str(payload.get("response_format", "pcm"))
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        raw_chunks: list[bytes] = []
        try:
            async for part in self._post_speech(payload, headers):
                if not part:
                    continue
                for offset in range(0, len(part), self.chunk_size):
                    piece = part[offset : offset + self.chunk_size]
                    if piece:
                        raw_chunks.append(piece)
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(
                "OpenAI TTS transport failed",
                code="TTS_TRANSPORT_ERROR",
                recoverable=True,
                details={"provider": self.spec.name, "error": str(exc)},
            ) from exc

        final_index = len(raw_chunks) - 1
        for seq, piece in enumerate(raw_chunks):
            await asyncio.sleep(0)
            yield AudioChunk(
                data=piece,
                seq=seq,
                utterance_id=request.utterance_id,
                codec=self._codec_for_response_format(fmt, request),
                sample_rate=request.sample_rate,
                is_final=seq == final_index,
                duration_ms=None,
                metadata={"provider": self.spec.name, "voice": payload["voice"], "model": self.model_name, "format": fmt},
            )

    async def synthesize(self, request: TTSRequest) -> list[AudioChunk]:
        return [chunk async for chunk in self.stream(request)]

    async def _post_speech(self, payload: Mapping[str, Any], headers: Mapping[str, str]) -> AsyncIterator[bytes]:
        if self._session is None:
            await self.start()
        assert self._session is not None
        self.last_request = {"url": self.endpoint, "headers": dict(headers), "json": dict(payload)}
        kwargs: dict[str, Any] = {"json": dict(payload), "headers": dict(headers)}
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        request = self._session.post(self.endpoint, **kwargs)
        if hasattr(request, "__aenter__"):
            async with request as response:
                async for item in self._parse_response_to_bytes(response):
                    yield item
            return
        response = await request if inspect.isawaitable(request) else request
        async for item in self._parse_response_to_bytes(response):
            yield item

    async def _parse_response_to_bytes(self, response: Any) -> AsyncIterator[bytes]:
        status = int(getattr(response, "status", 200))
        if status >= 400:
            body = await self._response_text(response)
            raise TTSError(
                f"OpenAI TTS request failed with HTTP {status}",
                code="TTS_HTTP_ERROR",
                recoverable=status < 500,
                details={"status": status, "body": body, "provider": self.spec.name},
            )

        content = getattr(response, "content", None)
        iter_chunked = getattr(content, "iter_chunked", None) if content is not None else None
        if iter_chunked is not None:
            async for part in iter_chunked(self.chunk_size):
                if part:
                    yield bytes(part)
            return

        read = getattr(response, "read", None)
        if read is not None:
            result = read()
            data = await result if inspect.isawaitable(result) else result
            if data:
                yield bytes(data)
            return

        data = getattr(response, "data", None)
        if isinstance(data, (bytes, bytearray, memoryview)):
            yield bytes(data)
            return

        raise TTSError("OpenAI TTS response did not contain audio bytes", code="TTS_BAD_RESPONSE", recoverable=True, details={"provider": self.spec.name})

    async def _response_text(self, response: Any) -> str:
        text = getattr(response, "text", None)
        if text is not None:
            result = text()
            return str(await result if inspect.isawaitable(result) else result)
        read = getattr(response, "read", None)
        if read is not None:
            result = read()
            body = await result if inspect.isawaitable(result) else result
            if isinstance(body, (bytes, bytearray, memoryview)):
                return bytes(body).decode("utf-8", errors="replace")
            return str(body)
        return ""


__all__ = [
    "OpenAITTSProvider",
    "DEFAULT_OPENAI_TTS_ENDPOINT",
    "DEFAULT_OPENAI_TTS_MODEL",
    "DEFAULT_OPENAI_TTS_VOICE",
]
