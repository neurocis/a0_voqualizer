from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

# Import plugin extension through /a0 like the live framework does, but provide
# small framework stubs so pytest does not need a full A0 runtime bootstrap.
_ORIG_SYS_PATH = list(sys.path)
A0_ROOT = str(Path("/a0"))
PLUGIN_ROOT = str(Path(__file__).resolve().parents[1])
for entry in ("", PLUGIN_ROOT):
    while entry in sys.path:
        sys.path.remove(entry)
while A0_ROOT in sys.path:
    sys.path.remove(A0_ROOT)
sys.path.insert(0, A0_ROOT)

helpers_pkg = types.ModuleType("helpers")
extension_mod = types.ModuleType("helpers.extension")
print_style_mod = types.ModuleType("helpers.print_style")
agent_mod = types.ModuleType("agent")

class Extension:
    def __init__(self, agent=None, **kwargs):
        self.agent = agent
        self.kwargs = kwargs

class PrintStyle:
    def __init__(self, *args, **kwargs):
        self.messages = []
    def print(self, message):
        self.messages.append(message)

class LoopData:
    pass

extension_mod.Extension = Extension
print_style_mod.PrintStyle = PrintStyle
agent_mod.LoopData = LoopData
helpers_pkg.extension = extension_mod
helpers_pkg.print_style = print_style_mod
sys.modules["helpers"] = helpers_pkg
sys.modules["helpers.extension"] = extension_mod
sys.modules["helpers.print_style"] = print_style_mod
sys.modules["agent"] = agent_mod

from usr.plugins.a0_voqualizer.extensions.python.response_stream_chunk._50_voqualizer import (  # noqa: E402
    VoqualizerResponseStreamChunk,
    emit_agent_delta_for_context,
)
from usr.plugins.a0_voqualizer.helpers.context_bridge import ContextBridge  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry  # noqa: E402

# Restore normal plugin test import behavior after imports.
sys.path[:] = _ORIG_SYS_PATH
for _name in ["helpers", "helpers.extension", "helpers.print_style", "agent"]:
    sys.modules.pop(_name, None)


def run(coro):
    return asyncio.run(coro)


class FakeContext:
    def __init__(self, id="ctx-1"):
        self.id = id
        self.config = "cfg"
        self.calls = []
    def communicate(self, msg, broadcast_level=1):
        self.calls.append((msg, broadcast_level))
        return "task"


class FakeRuntime:
    def __init__(self):
        self.contexts = {"ctx-1": FakeContext("ctx-1"), "ctx-2": FakeContext("ctx-2")}
    def get(self, context_id):
        return self.contexts.get(context_id)
    def create(self, **kwargs):
        ctx = FakeContext(f"ctx-{len(self.contexts)+1}")
        self.contexts[ctx.id] = ctx
        return ctx


def make_bridge(runtime):
    return ContextBridge(
        context_getter=runtime.get,
        context_factory=runtime.create,
        user_message_factory=lambda **kw: SimpleNamespace(**kw),
        config_factory=lambda: "cfg",
    )


async def install_bound_session(monkeypatch, *, session_id="sess-1", context_id="ctx-1"):
    runtime = FakeRuntime()
    bridge = make_bridge(runtime)
    bridge.bind_session(session_id, context_id=context_id)
    monkeypatch.setattr(
        "usr.plugins.a0_voqualizer.helpers.context_bridge.get_default_context_bridge",
        lambda: bridge,
    )
    # The extension imports get_default_context_bridge directly from the module at
    # call time, so monkeypatch the extension module's imported function path too.
    monkeypatch.setattr(
        "usr.plugins.a0_voqualizer.extensions.python.response_stream_chunk._50_voqualizer.get_default_context_bridge",
        lambda: bridge,
        raising=False,
    )

    BridgeRegistry.reset_instance()
    registry = BridgeRegistry.configure(
        max_concurrent_sessions=8,
        session_resume_window_seconds=30,
        max_session_seconds=300,
        audio_queue_max_frames=4,
    )
    session, _resumed = await registry.create_or_resume(session_id, context_id=context_id)
    emitted = []
    async def sender(event, payload):
        emitted.append((event, payload))
    session.sender = sender
    return bridge, session, emitted


def test_emit_agent_delta_for_context_sends_voqualizer_agent_delta(monkeypatch):
    async def scenario():
        _bridge, _session, emitted = await install_bound_session(monkeypatch)

        count = await emit_agent_delta_for_context(context_id="ctx-1", text="hello")

        assert count == 1
        assert emitted == [
            (
                "voqualizer_agent_delta",
                {"session_id": "sess-1", "context_id": "ctx-1", "text": "hello"},
            )
        ]

    run(scenario())


def test_emit_agent_delta_for_context_ignores_unbound_context(monkeypatch):
    async def scenario():
        _bridge, _session, emitted = await install_bound_session(monkeypatch)

        count = await emit_agent_delta_for_context(context_id="ctx-2", text="hello")

        assert count == 0
        assert emitted == []

    run(scenario())


def test_emit_agent_delta_for_context_skips_missing_sender(monkeypatch):
    async def scenario():
        _bridge, session, emitted = await install_bound_session(monkeypatch)
        session.sender = None

        count = await emit_agent_delta_for_context(context_id="ctx-1", text="hello")

        assert count == 0
        assert emitted == []

    run(scenario())


def test_extension_uses_agent_context_and_stream_chunk(monkeypatch):
    async def scenario():
        _bridge, _session, emitted = await install_bound_session(monkeypatch)
        agent = SimpleNamespace(context=SimpleNamespace(id="ctx-1"))
        extension = VoqualizerResponseStreamChunk(agent=agent)

        await extension.execute(stream_data={"chunk": "token", "full": "token"})

        assert emitted == [
            (
                "voqualizer_agent_delta",
                {"session_id": "sess-1", "context_id": "ctx-1", "text": "token"},
            )
        ]

    run(scenario())


def test_extension_ignores_empty_chunk(monkeypatch):
    async def scenario():
        _bridge, _session, emitted = await install_bound_session(monkeypatch)
        agent = SimpleNamespace(context=SimpleNamespace(id="ctx-1"))
        extension = VoqualizerResponseStreamChunk(agent=agent)

        await extension.execute(stream_data={"chunk": "", "full": "full"})

        assert emitted == []

    run(scenario())


def test_context_bridge_reverse_lookup_returns_copy():
    runtime = FakeRuntime()
    bridge = make_bridge(runtime)
    binding = bridge.bind_session("sess-1", context_id="ctx-1")
    found = bridge.bindings_for_context("ctx-1")

    assert found == [binding]
    found.clear()
    assert bridge.bindings_for_context("ctx-1") == [binding]
