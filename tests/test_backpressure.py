from __future__ import annotations

import pytest

from helpers.session import BridgeSession


@pytest.mark.anyio
async def test_bounded_queue_drops_oldest_and_preserves_newest_window():
    session = BridgeSession(session_id="bp", audio_queue_max_frames=3)

    assert session.enqueue_audio("f0") is True
    assert session.enqueue_audio("f1") is True
    assert session.enqueue_audio("f2") is True
    # Saturated: new frames must win, oldest frames must be evicted.
    assert session.enqueue_audio("f3") is False
    assert session.enqueue_audio("f4") is False

    assert session.audio_queue.qsize() == 3
    assert [session.audio_queue.get_nowait() for _ in range(3)] == ["f2", "f3", "f4"]


@pytest.mark.anyio
async def test_backpressure_metrics_surface_queue_state_and_drop_ratio():
    session = BridgeSession(session_id="metrics", audio_queue_max_frames=2)

    # Metrics do not force lazy queue creation.
    assert session.backpressure_metrics() == {
        "audio_queue_size": 0,
        "audio_queue_capacity": 2,
        "audio_frames_enqueued": 0,
        "audio_frames_dropped": 0,
        "audio_queue_drop_ratio": 0.0,
    }

    assert session.enqueue_audio("a") is True
    assert session.enqueue_audio("b") is True
    assert session.enqueue_audio("c") is False

    metrics = session.backpressure_metrics()
    assert metrics["audio_queue_size"] == 2
    assert metrics["audio_queue_capacity"] == 2
    assert metrics["audio_frames_enqueued"] == 3
    assert metrics["audio_frames_dropped"] == 1
    assert metrics["audio_queue_drop_ratio"] == pytest.approx(1 / 3)


def test_snapshot_exposes_backpressure_metrics_without_async_primitives():
    session = BridgeSession(session_id="snap", audio_queue_max_frames=4)
    snap = session.snapshot()
    assert snap["audio_queue_size"] == 0
    assert snap["audio_queue_capacity"] == 4
    assert snap["audio_frames_enqueued"] == 0
    assert snap["audio_frames_dropped"] == 0
    assert snap["audio_queue_drop_ratio"] == 0.0


@pytest.mark.anyio
async def test_backpressure_metrics_update_last_activity():
    session = BridgeSession(session_id="touch", audio_queue_max_frames=1)
    before = session.last_activity_at
    assert session.enqueue_audio("one") is True
    assert session.last_activity_at >= before
    after_first = session.last_activity_at
    assert session.enqueue_audio("two") is False
    assert session.last_activity_at >= after_first


@pytest.mark.anyio
async def test_queue_capacity_one_keeps_only_latest_frame():
    session = BridgeSession(session_id="cap1", audio_queue_max_frames=1)
    for frame in ["a", "b", "c", "d"]:
        session.enqueue_audio(frame)
    assert session.audio_frames_enqueued == 4
    assert session.audio_frames_dropped == 3
    assert session.audio_queue.qsize() == 1
    assert session.audio_queue.get_nowait() == "d"
