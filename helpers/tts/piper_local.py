"""Local in-process Piper TTS adapter for a0_voqualizer.

The adapter is intentionally testable without installing Piper or downloading a
voice model. Production use lazy-imports Piper during ``start()``; tests can
inject a fake synthesizer/model/runner.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any, Callable

from .base import AudioChunk, TTSCapabilities, TTSError, TTSProvider, TTSProviderSpec, TTSRequest, TTSUnavailableError


SynthesizerFactory = Callable[..., Any]


class PiperLocalTTSProvider(TTSProvider):
    """In-process local TTS provider backed by Piper.

    Supported test seams:
    - ``synthesizer``: prebuilt fake/real object with ``synthesize*`` method or callable behavior.
    - ``synthesizer_factory``: fake/real factory used by ``start()``.
    - ``runner``: plain callable ``runner(request, provider) -> bytes | Iterable[bytes]``.

    When no seam is supplied, ``start()`` lazy-imports a Piper implementation and
    requires a configured model path/voice path. Missing dependency/model is
    reported as ``TTSUnavailableError`` so normal pytest never needs Piper.
    """

    def __init__(
        self,
        spec: TTSProviderSpec | Mapping[str, Any] | None = None,
        *,
        synthesizer: Any | None = None,
        synthesizer_factory: SynthesizerFactory | None = None,
        runner: Callable[[TTSRequest, "PiperLocalTTSProvider"], Any] | None = None,
        chunk_size: int = 4096,
    ) -> None:
        super().__init__(
            spec
            or {
                "name": "piper-local",
                "type": "piper",
                "voice": "en_US-amy-medium",
                "sample_rate": 22050,
                "streaming": True,
            }
        )
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self._synthesizer = synthesizer
        self._synthesizer_factory = synthesizer_factory
        self._runner = runner
        self.chunk_size = chunk_size
        self._start_lock = asyncio.Lock()

    @property
    def voice_name(self) -> str:
        return self.spec.voice or str(self.spec.options.get("voice") or "en_US-amy-medium")

    @property
    def model_path(self) -> str | None:
        value = self.spec.model or self.spec.options.get("model_path") or self.spec.options.get("voice_path")
        return str(value) if value else None

    @property
    def config_path(self) -> str | None:
        value = self.spec.options.get("config_path") or self.spec.options.get("json_path")
        return str(value) if value else None

    @property
    def capabilities(self) -> TTSCapabilities:
        sample_rate = int(self.spec.sample_rate or self.spec.options.get("sample_rate", 22050))
        return TTSCapabilities(
            provider=self.spec.name,
            streaming=True,
            output_codecs=("pcm16/16k", "pcm16/24k"),
            sample_rates=(sample_rate,),
            voices=(self.voice_name,),
            languages=(str(self.spec.options.get("language", "en")),),
            max_text_chars=int(self.spec.options.get("max_text_chars", 4000)),
            supports_cancellation=True,
        )

    async def start(self) -> None:
        async with self._start_lock:
            if self._runner is not None or self._synthesizer is not None:
                self.started = True
                return

            factory = self._synthesizer_factory
            if factory is None:
                factory = self._load_default_factory()

            model_path = self.model_path
            if not model_path:
                raise TTSUnavailableError(
                    "Piper model path is not configured",
                    details={"provider": self.spec.name, "voice": self.voice_name, "option": "model/model_path/voice_path"},
                )
            if not os.path.exists(model_path):
                raise TTSUnavailableError(
                    "Piper model path does not exist",
                    details={"provider": self.spec.name, "model_path": model_path},
                )

            kwargs = dict(self.spec.options)
            kwargs.pop("model_path", None)
            kwargs.pop("voice_path", None)
            kwargs.pop("config_path", None)
            kwargs.pop("json_path", None)
            try:
                try:
                    self._synthesizer = factory(model_path, self.config_path, **kwargs)
                except TypeError:
                    try:
                        self._synthesizer = factory(model_path, **kwargs)
                    except TypeError:
                        self._synthesizer = factory(model_path)
            except TTSUnavailableError:
                raise
            except Exception as exc:
                raise TTSUnavailableError(
                    "Failed to initialize Piper synthesizer",
                    details={"provider": self.spec.name, "model_path": model_path, "error": str(exc)},
                ) from exc
            self.started = True

    async def stop(self) -> None:
        closer = getattr(self._synthesizer, "close", None)
        if closer is not None:
            result = closer()
            if inspect.isawaitable(result):
                await result
        self.started = False

    def _load_default_factory(self) -> SynthesizerFactory:
        try:
            from piper import PiperVoice  # type: ignore

            return PiperVoice.load
        except Exception:
            pass
        try:
            from piper.voice import PiperVoice  # type: ignore

            return PiperVoice.load
        except Exception:
            pass
        try:
            from piper_tts import PiperVoice  # type: ignore

            return PiperVoice.load
        except Exception as exc:
            raise TTSUnavailableError(
                "Piper dependency is not installed or cannot be imported",
                details={"provider": self.spec.name, "dependency": "piper-tts"},
            ) from exc

    async def stream(self, request: TTSRequest) -> AsyncIterator[AudioChunk]:
        if request.codec not in {"pcm16/16k", "pcm16/24k"}:
            raise TTSError(
                "Piper local adapter currently emits PCM16 output only",
                code="TTS_UNSUPPORTED_CODEC",
                recoverable=True,
                details={"codec": request.codec, "provider": self.spec.name},
            )
        if not self.started:
            await self.start()

        try:
            output = await self._invoke(request)
            parts = list(self._coerce_audio_parts(output))
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(
                "Piper synthesis failed",
                code="TTS_SYNTHESIS_FAILED",
                recoverable=True,
                details={"provider": self.spec.name, "error": str(exc)},
            ) from exc

        if not parts:
            parts = [b""]
        seq = 0
        final_index = sum(max(1, (len(part) + self.chunk_size - 1) // self.chunk_size) for part in parts) - 1
        for part in parts:
            if not part:
                yield self._chunk(b"", seq, request, is_final=seq == final_index)
                seq += 1
                continue
            for offset in range(0, len(part), self.chunk_size):
                await asyncio.sleep(0)
                yield self._chunk(part[offset : offset + self.chunk_size], seq, request, is_final=seq == final_index)
                seq += 1

    async def _invoke(self, request: TTSRequest) -> Any:
        if self._runner is not None:
            result = self._runner(request, self)
            return await result if inspect.isawaitable(result) else result
        synth = self._synthesizer
        if synth is None:
            raise TTSUnavailableError("Piper synthesizer is not initialized", details={"provider": self.spec.name})

        kwargs = self._synthesis_kwargs(request)
        method_names = (
            "synthesize_stream_raw",
            "synthesize_stream",
            "synthesize_raw",
            "synthesize",
            "tts",
        )
        for name in method_names:
            method = getattr(synth, name, None)
            if method is None:
                continue
            try:
                result = method(request.text, **kwargs)
            except TypeError:
                result = method(request.text)
            return await result if inspect.isawaitable(result) else result
        if callable(synth):
            result = synth(request.text, **kwargs)
            return await result if inspect.isawaitable(result) else result
        raise TTSUnavailableError(
            "Piper synthesizer has no supported synthesize method",
            details={"provider": self.spec.name, "methods": list(method_names)},
        )

    def _synthesis_kwargs(self, request: TTSRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        voice = request.voice or self.voice_name
        if voice:
            kwargs["voice"] = voice
        if request.speed and request.speed != 1.0:
            # Piper exposes length_scale: larger is slower. Request speed > 1 means faster.
            kwargs["length_scale"] = 1.0 / request.speed
        kwargs.update(dict(request.metadata.get("piper", {}) if isinstance(request.metadata, Mapping) else {}))
        return kwargs

    def _coerce_audio_parts(self, output: Any) -> Iterable[bytes]:
        if output is None:
            return []
        if isinstance(output, AudioChunk):
            return [output.data]
        if isinstance(output, (bytes, bytearray, memoryview)):
            return [bytes(output)]
        data_attr = getattr(output, "audio", None)
        if isinstance(data_attr, (bytes, bytearray, memoryview)):
            return [bytes(data_attr)]
        raw_attr = getattr(output, "raw_audio", None)
        if isinstance(raw_attr, (bytes, bytearray, memoryview)):
            return [bytes(raw_attr)]
        if isinstance(output, Iterable):
            parts: list[bytes] = []
            for item in output:
                if isinstance(item, AudioChunk):
                    parts.append(item.data)
                elif isinstance(item, (bytes, bytearray, memoryview)):
                    parts.append(bytes(item))
                else:
                    item_audio = getattr(item, "audio", None) or getattr(item, "raw_audio", None)
                    if isinstance(item_audio, (bytes, bytearray, memoryview)):
                        parts.append(bytes(item_audio))
                    else:
                        raise TypeError(f"unsupported Piper audio item: {type(item)!r}")
            return parts
        raise TypeError(f"unsupported Piper audio output: {type(output)!r}")

    def _chunk(self, data: bytes, seq: int, request: TTSRequest, *, is_final: bool) -> AudioChunk:
        return AudioChunk(
            data=data,
            seq=seq,
            utterance_id=request.utterance_id,
            codec=request.codec,
            sample_rate=request.sample_rate,
            is_final=is_final,
            duration_ms=None,
            metadata={"provider": self.spec.name, "voice": request.voice or self.voice_name, "backend": "piper"},
        )


# Backwards/ergonomic alias for callers that use the milestone wording.
PiperTTSProvider = PiperLocalTTSProvider
