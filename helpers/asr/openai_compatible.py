"""OpenAI-compatible / LocalAI ASR adapter for a0_voqualizer.

Many local gateways (LocalAI, vLLM-style proxies, self-hosted Whisper servers)
implement an OpenAI-compatible ``/v1/audio/transcriptions`` endpoint. This
provider keeps the same ASR interface while allowing endpoint/base-url override
and optional API-key behavior suitable for localhost deployments.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterable, AsyncIterator, Iterable, Mapping, Sequence
from urllib.parse import urljoin

from .base import ASRCapabilities, AudioChunk, TranscriptResult
from .openai_whisper import OpenAIWhisperASRProvider, SessionFactory


DEFAULT_COMPAT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_COMPAT_MODEL = "whisper-1"


def normalize_transcriptions_endpoint(endpoint: str | None = None, *, base_url: str | None = None) -> str:
    """Return a normalized OpenAI-compatible transcriptions endpoint.

    ``endpoint`` wins when provided. Otherwise ``base_url`` is joined with
    ``/v1/audio/transcriptions``. This supports both LocalAI-style base URLs and
    fully-qualified custom endpoint overrides.
    """

    if endpoint:
        return endpoint.rstrip("/") if endpoint.endswith("/") else endpoint
    root = (base_url or DEFAULT_COMPAT_BASE_URL).rstrip("/") + "/"
    return urljoin(root, "v1/audio/transcriptions")


class OpenAICompatibleASRProvider(OpenAIWhisperASRProvider):
    """ASR provider for OpenAI-compatible transcription endpoints.

    Differences from the hosted OpenAI provider:
    - default endpoint is localhost-friendly (LocalAI default-ish)
    - API key is optional by default because many local services do not require
      auth; when provided, an Authorization header is sent
    - provider ``type`` is ``localai`` / ``openai-compatible`` rather than
      hosted OpenAI
    """

    def __init__(
        self,
        spec: Mapping[str, Any] | None = None,
        *,
        api_key: str | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        cfg = dict(
            spec
            or {
                "name": "localai",
                "type": "localai",
                "base_url": DEFAULT_COMPAT_BASE_URL,
                "model": DEFAULT_COMPAT_MODEL,
                "streaming": False,
                "language": "auto",
            }
        )
        # ASRProviderSpec.from_config() preserves unknown *top-level* keys in
        # spec.options. Accept both a nested user-facing `options` mapping and
        # direct top-level spellings, then flatten them before delegating.
        nested_options = dict(cfg.pop("options", {}) or {})
        for key, value in nested_options.items():
            cfg.setdefault(key, value)
        if "api_key_env" not in cfg and "api_key_env" in nested_options:
            cfg["api_key_env"] = nested_options["api_key_env"]
        base_url = cfg.get("base_url")
        cfg["endpoint"] = normalize_transcriptions_endpoint(cfg.get("endpoint"), base_url=base_url)
        super().__init__(cfg, api_key=api_key, session_factory=session_factory)

    @property
    def base_url(self) -> str:
        return str(self.spec.options.get("base_url", DEFAULT_COMPAT_BASE_URL))

    @property
    def require_api_key(self) -> bool:
        return bool(self.spec.options.get("require_api_key", False))

    def capabilities(self) -> ASRCapabilities:
        caps = super().capabilities()
        return ASRCapabilities(
            provider=self.name,
            streaming=False,
            languages=caps.languages,
            input_sample_rates=caps.input_sample_rates,
            input_codecs=caps.input_codecs,
            partials=False,
            finals=True,
            word_timestamps=False,
            confidence=False,
        )

    def _api_key(self) -> str:
        key = self._explicit_api_key or os.environ.get(self.api_key_env)
        if key:
            return key
        if self.require_api_key:
            # Reuse hosted provider's graceful unavailable shape.
            return super()._api_key()
        return ""

    async def transcribe(
        self,
        audio: bytes | AudioChunk | Sequence[AudioChunk],
        *,
        language: str | None = None,
        sample_rate: int = 16000,
        metadata: Mapping[str, Any] | None = None,
    ) -> TranscriptResult:
        return await super().transcribe(audio, language=language, sample_rate=sample_rate, metadata=metadata)

    async def _post_transcription(self, fields: Mapping[str, Any], headers: Mapping[str, str]) -> Mapping[str, Any]:
        # Drop empty bearer token for unauthenticated LocalAI/default local mode.
        clean_headers = {k: v for k, v in headers.items() if not (k.lower() == "authorization" and v == "Bearer ")}
        return await super()._post_transcription(fields, clean_headers)

    async def stream(
        self,
        chunks: AsyncIterable[AudioChunk] | Iterable[AudioChunk],
        *,
        language: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[TranscriptResult]:
        async for item in super().stream(chunks, language=language, metadata=metadata):
            yield item


# LocalAI-specific alias for readability in config/provider factories.
LocalAIASRProvider = OpenAICompatibleASRProvider


__all__ = [
    "OpenAICompatibleASRProvider",
    "LocalAIASRProvider",
    "DEFAULT_COMPAT_BASE_URL",
    "DEFAULT_COMPAT_MODEL",
    "normalize_transcriptions_endpoint",
]
