from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "protocol" / "errors.md"


def test_a83_error_taxonomy_doc_exists():
    assert DOC.is_file()


def test_error_taxonomy_doc_covers_stable_codes_and_logging():
    text = DOC.read_text(encoding="utf-8")
    assert "A8.3" in text
    assert "Stable `voqualizer_error` codes" in text
    assert "logging via A0 standard" in text
    for code in ["UNKNOWN_EVENT", "HANDLER_ERROR", "BAD_REQUEST", "AUTH_REQUIRED", "NO_SESSION", "REGISTRY_FULL", "BAD_AUDIO_CHUNK", "TTS_FINALIZATION_ERROR", "CONTEXT_BRIDGE_BAD_REQUEST"]:
        assert code in text
    assert "helpers.print_style.PrintStyle" in text
    assert "voqualizer_error code=BAD_AUDIO_CHUNK" in text
    assert "does not log raw audio frames or per-session bearer tokens" in text


def test_error_taxonomy_doc_preserves_pytest_constraints():
    text = DOC.read_text(encoding="utf-8")
    for marker in ["No backend restart", "real credentials", "external network", "model downloads", "live A0 backend"]:
        assert marker.lower() in text.lower()
