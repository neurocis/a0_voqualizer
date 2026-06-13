"""Regression: browser wyoming_init requires WyomingWsBridge.describe()."""
import asyncio
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'helpers' / 'wyoming_ws_bridge.py'
HANDLER = ROOT / 'api' / 'ws_wyoming.py'


def _bridge():
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    bridge_mod = importlib.import_module('helpers.wyoming_ws_bridge')
    iface = wi.WyomingInterface(id='web', name='Web', ctxid='ctx-test', bind_host='127.0.0.1', bind_port=19081)
    runtime = ws.WyomingInterfaceRuntime(iface)
    return bridge_mod.WyomingWsBridge(runtime)


def test_bridge_source_exposes_sync_describe_for_init():
    src = BRIDGE.read_text()
    assert 'class WyomingWsBridge' in src
    assert 'def describe(self) -> WyomingEvent:' in src
    assert 'async def describe(' not in src
    assert 'return self.session.info_event()' in src


def test_ws_handler_calls_bridge_describe_during_init():
    src = HANDLER.read_text()
    assert 'info = self._bridge.describe()' in src
    assert 'info.type' in src
    assert 'wyoming_init' in src


def test_bridge_describe_returns_wyoming_info_event_shape():
    bridge = _bridge()
    info = bridge.describe()
    assert info.type == 'info'
    assert info.data['voqualizer']['interface_id'] == 'web'
    assert info.data['voqualizer']['ctxid'] == 'ctx-test'
    assert bridge.snapshot()['session_id']


def test_handle_text_envelope_uses_public_runtime_and_session_attrs():
    src = BRIDGE.read_text()
    assert 'await self.runtime.handle_event(self.session, ev)' in src
    assert 'self._runtime' not in src
    assert 'self._session' not in src
    bridge = _bridge()
    replies = asyncio.run(bridge.handle_text_envelope(event_type='describe', event_data={'ctxid': 'evil'}, payload=None))
    assert replies[0].type == 'info'
    assert replies[0].data['voqualizer']['ctxid'] == 'ctx-test'
