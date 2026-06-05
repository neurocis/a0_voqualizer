from pathlib import Path
import asyncio
import importlib

PLUGIN = Path(__file__).resolve().parents[1]
ADAPTERS = PLUGIN / 'helpers' / 'wyoming_a0_adapters.py'


class FakeAsrProvider:
    async def transcribe(self, audio, request=None):
        assert audio == b'pcm'
        assert request['ctxid'] == 'ctx-hero'
        return {'text': 'hello voice'}


class FakeTtsProvider:
    async def synthesize_stream(self, text, request=None):
        assert text == 'answer'
        assert request['ctxid'] == 'ctx-hero'
        yield b'audio-a'
        yield b'audio-b'


def make_session():
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    interface = wi.load_interfaces([{'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701}])[0]
    return ws.WyomingInterfaceRuntime(interface).create_session()


def test_a0_asr_adapter_wraps_provider_factory():
    proto = importlib.import_module('helpers.wyoming_protocol')
    adapters = importlib.import_module('helpers.wyoming_a0_adapters')
    session = make_session()
    adapter = adapters.build_a0_asr_adapter(lambda: FakeAsrProvider())

    async def run():
        await adapter.handle_event(session, proto.event('audio-start', utterance_id='utt-1', rate=16000))
        await adapter.handle_event(session, proto.WyomingEvent('audio-chunk', {}, b'pcm'))
        replies = await adapter.handle_event(session, proto.event('audio-stop', utterance_id='utt-1'))
        assert replies[0].type == 'transcript'
        assert replies[0].data['text'] == 'hello voice'
        assert replies[0].data['ctxid'] == 'ctx-hero'

    asyncio.run(run())


def test_a0_prompt_adapter_uses_fixed_ctxid_submitter():
    proto = importlib.import_module('helpers.wyoming_protocol')
    adapters = importlib.import_module('helpers.wyoming_a0_adapters')
    session = make_session()
    adapter = adapters.build_a0_prompt_adapter(lambda text, meta: ['ctx=', meta['ctxid'], ' text=', text])

    async def run():
        replies = await adapter.handle_event(session, proto.event('voqualizer-text-prompt', text='hello', ctxid='bad'))
        assert replies[-1].type == 'voqualizer-response-final'
        assert replies[-1].data['text'] == 'ctx=ctx-hero text=hello'
        assert replies[-1].data['ctxid'] == 'ctx-hero'

    asyncio.run(run())


def test_a0_tts_adapter_wraps_provider_factory():
    proto = importlib.import_module('helpers.wyoming_protocol')
    adapters = importlib.import_module('helpers.wyoming_a0_adapters')
    session = make_session()
    adapter = adapters.build_a0_tts_adapter(lambda: FakeTtsProvider())

    async def run():
        replies = await adapter.handle_event(session, proto.event('synthesize', text='answer', generation_id='gen-1'))
        assert [r.type for r in replies] == ['audio-start', 'audio-chunk', 'audio-chunk', 'audio-stop']
        assert replies[1].payload == b'audio-a'
        assert replies[2].payload == b'audio-b'
        assert replies[-1].data['ctxid'] == 'ctx-hero'

    asyncio.run(run())


def test_adapter_status_reports_configured_factories():
    adapters = importlib.import_module('helpers.wyoming_a0_adapters')
    status = adapters.adapter_status(asr_provider_factory=lambda: object(), prompt_submitter=lambda t, m: t)
    assert status['mode'] == 'provider_scaffold'
    assert status['asr_provider_factory'] is True
    assert status['prompt_submitter'] is True
    assert status['tts_provider_factory'] is False


def test_a0_adapter_source_avoids_old_custom_websocket_protocol_but_keeps_reference_files_allowed():
    source = ADAPTERS.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text', 'ack_fallback'):
        assert forbidden not in source
    assert 'build_a0_asr_adapter' in source
    assert 'build_a0_prompt_adapter' in source
    assert 'build_a0_tts_adapter' in source
