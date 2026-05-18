"""OpenAI-compatible / LocalAI TTS adapter for a0_voqualizer.

Many local TTS gateways expose an OpenAI-compatible ``/v1/audio/speech``
endpoint. This provider keeps the hosted OpenAI TTS contract while adding
LocalAI-friendly endpoint defaults and optional authentication.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin

from .base import TTSCapabilities, TTSRequest, TTSUnavailableError
from .openai_tts import DEFAULT_OPENAI_TTS_VOICE, OpenAITTSProvider, SessionFactory


DEFAULT_COMPAT_TTS_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_COMPAT_TTS_MODEL = "piper"


def normalize_speech_endpoint(endpoint: str | None = None, *, base_url: str | None = None) -> str:
    """Return a normalized OpenAI-compatible speech endpoint.

    ``endpoint`` wins when provided. Otherwise ``base_url`` is joined with
    ``/v1/audio/speech``. This supports localhost LocalAI deployments and
    custom compatible gateways.
    """

    if endpoint:
        return endpoint.rstrip("/") if endpoint.endswith("/") else endpoint
    root = (base_url or DEFAULT_COMPAT_TTS_BASE_URL).rstrip("/") + "/"
    return urljoin(root, "v1/audio/speech")


class OpenAICompatibleTTSProvider(OpenAITTSProvider):
    """TTS provider for OpenAI-compatible speech endpoints.

    Differences from the hosted OpenAI provider:
    - default endpoint is localhost-friendly
    - API key is optional by default for local services
    - endpoint can be supplied directly or derived from ``base_url``
    """

    def __init__(
        self,
        spec: Mapping[str, Any] | None = None,
        *,
        api_key: str | None = None,
        session_factory: SessionFactory | None = None,
        chunk_size: int = 4096,
    ) -> None:
        cfg = dict(
            spec
            or {
                "name": "localai-tts",
                "type": "openai-compatible",
                "base_url": DEFAULT_COMPAT_TTS_BASE_URL,
                "model": DEFAULT_COMPAT_TTS_MODEL,
                "voice": DEFAULT_OPENAI_TTS_VOICE,
                "sample_rate": 24000,
                "streaming": True,
            }
        )
        nested_options = dict(cfg.pop("options", {}) or {})
        for key, value in nested_options.items():
            cfg.setdefault(key, value)
        base_url = cfg.get("base_url")
        cfg.setdefault("model", DEFAULT_COMPAT_TTS_MODEL)
        cfg.setdefault("voice", DEFAULT_OPENAI_TTS_VOICE)
        cfg.setdefault("type", "openai-compatible")
        cfg.setdefault("name", "localai-tts")
        cfg["endpoint"] = normalize_speech_endpoint(cfg.get("endpoint"), base_url=base_url)
        super().__init__(cfg, api_key=api_key, session_factory=session_factory, chunk_size=chunk_size)

    @property
    def base_url(self) -> str:
        return str(self.spec.options.get("base_url", DEFAULT_COMPAT_TTS_BASE_URL))

    @property
    def require_api_key(self) -> bool:
        return bool(self.spec.options.get("require_api_key", False))

    @property
    def api_key_env(self) -> str:
        return self.spec.api_key_env or str(self.spec.options.get("api_key_env", "OPENAI_API_KEY"))

    @property
    def capabilities(self) -> TTSCapabilities:
        caps = super().capabilities
        voices = (self.voice_name,)
        extra_voices = self.spec.options.get("voices")
        if isinstance(extra_voices, (list, tuple)):
            voices = tuple(dict.fromkeys([self.voice_name, *[str(v) for v in extra_voices]]))
        return TTSCapabilities(
            provider=self.spec.name,
            streaming=True,
            output_codecs=caps.output_codecs,
            sample_rates=caps.sample_rates,
            voices=voices,
            languages=(str(self.spec.options.get("language", "auto")),),
            max_text_chars=caps.max_text_chars,
            supports_cancellation=True,
        )

    def _api_key(self) -> str:
        key = self._explicit_api_key or os.environ.get(self.api_key_env)
        if key:
            return key
        if self.require_api_key:
            raise TTSUnavailableError(
                "OpenAI-compatible TTS API key is not configured",
                details={"provider": self.spec.name, "api_key_env": self.api_key_env, "require_api_key": True},
            )
        return ""

    async def _post_speech(self, payload: Mapping[str, Any], headers: Mapping[str, str]):
        clean_headers = {
            key: value
            for key, value in headers.items()
            if not (key.lower() == "authorization" and value == "Bearer ")
        }
        async for item in super()._post_speech(payload, clean_headers):
            yield item

    def _build_payload(self, request: TTSRequest) -> dict[str, Any]:
        payload = super()._build_payload(request)
        # Compatible gateways often accept arbitrary OpenAI-shaped extras.
        # ``extra_body`` is already handled by the parent; ``compatible_extra``
        # provides a separate namespace for local gateway-specific fields.
        extra = self.spec.options.get("compatible_extra")
        if isinstance(extra, Mapping):
            payload.update(dict(extra))
        return payload


LocalAITTSProvider = OpenAICompatibleTTSProvider


__all__ = [
    "OpenAICompatibleTTSProvider",
    "LocalAITTSProvider",
    "DEFAULT_COMPAT_TTS_BASE_URL",
    "DEFAULT_COMPAT_TTS_MODEL",
    "normalize_speech_endpoint",
]
