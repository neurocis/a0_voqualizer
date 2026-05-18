"""a0_voqualizer BridgeSession state machine.

A :class:`BridgeSession` represents one live voice bridge between a remote WS
client and an Agent Zero ``AgentContext``. The session owns:

* identifiers and binding metadata (``session_id``, ``context_id``)
* negotiated codecs / provider names / language / barge_in flag
* a bounded :class:`asyncio.Queue` of incoming audio frames
  (oldest-dropped on overflow — backpressure tolerance for VoIP/mobile)
* an :class:`asyncio.Event` consumers watch to cancel in-flight TTS
  (barge-in plumbing)
* a state field driven by a strict transition graph
* lifecycle timestamps used by :class:`BridgeRegistry.gc_idle`
* an optional sender callable wired by the WS handler for outbound emits

The state machine
=================

``init`` → ``ready`` after handshake.
``ready`` ↔ ``listening`` ↔ ``speaking`` ↔ ``paused`` while live.
Any non-terminal state may move to ``ending`` (graceful drain) which then
moves to ``closed`` (terminal).

Illegal transitions raise :class:`InvalidStateTransition`.

The class is intentionally I/O-free except for the audio queue and the cancel
event — both are asyncio primitives — so it remains trivially unit-testable.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


# Public type aliases

SessionState = str  # one of STATES
SenderCallable = Callable[[str, dict], Awaitable[None]]
"""Async sender used to push outbound events to the WS client.

Signature: ``async def send(event_name: str, payload: dict) -> None``.
"""


# State graph

STATE_INIT = "init"
STATE_READY = "ready"
STATE_LISTENING = "listening"
STATE_SPEAKING = "speaking"
STATE_PAUSED = "paused"
STATE_ENDING = "ending"
STATE_CLOSED = "closed"

STATES: frozenset[str] = frozenset({
    STATE_INIT, STATE_READY, STATE_LISTENING,
    STATE_SPEAKING, STATE_PAUSED, STATE_ENDING, STATE_CLOSED,
})

# Adjacency list: state -> allowed next states.
# ``ending`` is reachable from every non-closed state. ``closed`` is terminal.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_INIT:      frozenset({STATE_READY, STATE_ENDING, STATE_CLOSED}),
    STATE_READY:     frozenset({STATE_LISTENING, STATE_PAUSED, STATE_ENDING, STATE_CLOSED}),
    STATE_LISTENING: frozenset({STATE_SPEAKING, STATE_READY, STATE_PAUSED, STATE_ENDING, STATE_CLOSED}),
    STATE_SPEAKING:  frozenset({STATE_LISTENING, STATE_READY, STATE_PAUSED, STATE_ENDING, STATE_CLOSED}),
    STATE_PAUSED:    frozenset({STATE_READY, STATE_LISTENING, STATE_ENDING, STATE_CLOSED}),
    STATE_ENDING:    frozenset({STATE_CLOSED}),
    STATE_CLOSED:    frozenset(),  # terminal
}


class InvalidStateTransition(ValueError):
    """Raised when :meth:`BridgeSession.transition_to` rejects a transition."""

    def __init__(self, current: str, requested: str) -> None:
        self.current = current
        self.requested = requested
        super().__init__(
            f"Invalid state transition: {current!r} -> {requested!r}"
        )


@dataclass
class BridgeSession:
    """Live voice-bridge session state.

    Construct via :meth:`BridgeRegistry.create_or_resume`; direct construction
    is supported for unit tests but the registry is the only authoritative
    place that enforces concurrency limits and resume semantics.

    Attributes:
        session_id: opaque string id (UUID4 or client-supplied).
        context_id: bound :class:`AgentContext` id (may be empty until bind).
        asr_provider: configured ASR provider name.
        tts_provider: configured TTS provider name.
        input_codec: codec string for ingress audio (e.g. ``"pcm16/16k"``).
        output_codec: codec string for egress TTS audio.
        language: ASR language code or ``"auto"``.
        barge_in: whether incoming user speech should cancel in-flight TTS.
        audio_queue_max_frames: bounded queue capacity (oldest-dropped).
        state: current :data:`SessionState`.
        created_at: monotonic seconds at construction (set automatically).
        last_activity_at: monotonic seconds of last :meth:`touch`.
        sender: optional outbound emit callback wired by the WS handler.
        metadata: free-form bag for adapter use.
    """

    session_id: str
    context_id: str = ""
    asr_provider: str = ""
    tts_provider: str = ""
    input_codec: str = ""
    output_codec: str = ""
    language: str = "auto"
    barge_in: bool = True
    audio_queue_max_frames: int = 256
    state: SessionState = STATE_INIT
    created_at: float = field(default_factory=time.monotonic)
    last_activity_at: float = field(default_factory=time.monotonic)
    sender: Optional[SenderCallable] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # asyncio primitives are built lazily so dataclass construction stays
    # event-loop-agnostic (the registry / WS handler creates them on first use).
    _audio_queue: Optional[asyncio.Queue] = field(default=None, repr=False, compare=False)
    _cancel_tts: Optional[asyncio.Event] = field(default=None, repr=False, compare=False)

    # Drop accounting (backpressure metrics surface for A2.5).
    audio_frames_dropped: int = 0
    audio_frames_enqueued: int = 0

    def __post_init__(self) -> None:
        if self.session_id == "":
            raise ValueError("BridgeSession.session_id must be non-empty")
        if self.audio_queue_max_frames < 1:
            raise ValueError("audio_queue_max_frames must be >= 1")
        if self.state not in STATES:
            raise ValueError(f"unknown state: {self.state!r}")

    # asyncio primitives (lazy)

    @property
    def audio_queue(self) -> asyncio.Queue:
        """Bounded queue for incoming audio frames.

        Lazily created so :class:`BridgeSession` can be instantiated outside an
        event loop (tests that don't exercise the queue still work).
        """
        if self._audio_queue is None:
            self._audio_queue = asyncio.Queue(maxsize=self.audio_queue_max_frames)
        return self._audio_queue

    @property
    def cancel_tts(self) -> asyncio.Event:
        """Event consumers (TTS pump) watch to abort in-flight synthesis."""
        if self._cancel_tts is None:
            self._cancel_tts = asyncio.Event()
        return self._cancel_tts

    # Lifecycle

    def touch(self, now: float | None = None) -> None:
        """Update ``last_activity_at`` to ``now`` or :func:`time.monotonic`."""
        self.last_activity_at = time.monotonic() if now is None else now

    def transition_to(self, new_state: SessionState) -> None:
        """Move to ``new_state`` if the graph allows; else raise.

        Self-transitions (``state -> state``) are allowed as no-ops to make
        idempotent updates from handlers ergonomic.
        """
        if new_state not in STATES:
            raise InvalidStateTransition(self.state, new_state)
        if new_state == self.state:
            return  # no-op
        if new_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidStateTransition(self.state, new_state)
        self.state = new_state
        self.touch()

    # Audio plumbing

    def enqueue_audio(self, frame: Any) -> bool:
        """Enqueue an audio frame with oldest-dropped backpressure policy.

        Returns ``True`` if the frame was queued without dropping, ``False`` if
        the queue was full and the oldest frame was evicted to make room.

        We never block: if the queue is full we ``get_nowait`` (discarding the
        oldest) and then ``put_nowait`` the new frame, so the producer always
        wins on a saturated bridge — matching the policy declared in PLAN.md §4.
        """
        q = self.audio_queue
        dropped = False
        if q.full():
            try:
                q.get_nowait()
                self.audio_frames_dropped += 1
                dropped = True
            except asyncio.QueueEmpty:  # pragma: no cover — race-only
                pass
        q.put_nowait(frame)
        self.audio_frames_enqueued += 1
        self.touch()
        return not dropped

    def cancel_in_flight_tts(self) -> None:
        """Signal the TTS pump to abort the current utterance (barge-in)."""
        self.cancel_tts.set()
        self.touch()

    def reset_cancel(self) -> None:
        """Clear the cancel flag at the start of a new TTS utterance."""
        if self._cancel_tts is not None:
            self._cancel_tts.clear()

    # Backpressure / queue metrics

    @property
    def audio_queue_size(self) -> int:
        """Current queued audio-frame count.

        Returns ``0`` until the lazy queue is first created. This avoids
        creating asyncio primitives just to inspect metrics from admin/status
        surfaces.
        """

        return 0 if self._audio_queue is None else self._audio_queue.qsize()

    @property
    def audio_queue_capacity(self) -> int:
        """Maximum number of audio frames the bounded queue can hold."""

        return self.audio_queue_max_frames

    @property
    def audio_queue_drop_ratio(self) -> float:
        """Dropped/enqueued ratio for backpressure telemetry."""

        if self.audio_frames_enqueued <= 0:
            return 0.0
        return self.audio_frames_dropped / self.audio_frames_enqueued

    def backpressure_metrics(self) -> dict[str, int | float]:
        """Return JSON-safe bounded-queue/backpressure metrics."""

        return {
            "audio_queue_size": self.audio_queue_size,
            "audio_queue_capacity": self.audio_queue_capacity,
            "audio_frames_enqueued": self.audio_frames_enqueued,
            "audio_frames_dropped": self.audio_frames_dropped,
            "audio_queue_drop_ratio": self.audio_queue_drop_ratio,
        }

    def close(self) -> None:
        """Mark the session ``closed`` and signal any pumps to exit.

        Idempotent: calling on an already-closed session is a no-op (and is
        allowed even though ``closed -> closed`` is technically not in the
        adjacency table — closing twice is harmless).
        """
        if self.state == STATE_CLOSED:
            return
        # Coerce through ending so observers see the lifecycle properly.
        if self.state != STATE_ENDING:
            self.transition_to(STATE_ENDING)
        self.transition_to(STATE_CLOSED)
        # Wake any consumer awaiting cancel_tts.
        if self._cancel_tts is not None:
            self._cancel_tts.set()

    # Convenience

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot (omits asyncio primitives)."""
        return {
            "session_id": self.session_id,
            "context_id": self.context_id,
            "state": self.state,
            "asr_provider": self.asr_provider,
            "tts_provider": self.tts_provider,
            "input_codec": self.input_codec,
            "output_codec": self.output_codec,
            "language": self.language,
            "barge_in": self.barge_in,
            "created_at": self.created_at,
            "last_activity_at": self.last_activity_at,
            **self.backpressure_metrics(),
        }


__all__ = [
    "BridgeSession",
    "InvalidStateTransition",
    "SenderCallable",
    "SessionState",
    "STATES",
    "STATE_INIT",
    "STATE_READY",
    "STATE_LISTENING",
    "STATE_SPEAKING",
    "STATE_PAUSED",
    "STATE_ENDING",
    "STATE_CLOSED",
]
