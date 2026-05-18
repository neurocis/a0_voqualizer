"""REST admin endpoint for a0_voqualizer (A1.5).

Single handler covering the admin surface needed for the in-plugin tester and
the future settings WebUI panel. Actions are dispatched on the `action` field
of the request body (kept consistent with existing A0 plugin conventions like
`a0_crosschatapi` and the core `/api/projects` handler).

Supported actions:

| Action | Returns |
|---|---|
| `providers` (default) | Merged ASR/TTS provider catalog + defaults |
| `config` | Full merged config (defaults ∪ overlay), schema-valid |
| `capabilities` | Codec lists, sample rates, languages — the same shape the WS handler emits in `voqualizer_ready` |
| `status` | Plugin health: dependency status + active session count (stub for A1.3 wiring) |
| `save` | Persist a runtime overlay; payload `overlay: {...}` validated against schema before write |
| `test_provider` | Run a minimal ASR/TTS provider smoke test with latency/result metadata |

Endpoint URL: `POST /api/plugins/a0_voqualizer/voqualizer_admin` (GET also
accepted for the read-only actions: `providers`, `config`, `capabilities`,
`status`).

Acceptance for A1.5: `action=providers` returns config-derived providers and
defaults from a fresh plugin install.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from typing import Any, Mapping

from helpers.api import ApiHandler
from flask import Request, Response


# Ensure plugin-relative imports work regardless of how the handler is loaded.
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)


class VoqualizerAdmin(ApiHandler):
    """Admin/CRUD REST surface for a0_voqualizer."""

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        action = (input.get("action") or "providers").strip().lower()

        try:
            if action == "providers":
                return self._action_providers()
            if action == "config":
                return self._action_config()
            if action == "capabilities":
                return self._action_capabilities()
            if action == "status":
                return self._action_status()
            if action == "save":
                return self._action_save(input.get("overlay") or {})
            if action == "test_provider":
                return await self._action_test_provider(
                    side=input.get("side", ""),
                    name=input.get("name", ""),
                )
            return {
                "ok": False,
                "code": "UNKNOWN_ACTION",
                "message": f"Unknown action: {action!r}",
                "actions": [
                    "providers", "config", "capabilities", "status",
                    "save", "test_provider",
                ],
            }
        except Exception as e:
            return {
                "ok": False,
                "code": e.__class__.__name__,
                "message": str(e),
            }

    # Action implementations

    def _action_providers(self) -> dict[str, Any]:
        from usr.plugins.a0_voqualizer.helpers import registry
        cfg = registry.load_config()
        return {
            "ok": True,
            "asr": {
                "providers": cfg["asr"]["providers"],
                "default": cfg["asr"]["default"],
            },
            "tts": {
                "providers": cfg["tts"]["providers"],
                "default": cfg["tts"]["default"],
            },
        }

    def _action_config(self) -> dict[str, Any]:
        from usr.plugins.a0_voqualizer.helpers import registry
        cfg = registry.load_config()
        return {"ok": True, "config": cfg}

    def _action_capabilities(self) -> dict[str, Any]:
        from usr.plugins.a0_voqualizer.helpers import registry
        cfg = registry.load_config()
        proto = cfg["protocol"]
        return {
            "ok": True,
            "capabilities": {
                "input_codecs": proto["input_codecs"],
                "output_codecs": proto["output_codecs"],
                "default_input_codec": proto["default_input_codec"],
                "default_output_codec": proto["default_output_codec"],
                "heartbeat_interval_seconds": proto["heartbeat_interval_seconds"],
                "session_resume_window_seconds": proto["session_resume_window_seconds"],
                "asr_providers": [p["name"] for p in cfg["asr"]["providers"]],
                "tts_providers": [p["name"] for p in cfg["tts"]["providers"]],
                "limits": cfg["limits"],
            },
        }

    def _action_status(self) -> dict[str, Any]:
        # Dependency status comes from hooks.py side-file
        import json as _json
        dep_status = None
        dep_path = os.path.join(_PLUGIN_DIR, ".dependency_status.json")
        if os.path.exists(dep_path):
            try:
                with open(dep_path) as f:
                    dep_status = _json.load(f)
            except Exception as e:  # noqa: BLE001
                dep_status = {"error": f"unreadable: {e}"}

        # Active session count from BridgeRegistry (A1.3); soft-import so the
        # stub is functional even before Voq_Core lands the registry class.
        active_sessions: int | None = None
        try:
            from usr.plugins.a0_voqualizer.helpers.registry import (
                BridgeRegistry,
            )  # type: ignore[attr-defined]
            try:
                active_sessions = BridgeRegistry.get_instance().count()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                active_sessions = 0
        except Exception:  # noqa: BLE001
            active_sessions = None

        return {
            "ok": True,
            "plugin": "a0_voqualizer",
            "dependencies": dep_status,
            "active_sessions": active_sessions,
        }

    def _action_save(self, overlay: dict) -> dict[str, Any]:
        from usr.plugins.a0_voqualizer.helpers import registry
        if not isinstance(overlay, dict):
            return {
                "ok": False,
                "code": "BAD_REQUEST",
                "message": "overlay must be a JSON object",
            }
        try:
            registry.save_overlay(overlay)
        except registry.ConfigError as e:
            return {
                "ok": False,
                "code": "CONFIG_INVALID",
                "message": str(e),
                "path": e.path,
            }
        # Return the merged config so the caller has the post-save view
        cfg = registry.load_config()
        return {"ok": True, "config": cfg}

    def _provider_spec(self, config: Mapping[str, Any], side: str, name: str) -> dict[str, Any] | None:
        section = config.get(side, {}) if isinstance(config, Mapping) else {}
        providers = section.get("providers", []) if isinstance(section, Mapping) else []
        for provider in providers:
            if isinstance(provider, Mapping) and provider.get("name") == name:
                return dict(provider)
        return None

    def _provider_endpoint_summary(self, spec: Mapping[str, Any]) -> str:
        for key in ("endpoint", "base_url", "model", "voice"):
            value = spec.get(key)
            if value:
                return str(value)
        options = spec.get("options")
        if isinstance(options, Mapping):
            for key in ("endpoint", "base_url", "model", "voice"):
                value = options.get(key)
                if value:
                    return str(value)
        return ""

    def _build_asr_provider(self, spec: Mapping[str, Any]):
        from usr.plugins.a0_voqualizer.api.ws_voqualizer import _build_asr_provider
        return _build_asr_provider(spec)

    def _build_tts_provider(self, spec: Mapping[str, Any]):
        from usr.plugins.a0_voqualizer.api.ws_voqualizer import _build_tts_provider
        return _build_tts_provider(spec)

    async def _action_test_provider(self, side: str, name: str) -> dict[str, Any]:
        side = str(side or "").strip().lower()
        name = str(name or "").strip()
        if side not in {"asr", "tts"}:
            return {
                "ok": False,
                "code": "INVALID_SIDE",
                "side": side,
                "name": name,
                "message": "Provider side must be 'asr' or 'tts'.",
            }
        if not name:
            return {
                "ok": False,
                "code": "PROVIDER_NOT_FOUND",
                "side": side,
                "name": name,
                "message": f"No {side.upper()} provider name was supplied.",
            }

        from usr.plugins.a0_voqualizer.helpers import registry
        cfg = registry.load_config()
        spec = self._provider_spec(cfg, side, name)
        if spec is None:
            return {
                "ok": False,
                "code": "PROVIDER_NOT_FOUND",
                "side": side,
                "name": name,
                "message": f"Unknown {side.upper()} provider: {name}",
            }
        if side == "asr":
            return await self._test_asr_provider(spec)
        return await self._test_tts_provider(spec)

    async def _test_asr_provider(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        from usr.plugins.a0_voqualizer.helpers.asr import ASRError, AudioChunk

        name = str(spec.get("name", ""))
        provider_type = str(spec.get("type", ""))
        endpoint = self._provider_endpoint_summary(spec)
        started = time.perf_counter()
        provider = None
        try:
            provider = self._build_asr_provider(spec)
            await provider.start()
            # One second of deterministic PCM16/16 kHz silence. Mock providers
            # pass deterministically; real providers exercise their configured
            # deps/env/network only when an operator clicks Test in a live UI.
            audio = AudioChunk(b"\x00\x00" * 16000, sample_rate=16000, seq=0, ts_ms=0, is_final=True)
            result = await provider.transcribe(
                audio,
                language=str(spec.get("language", "auto") or "auto"),
                sample_rate=16000,
                metadata={"source": "voqualizer_admin_test_provider"},
            )
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            transcript = getattr(result, "text", "") or ""
            return {
                "ok": True,
                "code": "OK",
                "side": "asr",
                "name": name,
                "type": provider_type,
                "endpoint": endpoint,
                "latency_ms": latency_ms,
                "transcript_preview": str(transcript)[:80],
                "message": f"ASR provider {name!r} smoke test passed in {latency_ms} ms.",
                "details": {
                    "language": getattr(result, "language", None),
                    "confidence": getattr(result, "confidence", None),
                    "sample_rate": 16000,
                    "duration_ms": 1000,
                },
            }
        except ASRError as exc:
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            return {
                "ok": False,
                "code": exc.code,
                "side": "asr",
                "name": name,
                "type": provider_type,
                "endpoint": endpoint,
                "latency_ms": latency_ms,
                "transcript_preview": "",
                "message": exc.message,
                "details": dict(exc.details),
            }
        except Exception as exc:  # noqa: BLE001 - admin smoke test must stay JSON-safe
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            return {
                "ok": False,
                "code": "ASR_UNAVAILABLE",
                "side": "asr",
                "name": name,
                "type": provider_type,
                "endpoint": endpoint,
                "latency_ms": latency_ms,
                "transcript_preview": "",
                "message": str(exc),
                "details": {"error_type": exc.__class__.__name__},
            }
        finally:
            if provider is not None:
                close = getattr(provider, "close", None)
                if close is not None:
                    try:
                        result = close()
                        if hasattr(result, "__await__"):
                            await result
                    except Exception:
                        pass


    def _tts_preview_mime(self, codec: str, fmt: str = "") -> str:
        fmt = str(fmt or "").lower()
        codec = str(codec or "").lower()
        if fmt == "wav" or codec == "wav":
            return "audio/wav"
        if fmt == "mp3" or codec == "mp3":
            return "audio/mpeg"
        if fmt == "opus" or codec == "opus":
            return "audio/ogg; codecs=opus"
        if fmt == "pcm" or codec.startswith("pcm16"):
            return "audio/L16"
        return "application/octet-stream"

    async def _test_tts_provider(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        from usr.plugins.a0_voqualizer.helpers.tts import TTSError, TTSRequest

        name = str(spec.get("name", ""))
        provider_type = str(spec.get("type", ""))
        endpoint = self._provider_endpoint_summary(spec)
        started = time.perf_counter()
        provider = None
        try:
            provider = self._build_tts_provider(spec)
            await provider.start()
            caps = provider.capabilities
            codec = "pcm16/16k"
            if hasattr(caps, "output_codecs") and codec not in tuple(caps.output_codecs):
                codec = tuple(caps.output_codecs)[0]
            sample_rate = 16000
            if hasattr(caps, "sample_rates") and sample_rate not in tuple(caps.sample_rates):
                sample_rate = int(tuple(caps.sample_rates)[0])
            request = TTSRequest(
                text="Provider test ok.",
                voice=str(spec.get("voice") or "") or None,
                codec=codec,
                sample_rate=sample_rate,
                metadata={"source": "voqualizer_admin_test_provider"},
            )
            bytes_returned = 0
            chunk_count = 0
            preview_audio = b""
            preview_limit = 512 * 1024
            preview_format = ""
            async for chunk in provider.stream(request):
                piece = bytes(chunk.data)
                bytes_returned += len(piece)
                chunk_count += 1
                codec = chunk.codec
                sample_rate = int(chunk.sample_rate)
                if isinstance(getattr(chunk, "metadata", None), Mapping):
                    preview_format = str(chunk.metadata.get("format") or preview_format or "")
                if piece and len(preview_audio) < preview_limit:
                    preview_audio += piece[: max(0, preview_limit - len(preview_audio))]
                if bytes_returned > 0:
                    break
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            ok = bytes_returned > 0
            return {
                "ok": ok,
                "code": "OK" if ok else "TTS_BAD_RESPONSE",
                "side": "tts",
                "name": name,
                "type": provider_type,
                "endpoint": endpoint,
                "latency_ms": latency_ms,
                "bytes_returned": bytes_returned,
                "codec": codec,
                "sample_rate": sample_rate,
                "audio_preview_b64": base64.b64encode(preview_audio).decode("ascii") if preview_audio else "",
                "audio_preview_format": preview_format or str(spec.get("format") or spec.get("response_format") or ""),
                "audio_preview_mime": self._tts_preview_mime(codec, preview_format or str(spec.get("format") or spec.get("response_format") or "")),
                "message": (
                    f"TTS provider {name!r} smoke test passed in {latency_ms} ms."
                    if ok else f"TTS provider {name!r} returned no audio bytes."
                ),
                "details": {"chunks_seen": chunk_count, "utterance": request.utterance_id},
            }
        except TTSError as exc:
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            return {
                "ok": False,
                "code": exc.code,
                "side": "tts",
                "name": name,
                "type": provider_type,
                "endpoint": endpoint,
                "latency_ms": latency_ms,
                "bytes_returned": 0,
                "codec": "",
                "sample_rate": 0,
                "message": exc.message,
                "details": dict(exc.details),
            }
        except Exception as exc:  # noqa: BLE001
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            return {
                "ok": False,
                "code": "TTS_UNAVAILABLE",
                "side": "tts",
                "name": name,
                "type": provider_type,
                "endpoint": endpoint,
                "latency_ms": latency_ms,
                "bytes_returned": 0,
                "codec": "",
                "sample_rate": 0,
                "message": str(exc),
                "details": {"error_type": exc.__class__.__name__},
            }
        finally:
            if provider is not None:
                stop = getattr(provider, "stop", None)
                if stop is not None:
                    try:
                        result = stop()
                        if hasattr(result, "__await__"):
                            await result
                    except Exception:
                        pass
