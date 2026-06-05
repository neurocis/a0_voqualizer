from pathlib import Path
import asyncio
import importlib

PLUGIN = Path(__file__).resolve().parents[1]
ASR = PLUGIN / 'helpers' / 'wyoming_asr.py'


def make_session():
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    interface = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
    ])[0]
    return ws.WyomingInterfaceRuntime(interface).create_session()


def test_asr_normalization_ignores_silence_and_throat_artifacts():
    asr = importlib.import_module('helpers.wyoming_asr')
    assert asr.should_ignore_asr_text('[BLANK_AUDIO]')
    assert asr.should_ignore_asr_text('[ Silence ]')
    assert asr.should_ignore_asr_text('[inaudible]')
    assert asr.should_ignore_asr_text('[Clears throat]')
    assert asr.should_ignore_asr_text('clearing throat')
    assert asr.should_ignore_asr_text('Thank you for watching.')
    assert not asr.should_ignore_asr_text('turn on the lights')


def test_audio_events_emit_final_transcript_bound_to_interface_ctxid():
    proto = importlib.import_module('helpers.wyoming_protocol')
    asr = importlib.import_module('helpers.wyoming_asr')
    session = make_session()

    async def provider(audio, metadata):
        assert audio == b'abc123'
        assert metadata['ctxid'] == 'ctx-hero'
        return 'hello hero'

    adapter = asr.WyomingAsrAdapter(provider)

    async def run():
        await adapter.handle_event(session, proto.event('audio-start', rate=16000, width=2, channels=1, utterance_id='utt-1'))
        await adapter.handle_event(session, proto.WyomingEvent('audio-chunk', {}, b'abc'))
        await adapter.handle_event(session, proto.WyomingEvent('audio-chunk', {}, b'123'))
        replies = await adapter.handle_event(session, proto.event('audio-stop', utterance_id='utt-1'))
        assert len(replies) == 1
        assert replies[0].type == 'transcript'
        assert replies[0].data['text'] == 'hello hero'
        assert replies[0].data['final'] is True
        assert replies[0].data['ctxid'] == 'ctx-hero'
        assert replies[0].data['interface_id'] == 'hero'
        assert replies[0].data['utterance_id'] == 'utt-1'

    asyncio.run(run())


def test_asr_ignored_and_duplicate_finals_are_reported_not_submitted():
    proto = importlib.import_module('helpers.wyoming_protocol')
    asr = importlib.import_module('helpers.wyoming_asr')
    session = make_session()
    calls = iter(['[ Silence ]', 'hello again', 'hello again'])

    async def provider(audio, metadata):
        return next(calls)

    adapter = asr.WyomingAsrAdapter(provider)

    async def utterance(utterance_id):
        await adapter.handle_event(session, proto.event('audio-start', utterance_id=utterance_id))
        await adapter.handle_event(session, proto.WyomingEvent('audio-chunk', {}, b'pcm'))
        return await adapter.handle_event(session, proto.event('audio-stop', utterance_id=utterance_id))

    async def run():
        ignored = await utterance('same')
        first = await utterance('same')
        duplicate = await utterance('same')
        assert ignored[0].type == 'voqualizer-asr-ignored'
        assert ignored[0].data['reason'] == 'false_positive_silence_or_filler'
        assert first[0].type == 'transcript'
        assert duplicate[0].type == 'voqualizer-asr-duplicate'
        assert duplicate[0].data['ctxid'] == 'ctx-hero'

    asyncio.run(run())


def test_wyoming_asr_source_avoids_legacy_socket_events():
    source = ASR.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text'):
        assert forbidden not in source
    assert 'audio-start' in source
    assert 'audio-chunk' in source
    assert 'audio-stop' in source
    assert 'transcript' in source
