from pathlib import Path
import asyncio
import importlib

PLUGIN = Path(__file__).resolve().parents[1]
SERVER = PLUGIN / 'helpers' / 'wyoming_server.py'


def make_interface():
    wi = importlib.import_module('helpers.wyoming_interfaces')
    return wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
    ])[0]


def test_runtime_pipeline_hook_handles_describe_and_prompt_events():
    proto = importlib.import_module('helpers.wyoming_protocol')
    ws = importlib.import_module('helpers.wyoming_server')
    runtime = ws.build_wyoming_pipeline_runtime(make_interface())
    session = runtime.create_session()

    async def run():
        info = await runtime.handle_event(session, proto.event('describe'))
        assert info[0].type == 'info'
        assert info[0].data['ctxid'] == 'ctx-hero'
        replies = await runtime.handle_event(session, proto.event('voqualizer-text-prompt', text='hello', ctxid='bad'))
        types = [r.type for r in replies]
        assert 'voqualizer-response-final' in types
        assert 'audio-start' in types
        assert 'audio-stop' in types
        assert all(r.data.get('ctxid') in (None, 'ctx-hero') for r in replies)

    asyncio.run(run())


def test_pipeline_manager_builds_one_runtime_per_interface_with_fixed_ctxid():
    proto = importlib.import_module('helpers.wyoming_protocol')
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    interfaces = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
        {'id': 'sidekick', 'name': 'Sidekick', 'ctxid': 'ctx-sidekick', 'bind_port': 10702},
    ])
    manager = ws.build_wyoming_pipeline_manager(interfaces)

    async def run():
        hero = manager.get_runtime('hero')
        sidekick = manager.get_runtime('sidekick')
        assert hero.interface.ctxid == 'ctx-hero'
        assert sidekick.interface.ctxid == 'ctx-sidekick'
        hero_session = hero.create_session()
        side_session = sidekick.create_session()
        hero_info = await hero.handle_event(hero_session, proto.event('describe'))
        side_info = await sidekick.handle_event(side_session, proto.event('describe'))
        assert hero_info[0].data['ctxid'] == 'ctx-hero'
        assert side_info[0].data['ctxid'] == 'ctx-sidekick'

    asyncio.run(run())


def test_pipeline_runtime_source_avoids_old_custom_websocket_events():
    source = SERVER.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text', 'ack_fallback'):
        assert forbidden not in source
    assert 'PipelineHandler' in source
    assert 'set_pipeline' in source
    assert 'build_wyoming_pipeline_runtime' in source
    assert 'build_wyoming_pipeline_manager' in source
