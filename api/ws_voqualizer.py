"""a0_voqualizer WebSocket endpoint (M1–M4).

Activates as a plugin WS handler under A0's unified ``/ws`` namespace. Clients
select this handler during the Socket.IO connect handshake by including
``plugins/a0_voqualizer/ws_voqualizer`` in their ``auth.handlers`` list.

Protocol surface implemented in this milestone (M1 / A1.4):

* ``voqualizer_init``    → create-or-resume a :class:`BridgeSession`,
                            reply ``voqualizer_ready`` with capabilities.
* ``voqualizer_ping``    → reply ``voqualizer_pong`` (heartbeat / RTT).
* ``voqualizer_control`` → control plane (mute/unmute/barge_in/end_session/
                            resume).
* ``voqualizer_audio_chunk`` → decode framed audio, feed ASR, emit
  ``voqualizer_asr_partial`` / ``voqualizer_asr_final`` events.
* ``voqualizer_user_text`` → direct deterministic TTS synthesis path for M4.5
  until M5 context bridge wires real agent responses.
* Any other event → :class:`WsResult.error` ``UNKNOWN_EVENT``.

The handler keeps :attr:`requires_auth` at its default (``True``); CSRF auto-
follows. All inbound payloads are best-effort validated; protocol errors are
returned as recoverable :class:`WsResult.error` results so the client can stay
connected.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
import traceback
import uuid
from collections.abc import Mapping
from typing import Any

from helpers.ws import WsHandler
from helpers.ws_manager import WsResult
from helpers.print_style import PrintStyle

# Plugin-local helpers live under usr/plugins/a0_voqualizer/helpers/. Importing
# them via their dotted path (rather than via sys.path manipulation) avoids
# shadowing the framework `helpers` package and matches the convention used by
# `a0_crosschatapi` and other reference plugins.
from usr.plugins.a0_voqualizer.helpers import registry as _registry_mod  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.frame import HEADER_SIZE, decode_frame, FrameError  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.codec import (  # noqa: E402
    convert_codec_to_pcm16,
    CodecError,
)
from usr.plugins.a0_voqualizer.helpers.heartbeat import build_pong  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.auth import (  # noqa: E402
    AUTH_ERROR_CODE,
    ensure_session_bearer_token,
    verify_session_bearer_token,
)
from usr.plugins.a0_voqualizer.helpers.jitter import JitterBuffer  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.asr import (  # noqa: E402
    ASRError,
    ASRProvider,
    AudioChunk,
    TranscriptKind,
    TranscriptResult,
    FasterWhisperASRProvider,
    LocalAIASRProvider,
    MockASRProvider,
    OpenAICompatibleASRProvider,
    OpenAIWhisperASRProvider,
)
from usr.plugins.a0_voqualizer.helpers.tts import (  # noqa: E402
    AudioChunk as TTSAudioChunk,
    MockTTSProvider,
    OpenAICompatibleTTSProvider,
    OpenAITTSProvider,
    PiperLocalTTSProvider,
    TTSProvider,
    TTSRequest,
    TTSError,
)
from usr.plugins.a0_voqualizer.helpers.registry import (  # noqa: E402
    BridgeRegistry,
    ConfigError,
    RegistryFull,
    load_config,
)
from usr.plugins.a0_voqualizer.helpers.session import (  # noqa: E402
    STATE_READY,
    STATE_ENDING,
    STATE_CLOSED,
    BridgeSession,
)
from usr.plugins.a0_voqualizer.helpers.error_taxonomy import (  # noqa: E402
    UNKNOWN_EVENT,
    HANDLER_ERROR,
    BAD_REQUEST,
    NO_SESSION,
    REGISTRY_FULL,
    BAD_AUDIO_CHUNK,
    log_voqualizer_error,
)


# Sentinel for "client didn't send this" so we can fall back to config defaults.
_UNSET = object()


def _server_time_ms() -> float:
    """Wall-clock epoch milliseconds (used for `server_time` and RTT math)."""
    return time.time() * 1000.0



def _clean_asr_transcript_text(text: str) -> str:
    """Remove common Whisper silence/echo hallucinations from final ASR text.

    Whisper-family endpoints often produce leading repeated filler like
    "Thank you. Thank you." when an utterance starts with silence, echo, or
    very low-information audio. Strip only a repeated leading run when
    additional prompt text follows, so legitimate short "thank you" messages
    remain intact.
    """

    if not isinstance(text, str):
        return ""
    clean = text.strip()
    if not clean:
        return ""
    leading_thanks = re.match(
        r"^((?:\s*thank\s+you[.!?,;:\-]*\s*){2,})(.*)$",
        clean,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if leading_thanks is not None:
        remainder = leading_thanks.group(2).strip()
        if re.search(r"[A-Za-z0-9]", remainder):
            clean = remainder
    return clean


def _asr_text_is_repeated_no_speech_hallucination(text: str) -> bool:
    """Detect low-information repeated Whisper no-speech hallucinations.

    Lemonade/Whisper-compatible endpoints can return repeated filler on silence
    or echo-contaminated audio, especially "Thank you Thank you Thank you".
    This suppresses repeated filler-only finals while preserving utterances that
    contain additional user content such as "thank you thank you can you...".
    """

    clean = " ".join(str(text or "").strip().split())
    if not clean:
        return True
    normalized = re.sub(r"[^a-z0-9']+", " ", clean.lower()).strip()
    words = normalized.split()
    if not words:
        return True

    # Repeated "thank you" pairs/triples are a common no-speech hallucination.
    if len(words) >= 4 and len(words) % 2 == 0 and all(
        words[i:i + 2] == ["thank", "you"] for i in range(0, len(words), 2)
    ):
        return True
    if len(words) >= 3 and all(word in {"thank", "you", "thanks"} for word in words):
        return True

    repeated_single_fillers = {"you", "um", "uh", "thanks", "bye", "goodbye"}
    if len(words) >= 3 and len(set(words)) == 1 and words[0] in repeated_single_fillers:
        return True

    filler_phrases = {
        "thank you thank you",
        "thank you thank you thank you",
        "thanks thanks thanks",
        "you you you",
    }
    return normalized in filler_phrases




def _safe_load_config() -> dict:
    """Load merged config. Falls back to defaults if overlay is corrupted."""
    try:
        return load_config()
    except (ConfigError, FileNotFoundError) as e:
        PrintStyle.warning(f"voqualizer: config load failed ({e}); using defaults")
        # Last-ditch: pretend no overlay exists.
        return load_config(apply_overlay=False)


def _build_capabilities(config: dict) -> dict[str, Any]:
    """Compose the ``capabilities`` block returned in ``voqualizer_ready``.

    Sourced entirely from the merged config so the admin REST endpoint and the
    WS handshake stay in sync.
    """
    asr = config.get("asr", {})
    tts = config.get("tts", {})
    proto = config.get("protocol", {})
    behavior = config.get("behavior", {})

    asr_providers = [
        {
            "name": p.get("name"),
            "type": p.get("type"),
            "streaming": p.get("streaming", False),
            "language": p.get("language", "auto"),
        }
        for p in asr.get("providers", [])
    ]
    tts_providers = [
        {
            "name": p.get("name"),
            "type": p.get("type"),
            "streaming": p.get("streaming", False),
            "voice": p.get("voice"),
            "sample_rate": p.get("sample_rate"),
        }
        for p in tts.get("providers", [])
    ]

    # Sample rates derivable from input codec strings (pcm16/8k → 8000, etc.).
    sample_rates: list[int] = []
    for codec in proto.get("input_codecs", []):
        if isinstance(codec, str) and "/" in codec:
            rate = codec.rsplit("/", 1)[-1].rstrip("k")
            try:
                sample_rates.append(int(rate) * 1000)
            except ValueError:
                pass
    sample_rates = sorted(set(sample_rates))

    return {
        "asr_providers": asr_providers,
        "asr_default": asr.get("default"),
        "tts_providers": tts_providers,
        "tts_default": tts.get("default"),
        "input_codecs": list(proto.get("input_codecs", [])),
        "output_codecs": list(proto.get("output_codecs", [])),
        "default_input_codec": proto.get("default_input_codec"),
        "default_output_codec": proto.get("default_output_codec"),
        "languages": ["auto", "en", "es", "fr", "de", "it", "pt", "nl", "ja", "zh", "ko", "ru"],
        "sample_rates": sample_rates,
        "heartbeat_interval_seconds": proto.get("heartbeat_interval_seconds", 15),
        "session_resume_window_seconds": proto.get("session_resume_window_seconds", 30),
        "barge_in_supported": bool(behavior.get("barge_in", True)),
        "protocol_version": "1.0",
    }


def _provider_config(config: dict, section: str, provider_name: str) -> dict[str, Any] | None:
    """Return one provider config from the merged config catalogue."""

    for provider in config.get(section, {}).get("providers", []):
        if provider.get("name") == provider_name:
            return dict(provider)
    return None


def _build_asr_provider(spec: Mapping[str, Any]) -> ASRProvider:
    """Construct an ASR provider from a config mapping.

    The factory intentionally recognizes ``mock`` for deterministic WS tests even
    though production defaults only include whisper/openai/localai providers.
    """

    provider_type = str(spec.get("type", "")).strip().lower()
    if provider_type in {"whisper", "faster-whisper", "whisper-local"}:
        return FasterWhisperASRProvider(spec)
    if provider_type in {"openai", "openai-whisper"}:
        return OpenAIWhisperASRProvider(spec)
    if provider_type in {"openai-compatible", "localai", "local-ai"}:
        return OpenAICompatibleASRProvider(spec)
    if provider_type == "mock":
        return MockASRProvider(spec, final_text=str(spec.get("final_text", "mock transcript")))
    raise ASRError(
        f"unsupported ASR provider type {provider_type!r}",
        code="ASR_PROVIDER_UNSUPPORTED",
        recoverable=True,
        details={"provider": spec.get("name"), "type": provider_type},
    )


def _build_tts_provider(spec: Mapping[str, Any]) -> TTSProvider:
    """Construct a TTS provider from a config mapping.

    The factory recognizes ``mock`` for deterministic WS tests while production
    configs can select Piper, hosted OpenAI, or OpenAI-compatible/LocalAI.
    """

    provider_type = str(spec.get("type", "")).strip().lower()
    if provider_type in {"piper", "piper-local", "piper_local"}:
        return PiperLocalTTSProvider(spec)
    if provider_type in {"openai", "openai-tts", "hosted-openai"}:
        return OpenAITTSProvider(spec)
    if provider_type in {"openai-compatible", "localai", "local-ai", "localai-tts"}:
        return OpenAICompatibleTTSProvider(spec)
    if provider_type == "mock":
        return MockTTSProvider(spec, chunk_size=int(spec.get("chunk_size", 8)))
    raise TTSError(
        f"unsupported TTS provider type {provider_type!r}",
        code="TTS_PROVIDER_UNSUPPORTED",
        recoverable=True,
        details={"provider": spec.get("name"), "type": provider_type},
    )


def _coerce_binary_payload(value: Any) -> bytes | None:
    """Coerce common Socket.IO/browser decoded binary shapes to bytes.

    Live browser clients may send Uint8Array/ArrayBuffer nested in an object.
    Depending on the Socket.IO/A0 dispatch path this can arrive in Python as
    bytes, bytearray, memoryview, a list of byte values, a Node-style
    ``{type: 'Buffer', data: [...]}``, or a mapping with numeric keys.  Keep the
    conversion narrow: every list/numeric mapping value must be an integer byte.
    """

    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        try:
            return base64.b64decode(value, validate=True)
        except Exception:
            return None
    if isinstance(value, list | tuple):
        try:
            return bytes(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, Mapping):
        # Node/socket.io decoded Buffer shape: {type: 'Buffer', data: [..]}.
        if str(value.get("type", "")).lower() == "buffer" and "data" in value:
            return _coerce_binary_payload(value.get("data"))
        # Browser binary wrappers often arrive as {data: [...]}, sometimes
        # nested under frame/audio/payload again.
        for key in ("frame_b64", "audio_b64", "payload_b64"):
            if key in value:
                coerced = _coerce_binary_payload(value.get(key))
                if coerced is not None:
                    return coerced
        for key in ("frame", "audio", "payload", "data", "buffer"):
            if key in value:
                coerced = _coerce_binary_payload(value.get(key))
                if coerced is not None:
                    return coerced
        # Some JSON serializers turn typed arrays into {'0': 1, '1': 2, ...}.
        if value:
            indexed: list[tuple[int, Any]] = []
            for key, item in value.items():
                try:
                    index = int(key)
                except (TypeError, ValueError):
                    indexed = []
                    break
                indexed.append((index, item))
            if indexed and sorted(index for index, _item in indexed) == list(range(len(indexed))):
                return _coerce_binary_payload([item for _index, item in sorted(indexed)])
    return None


def _extract_audio_frame_payload(data: Any) -> tuple[bytes, bool]:
    """Extract a binary protocol frame and optional final flag from event data."""

    if isinstance(data, Mapping):
        for key in ("frame", "audio", "payload", "data"):
            if key in data:
                raw = _coerce_binary_payload(data.get(key))
                if raw is not None:
                    if len(raw) < HEADER_SIZE:
                        raise FrameError("voqualizer_audio_chunk frame is shorter than A2 header")
                    return raw, bool(data.get("is_final", data.get("final", False)))
        raw = _coerce_binary_payload(data)
        if raw is not None:
            if len(raw) < HEADER_SIZE:
                raise FrameError("voqualizer_audio_chunk frame is shorter than A2 header")
            return raw, bool(data.get("is_final", data.get("final", False)))
    else:
        raw = _coerce_binary_payload(data)
        if raw is not None:
            if len(raw) < HEADER_SIZE:
                raise FrameError("voqualizer_audio_chunk frame is shorter than A2 header")
            return raw, False
    raise FrameError("voqualizer_audio_chunk requires a binary frame payload")


class WsVoqualizer(WsHandler):
    """Voqualizer WebSocket endpoint handler.

    One handler instance is constructed per connected sid (see
    :func:`helpers.ws.register_ws_namespace`).  The handler owns the binding
    between this connection and a :class:`BridgeSession` in the process-wide
    :class:`BridgeRegistry`.
    """

    # Reuse A0 framework authentication and CSRF enforcement. A0 base
    # WsHandler exposes requires_auth/requires_csrf as classmethods, so
    # we must override with a classmethod rather than a plain bool —
    # otherwise _check_security calls bool() and silently fails handler
    # activation, producing NO_HANDLERS at dispatch.
    @classmethod
    def requires_auth(cls) -> bool:
        return True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Per-connection: the session_id this WS connection is currently bound
        # to. We track only the id (not the BridgeSession instance) so that on
        # disconnect we can defer-remove via the registry without holding a
        # strong reference that survives a reconnect/resume.
        self._session_id: str | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _registry(self) -> BridgeRegistry:
        """Return the singleton, rebuilding it from config if needed."""
        reg = BridgeRegistry._instance
        if reg is None:
            try:
                cfg = _safe_load_config()
                reg = BridgeRegistry.from_config(cfg, replace=True)
            except Exception as e:
                PrintStyle.warning(
                    f"voqualizer: BridgeRegistry.from_config failed ({e}); using defaults"
                )
                reg = BridgeRegistry.instance()
        return reg

    async def _bind_sender(self, session: BridgeSession, sid: str) -> None:
        """Attach an outbound-emit callable so adapters can push events."""
        async def sender(event: str, payload: dict) -> None:
            await self.emit_to(sid, event, payload)
        session.sender = sender

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_disconnect(self, sid: str) -> None:
        # Tombstone the session for the resume window. The WS layer will not
        # automatically tear it down, so we do it here.
        if self._session_id:
            try:
                await self._registry().remove(self._session_id, tombstone=True)
            except Exception as e:
                PrintStyle.error(f"voqualizer: on_disconnect remove failed: {e}")
            self._session_id = None

    # ------------------------------------------------------------------
    # Event router
    # ------------------------------------------------------------------

    async def process(
        self,
        event: str,
        data: dict,
        sid: str,
    ) -> dict[str, Any] | WsResult | None:
        # Filter: only respond to voqualizer_ events so we don't accidentally
        # interfere with co-activated handlers in the same WS connection.
        if not event.startswith("voqualizer_"):
            return None

        try:
            if event == "voqualizer_init":
                return await self._handle_init(data, sid)
            if event == "voqualizer_ping":
                return await self._handle_ping(data, sid)
            if event == "voqualizer_control":
                return await self._handle_control(data, sid)
            if event == "voqualizer_audio_chunk":
                return await self._handle_audio_chunk(data, sid)
            if event == "voqualizer_user_text":
                return await self._handle_user_text(data, sid)
            log_voqualizer_error(UNKNOWN_EVENT, f"voqualizer does not handle {event!r}", operation=event)
            return WsResult.error(
                code=UNKNOWN_EVENT,
                message=f"voqualizer does not handle {event!r}",
            )
        except Exception as e:
            # Defensive: never let an exception escape the dispatcher. Include
            # exception type/repr because asyncio timeouts often stringify blank.
            message = f"voqualizer handler error: {type(e).__name__}: {e!r}"
            log_voqualizer_error(HANDLER_ERROR, message, operation=event, severity="error")
            return WsResult.error(
                code=HANDLER_ERROR,
                message=message,
            )

    # ------------------------------------------------------------------
    # voqualizer_init
    # ------------------------------------------------------------------

    async def _handle_init(self, data: dict, sid: str) -> dict[str, Any] | WsResult:
        cfg = _safe_load_config()
        registry = self._registry()

        asr_block = data.get("asr") or {}
        tts_block = data.get("tts") or {}
        proto = cfg.get("protocol", {})
        behavior = cfg.get("behavior", {})

        # Provider selection (validate against config catalog).
        asr_providers = {p["name"]: p for p in cfg.get("asr", {}).get("providers", [])}
        tts_providers = {p["name"]: p for p in cfg.get("tts", {}).get("providers", [])}
        asr_default = cfg.get("asr", {}).get("default")
        tts_default = cfg.get("tts", {}).get("default")

        asr_provider = asr_block.get("provider") or asr_default
        tts_provider = tts_block.get("provider") or tts_default
        if asr_provider not in asr_providers:
            return WsResult.error(
                code=BAD_REQUEST,
                message=f"unknown asr provider {asr_provider!r}",
                details={"available": list(asr_providers)},
            )
        if tts_provider not in tts_providers:
            return WsResult.error(
                code=BAD_REQUEST,
                message=f"unknown tts provider {tts_provider!r}",
                details={"available": list(tts_providers)},
            )

        # Codec selection.
        input_codecs = proto.get("input_codecs", [])
        output_codecs = proto.get("output_codecs", [])
        input_codec = asr_block.get("codec") or proto.get("default_input_codec")
        output_codec = tts_block.get("codec") or proto.get("default_output_codec")
        if input_codec not in input_codecs:
            return WsResult.error(
                code=BAD_REQUEST,
                message=f"unsupported input codec {input_codec!r}",
                details={"available": input_codecs},
            )
        if output_codec not in output_codecs:
            return WsResult.error(
                code=BAD_REQUEST,
                message=f"unsupported output codec {output_codec!r}",
                details={"available": output_codecs},
            )

        language = asr_block.get("language") or asr_providers[asr_provider].get(
            "language", "auto"
        )
        asr_final_silence_ms = asr_block.get("final_silence_ms")
        if asr_final_silence_ms is None:
            asr_final_silence_ms = asr_block.get("asr_final_silence_ms")
        if asr_final_silence_ms is None:
            asr_final_silence_ms = asr_providers[asr_provider].get("asr_final_silence_ms", 1000.0)
        try:
            asr_final_silence_ms = float(asr_final_silence_ms)
        except (TypeError, ValueError):
            return WsResult.error(
                code=BAD_REQUEST,
                message="asr_final_silence_ms must be numeric milliseconds",
            )
        if asr_final_silence_ms < 100 or asr_final_silence_ms > 10000:
            return WsResult.error(
                code=BAD_REQUEST,
                message="asr_final_silence_ms must be between 100 and 10000 ms",
            )

        asr_preroll_ms = asr_block.get("preroll_ms")
        if asr_preroll_ms is None:
            asr_preroll_ms = asr_block.get("asr_preroll_ms")
        if asr_preroll_ms is None:
            asr_preroll_ms = asr_providers[asr_provider].get("asr_preroll_ms", 600.0)
        try:
            asr_preroll_ms = float(asr_preroll_ms)
        except (TypeError, ValueError):
            return WsResult.error(
                code=BAD_REQUEST,
                message="asr_preroll_ms must be numeric milliseconds",
            )
        if asr_preroll_ms < 0 or asr_preroll_ms > 3000:
            return WsResult.error(
                code=BAD_REQUEST,
                message="asr_preroll_ms must be between 0 and 3000 ms",
            )

        # Session id: client-supplied or generated.
        session_id = data.get("session_id") or uuid.uuid4().hex
        if not isinstance(session_id, str) or not session_id.strip():
            return WsResult.error(
                code=BAD_REQUEST,
                message="session_id must be a non-empty string",
            )

        context_id = data.get("context_id") or ""
        barge_in_default = bool(behavior.get("barge_in", True))
        barge_in = bool(data.get("barge_in", barge_in_default))

        # Create or resume the BridgeSession.
        try:
            session, resumed = await registry.create_or_resume(
                session_id,
                context_id=context_id,
                asr_provider=asr_provider,
                tts_provider=tts_provider,
                input_codec=input_codec,
                output_codec=output_codec,
                language=language,
                barge_in=barge_in,
            )
        except RegistryFull as e:
            return WsResult.error(
                code=REGISTRY_FULL,
                message=str(e),
                details={"limit": e.limit, "current": e.current},
            )

        session.metadata["asr_final_silence_ms"] = asr_final_silence_ms
        session.metadata["asr_preroll_ms"] = asr_preroll_ms
        asr_submit_mode = str(data.get("asr_submit_mode") or asr_block.get("submit_mode") or "context_bridge").strip()
        if asr_submit_mode not in {"context_bridge", "frontend_prompt"}:
            asr_submit_mode = "context_bridge"
        session.metadata["asr_submit_mode"] = asr_submit_mode
        # A8.4+/v0.1.1: honor optional `tts.enabled=false` from init so the Speaker
        # toggle in the in-GUI buttons can hard-mute TTS for this session.
        tts_enabled_init = tts_block.get("enabled")
        if isinstance(tts_enabled_init, bool):
            session.tts_enabled = tts_enabled_init

        # Wire up sender + bookkeeping for this connection.
        await self._bind_sender(session, sid)
        self._session_id = session.session_id

        # Move into ready state (idempotent on resume).
        try:
            session.transition_to(STATE_READY)
        except Exception:
            pass  # already in a later state on resume — fine

        PrintStyle.info(
            f"voqualizer: {'resumed' if resumed else 'created'} session "
            f"{session.session_id} (asr={asr_provider}, tts={tts_provider}, "
            f"in={input_codec}, out={output_codec})"
        )

        bearer_token = ensure_session_bearer_token(session)

        return {
            "event": "voqualizer_ready",
            "session_id": session.session_id,
            "bearer_token": bearer_token,
            "resumed": resumed,
            "context_id": session.context_id,
            "server_time": _server_time_ms(),
            "capabilities": _build_capabilities(cfg),
            "negotiated": {
                "asr_provider": asr_provider,
                "tts_provider": tts_provider,
                "input_codec": input_codec,
                "output_codec": output_codec,
                "language": language,
                "barge_in": barge_in,
                "asr_final_silence_ms": asr_final_silence_ms,
                "asr_preroll_ms": asr_preroll_ms,
                "asr_submit_mode": session.metadata.get("asr_submit_mode", "context_bridge"),
            },
        }


    def _verify_session_token(
        self,
        session: BridgeSession,
        data: Any,
        operation: str,
    ) -> WsResult | None:
        """Validate the per-session bearer token for session-bound events.

        A0 framework auth/CSRF admits the WebSocket connection itself; this
        second token prevents another handler/client path from operating on a
        live Voqualizer session without the token issued at ``voqualizer_init``.
        """

        if verify_session_bearer_token(session, data):
            return None
        log_voqualizer_error(
            AUTH_ERROR_CODE,
            "valid voqualizer session bearer token required",
            session_id=session.session_id,
            operation=operation,
        )
        return WsResult.error(
            code=AUTH_ERROR_CODE,
            message="valid voqualizer session bearer token required",
            details={"session_id": session.session_id, "operation": operation},
        )

    # ------------------------------------------------------------------
    # voqualizer_ping
    # ------------------------------------------------------------------

    async def _handle_ping(self, data: dict, sid: str) -> dict[str, Any]:
        client_ts = data.get("ts")
        pong = build_pong(client_ts)

        # Touch the session if we have one bound, so heartbeats keep it alive.
        if self._session_id is not None:
            sess = self._registry().get(self._session_id)
            if sess is not None:
                sess.touch()

        return pong.to_payload()


    # ------------------------------------------------------------------
    # voqualizer_audio_chunk
    # ------------------------------------------------------------------

    async def _asr_provider_for_session(self, session: BridgeSession, cfg: dict) -> ASRProvider:
        """Return cached ASR provider instance for this bridge session."""

        cached = session.metadata.get("asr_provider_instance")
        if isinstance(cached, ASRProvider):
            return cached

        spec = _provider_config(cfg, "asr", session.asr_provider)
        if spec is None:
            raise ASRError(
                f"unknown ASR provider {session.asr_provider!r}",
                code="ASR_PROVIDER_NOT_FOUND",
                details={"provider": session.asr_provider},
            )
        provider = _build_asr_provider(spec)
        await provider.start()
        session.metadata["asr_provider_instance"] = provider
        return provider

    def _jitter_for_session(self, session: BridgeSession) -> JitterBuffer:
        jitter = session.metadata.get("jitter_buffer")
        if isinstance(jitter, JitterBuffer):
            return jitter
        jitter = JitterBuffer(window_size=16)
        session.metadata["jitter_buffer"] = jitter
        return jitter

    async def _emit_transcript(self, session: BridgeSession, result) -> None:
        payload = result.to_protocol_event()
        event = payload.pop("event")
        if event == "voqualizer_asr_final":
            cleaned_text = _clean_asr_transcript_text(str(payload.get("text") or ""))
            if not cleaned_text:
                return
            payload["text"] = cleaned_text
        payload["session_id"] = session.session_id
        if session.sender is not None:
            await session.sender(event, payload)

        # A5 context pipeline: final ASR transcripts enter the selected A0
        # AgentContext via ContextBridge.  The tester can now bind a live
        # session to an existing context_id; if omitted, the bridge creates a
        # Voqualizer context as before.  Keep failures non-fatal so transcript
        # display/audio ingress remain healthy even if the A0 context runtime is
        # temporarily unavailable.
        if event == "voqualizer_asr_final":
            text = str(payload.get("text") or "").strip()
            if session.metadata.get("asr_submit_mode") == "frontend_prompt":
                session.metadata["context_injection_skipped"] = "frontend_prompt"
                return
            if text:
                try:
                    from usr.plugins.a0_voqualizer.helpers.context_bridge import get_default_context_bridge

                    bridge = get_default_context_bridge()
                    bridge.inject_transcript(
                        session.session_id,
                        text,
                        context_id=session.context_id or None,
                        metadata={
                            "source": "voqualizer_asr_final",
                            "provider": session.asr_provider,
                            "asr_provider": session.asr_provider,
                        },
                    )
                    session.metadata["context_injections"] = int(session.metadata.get("context_injections", 0)) + 1
                except Exception as exc:
                    session.metadata["context_injection_errors"] = int(session.metadata.get("context_injection_errors", 0)) + 1
                    log_voqualizer_error(
                        HANDLER_ERROR,
                        f"context transcript injection failed: {type(exc).__name__}: {exc!r}",
                        session_id=session.session_id,
                        operation="voqualizer_asr_final",
                        severity="warning",
                    )

    @staticmethod
    def _chunk_duration_ms(chunk: AudioChunk) -> float:
        """Return PCM16 chunk duration in milliseconds."""
        if chunk.sample_rate <= 0:
            return 0.0
        return (len(chunk.pcm16) / 2 / chunk.sample_rate) * 1000.0

    @staticmethod
    def _chunk_rms(chunk: AudioChunk) -> float:
        """Return a lightweight RMS level for PCM16 little-endian audio."""
        data = chunk.pcm16
        if len(data) < 2:
            return 0.0
        total = 0.0
        count = 0
        # Avoid importing numpy for runtime plugin path; this is cheap enough for
        # 20ms frames and works in the minimal A0 runtime.
        for i in range(0, len(data) - 1, 2):
            sample = int.from_bytes(data[i:i + 2], "little", signed=True)
            total += float(sample * sample)
            count += 1
        if count <= 0:
            return 0.0
        return (total / count) ** 0.5

    @staticmethod
    def _new_asr_utterance_state(session: BridgeSession) -> dict[str, Any]:
        """Create a complete ASR utterance buffer state.

        Keep this in one place so the initial state and the post-final reset
        state cannot drift. A partial reset can make the first utterance work
        but leave later utterances without the expected pre-roll/segment fields.
        """
        return {
            "chunks": [],
            "preroll_chunks": [],
            "leading_audio_chunks": [],
            "duration_ms": 0.0,
            "speech_ms": 0.0,
            "trailing_silence_ms": 0.0,
            "has_speech": False,
            "speech_started_at_ms": None,
            "first_seq": None,
            "first_ts_ms": None,
            "first_audio_at_ms": None,
            "speech_start_seq": None,
            "speech_start_ts_ms": None,
            "last_partial_at_ms": 0.0,
            "last_partial_text": "",
            "last_final_text": "",
            "utterance_generation": int(session.metadata.get("asr_utterance_generation", 0)),
            "utterance_id": str(session.metadata.get("asr_current_utterance_id", "")),
            "preroll_chunks_available": 0,
            "preroll_chunks_merged": 0,
            "preroll_merged": False,
            "leading_chunks_available": 0,
            "leading_chunks_merged": 0,
            "leading_first_seq": None,
            "leading_last_seq": None,
            "leading_ring_ms": 0.0,
            "segment_first_seq": None,
            "segment_last_seq": None,
        }

    def _asr_utterance_state_for_session(self, session: BridgeSession) -> dict[str, Any]:
        """Return per-session utterance buffer state for batch HTTP ASR."""
        state = session.metadata.get("asr_utterance_state")
        if isinstance(state, dict):
            return state
        state = self._new_asr_utterance_state(session)
        session.metadata["asr_utterance_state"] = state
        return state

    @staticmethod
    def _asr_text_is_useful(text: str) -> bool:
        """Filter empty and very low-value batch Whisper fragments."""
        clean = " ".join(str(text or "").strip().split())
        if not clean:
            return False
        if _asr_text_is_repeated_no_speech_hallucination(clean):
            return False
        # Single filler tokens from tiny/noisy windows are usually worse than no
        # update. Keep meaningful one-word utterances once final if punctuation
        # or length suggests intent; partials/finals call this as a basic guard.
        return len(clean) >= 2

    @staticmethod
    def _dedupe_audio_chunks_by_seq(chunks: list[AudioChunk]) -> list[AudioChunk]:
        """Return chunks without duplicate seq values, preserving order."""
        seen: set[int] = set()
        out: list[AudioChunk] = []
        for item in chunks:
            seq = int(getattr(item, "seq", -1))
            if seq in seen:
                continue
            seen.add(seq)
            out.append(item)
        return out

    async def _transcribe_utterance_segment(
        self,
        session: BridgeSession,
        provider: ASRProvider,
        segment: list[AudioChunk],
        *,
        language: str | None,
        kind: TranscriptKind,
        metadata: Mapping[str, Any],
    ) -> TranscriptResult | None:
        """Run provider.transcribe() and coerce the event kind for utterance ASR."""
        request_started = _server_time_ms()
        result = await provider.transcribe(segment, language=language, metadata=metadata)
        request_ms = round(_server_time_ms() - request_started, 3)
        text = " ".join(result.text.strip().split())
        if not self._asr_text_is_useful(text):
            session.metadata["asr_empty_results_suppressed"] = int(session.metadata.get("asr_empty_results_suppressed", 0)) + 1
            session.metadata["asr_last_suppressed_text"] = text
            session.metadata["asr_last_request_ms"] = request_ms
            return None
        result_metadata = dict(result.metadata or {})
        result_metadata.update(dict(metadata or {}))
        result_metadata.update({
            "asr_request_ms": request_ms,
            "provider": result.provider,
            "final_transcript": text,
        })
        return TranscriptResult(
            text=text,
            kind=kind,
            confidence=result.confidence,
            t_start=result.t_start,
            t_end=result.t_end,
            language=result.language,
            provider=result.provider,
            metadata=result_metadata,
        )

    async def _emit_transcribed_utterance_segment(
        self,
        session: BridgeSession,
        provider: ASRProvider,
        segment: list[AudioChunk],
        *,
        language: str | None,
        kind: TranscriptKind,
        metadata: Mapping[str, Any],
        duplicate_key: str | None = None,
    ) -> None:
        """Transcribe and emit outside the audio-chunk ack path.

        Batch HTTP ASR calls can exceed the Socket.IO event ack budget.  Running
        them inline made ``voqualizer_audio_chunk`` fail with blank
        ``HANDLER_ERROR``/timeout logs and caused the browser to think audio
        ingress was broken.  The audio handler should only ingest/ack frames;
        transcript events can arrive asynchronously.
        """
        try:
            result = await self._transcribe_utterance_segment(
                session,
                provider,
                segment,
                language=language,
                kind=kind,
                metadata=metadata,
            )
            if result is None:
                return
            meta = dict(result.metadata or {})
            utterance_generation = meta.get("utterance_generation")
            if utterance_generation is not None:
                current_generation = int(session.metadata.get("asr_utterance_generation", 0) or 0)
                generation_is_stale = int(utterance_generation) < current_generation
                if generation_is_stale and result.kind != TranscriptKind.FINAL:
                    session.metadata["stale_asr_result_ignored"] = int(session.metadata.get("stale_asr_result_ignored", 0)) + 1
                    session.metadata["asr_last_stale_generation"] = int(utterance_generation)
                    return
                if generation_is_stale and result.kind == TranscriptKind.FINAL:
                    words = [w for w in result.text.split() if w]
                    # Conversational-mode first-word splits show up as a short
                    # stale final after a newer utterance generation has already
                    # started. Suppress those stale leading fragments, but keep
                    # explicit PTT/stop finals and longer natural utterances.
                    if meta.get("final_reason") != "forced_final" and len(words) <= 4:
                        session.metadata["stale_asr_result_ignored"] = int(session.metadata.get("stale_asr_result_ignored", 0)) + 1
                        session.metadata["asr_last_stale_generation"] = int(utterance_generation)
                        session.metadata["asr_last_stale_final_text"] = result.text
                        return
            if duplicate_key:
                previous = str(session.metadata.get(duplicate_key, ""))
                if result.text == previous:
                    return
                session.metadata[duplicate_key] = result.text
            await self._emit_transcript(session, result)
        except ASRError as exc:
            session.metadata["asr_background_errors"] = int(session.metadata.get("asr_background_errors", 0)) + 1
            if session.sender is not None:
                payload = exc.to_dict()
                payload["session_id"] = session.session_id
                await session.sender("voqualizer_error", payload)
        except Exception as exc:
            session.metadata["asr_background_errors"] = int(session.metadata.get("asr_background_errors", 0)) + 1
            log_voqualizer_error(
                HANDLER_ERROR,
                f"background ASR transcription failed: {type(exc).__name__}: {exc!r}",
                session_id=session.session_id,
                operation="voqualizer_audio_chunk",
                severity="error",
            )

    def _schedule_asr_utterance_transcription(
        self,
        session: BridgeSession,
        provider: ASRProvider,
        segment: list[AudioChunk],
        *,
        language: str | None,
        kind: TranscriptKind,
        metadata: Mapping[str, Any],
        duplicate_key: str | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._emit_transcribed_utterance_segment(
                session,
                provider,
                segment,
                language=language,
                kind=kind,
                metadata=metadata,
                duplicate_key=duplicate_key,
            )
        )
        tasks = session.metadata.setdefault("asr_background_tasks", set())
        if isinstance(tasks, set):
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def _cancel_tts_for_barge_in(self, session: BridgeSession, *, reason: str = "barge_in") -> None:
        """Immediately stop any active TTS playback for user barge-in.

        Setting ``session.cancel_tts`` stops backend TTS pumps at their next
        cancellation check.  Emitting ``voqualizer_tts_done(cancelled=True)``
        here also lets the browser stop queued playback immediately instead of
        waiting for the provider stream to yield again.
        """

        session.cancel_in_flight_tts()
        utterance_id = str(session.metadata.get("tts_active_utterance_id") or "")
        if not utterance_id or session.sender is None:
            return
        notified = session.metadata.setdefault("tts_barge_in_notified", set())
        if isinstance(notified, set) and utterance_id in notified:
            return
        if isinstance(notified, set):
            notified.add(utterance_id)
        try:
            await self._emit_tts_done(
                session,
                utterance_id=utterance_id,
                cancelled=True,
                chunks=int(session.metadata.get("tts_chunks_emitted", 0) or 0),
                reason=reason,
            )
        except Exception:
            pass


    async def _process_batch_asr_chunk(
        self,
        session: BridgeSession,
        provider: ASRProvider,
        chunk: AudioChunk,
        *,
        language: str | None,
    ) -> int:
        """Utterance-buffer batch/non-streaming ASR providers.

        OpenAI-compatible Whisper endpoints are batch transcription APIs. They
        produce poor fragments when called with 20ms browser mic frames. Buffer
        frames into an utterance, emit interim batch partials about once per
        second, and emit a final after trailing silence or a max segment cap.
        """

        partial_interval_ms = float(session.metadata.get("asr_partial_interval_ms", 1000.0) or 1000.0)
        final_silence_ms = float(session.metadata.get("asr_final_silence_ms", 1000.0) or 1000.0)
        max_segment_ms = float(session.metadata.get("asr_max_segment_ms", 8000.0) or 8000.0)
        min_speech_ms = float(session.metadata.get("asr_min_speech_ms", 500.0) or 500.0)
        speech_rms = float(session.metadata.get("asr_speech_rms", 250.0) or 250.0)
        preroll_ms = float(session.metadata.get("asr_preroll_ms", 600.0) or 600.0)

        state = self._asr_utterance_state_for_session(session)
        chunks: list[AudioChunk] = state.setdefault("chunks", [])
        preroll_chunks: list[AudioChunk] = state.setdefault("preroll_chunks", [])
        leading_audio_chunks: list[AudioChunk] = state.setdefault("leading_audio_chunks", [])
        chunk_ms = self._chunk_duration_ms(chunk)
        preroll_max_chunks = max(1, int((preroll_ms / max(chunk_ms, 1.0)) + 0.999))
        leading_max_chunks = preroll_max_chunks

        # Always keep the last `asr_preroll_ms` of audio, regardless of whether
        # current RMS/VAD thinks speech has started. This captures early speech
        # frames when speech detection fires late, e.g. missing unique first
        # tokens like Alpha/Pineapple.
        leading_audio_chunks.append(chunk)
        if len(leading_audio_chunks) > leading_max_chunks:
            del leading_audio_chunks[:len(leading_audio_chunks) - leading_max_chunks]
        state["leading_ring_ms"] = round(len(leading_audio_chunks) * chunk_ms, 3)

        if state.get("first_seq") is None:
            state["first_seq"] = chunk.seq
            state["first_ts_ms"] = chunk.ts_ms
            state["first_audio_at_ms"] = _server_time_ms()
        state["last_seq"] = chunk.seq
        state["last_ts_ms"] = chunk.ts_ms

        rms = self._chunk_rms(chunk)
        if rms >= speech_rms:
            was_speaking = bool(state.get("has_speech"))
            if not was_speaking:
                generation = int(session.metadata.get("asr_utterance_generation", 0) or 0) + 1
                session.metadata["asr_utterance_generation"] = generation
                utterance_id = f"asr-{session.session_id}-{generation}"
                session.metadata["asr_current_utterance_id"] = utterance_id
                state["utterance_generation"] = generation
                state["utterance_id"] = utterance_id
                state["speech_started_at_ms"] = float(state.get("duration_ms", 0.0))
                state["speech_start_seq"] = chunk.seq
                state["speech_start_ts_ms"] = chunk.ts_ms
                # Include the always-on leading ring immediately before detected
                # speech so first words/plosives are not clipped by RMS/VAD
                # threshold crossing. Unlike the older pre-speech-only buffer,
                # this ring also contains early speech frames if detection fires
                # late. Copy it exactly once into the ASR provider segment.
                leading_snapshot = list(leading_audio_chunks)
                state["leading_chunks_available"] = len(leading_snapshot)
                state["leading_first_seq"] = leading_snapshot[0].seq if leading_snapshot else None
                state["leading_last_seq"] = leading_snapshot[-1].seq if leading_snapshot else None
                chunks.extend(leading_snapshot)
                chunks[:] = self._dedupe_audio_chunks_by_seq(chunks)
                state["leading_chunks_merged"] = len(leading_snapshot)

                # Keep legacy names populated for existing diagnostics/tests.
                state["preroll_chunks_available"] = len(leading_snapshot)
                state["preroll_chunks_merged"] = len(leading_snapshot)
                state["preroll_merged"] = bool(leading_snapshot)
                preroll_chunks.clear()
            if not chunks or chunks[-1].seq != chunk.seq:
                chunks.append(chunk)
            state["has_speech"] = True
            state["speech_ms"] = float(state.get("speech_ms", 0.0)) + chunk_ms
            state["trailing_silence_ms"] = 0.0
            if session.barge_in and not was_speaking and session.metadata.get("tts_active_utterance_id"):
                await self._cancel_tts_for_barge_in(session)
        elif state.get("has_speech"):
            chunks.append(chunk)
            state["trailing_silence_ms"] = float(state.get("trailing_silence_ms", 0.0)) + chunk_ms
        else:
            preroll_chunks.append(chunk)
            if len(preroll_chunks) > preroll_max_chunks:
                del preroll_chunks[:len(preroll_chunks) - preroll_max_chunks]

        state["duration_ms"] = float(state.get("duration_ms", 0.0)) + chunk_ms

        duration_ms = float(state.get("duration_ms", 0.0))
        speech_ms = float(state.get("speech_ms", 0.0))
        trailing_ms = float(state.get("trailing_silence_ms", 0.0))
        has_speech = bool(state.get("has_speech")) and speech_ms >= min_speech_ms
        is_forced_final = bool(chunk.is_final)
        final_reason = "forced_final" if is_forced_final else ("trailing_silence" if (has_speech and trailing_ms >= final_silence_ms) else ("max_segment_ms" if (has_speech and duration_ms >= max_segment_ms) else ""))
        should_final = is_forced_final or bool(final_reason)
        should_partial = (
            has_speech
            and not should_final
            and duration_ms - float(state.get("last_partial_at_ms", 0.0)) >= partial_interval_ms
        )

        if not should_partial and not should_final:
            return 0

        segment = list(chunks)
        state["segment_first_seq"] = segment[0].seq if segment else None
        state["segment_last_seq"] = segment[-1].seq if segment else None
        metadata = {
            "session_id": session.session_id,
            "seq": chunk.seq,
            "buffered_chunks": len(segment),
            "buffered_ms": round(duration_ms, 3),
            "speech_ms": round(speech_ms, 3),
            "trailing_silence_ms": round(trailing_ms, 3),
            "first_seq": state.get("first_seq"),
            "first_ts_ms": state.get("first_ts_ms"),
            "speech_start_seq": state.get("speech_start_seq"),
            "speech_start_ts_ms": state.get("speech_start_ts_ms"),
            "speech_started_at_ms": state.get("speech_started_at_ms"),
            "asr_partial_interval_ms": partial_interval_ms,
            "asr_final_silence_ms": final_silence_ms,
            "asr_max_segment_ms": max_segment_ms,
            "asr_min_speech_ms": min_speech_ms,
            "asr_preroll_ms": preroll_ms,
            "preroll_chunks_available": state.get("preroll_chunks_available"),
            "preroll_chunks_merged": state.get("preroll_chunks_merged"),
            "preroll_merged": state.get("preroll_merged"),
            "leading_chunks_available": state.get("leading_chunks_available"),
            "leading_chunks_merged": state.get("leading_chunks_merged"),
            "leading_first_seq": state.get("leading_first_seq"),
            "leading_last_seq": state.get("leading_last_seq"),
            "leading_ring_ms": state.get("leading_ring_ms"),
            "segment_first_seq": state.get("segment_first_seq"),
            "segment_last_seq": state.get("segment_last_seq"),
            "utterance_id": state.get("utterance_id"),
            "utterance_generation": state.get("utterance_generation"),
            "is_forced_final": is_forced_final,
            "chunk_is_final": bool(chunk.is_final),
            "final_reason": final_reason,
            "last_seq": state.get("last_seq"),
            "last_ts_ms": state.get("last_ts_ms"),
        }

        if should_partial:
            state["last_partial_at_ms"] = duration_ms
            self._schedule_asr_utterance_transcription(
                session,
                provider,
                segment,
                language=language,
                kind=TranscriptKind.PARTIAL,
                metadata={**metadata, "utterance_event": "partial"},
                duplicate_key="asr_last_partial_text",
            )
            return 0

        # Final: schedule transcription and reset utterance immediately so audio
        # acks remain fast even when the HTTP ASR endpoint is slow.
        session.metadata["asr_last_final_metadata"] = metadata
        self._schedule_asr_utterance_transcription(
            session,
            provider,
            segment,
            language=language,
            kind=TranscriptKind.FINAL,
            metadata={**metadata, "utterance_event": "final"},
            duplicate_key="asr_last_final_text",
        )
        state.clear()
        state.update(self._new_asr_utterance_state(session))
        session.metadata["asr_utterance_state_reset_at_ms"] = now_ms()
        return 0

    async def _handle_audio_chunk(self, data: Any, sid: str) -> dict[str, Any] | WsResult:
        if self._session_id is None:
            return WsResult.error(
                code=NO_SESSION,
                message="send voqualizer_init before voqualizer_audio_chunk",
            )
        session = self._registry().get(self._session_id)
        if session is None:
            return WsResult.error(
                code=NO_SESSION,
                message=f"session {self._session_id!r} not active",
            )
        auth_error = self._verify_session_token(session, data, "voqualizer_audio_chunk")
        if auth_error is not None:
            return auth_error

        try:
            raw_frame, is_final = _extract_audio_frame_payload(data)
            parsed = decode_frame(raw_frame)
            queued_without_drop = session.enqueue_audio(parsed)
            ready_frames = self._jitter_for_session(session).push(parsed)
            emitted = 0
            cfg = _safe_load_config()
            provider = await self._asr_provider_for_session(session, cfg)

            provider_caps = provider.capabilities()
            is_batch_asr = not bool(getattr(provider_caps, "streaming", False))

            for frame in ready_frames:
                pcm16 = convert_codec_to_pcm16(frame.payload, session.input_codec, dst_rate=16000)
                chunk = AudioChunk(
                    pcm16=pcm16,
                    sample_rate=16000,
                    seq=frame.seq,
                    ts_ms=frame.ts_ms,
                    is_final=is_final,
                )
                if is_batch_asr:
                    emitted += await self._process_batch_asr_chunk(
                        session,
                        provider,
                        chunk,
                        language=session.language,
                    )
                    continue
                async for result in provider.stream(
                    [chunk],
                    language=session.language,
                    metadata={"session_id": session.session_id, "seq": frame.seq},
                ):
                    await self._emit_transcript(session, result)
                    emitted += 1

            ack = {
                "event": "voqualizer_audio_ack",
                "session_id": session.session_id,
                "seq": parsed.seq,
                "ts_ms": parsed.ts_ms,
                "queued": queued_without_drop,
                "emitted": emitted,
                "backpressure": session.backpressure_metrics(),
            }
            # GUI conversation-mode fallback visibility.  In the full A0 GUI
            # extension path, request ACKs are reliable even in cases where
            # server-pushed plugin events (voqualizer_asr_partial/final) do not
            # reach conversation-mode.js.  Mirror the latest transcript markers
            # into ACKs so the GUI can populate the visible prompt as a robust
            # fallback while keeping the normal context bridge path active.
            for key in (
                "asr_last_partial_text",
                "asr_last_partial_metadata",
                "asr_last_final_text",
                "asr_last_final_metadata",
                "context_injection_skipped",
                "context_injections",
                "context_injection_errors",
            ):
                if key in session.metadata:
                    ack[key] = session.metadata.get(key)
            return ack
        except (FrameError, CodecError, ASRError, ValueError, TypeError) as exc:
            if isinstance(exc, ASRError):
                err = exc.to_dict()
                return WsResult.error(
                    code=err["code"],
                    message=err["message"],
                    details={"recoverable": err["recoverable"], **err.get("details", {})},
                )
            log_voqualizer_error(BAD_AUDIO_CHUNK, str(exc), session_id=session.session_id, operation="voqualizer_audio_chunk")
            return WsResult.error(
                code=BAD_AUDIO_CHUNK,
                message=str(exc),
            )

    # ------------------------------------------------------------------
    # voqualizer_user_text / TTS synthesis path (A4.5)
    # ------------------------------------------------------------------

    async def _tts_provider_for_session(self, session: BridgeSession, cfg: dict) -> TTSProvider:
        """Return cached TTS provider instance for this bridge session."""

        cached = session.metadata.get("tts_provider_instance")
        if isinstance(cached, TTSProvider):
            return cached

        spec = _provider_config(cfg, "tts", session.tts_provider)
        if spec is None:
            raise TTSError(
                f"unknown TTS provider {session.tts_provider!r}",
                code="TTS_PROVIDER_NOT_FOUND",
                details={"provider": session.tts_provider},
            )
        provider = _build_tts_provider(spec)
        await provider.start()
        session.metadata["tts_provider_instance"] = provider
        return provider

    async def _emit_tts_chunk(self, session: BridgeSession, chunk: TTSAudioChunk) -> dict[str, Any]:
        payload = chunk.event_payload()
        event = payload.pop("event")
        payload["session_id"] = session.session_id
        # Socket.IO can carry bytes in object payloads; A4.5 keeps metadata
        # compatible with AudioChunk.event_payload() while exposing bytes to
        # deterministic tests and browser clients.
        payload["audio"] = chunk.data
        # JSON-safe fallback for browser/A0 dispatch paths that do not preserve
        # nested binary values reliably.
        payload["audio_b64"] = base64.b64encode(chunk.data).decode("ascii")
        payload["audio_encoding"] = "base64"
        if session.sender is not None:
            await session.sender(event, payload)

        # Also return a JSON-safe copy for direct-TTS acknowledgement fallback.
        # The GUI uses this when synthesized chunks are counted by the backend
        # but pushed Socket.IO chunk events are not observed by the browser.
        ack_payload = dict(payload)
        ack_payload["event"] = event
        ack_payload.pop("audio", None)
        return ack_payload

    async def _emit_tts_done(
        self,
        session: BridgeSession,
        *,
        utterance_id: str,
        cancelled: bool = False,
        chunks: int = 0,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": "voqualizer_tts_done",
            "session_id": session.session_id,
            "utterance_id": utterance_id,
            "cancelled": cancelled,
            "chunks": chunks,
        }
        if reason:
            payload["reason"] = reason
        if session.sender is not None:
            emit_payload = dict(payload)
            emit_payload.pop("event", None)
            await session.sender("voqualizer_tts_done", emit_payload)
        return payload

    async def _emit_tts_push_probe(
        self,
        session: BridgeSession,
        *,
        utterance_id: str,
        chunks: int = 0,
        pushed_emit_count: int = 0,
        pushed_done_emit_count: int = 0,
        delay_ms: int = 100,
    ) -> None:
        """Emit a delayed diagnostic probe after direct-TTS ACK unwinds.

        This intentionally does not affect playback. It lets the GUI distinguish
        between same-handler push events that are not delivered while the inbound
        request is running and pushed events that cannot reach the GUI socket at
        all. ACK chunks remain the reliable playback path.
        """
        try:
            await asyncio.sleep(max(0, delay_ms) / 1000)
            if session.sender is None:
                return
            await session.sender("voqualizer_tts_push_probe", {
                "session_id": session.session_id,
                "utterance_id": utterance_id,
                "chunks": chunks,
                "pushed_emit_count": pushed_emit_count,
                "pushed_done_emit_count": pushed_done_emit_count,
                "sender_present": True,
                "delay_ms": delay_ms,
            })
        except Exception as exc:
            log_voqualizer_error(
                "TTS_PUSH_PROBE_ERROR",
                f"voqualizer TTS push probe failed: {type(exc).__name__}: {exc!r}",
                session_id=session.session_id,
                operation="voqualizer_tts_push_probe",
                severity="warning",
            )

    async def _emit_tts_error(self, session: BridgeSession, err: TTSError) -> None:
        if session.sender is not None:
            payload = err.to_dict()
            payload["session_id"] = session.session_id
            log_voqualizer_error(
                payload.get("code", "TTS_ERROR"),
                payload.get("message", "tts error"),
                session_id=session.session_id,
                operation="voqualizer_user_text",
                details=payload.get("details") if isinstance(payload.get("details"), dict) else None,
            )
            await session.sender("voqualizer_error", payload)

    async def _handle_user_text(self, data: Mapping[str, Any], sid: str) -> dict[str, Any] | WsResult:
        if self._session_id is None:
            return WsResult.error(
                code=NO_SESSION,
                message="send voqualizer_init before voqualizer_user_text",
            )
        session = self._registry().get(self._session_id)
        if session is None:
            return WsResult.error(
                code=NO_SESSION,
                message=f"session {self._session_id!r} not active",
            )
        if not isinstance(data, Mapping):
            return WsResult.error(code=BAD_REQUEST, message="voqualizer_user_text requires an object payload")
        auth_error = self._verify_session_token(session, data, "voqualizer_user_text")
        if auth_error is not None:
            return auth_error
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return WsResult.error(code=BAD_REQUEST, message="voqualizer_user_text.text must be a non-empty string")

        utterance_id = str(data.get("utterance_id") or uuid.uuid4().hex)
        voice = data.get("voice")
        if voice is not None:
            voice = str(voice)

        cfg = _safe_load_config()
        provider_spec = _provider_config(cfg, "tts", session.tts_provider) or {}
        provider_format = str(
            provider_spec.get("format")
            or provider_spec.get("response_format")
            or (provider_spec.get("options") or {}).get("format")
            or (provider_spec.get("options") or {}).get("response_format")
            or ""
        ).lower()
        provider_sample_rate = int(
            provider_spec.get("sample_rate")
            or (provider_spec.get("options") or {}).get("sample_rate")
            or 0
        )
        if provider_format == "pcm" and provider_sample_rate == 24000:
            default_codec = "pcm16/24k"
        elif provider_format == "pcm" and provider_sample_rate == 16000:
            default_codec = "pcm16/16k"
        else:
            default_codec = session.output_codec or "pcm16/16k"
        codec = str(data.get("codec") or default_codec)
        sample_rate = int(data.get("sample_rate") or provider_sample_rate or (24000 if codec == "pcm16/24k" else 16000))
        provider_speed = float(
            provider_spec.get("speed")
            or (provider_spec.get("options") or {}).get("speed")
            or 1.0
        )
        speed = float(data.get("speed") or provider_speed)
        metadata = dict(data.get("metadata") or {})
        metadata.setdefault("source", "voqualizer_user_text")
        if provider_format:
            metadata.setdefault("response_format", provider_format)

        session.reset_cancel()
        session.metadata["tts_chunks_emitted"] = 0
        if isinstance(session.metadata.get("tts_barge_in_notified"), set):
            session.metadata["tts_barge_in_notified"].clear()
        request = TTSRequest(
            text=text,
            utterance_id=utterance_id,
            voice=voice,
            codec=codec,
            sample_rate=sample_rate,
            speed=speed,
            metadata=metadata,
        )

        chunks = 0
        pushed_emit_count = 0
        pushed_done_emit_count = 0
        sender_present = session.sender is not None
        ack_tts_chunks: list[dict[str, Any]] = []
        provider: TTSProvider | None = None
        try:
            # Use a fresh provider/client session for each direct GUI TTS
            # request. The Lemonade/OpenAI-compatible endpoint has been observed
            # to close keep-alive connections between requests; reusing the
            # cached aiohttp ClientSession then fails with ServerDisconnectedError
            # followed by "Connector is closed". A fresh provider per request
            # matches the direct Python repro and tester path, both of which work.
            spec = _provider_config(cfg, "tts", session.tts_provider)
            if spec is None:
                raise TTSError(
                    f"unknown TTS provider {session.tts_provider!r}",
                    code="TTS_PROVIDER_NOT_FOUND",
                    details={"provider": session.tts_provider},
                )
            provider = _build_tts_provider(spec)
            await provider.start()
            session.metadata["tts_active_utterance_id"] = utterance_id
            try:
                session.transition_to("speaking")
            except Exception:
                pass
            async for chunk in provider.stream(request):
                if session.cancel_tts.is_set():
                    await self._emit_tts_done(
                        session,
                        utterance_id=utterance_id,
                        cancelled=True,
                        chunks=chunks,
                        reason="barge_in",
                    )
                    if sender_present:
                        pushed_done_emit_count += 1
                        asyncio.create_task(self._emit_tts_push_probe(
                            session,
                            utterance_id=utterance_id,
                            chunks=chunks,
                            pushed_emit_count=pushed_emit_count,
                            pushed_done_emit_count=pushed_done_emit_count,
                        ))
                    return {
                        "event": "voqualizer_tts_cancelled",
                        "session_id": session.session_id,
                        "utterance_id": utterance_id,
                        "chunks": chunks,
                        "pushed_emit_count": pushed_emit_count,
                        "pushed_done_emit_count": pushed_done_emit_count,
                        "sender_present": sender_present,
                    }
                ack_chunk = await self._emit_tts_chunk(session, chunk)
                if sender_present:
                    pushed_emit_count += 1
                ack_tts_chunks.append(ack_chunk)
                chunks += 1
                session.metadata["tts_chunks_emitted"] = chunks
                if session.cancel_tts.is_set():
                    await self._emit_tts_done(
                        session,
                        utterance_id=utterance_id,
                        cancelled=True,
                        chunks=chunks,
                        reason="barge_in",
                    )
                    if sender_present:
                        pushed_done_emit_count += 1
                        asyncio.create_task(self._emit_tts_push_probe(
                            session,
                            utterance_id=utterance_id,
                            chunks=chunks,
                            pushed_emit_count=pushed_emit_count,
                            pushed_done_emit_count=pushed_done_emit_count,
                        ))
                    return {
                        "event": "voqualizer_tts_cancelled",
                        "session_id": session.session_id,
                        "utterance_id": utterance_id,
                        "chunks": chunks,
                        "pushed_emit_count": pushed_emit_count,
                        "pushed_done_emit_count": pushed_done_emit_count,
                        "sender_present": sender_present,
                    }
            ack_tts_done = await self._emit_tts_done(session, utterance_id=utterance_id, cancelled=False, chunks=chunks)
            if sender_present:
                pushed_done_emit_count += 1
                asyncio.create_task(self._emit_tts_push_probe(
                    session,
                    utterance_id=utterance_id,
                    chunks=chunks,
                    pushed_emit_count=pushed_emit_count,
                    pushed_done_emit_count=pushed_done_emit_count,
                ))
            return {
                "event": "voqualizer_tts_ack",
                "session_id": session.session_id,
                "utterance_id": utterance_id,
                "chunks": chunks,
                "tts_chunks": ack_tts_chunks,
                "tts_done": ack_tts_done,
                "delivery_fallback": "ack_chunks",
                "pushed_emit_count": pushed_emit_count,
                "pushed_done_emit_count": pushed_done_emit_count,
                "sender_present": sender_present,
            }
        except TTSError as exc:
            await self._emit_tts_error(session, exc)
            err = exc.to_dict()
            return WsResult.error(
                code=err["code"],
                message=err["message"],
                details={"recoverable": err["recoverable"], **err.get("details", {})},
            )
        except (ValueError, TypeError) as exc:
            return WsResult.error(code=BAD_REQUEST, message=str(exc))
        finally:
            if provider is not None:
                try:
                    await provider.stop()
                except Exception:
                    pass
            session.metadata.pop("tts_active_utterance_id", None)
            try:
                session.transition_to(STATE_READY)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # voqualizer_control
    # ------------------------------------------------------------------

    async def _handle_control(
        self, data: dict, sid: str
    ) -> dict[str, Any] | WsResult:
        action = data.get("action")
        if not isinstance(action, str) or not action.strip():
            return WsResult.error(
                code=BAD_REQUEST,
                message="voqualizer_control.action must be a non-empty string",
            )

        if self._session_id is None:
            return WsResult.error(
                code=NO_SESSION,
                message="send voqualizer_init before voqualizer_control",
            )
        registry = self._registry()
        session = registry.get(self._session_id)
        if session is None:
            return WsResult.error(
                code=NO_SESSION,
                message=f"session {self._session_id!r} not active",
            )

        auth_error = self._verify_session_token(session, data, f"voqualizer_control:{action}")
        if auth_error is not None:
            return auth_error

        if action == "end_session":
            await registry.remove(self._session_id, tombstone=True)
            ended_id = self._session_id
            self._session_id = None
            return {
                "event": "voqualizer_control_ack",
                "action": action,
                "session_id": ended_id,
                "state": STATE_CLOSED,
            }

        if action == "barge_in":
            session.cancel_in_flight_tts()
            return {
                "event": "voqualizer_control_ack",
                "action": action,
                "session_id": session.session_id,
                "state": session.state,
            }

        if action == "mute":
            try:
                session.transition_to("paused")
            except Exception:
                pass  # idempotent
            return {
                "event": "voqualizer_control_ack",
                "action": action,
                "session_id": session.session_id,
                "state": session.state,
            }

        if action == "unmute":
            try:
                session.transition_to(STATE_READY)
            except Exception:
                pass
            return {
                "event": "voqualizer_control_ack",
                "action": action,
                "session_id": session.session_id,
                "state": session.state,
            }

        if action == "resume":
            # A resume control on an active session is a no-op ack — the WS
            # reconnect path is the *real* resume mechanism (via init).
            session.touch()
            return {
                "event": "voqualizer_control_ack",
                "action": action,
                "session_id": session.session_id,
                "state": session.state,
            }

        if action == "set_tts_enabled":
            enabled = data.get("enabled")
            if not isinstance(enabled, bool):
                return WsResult.error(
                    code=BAD_REQUEST,
                    message="voqualizer_control.set_tts_enabled requires boolean 'enabled'",
                )
            session.tts_enabled = enabled
            if not enabled:
                # Hard-mute: cancel any in-flight TTS immediately.
                try: session.cancel_in_flight_tts()
                except Exception: pass
            session.touch()
            return {
                "event": "voqualizer_control_ack",
                "action": action,
                "session_id": session.session_id,
                "state": session.state,
                "tts_enabled": session.tts_enabled,
            }

        return WsResult.error(
            code=BAD_REQUEST,
            message=f"unknown control action {action!r}",
            details={"available": ["mute", "unmute", "barge_in", "end_session", "resume"]},
        )
