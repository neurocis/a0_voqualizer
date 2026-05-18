from __future__ import annotations

import pytest

from helpers.frame import AudioFrame, FrameHeader
from helpers.jitter import JitterBuffer, SEQ_SPACE, build_test_frames, seq_diff


def seqs(frames: list[AudioFrame]) -> list[int]:
    return [f.seq for f in frames]


def test_in_order_passthrough():
    jb = JitterBuffer(window_size=8)
    out: list[AudioFrame] = []
    for f in build_test_frames(range(10)):
        out.extend(jb.push(f))
    assert seqs(out) == list(range(10))
    assert jb.buffered == 0
    assert jb.stats.emitted == 10
    assert jb.stats.lost == 0


def test_reorders_out_of_order_frames():
    jb = JitterBuffer(window_size=8)
    arrival = [0, 2, 1, 4, 3, 5]
    delivered: list[AudioFrame] = []
    for f in build_test_frames(arrival):
        delivered.extend(jb.push(f))
    assert seqs(delivered) == [0, 1, 2, 3, 4, 5]
    assert jb.stats.lost == 0
    assert jb.stats.ejected == 0


def test_duplicates_are_dropped():
    jb = JitterBuffer(window_size=8)
    frames = build_test_frames([0, 0, 1, 1, 2])
    delivered: list[AudioFrame] = []
    for f in frames:
        delivered.extend(jb.push(f))
    assert seqs(delivered) == [0, 1, 2]
    assert jb.stats.duplicates >= 2


def test_late_frames_are_dropped_and_counted():
    jb = JitterBuffer(window_size=8)
    delivered: list[AudioFrame] = []
    for f in build_test_frames([0, 1, 2, 3]):
        delivered.extend(jb.push(f))
    # Now resubmit a stale seq.
    late_frame = AudioFrame(FrameHeader(seq=1, ts_ms=99), b"")
    delivered.extend(jb.push(late_frame))
    assert seqs(delivered) == [0, 1, 2, 3]
    assert jb.stats.late == 1


def test_ejects_after_window_exceeded():
    jb = JitterBuffer(window_size=4, initial_seq=0)
    delivered: list[AudioFrame] = []
    # seq 0 missing; future frames pile up until window is exceeded.
    for f in build_test_frames([1, 2, 3, 4, 5]):
        delivered.extend(jb.push(f))
    # Window 4 → pushing the 5th out-of-order frame should eject seq 0 and
    # release the buffered run.
    assert seqs(delivered) == [1, 2, 3, 4, 5]
    assert jb.stats.ejected == 1
    assert jb.stats.lost == 1
    assert jb.buffered == 0


def test_loss_tolerance_under_five_percent_in_realistic_stream():
    jb = JitterBuffer(window_size=8)
    # Simulate 200 frames; drop 8 (4%) and shuffle ±2 within local windows.
    total = 200
    drop = {37, 73, 88, 101, 134, 152, 167, 189}
    out_order: list[int] = []
    window = []
    for s in range(total):
        if s in drop:
            continue
        window.append(s)
        if len(window) == 4:
            # Light reorder: swap last two.
            window[-1], window[-2] = window[-2], window[-1]
            out_order.extend(window)
            window = []
    out_order.extend(window)

    delivered: list[AudioFrame] = []
    for f in build_test_frames(out_order):
        delivered.extend(jb.push(f))
    delivered.extend(jb.flush())

    expected_present = [s for s in range(total) if s not in drop]
    assert seqs(delivered) == expected_present
    assert jb.stats.lost == len(drop)
    assert jb.stats.loss_ratio() == pytest.approx(len(drop) / total, rel=1e-6)
    assert jb.stats.loss_ratio() < 0.05


def test_flush_empties_buffer_and_counts_gaps():
    jb = JitterBuffer(window_size=16)
    # Push 0, then a small gap, then 5, 6.
    delivered: list[AudioFrame] = []
    for f in build_test_frames([0, 5, 6]):
        delivered.extend(jb.push(f))
    # Buffer still holds 5 and 6 waiting for 1..4.
    assert jb.buffered == 2
    flushed = jb.flush()
    assert seqs(flushed) == [5, 6]
    assert jb.stats.lost == 4  # 1, 2, 3, 4 missing
    assert jb.buffered == 0


def test_reset_clears_state_and_counts():
    jb = JitterBuffer(window_size=8)
    for f in build_test_frames([0, 2, 4]):
        jb.push(f)
    jb.reset()
    assert jb.buffered == 0
    assert jb.next_seq is None
    assert jb.stats.resets == 1


def test_seq_wrap_around():
    jb = JitterBuffer(window_size=8)
    high = SEQ_SPACE - 1  # 65535
    wrap_seqs = [high - 1, high, 0, 1, 2]
    delivered: list[AudioFrame] = []
    for f in build_test_frames(wrap_seqs):
        delivered.extend(jb.push(f))
    assert seqs(delivered) == wrap_seqs
    assert jb.stats.lost == 0


def test_seq_diff_sign():
    assert seq_diff(5, 3) == 2
    assert seq_diff(3, 5) == -2
    # Wrap: 0 just ahead of 65535
    assert seq_diff(0, 65535) == 1
    assert seq_diff(65535, 0) == -1


def test_construction_rejects_bad_args():
    with pytest.raises(ValueError):
        JitterBuffer(window_size=0)
    with pytest.raises(ValueError):
        JitterBuffer(loss_tolerance=1.5)


def test_push_requires_audio_frame():
    jb = JitterBuffer()
    with pytest.raises(TypeError):
        jb.push(object())  # type: ignore[arg-type]


def test_stats_snapshot_shape():
    jb = JitterBuffer(window_size=4)
    for f in build_test_frames([0, 1, 2]):
        jb.push(f)
    snap = jb.stats.snapshot()
    assert {
        "pushed", "emitted", "duplicates", "late",
        "ejected", "lost", "resets", "max_buffered", "loss_ratio",
    }.issubset(snap.keys())
