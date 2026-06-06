"""Live A0 provider bindings for the Wyoming Voqualizer rewrite (W20).

This module composes the W12 `wyoming_a0_adapters` factories with the real
A0 Voqualizer ASR/TTS provider helpers and the plugin config loader, so that
a configured Wyoming interface can run actual transcription, prompt
submission, and TTS synthesis end-to-end via the Wyoming pipeline.

The legacy `api/ws_voqualizer.py` path is intentionally untouched and remains
in-tree for reference per the breaking-rewrite plan; this module imports the
shared provider helpers directly without revisiting old socket events.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from .wyoming_a0_adapters import (
    build_a0_asr_adapter,
    build_a0_prompt_adapter,
    build_a0_tts_adapter,
)
from .wyoming_pipeline import WyomingVoqualizerPipeline
from .wyoming_server import WyomingInterfaceRuntime
from .wyoming_a0_prompt_submitter import build_agent_context_submitter
from .wyoming_interfaces import WyomingInterface


def _safe_load_config() -> dict:
    try:
        from .registry import load_config, ConfigError
    except Exception:
        return {}
    try:
        return load_config()
    except Exception:
        try:
            return load_config(apply_overlay=False)
        except Exception:
            return {}


def _resolve_asr_spec(cfg: dict) -> dict | None:
    asr = cfg.get("asr") or {}
    providers = asr.get("providers") or []
    default_name = asr.get("default")
    for p in providers:
        if isinstance(p, dict) and p.get("name") == default_name:
            return p
    return providers[0] if providers else None


def _resolve_tts_spec(cfg: dict) -> dict | None:
    tts = cfg.get("tts") or {}
    providers = tts.get("providers") or []
    default_name = tts.get("default")
    for p in providers:
        if isinstance(p, dict) and p.get("name") == default_name:
            return p
    return providers[0] if providers else None


def build_live_asr_factory(cfg: dict | None = None) -> Callable[[], Any]:
    """Return an ASR provider factory using the plugin's configured default.

    Falls back to `MockASRProvider` if no provider can be constructed.
    """
    config = cfg if cfg is not None else _safe_load_config()
    spec = _resolve_asr_spec(config)

    def factory():
        try:
            from .asr import (
                ASRProviderSpec,
                MockASRProvider,
                FasterWhisperASRProvider,
                OpenAIWhisperASRProvider,
                OpenAICompatibleASRProvider,
                LocalAIASRProvider,
            )
        except Exception:
            from .asr import MockASRProvider  # type: ignore
            return MockASRProvider()
        if not spec:
            return MockASRProvider()
        try:
            provider_spec = ASRProviderSpec.from_config(spec)
            kind = (provider_spec.type or "mock").lower()
            if kind in ("mock",):
                return MockASRProvider()
            if kind in ("faster-whisper", "whisper-local", "whisper"):
                return FasterWhisperASRProvider(provider_spec)
            if kind in ("openai-whisper", "openai"):
                return OpenAIWhisperASRProvider(provider_spec)
            if kind in ("openai-compatible",):
                return OpenAICompatibleASRProvider(provider_spec)
            if kind in ("localai",):
                return LocalAIASRProvider(provider_spec)
        except Exception:
            pass
        return MockASRProvider()

    return factory


def build_live_tts_factory(cfg: dict | None = None) -> Callable[[], Any]:
    """Return a TTS provider factory using the plugin's configured default.

    Falls back to `MockTTSProvider` if no provider can be constructed.
    """
    config = cfg if cfg is not None else _safe_load_config()
    spec = _resolve_tts_spec(config)

    def factory():
        try:
            from .tts import (
                TTSProviderSpec,
                MockTTSProvider,
                OpenAITTSProvider,
                OpenAICompatibleTTSProvider,
                PiperLocalTTSProvider,
            )
        except Exception:
            from .tts import MockTTSProvider  # type: ignore
            return MockTTSProvider()
        if not spec:
            return MockTTSProvider()
        try:
            provider_spec = TTSProviderSpec.from_config(spec)
            kind = (provider_spec.type or "mock").lower()
            if kind in ("mock",):
                return MockTTSProvider()
            if kind in ("openai", "openai-tts"):
                return OpenAITTSProvider(provider_spec)
            if kind in ("openai-compatible", "localai-tts"):
                return OpenAICompatibleTTSProvider(provider_spec)
            if kind in ("piper", "piper-local"):
                return PiperLocalTTSProvider(provider_spec)
        except Exception:
            pass
        return MockTTSProvider()

    return factory


async def _default_prompt_submitter(text: str, metadata: dict[str, Any]) -> str:
    """Default prompt submitter that simply echoes the recognized/typed text.

    Real Agent Zero context submission lives in framework code paths that are
    not safely importable from a thin unit-test surface. This stub keeps the
    pipeline runnable end-to-end and lets the host framework override the
    submitter via `bind_live_providers_to_runtime(prompt_submitter=...)`.
    """
    return f"[ctxid={metadata.get('ctxid', '')}] {text}"


def bind_live_providers_to_runtime(
    interface: WyomingInterface,
    *,
    cfg: dict | None = None,
    prompt_submitter: Callable[[str, dict[str, Any]], Any] | None = None,
) -> WyomingInterfaceRuntime:
    """Construct a `WyomingInterfaceRuntime` wired with live providers.

    The returned runtime can be plugged into `WyomingTcpInterfaceManager` or
    invoked directly by the framework `/ws` Wyoming handler.
    """
    config = cfg if cfg is not None else _safe_load_config()
    asr_factory = build_live_asr_factory(config)
    tts_factory = build_live_tts_factory(config)
    submitter = prompt_submitter or build_agent_context_submitter(allow_echo_fallback=True, stream=True)
    pipeline = WyomingVoqualizerPipeline(
        asr_adapter=build_a0_asr_adapter(asr_factory),
        prompt_adapter=build_a0_prompt_adapter(submitter),
        tts_adapter=build_a0_tts_adapter(tts_factory),
    )
    runtime = WyomingInterfaceRuntime(interface)
    # Install the pipeline as the dispatch handler for every Wyoming event
    # type the pipeline understands. The pipeline itself routes by event type
    # to the ASR/prompt/TTS sub-adapters.
    pipeline.install_into(runtime)
    return runtime


def live_provider_status(cfg: dict | None = None) -> dict[str, Any]:
    config = cfg if cfg is not None else _safe_load_config()
    asr_spec = _resolve_asr_spec(config) or {}
    tts_spec = _resolve_tts_spec(config) or {}
    return {
        "mode": "live_providers",
        "asr": {
            "name": asr_spec.get("name"),
            "type": asr_spec.get("type"),
            "configured": bool(asr_spec),
        },
        "tts": {
            "name": tts_spec.get("name"),
            "type": tts_spec.get("type"),
            "configured": bool(tts_spec),
        },
        "prompt_submitter": "agent_context_streaming_with_echo_fallback",
    }
