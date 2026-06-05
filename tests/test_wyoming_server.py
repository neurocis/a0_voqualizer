from pathlib import Path
import asyncio
import importlib.util

PLUGIN = Path(__file__).resolve().parents[1]
INTERFACES = PLUGIN / 'helpers' / 'wyoming_interfaces.py'
SERVER = PLUGIN / 'helpers' / 'wyoming_server.py'
PROTO = PLUGIN / 'helpers' / 'wyoming_protocol.py'


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_interface_manager_lists_multiple_ctx_bound_interfaces():
    wi = load_module(INTERFACES, 'wyoming_interfaces_for_server_test')
    ws = load_module(SERVER, 'wyoming_server_for_manager_test')
    interfaces = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
        {'id': 'lab', 'name': 'Lab', 'ctxid': 'ctx-lab', 'bind_port': 10702},
    ])
    manager = ws.WyomingInterfaceManager(interfaces)
    infos = manager.list_info()
    assert [item['id'] for item in infos] == ['hero', 'lab']
    assert infos[0]['ctxid'] == 'ctx-hero'
    assert infos[1]['ctxid'] == 'ctx-lab'
    assert infos[0]['bind_port'] != infos[1]['bind_port']


def test_describe_returns_info_for_fixed_interface_ctxid():
    wi = load_module(INTERFACES, 'wyoming_interfaces_for_describe_test')
    ws = load_module(SERVER, 'wyoming_server_for_describe_test')
    proto = load_module(PROTO, 'wyoming_protocol_for_describe_test')
    interface = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
    ])[0]
    runtime = ws.WyomingInterfaceRuntime(interface)
    session = runtime.create_session()
    replies = asyncio.run(runtime.handle_event(session, proto.event('describe')))
    assert len(replies) == 1
    assert replies[0].type == 'info'
    assert replies[0].data['voqualizer']['interface_id'] == 'hero'
    assert replies[0].data['voqualizer']['ctxid'] == 'ctx-hero'
    assert replies[0].data['voqualizer']['session_id'] == session.session_id


def test_text_prompt_scaffold_uses_interface_ctxid_not_client_context():
    wi = load_module(INTERFACES, 'wyoming_interfaces_for_prompt_test')
    ws = load_module(SERVER, 'wyoming_server_for_prompt_test')
    proto = load_module(PROTO, 'wyoming_protocol_for_prompt_test')
    interface = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
    ])[0]
    runtime = ws.WyomingInterfaceRuntime(interface)
    runtime.on('voqualizer-text-prompt', ws.echo_text_prompt_handler)
    session = runtime.create_session()
    replies = asyncio.run(runtime.handle_event(session, proto.event('voqualizer-text-prompt', text='hi', ctxid='malicious')))
    assert replies[0].type == 'voqualizer-response-start'
    assert replies[0].data['ctxid'] == 'ctx-hero'
    assert replies[1].data['ctxid'] == 'ctx-hero'
    assert replies[1].data['text'] == 'hi'
    assert session.active_generation_id == replies[1].data['generation_id']


def test_wyoming_server_source_has_no_legacy_voqualizer_socket_events():
    source = SERVER.read_text()
    forbidden = [
        'voqualizer_init',
        'voqualizer_audio_chunk',
        'voqualizer_tts_chunk',
        'voqualizer_user_text',
    ]
    for marker in forbidden:
        assert marker not in source
    assert 'WyomingInterfaceManager' in source
    assert 'interface.ctxid' in source
