from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "security" / "review.md"


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_a81_security_review_doc_exists():
    assert DOC.is_file()


def test_security_review_covers_plan_acceptance_areas():
    content = text()
    for marker in ["CSRF", "auth", "rate limits", "input validation", "codec fuzzing"]:
        assert marker.lower() in content.lower()


def test_security_review_covers_a5_bearer_token_semantics():
    content = text()
    assert "bearer_token" in content
    assert "secrets.compare_digest" in content
    assert "voqualizer_audio_chunk" in content
    assert "voqualizer_user_text" in content
    assert "voqualizer_control" in content
    assert "Tokens are not shared" in content or "per-session" in content


def test_security_review_covers_csrf_and_same_origin_admin():
    content = text()
    assert "requires_auth" in content
    assert "same-origin" in content
    assert "plugins/a0_voqualizer/ws_voqualizer" in content
    assert "framework" in content.lower()


def test_security_review_covers_rate_limit_controls_and_recommendations():
    content = text()
    for marker in [
        "max_concurrent_sessions",
        "max_session_seconds",
        "audio_queue_max_frames",
        "max_audio_chunk_kb",
        "max_text_chunk_chars",
        "backpressure",
    ]:
        assert marker in content
    assert "Enforce `limits.max_audio_chunk_kb`" in content
    assert "Enforce `limits.max_text_chunk_chars`" in content


def test_security_review_covers_input_validation_and_codecs():
    content = text()
    for marker in [
        "voqualizer_init",
        "provider names",
        "requested input/output codecs",
        "A2 frame header",
        "PCM16 alignment",
        "unsupported codec",
        "recoverable protocol errors",
    ]:
        assert marker in content


def test_security_review_declares_no_external_runtime_requirements():
    content = text()
    for marker in [
        "No backend restart",
        "real credentials",
        "external network",
        "model downloads",
        "live browser",
        "live A0 backend",
    ]:
        assert marker.lower() in content.lower()
