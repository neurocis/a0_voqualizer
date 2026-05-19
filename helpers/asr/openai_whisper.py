"""OpenAI Whisper ASR adapter for a0_voqualizer.

This provider targets OpenAI's hosted transcription API while keeping tests fully
offline via injectable HTTP session factories. Audio is normalized to PCM16/16k
and wrapped as a WAV file upload.
"""

from __future__ import annotations

import asyncio
import io
import os
import wave
from typing import Any, AsyncIterable, AsyncIterator, Callable, Iterable, Mapping, Sequence

from ..codec import CodecError, ensure_pcm16_bytes, resample_pcm16
from .base import (
    ASRCapabilities,
    ASRError,
    ASRProvider,
    ASRProviderSpec,
    ASRUnavailableError,
    AudioChunk,
    TranscriptKind,
    TranscriptResult,
    iter_audio_chunks,
)


DEFAULT_OPENAI_TRANSCRIPTIONS_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_OPENAI_WHISPER_MODEL = "whisper-1"

SessionFactory = Callable[..., Any]


class OpenAIWhisperASRProvider(ASRProvider):
    """OpenAI-hosted Whisper transcription adapter.

    Streaming semantics for M3:
    - OpenAI Whisper transcription is final/batch in this adapter.
    - ``stream()`` buffers incoming audio, emits lightweight partial placeholders
      so the WS layer can prove event plumbing, then emits the final transcript.
    """

    def __init__(
        self,
        spec: ASRProviderSpec | Mapping[str, Any] | None = None,
        *,
        api_key: str | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        super().__init__(
            spec
            or {
                "name": "openai-whisper",
                "type": "openai",
                "endpoint": DEFAULT_OPENAI_TRANSCRIPTIONS_ENDPOINT,
                "model": DEFAULT_OPENAI_WHISPER_MODEL,
                "api_key_env": "OPENAI_API_KEY",
                "streaming": False,
                "language": "auto",
            }
        )
        self._explicit_api_key = api_key
        self._session_factory = session_factory
        self._started = False
        self._session: Any | None = None
        self._owns_session = False
        self._start_lock = asyncio.Lock()
        self.last_request: dict[str, Any] | None = None

    @property
    def endpoint(self) -> str:
        return self.spec.endpoint or DEFAULT_OPENAI_TRANSCRIPTIONS_ENDPOINT

    @property
    def model_name(self) -> str:
        return self.spec.model or DEFAULT_OPENAI_WHISPER_MODEL

    @property
    def api_key_env(self) -> str:
        return self.spec.api_key_env or "OPENAI_API_KEY"

    @property
    def is_started(self) -> bool:
        return self._started

    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(
            provider=self.name,
            streaming=False,
            languages=("auto", "en", "es", "fr", "de", "it", "pt", "nl", "ja", "zh", "ko", "ru"),
            input_sample_rates=(8000, 16000, 24000),
            input_codecs=("pcm16/8k", "pcm16/16k", "pcm16/24k"),
            partials=False,
            finals=True,
            word_timestamps=False,
            confidence=False,
        )

    def _api_key(self) -> str:
        key = self._explicit_api_key or os.environ.get(self.api_key_env)
        if not key:
            raise ASRUnavailableError(
                "OpenAI Whisper API key is not configured",
                details={"provider": self.name, "api_key_env": self.api_key_env},
            )
        return key

    async def start(self) -> None:
        async with self._start_lock:
            if self._session is not None:
                self._started = True
                return
            if self._session_factory is not None:
                self._session = self._session_factory()
                self._owns_session = True
                self._started = True
                return
            try:
                import aiohttp  # type: ignore
            except Exception as exc:  # pragma: no cover - aiohttp present in runtime requirements
                raise ASRUnavailableError(
                    "aiohttp is required for OpenAI Whisper ASR",
                    details={"provider": self.name, "dependency": "aiohttp"},
                ) from exc
            self._session = aiohttp.ClientSession()
            self._owns_session = True
            self._started = True

    async def close(self) -> None:
        session = self._session
        self._session = None
        self._started = False
        if self._owns_session and session is not None:
            close = getattr(session, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
        self._owns_session = False

    def _language_arg(self, language: str | None) -> str | None:
        lang = language or self.spec.language or "auto"
        return None if lang == "auto" else lang

    def _chunks_to_pcm16_16k(self, audio: bytes | AudioChunk | Sequence[AudioChunk], *, sample_rate: int) -> bytes:
        if isinstance(audio, AudioChunk):
            raw = ensure_pcm16_bytes(audio.pcm16)
            src_rate = audio.sample_rate
        elif isinstance(audio, Sequence) and not isinstance(audio, (bytes, bytearray, memoryview)):
            parts: list[bytes] = []
            for chunk in audio:
                if not isinstance(chunk, AudioChunk):
                    raise ASRError("audio sequence must contain AudioChunk items", code="BAD_ASR_AUDIO")
                part = ensure_pcm16_bytes(chunk.pcm16)
                if chunk.sample_rate != 16000:
                    part = resample_pcm16(part, chunk.sample_rate, 16000)
                parts.append(part)
            return b"".join(parts)
        else:
            raw = ensure_pcm16_bytes(audio)  # type: ignore[arg-type]
            src_rate = sample_rate
        if src_rate != 16000:
            return resample_pcm16(raw, src_rate, 16000)
        return raw

    @staticmethod
    def _pcm16_to_wav(pcm16: bytes, *, sample_rate: int = 16000) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm16)
        return buf.getvalue()

    def _build_form_fields(self, wav_bytes: bytes, *, language: str | None, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "model": self.model_name,
            "response_format": "json",
            "file": {
                "filename": "audio.wav",
                "content_type": "audio/wav",
                "bytes": wav_bytes,
            },
        }
        lang = self._language_arg(language)
        if lang is not None:
            fields["language"] = lang
        prompt = (metadata or {}).get("prompt") if metadata else None
        if prompt:
            fields["prompt"] = str(prompt)
        asr_options = dict(self.spec.options.get("asr_options", {}) or {})
        for key in ("temperature", "no_speech_threshold", "compression_ratio_threshold", "logprob_threshold", "suppress_tokens"):
            if key in self.spec.options and key not in asr_options:
                asr_options[key] = self.spec.options[key]
        for key, value in asr_options.items():
            if value is not None and value != "":
                fields[str(key)] = str(value)
        return fields

    @staticmethod
    def _fields_to_aiohttp_form(fields: Mapping[str, Any]) -> Any:
        try:
            import aiohttp  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ASRUnavailableError("aiohttp is required to build OpenAI multipart form") from exc
        form = aiohttp.FormData()
        for key, value in fields.items():
            if key == "file":
                form.add_field(
                    "file",
                    value["bytes"],
                    filename=value["filename"],
                    content_type=value["content_type"],
                )
            else:
                form.add_field(key, str(value))
        return form

    async def _post_transcription(self, fields: Mapping[str, Any], headers: Mapping[str, str]) -> Mapping[str, Any]:
        if self._session is None:
            await self.start()
        assert self._session is not None
        self.last_request = {"url": self.endpoint, "headers": dict(headers), "fields": dict(fields)}
        data = self._fields_to_aiohttp_form(fields)
        request = self._session.post(self.endpoint, data=data, headers=dict(headers))
        if hasattr(request, "__aenter__"):
            async with request as response:
                return await self._parse_response(response)
        response = await request if hasattr(request, "__await__") else request
        return await self._parse_response(response)

    async def _parse_response(self, response: Any) -> Mapping[str, Any]:
        status = int(getattr(response, "status", 200))
        if status >= 400:
            body = ""
            text = getattr(response, "text", None)
            if text is not None:
                result = text()
                body = await result if hasattr(result, "__await__") else str(result)
            raise ASRError(
                f"OpenAI Whisper transcription failed with HTTP {status}",
                code="ASR_HTTP_ERROR",
                recoverable=status < 500,
                details={"status": status, "body": body},
            )
        json_func = getattr(response, "json", None)
        if json_func is None:
            raise ASRError("OpenAI Whisper response missing json()", code="ASR_BAD_RESPONSE")
        result = json_func()
        payload = await result if hasattr(result, "__await__") else result
        if not isinstance(payload, Mapping):
            raise ASRError("OpenAI Whisper response JSON must be an object", code="ASR_BAD_RESPONSE")
        return payload

    async def transcribe(
        self,
        audio: bytes | AudioChunk | Sequence[AudioChunk],
        *,
        language: str | None = None,
        sample_rate: int = 16000,
        metadata: Mapping[str, Any] | None = None,
    ) -> TranscriptResult:
        api_key = self._api_key()
        try:
            pcm16 = self._chunks_to_pcm16_16k(audio, sample_rate=sample_rate)
        except CodecError as exc:
            raise ASRError(str(exc), code="BAD_ASR_AUDIO", recoverable=True) from exc
        wav_bytes = self._pcm16_to_wav(pcm16, sample_rate=16000)
        fields = self._build_form_fields(wav_bytes, language=language, metadata=metadata)
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = await self._post_transcription(fields, headers)
        text = str(payload.get("text", "")).strip()
        lang = payload.get("language") or language or self.spec.language or "auto"
        duration = len(pcm16) / 2 / 16000
        meta = dict(metadata or {})
        meta.update({"model": self.model_name, "endpoint": self.endpoint})
        if "duration" in payload:
            meta["duration"] = payload["duration"]
        return TranscriptResult(
            text=text,
            kind=TranscriptKind.FINAL,
            confidence=None,
            t_start=0.0,
            t_end=round(float(payload.get("duration", duration)), 3),
            language=str(lang),
            provider=self.name,
            metadata=meta,
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
            buffered.append(chunk)
            yield TranscriptResult(
                text="",
                kind=TranscriptKind.PARTIAL,
                confidence=None,
                t_start=0.0,
                t_end=(chunk.ts_ms / 1000.0) if chunk.ts_ms is not None else None,
                language=None,
                provider=self.name,
                metadata={"seq": chunk.seq, "buffered_chunks": len(buffered)},
            )
            if chunk.is_final:
                break
        yield await self.transcribe(buffered, language=language, metadata=metadata)


__all__ = ["OpenAIWhisperASRProvider", "DEFAULT_OPENAI_TRANSCRIPTIONS_ENDPOINT", "DEFAULT_OPENAI_WHISPER_MODEL"]
