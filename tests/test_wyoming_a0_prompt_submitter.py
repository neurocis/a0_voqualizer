"""Tests for W24 Agent Zero prompt submitter bridge."""
import asyncio
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBMITTER = ROOT / 'helpers' / 'wyoming_a0_prompt_submitter.py'
LIVE = ROOT / 'helpers' / 'wyoming_live_providers.py'


def test_prompt_submitter_source_avoids_retired_websocket_protocol():
    src = SUBMITTER.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_user_text', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'ack_fallback'):
        assert forbidden not in src, f'found forbidden: {forbidden}'
    for required in ('submit_to_agent_context', 'build_agent_context_submitter', 'safe_echo_submitter', 'AgentContext', 'UserMessage'):
        assert required in src, f'missing required: {required}'


def test_safe_echo_submitter_preserves_ctxid():
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')
    result = asyncio.run(mod.safe_echo_submitter('hello', {'ctxid': 'ctx-hero'}))
    assert 'ctx-hero' in result
    assert 'hello' in result


def test_builder_falls_back_to_echo_when_framework_unavailable():
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')
    submitter = mod.build_agent_context_submitter(allow_echo_fallback=True)
    result = asyncio.run(submitter('hello', {'ctxid': 'ctx-hero'}))
    assert 'ctx-hero' in result
    assert 'hello' in result


def test_live_providers_default_to_agent_context_submitter():
    src = LIVE.read_text()
    assert 'build_agent_context_submitter' in src
    assert 'agent_context_with_echo_fallback' in src


def test_streaming_submitter_uses_agent_stream_method(monkeypatch):
    import sys
    import types
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')

    class UserMessage:
        def __init__(self, content=None):
            self.content = content

    class Context:
        async def stream(self, message, metadata=None):
            assert metadata['ctxid'] == 'ctx-stream'
            async def gen():
                yield {'delta': 'one '}
                yield {'delta': 'two'}
            return gen()

    class AgentContext:
        @staticmethod
        def get(ctxid):
            assert ctxid == 'ctx-stream'
            return Context()

    fake = types.ModuleType('agent')
    fake.AgentContext = AgentContext
    fake.UserMessage = UserMessage
    monkeypatch.setitem(sys.modules, 'agent', fake)

    async def run():
        chunks = []
        async for chunk in mod.stream_to_agent_context('hello', {'ctxid': 'ctx-stream'}):
            chunks.append(chunk)
        return chunks

    assert asyncio.run(run()) == ['one ', 'two']


def test_builder_stream_mode_returns_async_iterable(monkeypatch):
    import sys
    import types
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')

    class UserMessage:
        def __init__(self, content=None):
            self.content = content

    class Context:
        def ask_stream(self, text):
            return ['a', 'b']

    class AgentContext:
        @staticmethod
        def get(ctxid):
            return Context()

    fake = types.ModuleType('agent')
    fake.AgentContext = AgentContext
    fake.UserMessage = UserMessage
    monkeypatch.setitem(sys.modules, 'agent', fake)

    async def run():
        submitter = mod.build_agent_context_submitter(stream=True)
        result = await submitter('hello', {'ctxid': 'ctx-stream'})
        chunks = []
        async for chunk in result:
            chunks.append(chunk)
        return chunks

    assert asyncio.run(run()) == ['a', 'b']


def test_live_provider_status_reports_streaming_submitter():
    src = LIVE.read_text()
    assert 'stream=True' in src
    assert 'agent_context_streaming_with_echo_fallback' in src
