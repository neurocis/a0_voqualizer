from __future__ import annotations

import asyncio
from types import SimpleNamespace

from usr.plugins.a0_voqualizer.helpers.context_bridge import ContextBridge
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry
from usr.plugins.a0_voqualizer.helpers.sentence_chunker import SentenceChunkerConfig, SentenceTTSChunker
from usr.plugins.a0_voqualizer.helpers.tts import AudioChunk as TTSAudioChunk
from usr.plugins.a0_voqualizer.helpers.tts import TTSProvider, TTSRequest
import usr.plugins.a0_voqualizer.helpers.context_bridge as context_bridge_mod
import usr.plugins.a0_voqualizer.helpers.sentence_chunker as sentence_chunker_mod
import usr.plugins.a0_voqualizer.helpers.agent_finalizer as finalizer_mod


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
        return FakeContext("created")


class FakeTTSProvider(TTSProvider):
    def __init__(self, spec=None):
        super().__init__(spec or {"name": "fake-tts", "type": "mock"})
        self.requests = []
    @property
    def capabilities(self):
        raise NotImplementedError
    async def stream(self, request: TTSRequest):
        self.requests.append(request)
        yield TTSAudioChunk(
            data=request.text.encode(),
            seq=0,
            utterance_id=request.utterance_id,
            codec=request.codec,
            sample_rate=request.sample_rate,
            is_final=True,
        )


def cfg():
    return {
        "tts": {"default": "fake-tts", "providers": [{"name": "fake-tts", "type": "mock", "sample_rate": 16000}]},
        "protocol": {"default_output_codec": "pcm16/16k"},
    }


async def install(*, now=0.0):
    runtime = FakeRuntime()
    bridge = ContextBridge(
        context_getter=runtime.get,
        context_factory=runtime.create,
        user_message_factory=lambda **kw: SimpleNamespace(**kw),
        config_factory=lambda: "cfg",
    )
    bridge.bind_session("sess-1", context_id="ctx-1")
    context_bridge_mod._default_bridge = bridge
    BridgeRegistry.reset_instance()
    registry = BridgeRegistry.configure(
        max_concurrent_sessions=8,
        session_resume_window_seconds=30,
        max_session_seconds=300,
        audio_queue_max_frames=4,
    )
    session, _ = await registry.create_or_resume(
        "sess-1",
        context_id="ctx-1",
        tts_provider="fake-tts",
        output_codec="pcm16/16k",
    )
    emitted = []
    async def sender(event, payload):
        emitted.append((event, payload))
    session.sender = sender
    provider = FakeTTSProvider({"name": "fake-tts", "type": "mock"})
    session.metadata["tts_provider_instance"] = provider
    clock = {"now": now}
    chunker = SentenceTTSChunker(
        config=SentenceChunkerConfig(first_audio_latency_ms=750, min_latency_flush_chars=10, max_buffer_chars=80),
        clock=lambda: clock["now"],
        utterance_id_factory=lambda: f"utt-{len(provider.requests)+1}",
        config_loader=cfg,
        tts_provider_factory=lambda spec: provider,
    )
    sentence_chunker_mod._default_sentence_tts_chunker = chunker
    return session, emitted, provider, clock, chunker


def test_sentence_boundary_triggers_tts_chunk():
    async def scenario():
        _session, emitted, provider, _clock, chunker = await install()
        result = await chunker.process_context_delta(context_id="ctx-1", text="Hello world. ")
        assert result["sessions"] == 1
        assert result["results"][0]["status"] == "ok"
        assert provider.requests[0].text == "Hello world."
        assert [event for event, _payload in emitted] == ["voqualizer_tts_chunk", "voqualizer_tts_done"]
        assert emitted[0][1]["metadata"]["source"] == "voqualizer_agent_sentence"

    run(scenario())


def test_partial_buffer_waits_before_latency_budget():
    async def scenario():
        _session, emitted, provider, clock, chunker = await install()
        result = await chunker.process_context_delta(context_id="ctx-1", text="This is a partial phrase")
        assert result["results"][0]["status"] == "buffered"
        assert emitted == []
        assert provider.requests == []
        clock["now"] = 0.5
        result = await chunker.process_context_delta(context_id="ctx-1", text=" continuing")
        assert result["results"][0]["status"] == "buffered"
        assert emitted == []

    run(scenario())


def test_latency_budget_flushes_first_audio_under_one_second():
    async def scenario():
        _session, emitted, provider, clock, chunker = await install()
        await chunker.process_context_delta(context_id="ctx-1", text="This is a long phrase")
        clock["now"] = 0.751
        result = await chunker.process_context_delta(context_id="ctx-1", text=" still no period")
        assert result["results"][0]["status"] == "ok"
        assert provider.requests[0].text == "This is a long phrase still no period"
        assert emitted[0][0] == "voqualizer_tts_chunk"

    run(scenario())


def test_finalizer_flushes_remaining_buffer_without_duplicate_full_tts():
    async def scenario():
        _session, emitted, provider, _clock, chunker = await install()
        await chunker.process_context_delta(context_id="ctx-1", text="First sentence. ")
        await chunker.process_context_delta(context_id="ctx-1", text="Trailing fragment")
        result = await finalizer_mod.finalize_agent_response_for_context(
            context_id="ctx-1",
            text="First sentence. Trailing fragment",
            config_loader=cfg,
            tts_provider_factory=lambda spec: provider,
            utterance_id_factory=lambda: "final-full",
        )
        assert result["emitted"] == 1
        assert [request.text for request in provider.requests] == ["First sentence.", "Trailing fragment"]
        assert [event for event, _payload in emitted] == [
            "voqualizer_tts_chunk",
            "voqualizer_tts_done",
            "voqualizer_agent_response_final",
            "voqualizer_tts_chunk",
            "voqualizer_tts_done",
        ]
        assert emitted[2][1]["text"] == "First sentence. Trailing fragment"

    run(scenario())


def test_sentence_chunker_respects_barge_in_cancellation():
    async def scenario():
        session, emitted, provider, _clock, chunker = await install()
        original_sender = session.sender
        async def sender(event, payload):
            await original_sender(event, payload)
            if event == "voqualizer_tts_chunk":
                session.cancel_in_flight_tts()
        session.sender = sender
        result = await chunker.process_context_delta(context_id="ctx-1", text="Cancel after audio. ")
        assert result["results"][0]["status"] == "cancelled"
        assert [event for event, _payload in emitted] == ["voqualizer_tts_chunk", "voqualizer_tts_done"]
        assert emitted[-1][1]["cancelled"] is True
        assert emitted[-1][1]["reason"] == "barge_in"

    run(scenario())
