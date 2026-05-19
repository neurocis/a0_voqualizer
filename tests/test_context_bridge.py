from __future__ import annotations

from dataclasses import dataclass, field
import sys

import pytest

sys.path.insert(0, "/a0")

from usr.plugins.a0_voqualizer.helpers.context_bridge import (
    ContextBridge,
    ContextBridgeInputError,
    ContextBridgeUnavailableError,
)


@dataclass
class FakeUserMessage:
    message: str = ""
    id: str = ""
    attachments: list[str] = field(default_factory=list)


class FakeLog:
    def __init__(self):
        self.items = []
    def log(self, **kwargs):
        self.items.append(kwargs)
        return kwargs


class FakeContext:
    counter = 0

    def __init__(self, config=None, id=None, name=None, data=None):
        FakeContext.counter += 1
        self.id = id or f"ctx-{FakeContext.counter}"
        self.config = config
        self.name = name
        self.data = data or {}
        self.calls = []
        self.log = FakeLog()

    def communicate(self, msg, broadcast_level=1):
        task = {"task": len(self.calls) + 1, "context_id": self.id}
        self.calls.append((msg, broadcast_level, task))
        return task


class FakeRuntime:
    def __init__(self):
        self.contexts = {}
        self.created = []

    def add(self, context):
        self.contexts[context.id] = context
        return context

    def get(self, context_id):
        return self.contexts.get(context_id)

    def create(self, **kwargs):
        context = FakeContext(**kwargs)
        self.created.append(context)
        self.add(context)
        return context


def make_bridge(runtime, config="fake-config"):
    return ContextBridge(
        context_getter=runtime.get,
        context_factory=runtime.create,
        user_message_factory=lambda **kw: FakeUserMessage(**kw),
        config_factory=lambda: config,
    )


def test_bind_session_reuses_existing_context_id():
    runtime = FakeRuntime()
    existing = runtime.add(FakeContext(id="chat-1", config="cfg"))
    bridge = make_bridge(runtime)

    binding = bridge.bind_session("sess-1", context_id="chat-1")

    assert binding.session_id == "sess-1"
    assert binding.context_id == "chat-1"
    assert binding.reused is True
    assert binding.created is False
    assert runtime.created == []
    assert bridge.get_binding("sess-1") is binding
    assert runtime.get(binding.context_id) is existing


def test_bind_session_creates_context_when_no_context_id():
    runtime = FakeRuntime()
    bridge = make_bridge(runtime, config={"model": "fake"})

    binding = bridge.bind_session("voice-session", metadata={"language": "en"})

    assert binding.created is True
    assert binding.reused is False
    assert binding.context_id.startswith("ctx-")
    assert len(runtime.created) == 1
    created = runtime.created[0]
    assert created.config == {"model": "fake"}
    assert created.name == "Voqualizer voice-se"
    assert created.data["voqualizer_session_id"] == "voice-session"
    assert created.data["language"] == "en"


def test_bind_session_returns_same_context_for_same_session_even_with_later_context_id():
    runtime = FakeRuntime()
    first = runtime.add(FakeContext(id="chat-a"))
    runtime.add(FakeContext(id="chat-b"))
    bridge = make_bridge(runtime)

    binding1 = bridge.bind_session("sess", context_id="chat-a")
    binding2 = bridge.bind_session("sess", context_id="chat-b")

    assert binding1 is binding2
    assert binding2.context_id == "chat-a"
    assert runtime.get(binding2.context_id) is first


def test_bind_session_rejects_unknown_context_id():
    runtime = FakeRuntime()
    bridge = make_bridge(runtime)

    with pytest.raises(ContextBridgeInputError) as excinfo:
        bridge.bind_session("sess", context_id="missing")

    err = excinfo.value.to_dict()
    assert err["code"] == "CONTEXT_BRIDGE_BAD_REQUEST"
    assert err["details"]["context_id"] == "missing"


def test_bind_session_can_require_existing_binding():
    runtime = FakeRuntime()
    bridge = make_bridge(runtime)

    with pytest.raises(ContextBridgeInputError):
        bridge.bind_session("sess", create=False)


def test_inject_transcript_uses_communicate_user_message_and_returns_task():
    runtime = FakeRuntime()
    context = runtime.add(FakeContext(id="chat-1"))
    bridge = make_bridge(runtime)

    result = bridge.inject_transcript(
        "sess-1",
        "hello from speech",
        context_id="chat-1",
        message_id="msg-123",
        broadcast_level=2,
    )

    assert result.session_id == "sess-1"
    assert result.context_id == "chat-1"
    assert result.message_id == "msg-123"
    assert result.task == {"task": 1, "context_id": "chat-1"}
    assert len(context.calls) == 1
    msg, broadcast_level, _task = context.calls[0]
    assert isinstance(msg, FakeUserMessage)
    assert msg.message == "hello from speech"
    assert msg.id == "msg-123"
    assert broadcast_level == 2


def test_inject_transcript_creates_binding_and_reuses_for_next_transcript():
    runtime = FakeRuntime()
    bridge = make_bridge(runtime)

    first = bridge.inject_transcript("sess", "first")
    second = bridge.inject_transcript("sess", "second")

    assert first.context_id == second.context_id
    assert len(runtime.created) == 1
    context = runtime.created[0]
    assert [call[0].message for call in context.calls] == ["first", "second"]


def test_inject_transcript_rejects_empty_text_and_session_id():
    runtime = FakeRuntime()
    bridge = make_bridge(runtime)

    with pytest.raises(ContextBridgeInputError):
        bridge.inject_transcript("", "hello")
    with pytest.raises(ContextBridgeInputError):
        bridge.inject_transcript("sess", "   ")


def test_inject_transcript_fails_json_safely_if_bound_context_disappears():
    runtime = FakeRuntime()
    runtime.add(FakeContext(id="chat-1"))
    bridge = make_bridge(runtime)
    bridge.bind_session("sess", context_id="chat-1")
    runtime.contexts.clear()

    with pytest.raises(ContextBridgeUnavailableError) as excinfo:
        bridge.inject_transcript("sess", "hello")

    err = excinfo.value.to_dict()
    assert err["code"] == "CONTEXT_BRIDGE_UNAVAILABLE"
    assert err["details"] == {"session_id": "sess", "context_id": "chat-1"}


def test_unbind_session_removes_binding():
    runtime = FakeRuntime()
    bridge = make_bridge(runtime)
    binding = bridge.bind_session("sess")

    assert bridge.unbind_session("sess") is binding
    assert bridge.get_binding("sess") is None


def test_inject_transcript_prefixes_visible_asr_prompt_when_submitted():
    runtime = FakeRuntime()
    context = runtime.add(FakeContext(id="chat-1"))
    bridge = make_bridge(runtime)

    result = bridge.inject_transcript(
        "sess-1",
        "What is the status?",
        context_id="chat-1",
        message_id="msg-asr",
        metadata={"source": "voqualizer_asr_final", "asr_provider": "Lemonade / Whisper-Large-v3-Turbo"},
    )

    msg, _broadcast_level, _task = context.calls[0]
    assert msg.message == "{ASR: Lemonade / Whisper-Large-v3-Turbo} What is the status?"
    assert result.text == msg.message


def test_inject_transcript_does_not_prefix_non_asr_metadata():
    runtime = FakeRuntime()
    context = runtime.add(FakeContext(id="chat-1"))
    bridge = make_bridge(runtime)

    bridge.inject_transcript("sess-1", "plain prompt", context_id="chat-1", metadata={"source": "manual"})

    msg, _broadcast_level, _task = context.calls[0]
    assert msg.message == "plain prompt"


def test_inject_transcript_logs_visible_asr_prompt_to_context_log():
    runtime = FakeRuntime()
    context = runtime.add(FakeContext(id="chat-visible"))
    bridge = make_bridge(runtime)

    bridge.inject_transcript(
        "sess-visible",
        "visible prompt",
        context_id="chat-visible",
        message_id="msg-visible",
        metadata={"source": "voqualizer_asr_final", "asr_provider": "Whisper Test"},
    )

    assert context.log.items == [{
        "type": "user",
        "heading": "",
        "content": "{ASR: Whisper Test} visible prompt",
        "kvps": {"source": "a0_voqualizer_asr", "asr_provider": "Whisper Test"},
        "id": "msg-visible",
    }]
