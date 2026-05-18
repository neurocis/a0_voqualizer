"""Audio-frame jitter buffer for the Voqualizer protocol.

A2.3 — reorder out-of-order audio frames by their 16-bit sequence number,
tolerate gaps (loss) up to a configurable threshold, and eject stale frames
that sit in the buffer beyond a window.

The buffer is purely synchronous and protocol-agnostic. It operates on
:class:`helpers.frame.AudioFrame` instances and yields them in playout order.
The WS layer wraps this for per-session reordering.

Design:

* ``next_seq`` tracks the next expected sequence number.
* Frames arriving with the expected ``seq`` are released immediately, and any
  in-buffer successors that now form a contiguous run are released after
  them.
* Out-of-order frames are buffered (deduplicated by ``seq``) until their slot
  becomes the head, or until the buffer accumulates more than
  ``window_size`` frames or the head-of-line wait exceeds the window-in-time,
  in which case we eject (drop) the missing frames and resync.
* Late frames whose ``seq`` is behind ``next_seq`` are silently dropped and
  counted as duplicates/late.
* 16-bit sequence wrap-around is handled by comparing signed deltas modulo
  65536 (``MAX_SEQ + 1``).

Metrics surface as plain attributes so callers (BridgeSession, telemetry)
can read them without locking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .frame import AudioFrame, FrameHeader, MAX_U16

SEQ_SPACE: int = MAX_U16 + 1
SEQ_HALF: int = SEQ_SPACE // 2


def seq_diff(a: int, b: int) -> int:
    """Return signed distance ``a - b`` in 16-bit sequence space.

    Result is in ``[-32768, 32767]``. ``a`` is ahead of ``b`` when positive.
    """

    return ((a - b + SEQ_HALF) % SEQ_SPACE) - SEQ_HALF


@dataclass
class JitterStats:
    """Counters for a :class:`JitterBuffer`."""

    pushed: int = 0
    emitted: int = 0
    duplicates: int = 0
    late: int = 0
    ejected: int = 0
    lost: int = 0
    resets: int = 0
    max_buffered: int = 0

    def loss_ratio(self) -> float:
        """Fraction of expected frames that were never delivered."""

        expected = self.emitted + self.lost
        return self.lost / expected if expected else 0.0

    def snapshot(self) -> dict[str, int | float]:
        return {
            "pushed": self.pushed,
            "emitted": self.emitted,
            "duplicates": self.duplicates,
            "late": self.late,
            "ejected": self.ejected,
            "lost": self.lost,
            "resets": self.resets,
            "max_buffered": self.max_buffered,
            "loss_ratio": self.loss_ratio(),
        }


class JitterBuffer:
    """Sequence-aware reorder buffer with bounded waiting window."""

    def __init__(
        self,
        window_size: int = 16,
        loss_tolerance: float = 0.05,
        *,
        initial_seq: int | None = None,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        if not 0.0 <= loss_tolerance <= 1.0:
            raise ValueError("loss_tolerance must be in [0, 1]")
        if initial_seq is not None and not 0 <= initial_seq <= MAX_U16:
            raise ValueError(f"initial_seq must be in [0, {MAX_U16}]")
        self.window_size = int(window_size)
        self.loss_tolerance = float(loss_tolerance)
        self._frames: dict[int, AudioFrame] = {}
        self._next_seq: Optional[int] = initial_seq
        self.stats = JitterStats()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def next_seq(self) -> Optional[int]:
        return self._next_seq

    @property
    def buffered(self) -> int:
        return len(self._frames)

    def push(self, frame: AudioFrame) -> list[AudioFrame]:
        """Submit a frame; return any frames that become deliverable."""

        if not isinstance(frame, AudioFrame):
            raise TypeError("push expects an AudioFrame")
        self.stats.pushed += 1
        seq = frame.seq

        if self._next_seq is None:
            self._next_seq = seq
            ready = [frame]
            ready.extend(self._drain_contiguous())
            return self._emit(ready)

        delta = seq_diff(seq, self._next_seq)
        if delta < 0:
            # Frame from the past — late delivery. In practice this is also
            # usually a duplicate of a frame already emitted, so count both.
            self.stats.late += 1
            self.stats.duplicates += 1
            return []

        if seq in self._frames:
            self.stats.duplicates += 1
            return []

        if delta == 0:
            ready = [frame]
            ready.extend(self._drain_contiguous())
            return self._emit(ready)

        # Out-of-order future frame.
        self._frames[seq] = frame
        if len(self._frames) > self.stats.max_buffered:
            self.stats.max_buffered = len(self._frames)
        if len(self._frames) > self.window_size:
            return self._eject_until_progress()
        return []

    def push_many(self, frames: Iterable[AudioFrame]) -> list[AudioFrame]:
        out: list[AudioFrame] = []
        for f in frames:
            out.extend(self.push(f))
        return out

    def flush(self) -> list[AudioFrame]:
        """Eject all remaining buffered frames as a final drain.

        Missing frames in any gaps are counted as lost. Useful at end of
        session or before resampling/resync.
        """

        if self._next_seq is None or not self._frames:
            return []
        out: list[AudioFrame] = []
        for seq in sorted(self._frames.keys(), key=lambda s: seq_diff(s, self._next_seq or 0)):
            frame = self._frames.pop(seq)
            gap = seq_diff(seq, self._next_seq) if self._next_seq is not None else 0
            if gap > 0:
                self.stats.lost += gap
            self._next_seq = (seq + 1) % SEQ_SPACE
            out.append(frame)
        return self._emit(out, count_emit=True)

    def reset(self) -> None:
        """Drop all buffered frames and forget the expected seq."""

        self._frames.clear()
        self._next_seq = None
        self.stats.resets += 1

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _drain_contiguous(self) -> list[AudioFrame]:
        out: list[AudioFrame] = []
        assert self._next_seq is not None
        nxt = (self._next_seq + 1) % SEQ_SPACE
        while nxt in self._frames:
            out.append(self._frames.pop(nxt))
            nxt = (nxt + 1) % SEQ_SPACE
        return out

    def _eject_until_progress(self) -> list[AudioFrame]:
        """Drop missing frames at the head until buffer shrinks below window.

        Counts ejected (missing) frames as both ``ejected`` and ``lost``.
        """

        out: list[AudioFrame] = []
        assert self._next_seq is not None
        # Find the smallest seq still buffered (in cyclic distance).
        nearest = min(
            self._frames.keys(),
            key=lambda s: seq_diff(s, self._next_seq or 0),
        )
        gap = seq_diff(nearest, self._next_seq)
        if gap > 0:
            self.stats.ejected += gap
            self.stats.lost += gap
        self._next_seq = nearest
        out.append(self._frames.pop(nearest))
        out.extend(self._drain_contiguous())
        return self._emit(out)

    def _emit(self, frames: list[AudioFrame], count_emit: bool = True) -> list[AudioFrame]:
        if not frames:
            return frames
        last_seq = frames[-1].seq
        self._next_seq = (last_seq + 1) % SEQ_SPACE
        if count_emit:
            self.stats.emitted += len(frames)
        return frames


def build_test_frames(seqs: Iterable[int], *, ts_step: int = 20) -> list[AudioFrame]:
    """Helper for tests: build AudioFrames with monotonically stepping ``ts_ms``."""

    out: list[AudioFrame] = []
    for i, seq in enumerate(seqs):
        ts = (i * ts_step) % SEQ_SPACE
        out.append(AudioFrame(FrameHeader(seq=seq % SEQ_SPACE, ts_ms=ts), b""))
    return out
