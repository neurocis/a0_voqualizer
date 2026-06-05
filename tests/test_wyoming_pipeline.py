from pathlib import Path
import asyncio
import importlib

PLUGIN = Path(__file__).resolve().parents[1]
PIPELINE = PLUGIN / 'helpers' / 'wyoming_pipeline.py'


def make_session():
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    interface = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
    ])[0]
    return ws.WyomingInterfaceRuntime(interface).create_session()


def test_pipeline_text_prompt_returns_response_and_authoritative_tts_audio():
    proto = importlib.import_module('helpers.wyoming_protocol')
    prompt_mod = importlib.import_module('helpers.wyoming_prompt')
    tts_mod = importlib.import_module('helpers.wyoming_tts')
    pipe_mod = importlib.import_module('helpers.wyoming_pipeline')
    session = make_session()
    pipeline = pipe_mod.WyomingVoqualizerPipeline(
        prompt=prompt_mod.WyomingPromptAdapter(lambda text, meta: 'answer'),
        tts=tts_mod.WyomingTtsAdapter(lambda text, meta: [b'pcm1', b'pcm2']),
    )

    async def run():
        replies = await pipeline.handle_event(session, proto.event('voqualizer-text-prompt', text='hello', ctxid='bad'))
        types = [r.type for r in replies]
        assert types == [
            'voqualizer-response-start',
            'voqualizer-response-chunk',
            'voqualizer-response-final',
            'audio-start',
            'audio-chunk',
            'audio-chunk',
            'audio-stop',
        ]
        assert all(r.data.get('ctxid') in (None, 'ctx-hero') for r in replies)
        assert replies[-1].data['chunk_count'] == 2
        snap = pipeline.snapshot()
        assert snap['prompt_events'] == 1
        assert snap['last_ctxid'] == 'ctx-hero'

    asyncio.run(run())


def test_pipeline_asr_audio_stop_transcript_enters_prompt_path():
    proto = importlib.import_module('helpers.wyoming_protocol')
    asr_mod = importlib.import_module('helpers.wyoming_asr')
    prompt_mod = importlib.import_module('helpers.wyoming_prompt')
    tts_mod = importlib.import_module('helpers.wyoming_tts')
    pipe_mod = importlib.import_module('helpers.wyoming_pipeline')
    session = make_session()
    pipeline = pipe_mod.WyomingVoqualizerPipeline(
        asr=asr_mod.WyomingAsrAdapter(lambda audio, meta: 'voice prompt'),
        prompt=prompt_mod.WyomingPromptAdapter(lambda text, meta: 'voice answer'),
        tts=tts_mod.WyomingTtsAdapter(lambda text, meta: [b'pcm']),
    )

    async def run():
        await pipeline.handle_event(session, proto.event('audio-start', utterance_id='utt-1'))
        await pipeline.handle_event(session, proto.WyomingEvent('audio-chunk', {}, b'pcm'))
        replies = await pipeline.handle_event(session, proto.event('audio-stop', utterance_id='utt-1'))
        types = [r.type for r in replies]
        assert 'transcript' in types
        assert 'voqualizer-response-final' in types
        assert 'audio-chunk' in types
        assert replies[0].data['ctxid'] == 'ctx-hero'
        assert pipeline.snapshot()['asr_events'] == 3

    asyncio.run(run())


def test_pipeline_cancel_is_interface_scoped_and_advances_generation():
    proto = importlib.import_module('helpers.wyoming_protocol')
    pipe_mod = importlib.import_module('helpers.wyoming_pipeline')
    session = make_session()
    pipeline = pipe_mod.WyomingVoqualizerPipeline()

    async def run():
        old_generation = session.new_generation()
        replies = await pipeline.handle_event(session, proto.event('cancel', reason='barge_in'))
        assert [r.type for r in replies] == ['voqualizer-generation-cancelled', 'audio-stop']
        assert session.active_generation_id != old_generation
        assert all(r.data['ctxid'] == 'ctx-hero' for r in replies)

    asyncio.run(run())


def test_pipeline_source_avoids_old_voqualizer_socket_protocol():
    source = PIPELINE.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text', 'ack_fallback'):
        assert forbidden not in source
    assert 'WyomingVoqualizerPipeline' in source
    assert 'audio-start' in source
    assert 'audio-chunk' in source
    assert 'voqualizer-response-final' in source
