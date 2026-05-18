"""ASR provider adapters for a0_voqualizer."""

from .whisper_local import FasterWhisperASRProvider, WhisperLocalASRProvider
from .openai_whisper import OpenAIWhisperASRProvider
from .openai_compatible import OpenAICompatibleASRProvider, LocalAIASRProvider
from .base import (
    ASRCapabilities,
    ASRError,
    ASRProvider,
    ASRProviderSpec,
    ASRUnavailableError,
    AudioChunk,
    MockASRProvider,
    TranscriptKind,
    TranscriptResult,
    iter_audio_chunks,
)

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
    "FasterWhisperASRProvider",
    "WhisperLocalASRProvider",
    "OpenAIWhisperASRProvider",
    "OpenAICompatibleASRProvider",
    "LocalAIASRProvider",
]
