"""Tests for a0_voqualizer BridgeRegistry + BridgeSession (A1.3).

Run:
    pytest tests/test_registry.py -v
from inside /a0/usr/plugins/a0_voqualizer/ using the framework venv:
    /opt/venv-a0/bin/python -m pytest tests/test_registry.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from helpers.registry import (  # noqa: E402
    BridgeRegistry,
    RegistryFull,
)
from helpers.session import (  # noqa: E402
    BridgeSession,
    InvalidStateTransition,
    STATE_INIT,
    STATE_READY,
    STATE_LISTENING,
    STATE_SPEAKING,
    STATE_PAUSED,
    STATE_ENDING,
    STATE_CLOSED,
)


# ---------------------------------------------------------------------------
# Test fixtures: a mock monotonic clock
# ---------------------------------------------------------------------------


class FakeClock:
    """Manually advanced monotonic clock for deterministic resume/TTL tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def registry(clock: FakeClock) -> BridgeRegistry:
    BridgeRegistry.reset_instance()
    return BridgeRegistry(
        max_concurrent_sessions=3,
        session_resume_window_seconds=30.0,
        max_session_seconds=60.0,
        audio_queue_max_frames=4,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# BridgeSession — state transitions
# ---------------------------------------------------------------------------


class TestBridgeSessionState:
    def test_valid_path_init_to_closed(self) -> None:
        s = BridgeSession(session_id="s1")
        assert s.state == STATE_INIT
        s.transition_to(STATE_READY)
        s.transition_to(STATE_LISTENING)
        s.transition_to(STATE_SPEAKING)
        s.transition_to(STATE_LISTENING)
        s.transition_to(STATE_PAUSED)
        s.transition_to(STATE_READY)
        s.transition_to(STATE_ENDING)
        s.transition_to(STATE_CLOSED)
        assert s.state == STATE_CLOSED

    def test_self_transition_is_noop(self) -> None:
        s = BridgeSession(session_id="s1")
        s.transition_to(STATE_INIT)  # init -> init
        assert s.state == STATE_INIT

    def test_illegal_transitions_raise(self) -> None:
        s = BridgeSession(session_id="s1")
        # init -> speaking is illegal (must go via ready/listening)
        with pytest.raises(InvalidStateTransition):
            s.transition_to(STATE_SPEAKING)
        # ending -> ready is illegal (ending only goes to closed)
        s.transition_to(STATE_READY)
        s.transition_to(STATE_ENDING)
        with pytest.raises(InvalidStateTransition):
            s.transition_to(STATE_READY)
        # closed is terminal
        s.transition_to(STATE_CLOSED)
        with pytest.raises(InvalidStateTransition):
            s.transition_to(STATE_READY)

    def test_unknown_state_raises(self) -> None:
        s = BridgeSession(session_id="s1")
        with pytest.raises(InvalidStateTransition):
            s.transition_to("chewing-gum")

    def test_close_is_idempotent(self) -> None:
        s = BridgeSession(session_id="s1")
        s.transition_to(STATE_READY)
        s.close()
        assert s.state == STATE_CLOSED
        # second close is a no-op
        s.close()
        assert s.state == STATE_CLOSED

    def test_empty_session_id_rejected(self) -> None:
        with pytest.raises(ValueError):
            BridgeSession(session_id="")

    def test_zero_queue_size_rejected(self) -> None:
        with pytest.raises(ValueError):
            BridgeSession(session_id="s1", audio_queue_max_frames=0)


# ---------------------------------------------------------------------------
# BridgeSession — audio queue & barge-in
# ---------------------------------------------------------------------------


class TestBridgeSessionAudio:
    def test_enqueue_below_capacity(self) -> None:
        s = BridgeSession(session_id="s1", audio_queue_max_frames=3)

        async def run() -> None:
            assert s.enqueue_audio(b"\x01") is True
            assert s.enqueue_audio(b"\x02") is True
            assert s.enqueue_audio(b"\x03") is True
            assert s.audio_queue.qsize() == 3
            assert s.audio_frames_dropped == 0
            assert s.audio_frames_enqueued == 3

        asyncio.run(run())

    def test_enqueue_drops_oldest_when_full(self) -> None:
        s = BridgeSession(session_id="s1", audio_queue_max_frames=2)

        async def run() -> None:
            assert s.enqueue_audio("a") is True
            assert s.enqueue_audio("b") is True
            # Third frame must evict the oldest ("a").
            assert s.enqueue_audio("c") is False
            assert s.audio_frames_dropped == 1
            assert s.audio_frames_enqueued == 3
            # Queue now contains b, c — pull them in order.
            assert s.audio_queue.get_nowait() == "b"
            assert s.audio_queue.get_nowait() == "c"

        asyncio.run(run())

    def test_cancel_in_flight_tts_visible_within_one_tick(self) -> None:
        s = BridgeSession(session_id="s1")

        async def run() -> None:
            consumer_saw = asyncio.Event()

            async def consumer() -> None:
                # Wait on cancel_tts; signal that we observed it.
                await s.cancel_tts.wait()
                consumer_saw.set()

            task = asyncio.create_task(consumer())
            # Allow consumer to enter the wait.
            await asyncio.sleep(0)
            s.cancel_in_flight_tts()
            # One event-loop tick is enough.
            await asyncio.wait_for(consumer_saw.wait(), timeout=1.0)
            await task
            assert s.cancel_tts.is_set() is True

        asyncio.run(run())

    def test_reset_cancel_clears_flag(self) -> None:
        s = BridgeSession(session_id="s1")
        s.cancel_in_flight_tts()
        assert s.cancel_tts.is_set() is True
        s.reset_cancel()
        assert s.cancel_tts.is_set() is False


# ---------------------------------------------------------------------------
# BridgeRegistry — create / get / remove
# ---------------------------------------------------------------------------


class TestBridgeRegistryBasics:
    def test_create_get_remove(self, registry: BridgeRegistry) -> None:
        async def run() -> None:
            s, resumed = await registry.create_or_resume("sess-a")
            assert isinstance(s, BridgeSession)
            assert resumed is False
            assert registry.get("sess-a") is s
            assert registry.count() == 1
            removed = await registry.remove("sess-a")
            assert removed is True
            # Live map cleared.
            assert registry.get("sess-a") is None
            assert registry.count() == 0
            # Second remove returns False.
            assert await registry.remove("sess-a") is False

        asyncio.run(run())

    def test_empty_session_id_rejected(self, registry: BridgeRegistry) -> None:
        async def run() -> None:
            with pytest.raises(ValueError):
                await registry.create_or_resume("")

        asyncio.run(run())

    def test_iter_active_snapshot(self, registry: BridgeRegistry) -> None:
        async def run() -> None:
            await registry.create_or_resume("a")
            await registry.create_or_resume("b")
            ids = sorted(s.session_id for s in registry.iter_active())
            assert ids == ["a", "b"]

        asyncio.run(run())


# ---------------------------------------------------------------------------
# BridgeRegistry — concurrency limit
# ---------------------------------------------------------------------------


class TestBridgeRegistryLimits:
    def test_enforces_max_concurrent_sessions(self, registry: BridgeRegistry) -> None:
        async def run() -> None:
            await registry.create_or_resume("s1")
            await registry.create_or_resume("s2")
            await registry.create_or_resume("s3")
            # registry capacity is 3 in the fixture
            with pytest.raises(RegistryFull) as exc:
                await registry.create_or_resume("s4")
            assert exc.value.current == 3
            assert exc.value.limit == 3

        asyncio.run(run())

    def test_capacity_recovers_after_remove(self, registry: BridgeRegistry) -> None:
        async def run() -> None:
            await registry.create_or_resume("s1")
            await registry.create_or_resume("s2")
            await registry.create_or_resume("s3")
            await registry.remove("s2", tombstone=False)
            # Should now have room for one more fresh session.
            s4, resumed = await registry.create_or_resume("s4")
            assert resumed is False
            assert registry.count() == 3
            assert s4.session_id == "s4"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# BridgeRegistry — resume semantics
# ---------------------------------------------------------------------------


class TestBridgeRegistryResume:
    def test_resume_live_session(self, registry: BridgeRegistry) -> None:
        async def run() -> None:
            s1, was_resumed_first = await registry.create_or_resume("sx")
            s2, was_resumed_second = await registry.create_or_resume("sx")
            assert was_resumed_first is False
            assert was_resumed_second is True
            # Live resume returns the *same* instance.
            assert s1 is s2

        asyncio.run(run())

    def test_resume_within_window_after_remove(
        self, registry: BridgeRegistry, clock: FakeClock
    ) -> None:
        async def run() -> None:
            first, _ = await registry.create_or_resume(
                "sx",
                context_id="ctx-1",
                asr_provider="whisper-local",
                tts_provider="piper-local",
                input_codec="pcm16/16k",
                output_codec="pcm16/24k",
                language="en",
            )
            first_id = id(first)
            await registry.remove("sx")  # tombstones by default
            # Advance within resume window.
            clock.advance(15.0)
            resumed, was_resumed = await registry.create_or_resume("sx")
            assert was_resumed is True
            # Same session_id, but a new BridgeSession instance (the prior one
            # may have been bound to a dead event loop).
            assert resumed.session_id == "sx"
            assert id(resumed) != first_id
            # Resume carries forward provider/codec/language metadata.
            assert resumed.context_id == "ctx-1"
            assert resumed.asr_provider == "whisper-local"
            assert resumed.tts_provider == "piper-local"
            assert resumed.input_codec == "pcm16/16k"
            assert resumed.output_codec == "pcm16/24k"
            assert resumed.language == "en"
            # Resumed sessions land in 'ready' (handshake completed previously).
            assert resumed.state == STATE_READY

        asyncio.run(run())

    def test_fresh_session_beyond_resume_window(
        self, registry: BridgeRegistry, clock: FakeClock
    ) -> None:
        async def run() -> None:
            await registry.create_or_resume(
                "sx",
                context_id="ctx-1",
                asr_provider="whisper-local",
            )
            await registry.remove("sx")
            # Advance past resume window (30s in fixture).
            clock.advance(60.0)
            fresh, was_resumed = await registry.create_or_resume("sx")
            assert was_resumed is False
            # Fresh session: no carried-over metadata.
            assert fresh.context_id == ""
            assert fresh.asr_provider == ""

        asyncio.run(run())


# ---------------------------------------------------------------------------
# BridgeRegistry — idle GC
# ---------------------------------------------------------------------------


class TestBridgeRegistryGc:
    def test_gc_idle_removes_timed_out_sessions(
        self, registry: BridgeRegistry, clock: FakeClock
    ) -> None:
        async def run() -> None:
            await registry.create_or_resume("old")
            await registry.create_or_resume("fresh")
            # Advance most of the way to TTL, then touch only "fresh".
            clock.advance(50.0)
            fresh = registry.get("fresh")
            assert fresh is not None
            fresh.touch(clock())
            # Advance past TTL (60s in fixture).
            clock.advance(15.0)
            evicted = await registry.gc_idle()
            assert evicted == ["old"]
            assert registry.get("old") is None
            assert registry.get("fresh") is not None
            assert registry.get("fresh").state != STATE_CLOSED

        asyncio.run(run())

    def test_gc_idle_with_explicit_ttl(
        self, registry: BridgeRegistry, clock: FakeClock
    ) -> None:
        async def run() -> None:
            await registry.create_or_resume("x")
            clock.advance(5.0)
            evicted = await registry.gc_idle(ttl=1.0)
            assert evicted == ["x"]

        asyncio.run(run())

    def test_gc_idle_returns_empty_when_nothing_aged(
        self, registry: BridgeRegistry
    ) -> None:
        async def run() -> None:
            await registry.create_or_resume("x")
            evicted = await registry.gc_idle()
            assert evicted == []

        asyncio.run(run())


# ---------------------------------------------------------------------------
# BridgeRegistry — config wiring
# ---------------------------------------------------------------------------


class TestBridgeRegistryConfig:
    def test_from_config_uses_config_values(self) -> None:
        BridgeRegistry.reset_instance()
        cfg = {
            "limits": {
                "max_concurrent_sessions": 7,
                "max_session_seconds": 600,
                "audio_queue_max_frames": 12,
            },
            "protocol": {
                "session_resume_window_seconds": 25,
            },
        }
        reg = BridgeRegistry.from_config(cfg, replace=False)
        assert reg.max_concurrent_sessions == 7
        assert reg.session_resume_window_seconds == 25.0
        assert reg.max_session_seconds == 600.0
        assert reg.audio_queue_max_frames == 12

    def test_configure_replaces_singleton(self) -> None:
        BridgeRegistry.reset_instance()
        a = BridgeRegistry.instance()
        b = BridgeRegistry.configure(
            max_concurrent_sessions=2,
            session_resume_window_seconds=10.0,
            max_session_seconds=100.0,
            audio_queue_max_frames=8,
        )
        assert a is not b
        assert BridgeRegistry.instance() is b

    def test_invalid_init_args_rejected(self) -> None:
        with pytest.raises(ValueError):
            BridgeRegistry(max_concurrent_sessions=0)
        with pytest.raises(ValueError):
            BridgeRegistry(session_resume_window_seconds=-1.0)
        with pytest.raises(ValueError):
            BridgeRegistry(max_session_seconds=0)
        with pytest.raises(ValueError):
            BridgeRegistry(audio_queue_max_frames=0)
