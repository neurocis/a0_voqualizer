from __future__ import annotations

import asyncio
from types import SimpleNamespace

from usr.plugins.a0_voqualizer.helpers.context_bridge import ContextBridge
from usr.plugins.a0_voqualizer.helpers.cx_stream import CxStreamHub, stream_id_for
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry


def run(coro):
    return asyncio.run(coro)


class FakeContext:
    def __init__(self, id="ctx-1"):
        self.id = id
        self.config = "cfg"

    def communicate(self, msg, broadcast_level=1):
        return "task"


class FakeRuntime:
    def __init__(self):
        self.contexts = {"ctx-1": FakeContext("ctx-1")}

    def get(self, context_id):
        return self.contexts.get(context_id)

    def create(self, **kwargs):
        ctx = FakeContext("ctx-new")
        self.contexts[ctx.id] = ctx
        return ctx


def make_bridge(runtime):
    return ContextBridge(
        context_getter=runtime.get,
        context_factory=runtime.create,
        user_message_factory=lambda **kw: SimpleNamespace(**kw),
        config_factory=lambda: "cfg",
    )


async def install_session(monkeypatch, *, session_id="sess-1", context_id="ctx-1", bridge_bound=True):
    runtime = FakeRuntime()
    bridge = make_bridge(runtime)
    if bridge_bound:
        bridge.bind_session(session_id, context_id=context_id)
    monkeypatch.setattr(
        "usr.plugins.a0_voqualizer.helpers.cx_stream.get_default_context_bridge",
        lambda: bridge,
    )
    BridgeRegistry.reset_instance()
    registry = BridgeRegistry.configure(
        max_concurrent_sessions=8,
        session_resume_window_seconds=30,
        max_session_seconds=300,
        audio_queue_max_frames=4,
    )
    session, _ = await registry.create_or_resume(session_id, context_id=context_id)
    emitted = []

    async def sender(event, payload):
        emitted.append((event, payload))

    session.sender = sender
    return session, emitted


def test_stream_id_for_is_stable():
    assert stream_id_for("ctx-1", "msg-1") == "cx-ctx-1-msg-1-1"


def test_cx_stream_token_emits_start_and_token(monkeypatch):
    async def scenario():
        _session, emitted = await install_session(monkeypatch)
        hub = CxStreamHub()

        result = await hub.token(context_id="ctx-1", delta="Hel", full="Hel", message_id="msg-1")

        assert result["event"] == "voqualizer_cx_token"
        assert [event for event, _payload in emitted] == ["voqualizer_cx_stream_start", "voqualizer_cx_token"]
        start = emitted[0][1]
        token = emitted[1][1]
        assert start["session_id"] == "sess-1"
        assert start["context_id"] == "ctx-1"
        assert start["stream_id"] == "cx-ctx-1-msg-1-1"
        assert token["delta"] == "Hel"
        assert token["text"] == "Hel"
        assert token["seq"] == 1
        assert token["channel"] == "assistant"
        assert token["role"] == "assistant"
        assert token["is_final"] is False

    run(scenario())


def test_cx_stream_uses_session_context_fallback(monkeypatch):
    async def scenario():
        _session, emitted = await install_session(monkeypatch, bridge_bound=False)
        hub = CxStreamHub()

        await hub.token(context_id="ctx-1", delta="Hi")

        assert [event for event, _payload in emitted] == ["voqualizer_cx_stream_start", "voqualizer_cx_token"]

    run(scenario())


def test_cx_stream_final_marks_final(monkeypatch):
    async def scenario():
        _session, emitted = await install_session(monkeypatch)
        hub = CxStreamHub()
        await hub.token(context_id="ctx-1", delta="Hi", full="Hi")
        result = await hub.final(context_id="ctx-1", text="Hi there", finish_reason="stop")

        assert result["event"] == "voqualizer_cx_stream_final"
        final = emitted[-1][1]
        assert emitted[-1][0] == "voqualizer_cx_stream_final"
        assert final["text"] == "Hi there"
        assert final["is_final"] is True
        assert final["finish_reason"] == "stop"
        assert final["seq"] == 2

    run(scenario())
