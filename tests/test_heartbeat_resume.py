from __future__ import annotations

import pytest

from helpers.heartbeat import build_pong, compute_rtt_ms
from helpers.registry import BridgeRegistry


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.parametrize(
    "client_ts,server_now,expected",
    [
        (980.0, 1000.0, 20.0),
        (1000.1234, 1001.9876, 1.864),
        (1001.0, 1000.0, None),  # client clock ahead
        (0.0, 100_000.0, None),  # stale/skewed
        ("not-a-number", 1000.0, None),
        (None, 1000.0, None),
    ],
)
def test_compute_rtt_ms_clock_skew_guard(client_ts, server_now, expected):
    assert compute_rtt_ms(client_ts, server_now) == expected


def test_build_pong_payload_shape():
    pong = build_pong(900.0, server_now_ms=925.0)
    assert pong.ts == 900.0
    assert pong.server_time == 925.0
    assert pong.rtt_ms == 25.0
    assert pong.to_payload() == {
        "event": "voqualizer_pong",
        "ts": 900.0,
        "server_time": 925.0,
        "rtt_ms": 25.0,
    }


@pytest.mark.anyio
async def test_disconnect_reconnect_within_30s_resumes_same_session_id():
    clock = Clock()
    reg = BridgeRegistry(
        max_concurrent_sessions=4,
        max_session_seconds=300,
        session_resume_window_seconds=30,
        audio_queue_max_frames=8,
        clock=clock,
    )

    first, resumed = await reg.create_or_resume(
        "resume-30",
        context_id="ctx-a",
        asr_provider="whisper-local",
        tts_provider="piper-local",
        input_codec="pcm16/16k",
        output_codec="pcm16/16k",
        language="en",
        barge_in=True,
    )
    assert resumed is False
    created_at = first.created_at

    # Simulate websocket disconnect: live session removed, tombstone retained.
    assert await reg.remove("resume-30", tombstone=True) is True
    assert reg.get("resume-30") is None

    # Reconnect inside 30s and init with the same session_id.
    clock.advance(29.9)
    second, resumed = await reg.create_or_resume(
        "resume-30",
        context_id="ctx-a",
        asr_provider="whisper-local",
        tts_provider="piper-local",
        input_codec="pcm16/16k",
        output_codec="pcm16/16k",
        language="en",
        barge_in=True,
    )
    assert resumed is True
    assert second.session_id == "resume-30"
    assert second.created_at == created_at
    assert second.state == "ready"
    assert reg.get("resume-30") is second


@pytest.mark.anyio
async def test_reconnect_after_resume_window_creates_fresh_session():
    clock = Clock()
    reg = BridgeRegistry(
        max_concurrent_sessions=4,
        max_session_seconds=300,
        session_resume_window_seconds=30,
        audio_queue_max_frames=8,
        clock=clock,
    )

    first, resumed = await reg.create_or_resume("expired", context_id="ctx-old")
    assert resumed is False
    first_created_at = first.created_at
    assert await reg.remove("expired", tombstone=True) is True

    clock.advance(30.001)
    second, resumed = await reg.create_or_resume("expired", context_id="ctx-new")
    assert resumed is False
    assert second.session_id == "expired"
    assert second.context_id == "ctx-new"
    assert second.created_at != first_created_at


@pytest.mark.anyio
async def test_heartbeat_touch_prevents_idle_gc():
    clock = Clock()
    reg = BridgeRegistry(
        max_concurrent_sessions=4,
        max_session_seconds=10,
        session_resume_window_seconds=30,
        audio_queue_max_frames=8,
        clock=clock,
    )
    session, _ = await reg.create_or_resume("hb", context_id="ctx")

    clock.advance(9)
    session.touch(clock())  # Equivalent to voqualizer_ping touching bound session.
    clock.advance(9)
    assert await reg.gc_idle(now=clock(), ttl=10) == []
    assert reg.get("hb") is session

    clock.advance(11)
    assert await reg.gc_idle(now=clock(), ttl=10) == ["hb"]
    assert reg.get("hb") is None
