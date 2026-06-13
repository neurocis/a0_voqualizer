"""Regression: ws_wyoming must accept framework-wrapped Wyoming event envelopes."""
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / 'api' / 'ws_wyoming.py'


def test_normalizer_source_markers_present():
    src = HANDLER.read_text()
    assert 'def _normalize_wyoming_event_envelope(data: dict) -> dict:' in src
    assert 'for key in ("event", "envelope", "wyoming_event")' in src
    assert 'envelope = _normalize_wyoming_event_envelope(data or {})' in src
    assert 'event_type = str(envelope.get("type") or "").strip()' in src
    assert 'payload_length = int(envelope.get("payload_length") or 0)' in src


def test_normalizer_accepts_direct_and_nested_shapes():
    mod = importlib.import_module('api.ws_wyoming')
    direct = {'type': 'voqualizer-text-prompt', 'data': {'text': 'hello'}}
    assert mod._normalize_wyoming_event_envelope(direct) is direct
    for key in ('event', 'envelope', 'wyoming_event', 'data'):
        wrapped = {key: {'type': 'voqualizer-text-prompt', 'data': {'text': key}}}
        out = mod._normalize_wyoming_event_envelope(wrapped)
        assert out['type'] == 'voqualizer-text-prompt'
        assert out['data']['text'] == key


def test_normalizer_leaves_bad_shape_for_existing_error_path():
    mod = importlib.import_module('api.ws_wyoming')
    bad = {'data': {'text': 'missing type'}}
    assert mod._normalize_wyoming_event_envelope(bad) is bad
