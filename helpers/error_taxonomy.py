"""Stable Voqualizer protocol error taxonomy and telemetry helpers (A8.3).

The WebSocket handler returns framework ``WsResult.error`` objects for request
acks and emits ``voqualizer_error`` events for asynchronous failures.  This
module centralizes the public error codes so clients can treat them as stable
protocol values and so handler code can log them through the A0 ``PrintStyle``
facility with a consistent prefix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal


ErrorSeverity = Literal["info", "warning", "error"]


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """Public metadata for one stable Voqualizer error code."""

    code: str
    category: str
    severity: ErrorSeverity = "warning"
    recoverable: bool = True
    description: str = ""
    introduced: str = "A8.3"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "severity": self.severity,
            "recoverable": self.recoverable,
            "description": self.description,
            "introduced": self.introduced,
        }


# Stable protocol/control-plane codes returned by WsVoqualizer itself.
UNKNOWN_EVENT: Final = "UNKNOWN_EVENT"
HANDLER_ERROR: Final = "HANDLER_ERROR"
BAD_REQUEST: Final = "BAD_REQUEST"
AUTH_REQUIRED: Final = "AUTH_REQUIRED"
NO_SESSION: Final = "NO_SESSION"
REGISTRY_FULL: Final = "REGISTRY_FULL"
BAD_AUDIO_CHUNK: Final = "BAD_AUDIO_CHUNK"

# Stable provider/context categories surfaced by adapters/finalizers.
ASR_PROVIDER_UNSUPPORTED: Final = "ASR_PROVIDER_UNSUPPORTED"
ASR_PROVIDER_NOT_FOUND: Final = "ASR_PROVIDER_NOT_FOUND"
ASR_UNAVAILABLE: Final = "ASR_UNAVAILABLE"
ASR_HTTP_ERROR: Final = "ASR_HTTP_ERROR"
ASR_BAD_RESPONSE: Final = "ASR_BAD_RESPONSE"
BAD_ASR_AUDIO: Final = "BAD_ASR_AUDIO"
BAD_ASR_CONFIG: Final = "BAD_ASR_CONFIG"
TTS_PROVIDER_UNSUPPORTED: Final = "TTS_PROVIDER_UNSUPPORTED"
TTS_PROVIDER_NOT_FOUND: Final = "TTS_PROVIDER_NOT_FOUND"
TTS_UNAVAILABLE: Final = "TTS_UNAVAILABLE"
TTS_UNSUPPORTED_CODEC: Final = "TTS_UNSUPPORTED_CODEC"
TTS_SYNTHESIS_FAILED: Final = "TTS_SYNTHESIS_FAILED"
TTS_TRANSPORT_ERROR: Final = "TTS_TRANSPORT_ERROR"
TTS_HTTP_ERROR: Final = "TTS_HTTP_ERROR"
TTS_BAD_RESPONSE: Final = "TTS_BAD_RESPONSE"
TTS_CANCELLED: Final = "TTS_CANCELLED"
TTS_FINALIZATION_ERROR: Final = "TTS_FINALIZATION_ERROR"
CONTEXT_BRIDGE_ERROR: Final = "CONTEXT_BRIDGE_ERROR"
CONTEXT_BRIDGE_UNAVAILABLE: Final = "CONTEXT_BRIDGE_UNAVAILABLE"
CONTEXT_BRIDGE_BAD_REQUEST: Final = "CONTEXT_BRIDGE_BAD_REQUEST"


ERROR_SPECS: Final[dict[str, ErrorSpec]] = {
    UNKNOWN_EVENT: ErrorSpec(UNKNOWN_EVENT, "protocol", description="Unknown voqualizer_* event name."),
    HANDLER_ERROR: ErrorSpec(HANDLER_ERROR, "internal", severity="error", description="Unexpected handler exception."),
    BAD_REQUEST: ErrorSpec(BAD_REQUEST, "validation", description="Malformed or semantically invalid request."),
    AUTH_REQUIRED: ErrorSpec(AUTH_REQUIRED, "auth", description="Missing or invalid per-session bearer token."),
    NO_SESSION: ErrorSpec(NO_SESSION, "session", description="Session-bound operation before init or after removal."),
    REGISTRY_FULL: ErrorSpec(REGISTRY_FULL, "rate_limit", description="Concurrent session limit reached."),
    BAD_AUDIO_CHUNK: ErrorSpec(BAD_AUDIO_CHUNK, "audio", description="Malformed audio frame or unsupported audio payload."),
    ASR_PROVIDER_UNSUPPORTED: ErrorSpec(ASR_PROVIDER_UNSUPPORTED, "asr", description="Unsupported ASR provider type."),
    ASR_PROVIDER_NOT_FOUND: ErrorSpec(ASR_PROVIDER_NOT_FOUND, "asr", description="Configured ASR provider not found."),
    ASR_UNAVAILABLE: ErrorSpec(ASR_UNAVAILABLE, "asr", description="ASR provider temporarily unavailable."),
    ASR_HTTP_ERROR: ErrorSpec(ASR_HTTP_ERROR, "asr", description="Hosted ASR provider HTTP error."),
    ASR_BAD_RESPONSE: ErrorSpec(ASR_BAD_RESPONSE, "asr", description="Hosted ASR provider returned malformed response."),
    BAD_ASR_AUDIO: ErrorSpec(BAD_ASR_AUDIO, "asr", description="ASR adapter rejected input audio."),
    BAD_ASR_CONFIG: ErrorSpec(BAD_ASR_CONFIG, "asr", recoverable=False, description="ASR provider config invalid."),
    TTS_PROVIDER_UNSUPPORTED: ErrorSpec(TTS_PROVIDER_UNSUPPORTED, "tts", description="Unsupported TTS provider type."),
    TTS_PROVIDER_NOT_FOUND: ErrorSpec(TTS_PROVIDER_NOT_FOUND, "tts", description="Configured TTS provider not found."),
    TTS_UNAVAILABLE: ErrorSpec(TTS_UNAVAILABLE, "tts", description="TTS provider temporarily unavailable."),
    TTS_UNSUPPORTED_CODEC: ErrorSpec(TTS_UNSUPPORTED_CODEC, "tts", description="TTS provider cannot emit requested codec."),
    TTS_SYNTHESIS_FAILED: ErrorSpec(TTS_SYNTHESIS_FAILED, "tts", description="Local TTS synthesis failed."),
    TTS_TRANSPORT_ERROR: ErrorSpec(TTS_TRANSPORT_ERROR, "tts", description="Hosted TTS transport failed."),
    TTS_HTTP_ERROR: ErrorSpec(TTS_HTTP_ERROR, "tts", description="Hosted TTS provider HTTP error."),
    TTS_BAD_RESPONSE: ErrorSpec(TTS_BAD_RESPONSE, "tts", description="Hosted TTS provider returned malformed audio response."),
    TTS_CANCELLED: ErrorSpec(TTS_CANCELLED, "tts", severity="info", description="TTS was cancelled, usually by barge-in."),
    TTS_FINALIZATION_ERROR: ErrorSpec(TTS_FINALIZATION_ERROR, "tts", description="Agent final-response TTS finalization failed."),
    CONTEXT_BRIDGE_ERROR: ErrorSpec(CONTEXT_BRIDGE_ERROR, "context", description="Agent context bridge failed."),
    CONTEXT_BRIDGE_UNAVAILABLE: ErrorSpec(CONTEXT_BRIDGE_UNAVAILABLE, "context", description="Agent context runtime unavailable."),
    CONTEXT_BRIDGE_BAD_REQUEST: ErrorSpec(CONTEXT_BRIDGE_BAD_REQUEST, "context", description="Context bridge input invalid."),
}


def is_stable_error_code(code: str) -> bool:
    """Return True when ``code`` is in the public A8.3 taxonomy."""

    return code in ERROR_SPECS


def error_spec(code: str) -> ErrorSpec:
    """Return metadata for a stable code, or a generic internal spec."""

    return ERROR_SPECS.get(code) or ErrorSpec(code=code, category="internal", severity="error", description="Unregistered Voqualizer error code.")


def error_taxonomy_payload() -> dict[str, Any]:
    """Return a JSON-safe taxonomy payload for docs/admin/tests."""

    return {code: spec.to_dict() for code, spec in sorted(ERROR_SPECS.items())}


def build_voqualizer_error(
    code: str,
    message: str,
    *,
    session_id: str | None = None,
    recoverable: bool | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable ``voqualizer_error`` event payload shape."""

    spec = error_spec(code)
    payload: dict[str, Any] = {
        "event": "voqualizer_error",
        "code": code,
        "message": message,
        "recoverable": spec.recoverable if recoverable is None else bool(recoverable),
        "category": spec.category,
        "severity": spec.severity,
        "details": dict(details or {}),
    }
    if session_id:
        payload["session_id"] = session_id
    return payload


def log_voqualizer_error(
    code: str,
    message: str,
    *,
    session_id: str | None = None,
    operation: str | None = None,
    details: dict[str, Any] | None = None,
    severity: ErrorSeverity | None = None,
) -> None:
    """Log a Voqualizer error through A0's PrintStyle standard.

    The format is intentionally grep-friendly and avoids serializing raw binary
    data or bearer tokens.  Details are represented by keys only.
    """

    spec = error_spec(code)
    level: ErrorSeverity = severity or spec.severity
    parts = [f"voqualizer_error code={code}", f"category={spec.category}"]
    if session_id:
        parts.append(f"session_id={session_id}")
    if operation:
        parts.append(f"operation={operation}")
    if details:
        safe_keys = ",".join(sorted(str(k) for k in details.keys()))
        parts.append(f"detail_keys={safe_keys}")
    parts.append(f"message={message}")
    line = " ".join(parts)

    try:
        from helpers.print_style import PrintStyle  # type: ignore
    except Exception:  # pragma: no cover - only outside A0 framework
        print(line)
        return

    if level == "error":
        PrintStyle.error(line)
    elif level == "info":
        PrintStyle.info(line)
    else:
        PrintStyle.warning(line)


__all__ = [
    "ErrorSpec",
    "ERROR_SPECS",
    "error_taxonomy_payload",
    "error_spec",
    "is_stable_error_code",
    "build_voqualizer_error",
    "log_voqualizer_error",
    "UNKNOWN_EVENT",
    "HANDLER_ERROR",
    "BAD_REQUEST",
    "AUTH_REQUIRED",
    "NO_SESSION",
    "REGISTRY_FULL",
    "BAD_AUDIO_CHUNK",
]
