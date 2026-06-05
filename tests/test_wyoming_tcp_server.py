from pathlib import Path
import asyncio
import importlib

PLUGIN = Path(__file__).resolve().parents[1]


def test_stream_round_trip_reads_and_writes_payload_event():
    proto = importlib.import_module('helpers.wyoming_protocol')

    async def run():
        reader = asyncio.StreamReader()
        encoded = proto.encode_event(proto.WyomingEvent('audio-chunk', {'rate': 16000}, b'pcm'))
        reader.feed_data(encoded)
        reader.feed_eof()
        event = await proto.read_event_from_stream(reader)
        assert event.type == 'audio-chunk'
        assert event.data == {'rate': 16000}
        assert event.payload == b'pcm'

        class Writer:
            def __init__(self):
                self.data = b''
            def write(self, data):
                self.data += data
            async def drain(self):
                return None
        writer = Writer()
        await proto.write_event_to_stream(writer, proto.event('describe'))
        assert writer.data == b'{"type":"describe"}\n'

    asyncio.run(run())


def test_tcp_server_handle_client_dispatches_describe_info_and_closes_session():
    wi = importlib.import_module('helpers.wyoming_interfaces')
    ws = importlib.import_module('helpers.wyoming_server')
    proto = importlib.import_module('helpers.wyoming_protocol')

    async def run():
        interface = wi.load_interfaces([
            {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_host': '127.0.0.1', 'bind_port': 10701},
        ])[0]
        runtime = ws.WyomingInterfaceRuntime(interface)
        tcp = ws.WyomingTcpServer(runtime)
        reader = asyncio.StreamReader()
        reader.feed_data(proto.encode_event(proto.event('describe')))
        reader.feed_eof()

        class Writer:
            def __init__(self):
                self.data = b''
                self.closed = False
            def write(self, data):
                self.data += data
            async def drain(self):
                return None
            def get_extra_info(self, name):
                return ('test-client', 1) if name == 'peername' else None
            def close(self):
                self.closed = True
            async def wait_closed(self):
                return None

        writer = Writer()
        await tcp.handle_client(reader, writer)
        header, payload = writer.data.split(b'\n', 1)
        decoded = proto.decode_event(header, payload)
        assert decoded.type == 'info'
        assert decoded.data['voqualizer']['ctxid'] == 'ctx-hero'
        assert decoded.data['voqualizer']['interface_id'] == 'hero'
        assert runtime.sessions == {}
        assert writer.closed is True

    asyncio.run(run())


def test_tcp_server_source_avoids_legacy_custom_ws_protocol_names():
    source = (PLUGIN / 'helpers' / 'wyoming_server.py').read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text'):
        assert forbidden not in source
    assert 'asyncio.start_server' in source
    assert 'read_event_from_stream' in source
    assert 'write_event_to_stream' in source
    assert 'WyomingTcpInterfaceManager' in source
