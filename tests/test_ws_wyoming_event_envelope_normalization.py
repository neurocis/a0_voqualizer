"""Regression: ws_wyoming must accept framework-wrapped Wyoming event envelopes."""
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / 'api' / 'ws_wyoming.py'


def test_normalizer_source_markers_present():
    src = HANDLER.read_text()
    assert 'def _normalize_wyoming_event_envelope(data: dict) -> dict:' in src
    assert 'def walk(obj: object, depth: int = 0)' in src
    assert '"args"' in src
    assert '"arguments"' in src
    assert '"payload"' in src
    assert 'envelope = _normalize_wyoming_event_envelope(data or {})' in src
    assert 'event_type = str(envelope.get("type") or "").strip()' in src
    assert 'shape=' in src


def test_normalizer_accepts_direct_and_nested_shapes():
    mod = importlib.import_module('api.ws_wyoming')
    direct = {'type': 'voqualizer-text-prompt', 'data': {'text': 'hello'}}
    assert mod._normalize_wyoming_event_envelope(direct) is direct
    wrappers = (
        {'event': {'type': 'voqualizer-text-prompt', 'data': {'text': 'event'}}},
        {'envelope': {'type': 'voqualizer-text-prompt', 'data': {'text': 'envelope'}}},
        {'wyoming_event': {'type': 'voqualizer-text-prompt', 'data': {'text': 'wyoming_event'}}},
        {'data': {'type': 'voqualizer-text-prompt', 'data': {'text': 'data'}}},
        {'payload': {'type': 'voqualizer-text-prompt', 'data': {'text': 'payload'}}},
        {'input': {'type': 'voqualizer-text-prompt', 'data': {'text': 'input'}}},
        {'args': [{'type': 'voqualizer-text-prompt', 'data': {'text': 'args'}}]},
        {'arguments': [{'ignored': True}, {'type': 'voqualizer-text-prompt', 'data': {'text': 'arguments'}}]},
        {'outer': {'inner': {'type': 'voqualizer-text-prompt', 'data': {'text': 'fallback-values'}}}},
    )
    for wrapped in wrappers:
        out = mod._normalize_wyoming_event_envelope(wrapped)
        assert out['type'] == 'voqualizer-text-prompt'
        assert out['data']['text']


def test_normalizer_leaves_bad_shape_for_existing_error_path_and_reports_shape():
    mod = importlib.import_module('api.ws_wyoming')
    bad = {'data': {'text': 'missing type'}}
    assert mod._normalize_wyoming_event_envelope(bad) is bad
    shape = mod._wyoming_event_shape_debug(bad, bad)
    assert shape['top_keys'] == ['data']
    assert shape['top_data_kind'] == 'dict'
