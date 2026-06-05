from pathlib import Path
import asyncio
import importlib

PLUGIN = Path(__file__).resolve().parents[1]
PROMPT = PLUGIN / 'helpers' / 'wyoming_prompt.py'


def make_session():
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    interface = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
    ])[0]
    return ws.WyomingInterfaceRuntime(interface).create_session()


def test_text_prompt_uses_interface_ctxid_and_streams_response_events():
    proto = importlib.import_module('helpers.wyoming_protocol')
    prompt = importlib.import_module('helpers.wyoming_prompt')
    session = make_session()

    async def provider(text, metadata):
        assert text == 'hello'
        assert metadata['ctxid'] == 'ctx-hero'
        assert metadata['interface_id'] == 'hero'
        return ['hi ', 'there']

    adapter = prompt.WyomingPromptAdapter(provider)

    async def run():
        replies = await adapter.handle_event(session, proto.event('voqualizer-text-prompt', text='hello', ctxid='malicious'))
        assert [r.type for r in replies] == [
            'voqualizer-response-start',
            'voqualizer-response-chunk',
            'voqualizer-response-chunk',
            'voqualizer-response-final',
        ]
        assert replies[0].data['ctxid'] == 'ctx-hero'
        assert replies[-1].data['text'] == 'hi there'
        assert replies[-1].data['generation_id'] == session.active_generation_id
        assert replies[-1].data['chunk_count'] == 2

    asyncio.run(run())


def test_transcript_event_can_submit_prompt_for_fixed_interface():
    proto = importlib.import_module('helpers.wyoming_protocol')
    prompt = importlib.import_module('helpers.wyoming_prompt')
    session = make_session()
    adapter = prompt.WyomingPromptAdapter(lambda text, meta: 'answered ' + text)

    async def run():
        replies = await adapter.handle_event(session, proto.event('transcript', text='voice prompt', final=True))
        assert replies[0].type == 'voqualizer-response-start'
        assert replies[-1].type == 'voqualizer-response-final'
        assert replies[-1].data['text'] == 'answered voice prompt'
        assert replies[-1].data['source'] == 'transcript'
        assert replies[-1].data['ctxid'] == 'ctx-hero'

    asyncio.run(run())


def test_response_tool_json_is_collapsed_before_final_event():
    proto = importlib.import_module('helpers.wyoming_protocol')
    prompt = importlib.import_module('helpers.wyoming_prompt')
    session = make_session()
    envelope = '{"headline":"Done","tool_name":"response","tool_args":{"text":"Clean body"}}'
    adapter = prompt.WyomingPromptAdapter(lambda text, meta: envelope)

    async def run():
        replies = await adapter.handle_event(session, proto.event('voqualizer-text-prompt', text='x'))
        final = replies[-1]
        assert final.type == 'voqualizer-response-final'
        assert final.data['headline'] == 'Done'
        assert final.data['text'] == 'Clean body'
        assert final.data['display_kind'] == 'response_tool'

    asyncio.run(run())


def test_cancel_advances_generation_and_marks_previous_cancelled():
    proto = importlib.import_module('helpers.wyoming_protocol')
    prompt = importlib.import_module('helpers.wyoming_prompt')
    session = make_session()
    adapter = prompt.WyomingPromptAdapter(lambda text, meta: 'ok')

    async def run():
        first = await adapter.handle_event(session, proto.event('voqualizer-text-prompt', text='x'))
        old_generation = first[0].data['generation_id']
        cancel = await adapter.handle_event(session, proto.event('cancel', reason='barge_in'))
        assert cancel[0].type == 'voqualizer-generation-cancelled'
        assert cancel[0].data['reason'] == 'barge_in'
        assert cancel[0].data['generation_id'] != old_generation
        assert session.active_generation_id == cancel[0].data['generation_id']

    asyncio.run(run())


def test_wyoming_prompt_source_avoids_legacy_socket_events():
    source = PROMPT.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text'):
        assert forbidden not in source
    assert 'voqualizer-text-prompt' in source
    assert 'transcript' in source
    assert 'voqualizer-response-final' in source
    assert 'collapse_response_tool_json' in source
