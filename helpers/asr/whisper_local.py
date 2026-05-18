"""Local faster-whisper ASR adapter for a0_voqualizer.

The adapter is intentionally testable without downloading a model: callers may
inject a ``model_factory`` or prebuilt ``model``. In production, the adapter
imports ``faster_whisper.WhisperModel`` lazily during ``start()``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterable, AsyncIterator, Callable, Iterable, Mapping, Sequence

import numpy as np

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


ModelFactory = Callable[..., Any]


@dataclass(frozen=True)
class WhisperSegmentView:
    """Small normalized view of a faster-whisper segment."""

    text: str
    start: float | None = None
    end: float | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


class FasterWhisperASRProvider(ASRProvider):
    """Local ASR provider backed by ``faster-whisper``.

    Streaming semantics for v0.1/M3:
    - ``transcribe()`` runs faster-whisper on a buffered utterance and returns a
      final transcript.
    - ``stream()`` buffers incoming chunks and emits deterministic lightweight
      partial markers while audio arrives, then emits the final faster-whisper
      transcript when the stream ends or a chunk has ``is_final=True``.

    This keeps the WebSocket path responsive without claiming true decoder-level
    streaming partials. A later milestone can replace the partial strategy with
    VAD/windowed decoding if needed.
    """

    def __init__(
        self,
        spec: ASRProviderSpec | Mapping[str, Any] | None = None,
        *,
        model: Any | None = None,
        model_factory: ModelFactory | None = None,
    ) -> None:
        super().__init__(
            spec
            or {
                "name": "whisper-local",
                "type": "whisper",
                "model": "large-v3",
                "language": "auto",
                "streaming": True,
                "vad": True,
            }
        )
        self._model = model
        self._model_factory = model_factory
        self._started = False
        self._model_load_lock = asyncio.Lock()

    @property
    def model_name(self) -> str:
        return self.spec.model or "large-v3"

    @property
    def is_started(self) -> bool:
        return self._started

    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(
            provider=self.name,
            streaming=True,
            languages=("auto", "en", "es", "fr", "de", "it", "pt", "nl", "ja", "zh", "ko", "ru"),
            input_sample_rates=(8000, 16000, 24000),
            input_codecs=("pcm16/8k", "pcm16/16k", "pcm16/24k"),
            partials=True,
            finals=True,
            word_timestamps=False,
            confidence=True,
        )

    async def start(self) -> None:
        async with self._model_load_lock:
            if self._model is not None:
                self._started = True
                return
            factory = self._model_factory
            if factory is None:
                try:
                    from faster_whisper import WhisperModel  # type: ignore
                except Exception as exc:
                    raise ASRUnavailableError(
                        "faster-whisper is not installed or cannot be imported",
                        details={"provider": self.name, "dependency": "faster_whisper"},
                    ) from exc
                factory = WhisperModel
            opts = dict(self.spec.options)
            device = opts.pop("device", "auto")
            compute_type = opts.pop("compute_type", "default")
            self._model = factory(self.model_name, device=device, compute_type=compute_type, **opts)
            self._started = True

    async def close(self) -> None:
        self._started = False

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
    def _pcm16_to_float32(pcm16: bytes) -> np.ndarray:
        if not pcm16:
            return np.zeros(0, dtype=np.float32)
        arr = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
        return arr / 32768.0

    @staticmethod
    def _confidence_from_segments(segments: Sequence[WhisperSegmentView]) -> float | None:
        probs: list[float] = []
        for seg in segments:
            if seg.avg_logprob is not None:
                # Convert log probability into a rough [0,1] confidence.
                probs.append(float(np.clip(np.exp(seg.avg_logprob), 0.0, 1.0)))
            elif seg.no_speech_prob is not None:
                probs.append(float(np.clip(1.0 - seg.no_speech_prob, 0.0, 1.0)))
        if not probs:
            return None
        return round(float(sum(probs) / len(probs)), 3)

    @staticmethod
    def _normalize_segments(raw_segments: Iterable[Any]) -> list[WhisperSegmentView]:
        out: list[WhisperSegmentView] = []
        for seg in raw_segments:
            text = getattr(seg, "text", None)
            if text is None and isinstance(seg, Mapping):
                text = seg.get("text")
            if text is None:
                text = str(seg)
            start = getattr(seg, "start", None) if not isinstance(seg, Mapping) else seg.get("start")
            end = getattr(seg, "end", None) if not isinstance(seg, Mapping) else seg.get("end")
            avg_logprob = getattr(seg, "avg_logprob", None) if not isinstance(seg, Mapping) else seg.get("avg_logprob")
            no_speech_prob = getattr(seg, "no_speech_prob", None) if not isinstance(seg, Mapping) else seg.get("no_speech_prob")
            out.append(
                WhisperSegmentView(
                    text=str(text).strip(),
                    start=float(start) if start is not None else None,
                    end=float(end) if end is not None else None,
                    avg_logprob=float(avg_logprob) if avg_logprob is not None else None,
                    no_speech_prob=float(no_speech_prob) if no_speech_prob is not None else None,
                )
            )
        return out

    async def transcribe(
        self,
        audio: bytes | AudioChunk | Sequence[AudioChunk],
        *,
        language: str | None = None,
        sample_rate: int = 16000,
        metadata: Mapping[str, Any] | None = None,
    ) -> TranscriptResult:
        if not self._started or self._model is None:
            await self.start()
        assert self._model is not None
        try:
            pcm16 = self._chunks_to_pcm16_16k(audio, sample_rate=sample_rate)
            samples = self._pcm16_to_float32(pcm16)
        except CodecError as exc:
            raise ASRError(str(exc), code="BAD_ASR_AUDIO", recoverable=True) from exc

        options = {
            "language": self._language_arg(language),
            "vad_filter": bool(self.spec.options.get("vad", True)),
        }
        # faster-whisper accepts None language; fake test models do too.
        segments_iter, info = self._model.transcribe(samples, **options)
        segments = self._normalize_segments(list(segments_iter))
        text = " ".join(seg.text for seg in segments if seg.text).strip()
        if not text:
            text = ""
        lang = getattr(info, "language", None)
        if lang is None and isinstance(info, Mapping):
            lang = info.get("language")
        lang = lang or language or self.spec.language or "auto"
        t_start = next((seg.start for seg in segments if seg.start is not None), 0.0)
        t_end = next((seg.end for seg in reversed(segments) if seg.end is not None), len(pcm16) / 2 / 16000)
        meta = dict(metadata or {})
        meta.update({"model": self.model_name, "segments": len(segments)})
        return TranscriptResult(
            text=text,
            kind=TranscriptKind.FINAL,
            confidence=self._confidence_from_segments(segments),
            t_start=t_start,
            t_end=round(float(t_end), 3),
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
            # Lightweight partial surfacing: announce that audio is flowing. True
            # decoder-window partials are deferred until A3.5/M8 tuning.
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


# Backward-compatible alias expected by some callers/tests.
WhisperLocalASRProvider = FasterWhisperASRProvider


__all__ = ["FasterWhisperASRProvider", "WhisperLocalASRProvider", "WhisperSegmentView"]
