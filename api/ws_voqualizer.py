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
import time
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
            # Defensive: never let an exception escape the dispatcher.
            log_voqualizer_error(HANDLER_ERROR, f"voqualizer handler error: {e}", operation=event, severity="error")
            return WsResult.error(
                code=HANDLER_ERROR,
                message=f"voqualizer handler error: {e}",
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
        payload["session_id"] = session.session_id
        if session.sender is not None:
            await session.sender(event, payload)

    @staticmethod
    def _chunk_duration_ms(chunk: AudioChunk) -> float:
        """Return PCM16 chunk duration in milliseconds."""
        if chunk.sample_rate <= 0:
            return 0.0
        return (len(chunk.pcm16) / 2 / chunk.sample_rate) * 1000.0

    def _asr_batch_buffer_for_session(self, session: BridgeSession) -> list[AudioChunk]:
        """Return the per-session ASR buffer used for batch HTTP providers."""
        buffer = session.metadata.get("asr_batch_buffer")
        if isinstance(buffer, list):
            return buffer
        buffer = []
        session.metadata["asr_batch_buffer"] = buffer
        return buffer

    async def _process_batch_asr_chunk(
        self,
        session: BridgeSession,
        provider: ASRProvider,
        chunk: AudioChunk,
        *,
        language: str | None,
    ) -> int:
        """Buffer small mic frames before calling batch/non-streaming ASR.

        Browser mic frames are normally 20ms. OpenAI-compatible Whisper-style
        endpoints are HTTP batch transcription APIs, not realtime ASR engines;
        sending one 20ms WAV per frame produces successful-but-empty transcripts.
        Aggregate to at least 200ms by default so those providers receive a
        usable audio segment while keeping latency low.
        """

        min_ms = float(session.metadata.get("asr_batch_min_ms", 200.0) or 200.0)
        buffer = self._asr_batch_buffer_for_session(session)
        buffer.append(chunk)
        duration_ms = sum(self._chunk_duration_ms(item) for item in buffer)
        if duration_ms < min_ms and not chunk.is_final:
            return 0

        segment = list(buffer)
        buffer.clear()
        result = await provider.transcribe(
            segment,
            language=language,
            metadata={
                "session_id": session.session_id,
                "seq": chunk.seq,
                "buffered_chunks": len(segment),
                "buffered_ms": round(duration_ms, 3),
                "asr_batch_min_ms": min_ms,
            },
        )
        # Empty finals from batch Whisper are not useful UI output. They still
        # indicate the provider ran, but suppressing them avoids a wall of blank
        # ASR events while the user is between words or speaking softly.
        if result.text.strip():
            await self._emit_transcript(session, result)
            return 1
        session.metadata["asr_empty_results_suppressed"] = int(session.metadata.get("asr_empty_results_suppressed", 0)) + 1
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

            return {
                "event": "voqualizer_audio_ack",
                "session_id": session.session_id,
                "seq": parsed.seq,
                "ts_ms": parsed.ts_ms,
                "queued": queued_without_drop,
                "emitted": emitted,
                "backpressure": session.backpressure_metrics(),
            }
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

    async def _emit_tts_chunk(self, session: BridgeSession, chunk: TTSAudioChunk) -> None:
        payload = chunk.event_payload()
        event = payload.pop("event")
        payload["session_id"] = session.session_id
        # Socket.IO can carry bytes in object payloads; A4.5 keeps metadata
        # compatible with AudioChunk.event_payload() while exposing bytes to
        # deterministic tests and browser clients.
        payload["audio"] = chunk.data
        if session.sender is not None:
            await session.sender(event, payload)

    async def _emit_tts_done(
        self,
        session: BridgeSession,
        *,
        utterance_id: str,
        cancelled: bool = False,
        chunks: int = 0,
        reason: str | None = None,
    ) -> None:
        if session.sender is not None:
            payload: dict[str, Any] = {
                "session_id": session.session_id,
                "utterance_id": utterance_id,
                "cancelled": cancelled,
                "chunks": chunks,
            }
            if reason:
                payload["reason"] = reason
            await session.sender("voqualizer_tts_done", payload)

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
        codec = str(data.get("codec") or session.output_codec or "pcm16/16k")
        sample_rate = int(data.get("sample_rate") or (24000 if codec == "pcm16/24k" else 16000))
        speed = float(data.get("speed", 1.0))
        metadata = dict(data.get("metadata") or {})
        metadata.setdefault("source", "voqualizer_user_text")

        session.reset_cancel()
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
        try:
            cfg = _safe_load_config()
            provider = await self._tts_provider_for_session(session, cfg)
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
                    return {
                        "event": "voqualizer_tts_cancelled",
                        "session_id": session.session_id,
                        "utterance_id": utterance_id,
                        "chunks": chunks,
                    }
                await self._emit_tts_chunk(session, chunk)
                chunks += 1
                if session.cancel_tts.is_set():
                    await self._emit_tts_done(
                        session,
                        utterance_id=utterance_id,
                        cancelled=True,
                        chunks=chunks,
                        reason="barge_in",
                    )
                    return {
                        "event": "voqualizer_tts_cancelled",
                        "session_id": session.session_id,
                        "utterance_id": utterance_id,
                        "chunks": chunks,
                    }
            await self._emit_tts_done(session, utterance_id=utterance_id, cancelled=False, chunks=chunks)
            return {
                "event": "voqualizer_tts_ack",
                "session_id": session.session_id,
                "utterance_id": utterance_id,
                "chunks": chunks,
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

        return WsResult.error(
            code=BAD_REQUEST,
            message=f"unknown control action {action!r}",
            details={"available": ["mute", "unmute", "barge_in", "end_session", "resume"]},
        )
