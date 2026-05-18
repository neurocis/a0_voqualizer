from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tools" / "load_test_32_sessions.py"
REPORT = ROOT / "docs" / "performance" / "load-test-32-sessions.md"


def load_harness():
    spec = importlib.util.spec_from_file_location("voqualizer_load_test_32_sessions", HARNESS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(spec.name, None)


def test_a82_artifacts_exist():
    assert HARNESS.is_file()
    assert REPORT.is_file()


def test_metrics_report_documents_acceptance_and_constraints():
    text = REPORT.read_text(encoding="utf-8")
    assert "A8.2" in text
    assert "32 concurrent sessions" in text
    assert "< 1s first-audio latency" in text or "< 1000 ms" in text
    assert "metrics report" in text
    assert "voqualizer_init" in text
    assert "bearer_token" in text
    assert "voqualizer_audio_chunk" in text
    assert "A2 4-byte frame" in text
    assert "No backend restart" in text
    assert "live A0 backend" in text


def test_harness_declares_32_session_target_and_real_protocol_events():
    text = HARNESS.read_text(encoding="utf-8")
    assert "CONCURRENT_SESSIONS = 32" in text
    assert "TARGET_FIRST_AUDIO_LATENCY_MS = 1000.0" in text
    assert "WsVoqualizer" in text
    assert "BridgeRegistry.from_config" in text
    assert "voqualizer_init" in text
    assert "voqualizer_audio_chunk" in text
    assert "bearer_token" in text
    assert "encode_frame" in text
    assert "voqualizer_asr_partial" in text


def test_harness_runs_32_concurrent_sessions_under_latency_target():
    harness = load_harness()
    metrics = asyncio.run(harness.run_load_test())

    assert metrics.concurrent_sessions == 32
    assert len(metrics.sessions) == 32
    assert metrics.target_first_audio_latency_ms == 1000.0
    assert metrics.passed is True
    assert metrics.max_first_transcript_ms < 1000.0
    assert metrics.p95_first_transcript_ms < 1000.0
    assert metrics.max_first_audio_ack_ms < 1000.0
    assert all(session.emitted_events >= 1 for session in metrics.sessions)
    assert all(session.queued for session in metrics.sessions)
    assert {session.seq for session in metrics.sessions} == set(range(32))


def test_harness_renders_metrics_report_markdown():
    harness = load_harness()
    metrics = asyncio.run(harness.run_load_test())
    rendered = harness.render_markdown_report(metrics)

    assert "Status: **PASS**" in rendered
    assert "Concurrent sessions: **32**" in rendered
    assert "Measured max first-transcript latency" in rendered
    assert "Per-session metrics" in rendered
    assert "JSON summary" in rendered
    assert "load-00" in rendered
    assert "load-31" in rendered
