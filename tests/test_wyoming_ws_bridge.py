from pathlib import Path
import asyncio
import importlib
import json

PLUGIN = Path(__file__).resolve().parents[1]
BRIDGE = PLUGIN / 'helpers' / 'wyoming_ws_bridge.py'


def make_runtime():
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    interface = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701},
    ])[0]
    return ws.build_wyoming_pipeline_runtime(interface)


class FakeWs:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []

    async def recv(self):
        if not self.incoming:
            return None
        return self.incoming.pop(0)

    async def send(self, frame):
        self.sent.append(frame)


def test_ws_bridge_dispatches_text_only_event_and_returns_text_envelopes():
    bridge_mod = importlib.import_module('helpers.wyoming_ws_bridge')
    runtime = make_runtime()
    bridge = bridge_mod.WyomingWsBridge(runtime)
    ws = FakeWs([
        json.dumps({'type': 'voqualizer-text-prompt', 'data': {'text': 'hello', 'ctxid': 'malicious'}}),
    ])

    async def run():
        await bridge.run(ws.recv, ws.send)

    asyncio.run(run())
    types = [json.loads(frame)['type'] for frame in ws.sent if isinstance(frame, str)]
    assert 'voqualizer-response-final' in types
    assert 'audio-start' in types
    assert 'audio-stop' in types
    snap = bridge.snapshot()
    assert snap['ctxid'] == 'ctx-hero'
    assert snap['text_events_in'] == 1
    assert snap['text_events_out'] >= len(types)
    assert snap['closed'] is True


def test_ws_bridge_pairs_text_envelope_with_following_binary_payload():
    bridge_mod = importlib.import_module('helpers.wyoming_ws_bridge')
    runtime = make_runtime()
    bridge = bridge_mod.WyomingWsBridge(runtime)
    ws = FakeWs([
        json.dumps({'type': 'audio-chunk', 'data': {'payload_length': 4}}),
        b'PCM!',
        json.dumps({'type': 'audio-stop', 'data': {'utterance_id': 'utt-1'}}),
    ])

    async def run():
        await bridge.run(ws.recv, ws.send)

    asyncio.run(run())
    text_replies = [json.loads(frame) for frame in ws.sent if isinstance(frame, str)]
    transcript = [reply for reply in text_replies if reply['type'] == 'transcript']
    # transcript may or may not be emitted depending on scaffold provider, but bridge must accept binary payload
    snap = bridge.snapshot()
    assert snap['binary_events_in'] == 1
    assert snap['bad_frames'] == 0


def test_ws_bridge_rejects_binary_without_text_envelope_and_bad_text_frames():
    bridge_mod = importlib.import_module('helpers.wyoming_ws_bridge')
    runtime = make_runtime()
    bridge = bridge_mod.WyomingWsBridge(runtime)
    ws = FakeWs([
        b'unexpected-binary',
        'not-json',
    ])

    async def run():
        await bridge.run(ws.recv, ws.send)

    asyncio.run(run())
    text_replies = [json.loads(frame) for frame in ws.sent if isinstance(frame, str)]
    errors = [reply for reply in text_replies if reply.get('type') == 'error']
    assert errors, 'bridge should report error frames'
    codes = {reply['data']['code'] for reply in errors}
    assert 'unexpected_binary' in codes
    assert 'bad_frame' in codes
    snap = bridge.snapshot()
    assert snap['bad_frames'] == 2


def test_ws_bridge_ignores_client_attempt_to_override_ctxid():
    bridge_mod = importlib.import_module('helpers.wyoming_ws_bridge')
    runtime = make_runtime()
    bridge = bridge_mod.WyomingWsBridge(runtime)
    incoming = bridge.decode_text_frame(json.dumps({'type': 'voqualizer-text-prompt', 'data': {'text': 'x', 'ctxid': 'evil', 'interface_id': 'other'}}))
    assert 'ctxid' not in incoming.data
    assert 'interface_id' not in incoming.data


def test_ws_bridge_source_avoids_legacy_custom_websocket_event_names():
    source = BRIDGE.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text', 'ack_fallback'):
        assert forbidden not in source
    assert 'WyomingWsBridge' in source
    assert 'encode_for_browser' in source
    assert 'decode_text_frame' in source
    assert 'attach_binary_payload' in source
