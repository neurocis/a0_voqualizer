"""Deterministic A8.2 32-session Voqualizer load harness.

This module intentionally runs in-process against ``WsVoqualizer`` with mock ASR
providers. It does not open sockets, restart A0, use real credentials, download
models, call live providers, or require external network access.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Import the handler as the framework does, even when this file is executed from
# the plugin checkout by pytest or by hand.
_ORIG_SYS_PATH = list(sys.path)
A0_ROOT = str(Path("/a0"))
PLUGIN_ROOT = str(Path(__file__).resolve().parents[1])
for entry in ("", PLUGIN_ROOT):
    while entry in sys.path:
        sys.path.remove(entry)
while A0_ROOT in sys.path:
    sys.path.remove(A0_ROOT)
sys.path.insert(0, A0_ROOT)
sys.modules.pop("helpers", None)

from usr.plugins.a0_voqualizer.api import ws_voqualizer as ws_mod  # noqa: E402
from usr.plugins.a0_voqualizer.api.ws_voqualizer import WsVoqualizer  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.frame import encode_frame  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry  # noqa: E402

sys.path[:] = _ORIG_SYS_PATH
for _name in list(sys.modules):
    if _name == "helpers" or _name.startswith("helpers."):
        sys.modules.pop(_name, None)


CONCURRENT_SESSIONS = 32
TARGET_FIRST_AUDIO_LATENCY_MS = 1000.0
PCM16_20MS_16K = b"\x00\x00" * 320


@dataclass(frozen=True)
class SessionLoadMetric:
    session_id: str
    init_latency_ms: float
    first_audio_ack_latency_ms: float
    first_transcript_latency_ms: float
    emitted_events: int
    queued: bool
    seq: int
    ts_ms: int


@dataclass(frozen=True)
class LoadTestMetrics:
    concurrent_sessions: int
    target_first_audio_latency_ms: float
    passed: bool
    total_duration_ms: float
    p50_first_audio_ack_ms: float
    p95_first_audio_ack_ms: float
    max_first_audio_ack_ms: float
    p50_first_transcript_ms: float
    p95_first_transcript_ms: float
    max_first_transcript_ms: float
    sessions: list[SessionLoadMetric]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapturingLoadWs(WsVoqualizer):
    def __init__(self, session_id: str):
        super().__init__(None, threading.Lock())
        self.session_id_for_test = session_id
        self.emitted: list[tuple[str, str, dict[str, Any]]] = []
        self.first_transcript_at: float | None = None

    async def emit_to(self, sid, event, data, *, correlation_id=None):  # type: ignore[override]
        now = time.perf_counter()
        self.emitted.append((sid, event, data))
        if event in {"voqualizer_asr_partial", "voqualizer_asr_final"} and self.first_transcript_at is None:
            self.first_transcript_at = now


def load_test_config() -> dict[str, Any]:
    return {
        "asr": {
            "default": "mock-asr",
            "providers": [
                {
                    "name": "mock-asr",
                    "type": "mock",
                    "streaming": True,
                    "language": "en",
                    "final_text": "load test final",
                }
            ],
        },
        "tts": {
            "default": "mock-tts",
            "providers": [{"name": "mock-tts", "type": "mock", "voice": "mock", "chunk_size": 4}],
        },
        "protocol": {
            "input_codecs": ["pcm16/16k"],
            "output_codecs": ["pcm16/16k"],
            "default_input_codec": "pcm16/16k",
            "default_output_codec": "pcm16/16k",
            "heartbeat_interval_seconds": 15,
            "session_resume_window_seconds": 30,
        },
        "behavior": {"barge_in": True},
        "limits": {
            "audio_queue_max_frames": 64,
            "max_concurrent_sessions": CONCURRENT_SESSIONS,
            "max_session_seconds": 300,
            "max_audio_chunk_kb": 64,
            "max_text_chunk_chars": 4000,
        },
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


async def _run_one_session(index: int) -> SessionLoadMetric:
    session_id = f"load-{index:02d}"
    sid = f"SID-{index:02d}"
    handler = CapturingLoadWs(session_id)

    init_start = time.perf_counter()
    ready = await handler.process(
        "voqualizer_init",
        {"session_id": session_id, "asr": {"provider": "mock-asr", "codec": "pcm16/16k"}, "tts": {"provider": "mock-tts"}},
        sid,
    )
    init_end = time.perf_counter()
    assert isinstance(ready, dict), ready
    assert ready["event"] == "voqualizer_ready"
    bearer_token = ready["bearer_token"]

    frame = encode_frame(index, index * 20, PCM16_20MS_16K)
    audio_start = time.perf_counter()
    ack = await handler.process("voqualizer_audio_chunk", {"frame": frame, "bearer_token": bearer_token}, sid)
    audio_end = time.perf_counter()
    assert isinstance(ack, dict), ack
    assert ack["event"] == "voqualizer_audio_ack"

    first_transcript_at = handler.first_transcript_at or audio_end
    return SessionLoadMetric(
        session_id=session_id,
        init_latency_ms=(init_end - init_start) * 1000.0,
        first_audio_ack_latency_ms=(audio_end - audio_start) * 1000.0,
        first_transcript_latency_ms=(first_transcript_at - audio_start) * 1000.0,
        emitted_events=len(handler.emitted),
        queued=bool(ack["queued"]),
        seq=int(ack["seq"]),
        ts_ms=int(ack["ts_ms"]),
    )


async def run_load_test(concurrent_sessions: int = CONCURRENT_SESSIONS) -> LoadTestMetrics:
    if concurrent_sessions != CONCURRENT_SESSIONS:
        raise ValueError("A8.2 harness is fixed at 32 concurrent sessions")

    cfg = load_test_config()
    BridgeRegistry.reset_instance()
    BridgeRegistry.from_config(cfg, replace=True)
    previous_loader = ws_mod._safe_load_config
    ws_mod._safe_load_config = lambda: cfg
    started = time.perf_counter()
    try:
        sessions = await asyncio.gather(*(_run_one_session(i) for i in range(concurrent_sessions)))
    finally:
        ws_mod._safe_load_config = previous_loader
    ended = time.perf_counter()

    ack_latencies = [item.first_audio_ack_latency_ms for item in sessions]
    transcript_latencies = [item.first_transcript_latency_ms for item in sessions]
    max_transcript = max(transcript_latencies) if transcript_latencies else 0.0
    return LoadTestMetrics(
        concurrent_sessions=concurrent_sessions,
        target_first_audio_latency_ms=TARGET_FIRST_AUDIO_LATENCY_MS,
        passed=max_transcript < TARGET_FIRST_AUDIO_LATENCY_MS and len(sessions) == CONCURRENT_SESSIONS,
        total_duration_ms=(ended - started) * 1000.0,
        p50_first_audio_ack_ms=statistics.median(ack_latencies),
        p95_first_audio_ack_ms=percentile(ack_latencies, 95),
        max_first_audio_ack_ms=max(ack_latencies) if ack_latencies else 0.0,
        p50_first_transcript_ms=statistics.median(transcript_latencies),
        p95_first_transcript_ms=percentile(transcript_latencies, 95),
        max_first_transcript_ms=max_transcript,
        sessions=list(sessions),
    )


def render_markdown_report(metrics: LoadTestMetrics) -> str:
    status = "PASS" if metrics.passed else "FAIL"
    rows = "\n".join(
        f"| {s.session_id} | {s.init_latency_ms:.3f} | {s.first_audio_ack_latency_ms:.3f} | {s.first_transcript_latency_ms:.3f} | {s.emitted_events} | {s.queued} |"
        for s in metrics.sessions
    )
    return f"""# A8.2 Load test metrics — 32 concurrent sessions

Artifact: **A8.2 — Load test (32 concurrent sessions)**

Acceptance: **< 1s first-audio latency under load; metrics report**

## Result

- Status: **{status}**
- Concurrent sessions: **{metrics.concurrent_sessions}**
- Target first-audio latency: **< {metrics.target_first_audio_latency_ms:.0f} ms**
- Measured max first-transcript latency: **{metrics.max_first_transcript_ms:.3f} ms**
- Measured p95 first-transcript latency: **{metrics.p95_first_transcript_ms:.3f} ms**
- Measured p50 first-transcript latency: **{metrics.p50_first_transcript_ms:.3f} ms**
- Measured max audio ACK latency: **{metrics.max_first_audio_ack_ms:.3f} ms**
- Total in-process run duration: **{metrics.total_duration_ms:.3f} ms**

## Method

The harness in `tools/load_test_32_sessions.py` runs 32 concurrent in-process
`WsVoqualizer` sessions with deterministic mock ASR and the real protocol path:

1. Configure `BridgeRegistry` with `max_concurrent_sessions: 32`.
2. Create 32 independent `WsVoqualizer` handler instances.
3. Send `voqualizer_init` for each session.
4. Store each issued per-session `bearer_token`.
5. Encode a PCM16/16k 20 ms audio chunk with the A2 4-byte frame header.
6. Send `voqualizer_audio_chunk` concurrently for all sessions.
7. Measure first-audio latency from chunk send to first ASR transcript emit.

No sockets are opened; no backend restart, credentials, external network, model
downloads, live browser, live LLM/provider calls, platform SDKs, telephony
accounts, Node install, Asterisk install, or live A0 backend are required.

## Aggregate metrics

| Metric | Value (ms) |
|---|---:|
| p50 first audio ACK | {metrics.p50_first_audio_ack_ms:.3f} |
| p95 first audio ACK | {metrics.p95_first_audio_ack_ms:.3f} |
| max first audio ACK | {metrics.max_first_audio_ack_ms:.3f} |
| p50 first transcript | {metrics.p50_first_transcript_ms:.3f} |
| p95 first transcript | {metrics.p95_first_transcript_ms:.3f} |
| max first transcript | {metrics.max_first_transcript_ms:.3f} |

## Per-session metrics

| Session | init ms | first audio ACK ms | first transcript ms | emitted events | queued |
|---|---:|---:|---:|---:|---|
{rows}

## JSON summary

```json
{json.dumps(metrics.to_dict(), indent=2)}
```
"""


def main() -> int:
    metrics = asyncio.run(run_load_test())
    print(render_markdown_report(metrics))
    return 0 if metrics.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
