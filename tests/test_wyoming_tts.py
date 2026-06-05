from pathlib import Path
import asyncio
import importlib

PLUGIN = Path(__file__).resolve().parents[1]
TTS = PLUGIN / 'helpers' / 'wyoming_tts.py'


def make_session():
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    interface = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
    ])[0]
    return ws.WyomingInterfaceRuntime(interface).create_session()


def test_synthesize_emits_authoritative_wyoming_audio_stream():
    proto = importlib.import_module('helpers.wyoming_protocol')
    tts = importlib.import_module('helpers.wyoming_tts')
    session = make_session()

    async def provider(text, metadata):
        assert text == 'hello'
        assert metadata['ctxid'] == 'ctx-hero'
        return [b'audio1', b'audio2']

    adapter = tts.WyomingTtsAdapter(provider, sample_rate=24000)

    async def run():
        replies = await adapter.handle_event(session, proto.event('synthesize', text='hello', generation_id='gen-1'))
        assert [r.type for r in replies] == ['audio-start', 'audio-chunk', 'audio-chunk', 'audio-stop']
        assert replies[0].data['ctxid'] == 'ctx-hero'
        assert replies[1].payload == b'audio1'
        assert replies[1].data['chunk_seq'] == 0
        assert replies[2].payload == b'audio2'
        assert replies[2].data['chunk_seq'] == 1
        assert replies[-1].data['chunk_count'] == 2
        assert replies[-1].data['generation_id'] == 'gen-1'

    asyncio.run(run())


def test_response_final_can_trigger_tts_for_same_generation():
    proto = importlib.import_module('helpers.wyoming_protocol')
    tts = importlib.import_module('helpers.wyoming_tts')
    session = make_session()
    adapter = tts.WyomingTtsAdapter(lambda text, meta: [b'pcm'])

    async def run():
        replies = await adapter.handle_event(session, proto.event('voqualizer-response-final', text='answer', generation_id='gen-r'))
        assert replies[0].type == 'audio-start'
        assert replies[1].type == 'audio-chunk'
        assert replies[-1].type == 'audio-stop'
        assert replies[-1].data['generation_id'] == 'gen-r'
        assert session.active_generation_id == 'gen-r'

    asyncio.run(run())


def test_cancel_advances_generation_and_clears_chunk_state():
    proto = importlib.import_module('helpers.wyoming_protocol')
    tts = importlib.import_module('helpers.wyoming_tts')
    session = make_session()
    adapter = tts.WyomingTtsAdapter(lambda text, meta: [b'pcm'])

    async def run():
        await adapter.handle_event(session, proto.event('synthesize', text='old', generation_id='gen-old'))
        cancel = await adapter.handle_event(session, proto.event('cancel', reason='barge_in'))
        assert cancel[0].type == 'audio-stop'
        assert cancel[0].data['reason'] == 'barge_in'
        assert cancel[0].data['generation_id'] != 'gen-old'
        assert adapter.seen_chunks_by_session[session.session_id] == set()

    asyncio.run(run())


def test_wyoming_tts_source_is_authoritative_and_avoids_old_paths():
    source = TTS.read_text()
    for forbidden in ('voqualizer_tts_chunk', 'ack_fallback', 'voqualizer_user_text', 'direct final-response'):
        assert forbidden not in source
    assert 'synthesize' in source
    assert 'audio-start' in source
    assert 'audio-chunk' in source
    assert 'audio-stop' in source
    assert 'generation_id' in source
