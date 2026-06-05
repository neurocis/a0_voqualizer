"""Lightweight test of WsWyoming W16 handler shape without the WS runtime."""
from pathlib import Path
import importlib
import asyncio
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / 'api' / 'ws_wyoming.py'
BRIDGE = ROOT / 'helpers' / 'wyoming_ws_bridge.py'


def test_ws_wyoming_handler_source_uses_wyoming_protocol_only():
    src = API.read_text()
    for forbidden in (
        'voqualizer_init',
        'voqualizer_user_text',
        'voqualizer_audio_chunk',
        'voqualizer_tts_chunk',
        'ack_fallback',
    ):
        assert forbidden not in src
    for required in (
        'wyoming_init',
        'wyoming_event',
        'wyoming_payload',
        'wyoming_close',
        'WyomingWsBridge',
        'class WsWyoming(WsHandler)',
        'requires_auth',
        'handle_text_envelope',
    ):
        assert required in src, required


def test_ws_wyoming_handler_module_compiles():
    import py_compile
    py_compile.compile(str(API), doraise=True)


def test_handle_text_envelope_helper_present():
    src = BRIDGE.read_text()
    assert 'async def handle_text_envelope' in src


def test_handle_text_envelope_strips_client_supplied_ctxid_and_interface_id():
    proto = importlib.import_module('helpers.wyoming_protocol')
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    bridge_mod = importlib.import_module('helpers.wyoming_ws_bridge')
    interface = wi.load_interfaces([{'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701}])[0]
    runtime = ws.WyomingInterfaceRuntime(interface)
    bridge = bridge_mod.WyomingWsBridge(runtime)
    async def run():
        replies = await bridge.handle_text_envelope(
            event_type='describe',
            event_data={'ctxid': 'attacker', 'interface_id': 'rogue'},
        )
        assert replies and replies[0].type == 'info'
        assert replies[0].data.get('ctxid') == 'ctx-hero'
        assert replies[0].data.get('interface_id') == 'hero'
    asyncio.run(run())
