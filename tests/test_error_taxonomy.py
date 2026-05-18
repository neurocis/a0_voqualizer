from __future__ import annotations

from helpers.error_taxonomy import (
    AUTH_REQUIRED,
    BAD_AUDIO_CHUNK,
    ERROR_SPECS,
    HANDLER_ERROR,
    NO_SESSION,
    UNKNOWN_EVENT,
    build_voqualizer_error,
    error_taxonomy_payload,
    is_stable_error_code,
    log_voqualizer_error,
)


def test_error_taxonomy_defines_stable_core_codes():
    for code in [
        UNKNOWN_EVENT,
        HANDLER_ERROR,
        "BAD_REQUEST",
        AUTH_REQUIRED,
        NO_SESSION,
        "REGISTRY_FULL",
        BAD_AUDIO_CHUNK,
        "ASR_PROVIDER_UNSUPPORTED",
        "TTS_PROVIDER_UNSUPPORTED",
        "TTS_FINALIZATION_ERROR",
        "CONTEXT_BRIDGE_BAD_REQUEST",
    ]:
        assert code in ERROR_SPECS
        assert is_stable_error_code(code)
        assert ERROR_SPECS[code].to_dict()["code"] == code


def test_error_taxonomy_payload_is_json_safe_and_categorized():
    payload = error_taxonomy_payload()
    assert payload[AUTH_REQUIRED]["category"] == "auth"
    assert payload[BAD_AUDIO_CHUNK]["category"] == "audio"
    assert payload[HANDLER_ERROR]["severity"] == "error"
    assert payload["REGISTRY_FULL"]["category"] == "rate_limit"


def test_build_voqualizer_error_uses_stable_event_shape():
    err = build_voqualizer_error(BAD_AUDIO_CHUNK, "bad frame", session_id="s1", details={"field": "frame"})
    assert err == {
        "event": "voqualizer_error",
        "code": BAD_AUDIO_CHUNK,
        "message": "bad frame",
        "recoverable": True,
        "category": "audio",
        "severity": "warning",
        "details": {"field": "frame"},
        "session_id": "s1",
    }


def test_log_voqualizer_error_uses_a0_printstyle_without_tokens(monkeypatch):
    seen: list[str] = []

    class FakePrintStyle:
        @staticmethod
        def warning(message):
            seen.append(message)

        @staticmethod
        def error(message):
            seen.append(message)

        @staticmethod
        def info(message):
            seen.append(message)

    import sys
    import types

    mod = types.ModuleType("helpers.print_style")
    mod.PrintStyle = FakePrintStyle
    monkeypatch.setitem(sys.modules, "helpers.print_style", mod)

    log_voqualizer_error(AUTH_REQUIRED, "no token", session_id="s1", operation="voqualizer_audio_chunk", details={"bearer_token": "secret"})

    assert seen
    line = seen[0]
    assert "voqualizer_error code=AUTH_REQUIRED" in line
    assert "category=auth" in line
    assert "session_id=s1" in line
    assert "operation=voqualizer_audio_chunk" in line
    assert "detail_keys=bearer_token" in line
    assert "secret" not in line
