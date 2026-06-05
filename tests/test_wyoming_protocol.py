from pathlib import Path
import importlib.util

PLUGIN = Path(__file__).resolve().parents[1]
PROTO = PLUGIN / 'helpers' / 'wyoming_protocol.py'
INTERFACES = PLUGIN / 'helpers' / 'wyoming_interfaces.py'
DOC = PLUGIN / 'docs' / 'wyoming-voqualizer-migration.md'


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wyoming_event_round_trip_with_payload():
    proto = load_module(PROTO, 'wyoming_protocol_under_test')
    original = proto.WyomingEvent('audio-chunk', {'rate': 16000, 'width': 2}, b'abc')
    encoded = proto.encode_event(original)
    header, payload = encoded.split(b'\n', 1)
    decoded = proto.decode_event(header, payload)
    assert decoded.type == 'audio-chunk'
    assert decoded.data == {'rate': 16000, 'width': 2}
    assert decoded.payload == b'abc'


def test_wyoming_event_rejects_bad_payload_length():
    proto = load_module(PROTO, 'wyoming_protocol_under_test_bad')
    try:
        proto.decode_event(b'{"type":"audio-chunk","payload_length":9}', b'abc')
    except Exception as exc:
        assert 'payload length mismatch' in str(exc)
    else:
        raise AssertionError('expected payload length mismatch')


def test_wyoming_interface_is_one_to_one_with_ctxid():
    mod = load_module(INTERFACES, 'wyoming_interfaces_under_test')
    interfaces = mod.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-a', 'bind_port': 10701},
        {'id': 'kitchen', 'name': 'Kitchen', 'ctxid': 'ctx-b', 'bind_port': 10702},
    ])
    assert interfaces[0].ctxid == 'ctx-a'
    assert interfaces[1].ctxid == 'ctx-b'
    assert interfaces[0].bind_port != interfaces[1].bind_port


def test_wyoming_migration_doc_records_breaking_design():
    text = DOC.read_text()
    assert 'breaking' in text.lower()
    assert '1:1 to exactly one A0 `ctxID`' in text
    assert 'Multiple Wyoming interfaces may be active concurrently' in text
    assert 'Browser/mobile web UI is only one client' in text
    assert 'W0' in text and 'W7' in text
