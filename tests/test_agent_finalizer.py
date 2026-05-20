from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

# Import process_chain_end extension through /a0 with tiny framework stubs.
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

from usr.plugins.a0_voqualizer.extensions.python.process_chain_end._50_voqualizer import (  # noqa: E402
    VoqualizerProcessChainEnd,
)
from usr.plugins.a0_voqualizer.helpers.agent_finalizer import finalize_agent_response_for_context, _extract_streaming_text_section, _looks_like_structured_response_stream, _tts_speakable_text  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.context_bridge import ContextBridge  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.tts import AudioChunk as TTSAudioChunk  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.tts import TTSProvider, TTSRequest, TTSError  # noqa: E402
import usr.plugins.a0_voqualizer.helpers.context_bridge as context_bridge_mod  # noqa: E402
import usr.plugins.a0_voqualizer.helpers.agent_finalizer as finalizer_mod  # noqa: E402

sys.path[:] = _ORIG_SYS_PATH
for _name in ["helpers", "helpers.extension", "helpers.print_style", "agent"]:
    sys.modules.pop(_name, None)


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


def cfg():
    return {
        "tts": {
            "default": "fake-tts",
            "providers": [{"name": "fake-tts", "type": "mock", "voice": "fake", "sample_rate": 16000}],
        },
        "protocol": {"default_output_codec": "pcm16/16k"},
    }


class FakeTTSProvider(TTSProvider):
    def __init__(self, spec=None):
        super().__init__(spec or {"name": "fake-tts", "type": "mock"})
        self.requests = []
    @property
    def capabilities(self):
        raise NotImplementedError
    async def stream(self, request: TTSRequest):
        self.requests.append(request)
        yield TTSAudioChunk(data=b"one", seq=0, utterance_id=request.utterance_id, codec=request.codec, sample_rate=request.sample_rate)
        yield TTSAudioChunk(data=b"two", seq=1, utterance_id=request.utterance_id, codec=request.codec, sample_rate=request.sample_rate, is_final=True)


class ErrorTTSProvider(TTSProvider):
    @property
    def capabilities(self):
        raise NotImplementedError
    async def stream(self, request: TTSRequest):
        raise TTSError("boom", code="TTS_BOOM", details={"x": 1})
        yield  # pragma: no cover


async def install_session(*, session_id="sess-1", context_id="ctx-1", sender=True):
    runtime = FakeRuntime()
    bridge = make_bridge(runtime)
    bridge.bind_session(session_id, context_id=context_id)
    context_bridge_mod._default_bridge = bridge
    BridgeRegistry.reset_instance()
    registry = BridgeRegistry.configure(
        max_concurrent_sessions=8,
        session_resume_window_seconds=30,
        max_session_seconds=300,
        audio_queue_max_frames=4,
    )
    session, _resumed = await registry.create_or_resume(
        session_id,
        context_id=context_id,
        tts_provider="fake-tts",
        output_codec="pcm16/16k",
    )
    emitted = []
    if sender:
        async def emit(event, payload):
            emitted.append((event, payload))
        session.sender = emit
    return bridge, session, emitted


def test_finalize_emits_final_response_and_tts_chunks_done():
    async def scenario():
        _bridge, _session, emitted = await install_session()
        provider = FakeTTSProvider({"name": "fake-tts", "type": "mock"})

        result = await finalize_agent_response_for_context(
            context_id="ctx-1",
            text="Assistant final answer.",
            config_loader=cfg,
            tts_provider_factory=lambda spec: provider,
            utterance_id_factory=lambda: "utt-final",
        )

        assert result["emitted"] == 1
        assert result["tts"] == [{"session_id": "sess-1", "status": "ok", "chunks": 2, "utterance_id": "utt-final"}]
        assert [event for event, _payload in emitted] == [
            "voqualizer_agent_response_final",
            "voqualizer_tts_chunk",
            "voqualizer_tts_chunk",
            "voqualizer_tts_done",
        ]
        final = emitted[0][1]
        assert final == {
            "session_id": "sess-1",
            "context_id": "ctx-1",
            "text": "Assistant final answer.",
            "speech_text": "Assistant final answer.",
            "utterance_id": "utt-final",
        }
        assert emitted[1][1]["audio"] == b"one"
        assert emitted[1][1]["metadata"]["source"] == "voqualizer_agent_response_final"
        assert emitted[-1][1]["cancelled"] is False
        assert provider.requests[0].text == "Assistant final answer."

    run(scenario())


def test_finalize_skips_unbound_context():
    async def scenario():
        _bridge, _session, emitted = await install_session()
        result = await finalize_agent_response_for_context(
            context_id="ctx-2",
            text="nobody hears this",
            config_loader=cfg,
            tts_provider_factory=lambda spec: FakeTTSProvider(spec),
        )
        assert result["emitted"] == 0
        assert result["reason"] == "no_bindings"
        assert emitted == []

    run(scenario())


def test_finalize_emits_json_safe_tts_error():
    async def scenario():
        _bridge, _session, emitted = await install_session()
        result = await finalize_agent_response_for_context(
            context_id="ctx-1",
            text="boom please",
            config_loader=cfg,
            tts_provider_factory=lambda spec: ErrorTTSProvider({"name": "fake-tts"}),
            utterance_id_factory=lambda: "utt-error",
        )
        assert result["emitted"] == 1
        assert result["tts"][0]["status"] == "error"
        assert result["tts"][0]["error"]["code"] == "TTS_BOOM"
        assert [event for event, _payload in emitted] == ["voqualizer_agent_response_final", "voqualizer_error"]
        assert emitted[-1][1]["code"] == "TTS_BOOM"
        assert emitted[-1][1]["session_id"] == "sess-1"

    run(scenario())


def test_finalize_obeys_existing_barge_in_cancel_flag():
    async def scenario():
        _bridge, session, emitted = await install_session()
        provider = FakeTTSProvider({"name": "fake-tts"})
        async def cancelling_factory(spec):
            return provider
        # Cancel after final response but before first emitted chunk by wrapping sender.
        original_sender = session.sender
        async def sender(event, payload):
            await original_sender(event, payload)
            if event == "voqualizer_agent_response_final":
                session.cancel_in_flight_tts()
        session.sender = sender

        result = await finalize_agent_response_for_context(
            context_id="ctx-1",
            text="cancel me",
            config_loader=cfg,
            tts_provider_factory=lambda spec: provider,
            utterance_id_factory=lambda: "utt-cancel",
        )

        assert result["tts"][0]["status"] == "cancelled"
        assert [event for event, _payload in emitted] == ["voqualizer_agent_response_final", "voqualizer_tts_done"]
        assert emitted[-1][1]["cancelled"] is True
        assert emitted[-1][1]["reason"] == "barge_in"

    run(scenario())


def test_tts_speakable_text_extracts_json_tool_text_and_normalizes_markdown():
    raw = '{"thoughts":["hidden"],"headline":"h","tool_name":"response","tool_args":{"text":"## Answer\n\n- **Hello** [world](https://example.test)\n- `code` sample\n\n```python\nprint(1)\n```"}}'

    spoken = _tts_speakable_text(raw)

    assert "thoughts" not in spoken
    assert "tool_args" not in spoken
    assert "##" not in spoken
    assert "**" not in spoken
    assert "https://" not in spoken
    assert "print(1)" not in spoken
    assert "Hello world" in spoken
    assert "code sample" in spoken


def test_finalize_sends_original_final_text_but_tts_uses_speech_text():
    async def scenario():
        _bridge, _session, emitted = await install_session()
        provider = FakeTTSProvider({"name": "fake-tts", "type": "mock"})
        raw = '{"tool_args":{"text":"# Spoken title\n\n- say **this** only"},"thoughts":["silent"]}'

        await finalize_agent_response_for_context(
            context_id="ctx-1",
            text=raw,
            config_loader=cfg,
            tts_provider_factory=lambda spec: provider,
            utterance_id_factory=lambda: "utt-speech",
        )

        assert emitted[0][1]["text"] == raw
        assert emitted[0][1]["speech_text"] == "Spoken title\nsay this only"
        assert provider.requests[0].text == "Spoken title\nsay this only"

    run(scenario())


def test_process_chain_end_extension_uses_loop_data_final_response(monkeypatch):
    async def scenario():
        calls = []
        async def fake_finalize(**kwargs):
            calls.append(kwargs)
        monkeypatch.setattr(finalizer_mod, "finalize_agent_response_for_context", fake_finalize)
        # The extension imports the helper inside execute from the module, so this monkeypatch is enough.
        agent = SimpleNamespace(context=SimpleNamespace(id="ctx-1"), loop_data=SimpleNamespace(last_response="fallback"))
        ext = VoqualizerProcessChainEnd(agent=agent)
        await ext.execute(loop_data=SimpleNamespace(last_response="final text"))
        assert calls == [{"context_id": "ctx-1", "text": "final text"}]

    run(scenario())


def test_process_chain_end_extension_ignores_empty_response(monkeypatch):
    async def scenario():
        calls = []
        async def fake_finalize(**kwargs):
            calls.append(kwargs)
        monkeypatch.setattr(finalizer_mod, "finalize_agent_response_for_context", fake_finalize)
        agent = SimpleNamespace(context=SimpleNamespace(id="ctx-1"), loop_data=SimpleNamespace(last_response=""))
        ext = VoqualizerProcessChainEnd(agent=agent)
        await ext.execute(loop_data=SimpleNamespace(last_response=""))
        assert calls == []

    run(scenario())


def test_agent_finalizer_uses_provider_tts_speed_source_marker():
    from pathlib import Path
    source = Path('/a0/usr/plugins/a0_voqualizer/helpers/agent_finalizer.py').read_text()
    assert 'speed = float(spec.get("speed")' in source
    assert 'speed=speed' in source


def test_agent_finalizer_uses_provider_pcm_and_base64_markers():
    source = Path('/a0/usr/plugins/a0_voqualizer/helpers/agent_finalizer.py').read_text()
    assert '_codec_for_tts_spec' in source
    assert 'response_format' in source
    assert 'audio_b64' in source
    assert 'base64.b64encode' in source
    assert 'pcm16/24k' in source


def test_tts_speakable_text_extracts_python_literal_tool_text():
    raw = "{'thoughts': ['hidden'], 'tool_name': 'response', 'tool_args': {'text': '## Real answer\n\n- **Speak** this'}}"

    spoken = _tts_speakable_text(raw)

    assert spoken == "Real answer\nSpeak this"
    assert "thoughts" not in spoken
    assert "tool_args" not in spoken


def test_structured_response_stream_detection_defers_partial_json():
    partial = '{"thoughts":["hidden"],"tool_name":"response","tool_args":{"text":"## Answer'

    assert _looks_like_structured_response_stream(partial) is True
    assert _looks_like_structured_response_stream("Plain **markdown** answer.") is False


def test_extract_streaming_text_section_from_partial_json():
    partial = '{"thoughts":["hidden"],"tool_args":{"text":"## Answer\n\n- **Hello'
    assert _extract_streaming_text_section(partial) == "## Answer\n\n- **Hello"


def test_agent_finalizer_tracks_tts_chunks_for_barge_in_source_marker():
    from pathlib import Path
    src = Path('/a0/usr/plugins/a0_voqualizer/helpers/agent_finalizer.py').read_text()
    assert 'tts_chunks_emitted' in src
    assert 'tts_barge_in_notified' in src


def test_tts_finalizer_has_route_diagnostics_and_fallback_markers():
    src = Path('/a0/usr/plugins/a0_voqualizer/helpers/agent_finalizer.py').read_text()
    assert '_record_tts_route' in src
    assert '_sessions_for_context_with_fallback' in src
    assert '_all_context_candidate_ids' in src
    assert 'tts_route_' in src
    assert 'provider_error_type' in src
    assert 'provider_error_repr' in src
    assert 'chunks_emitted' in src
    assert 'route_context_id' in src
    assert 'sessions_considered' in src
    assert 'registry.iter_active()' in src
