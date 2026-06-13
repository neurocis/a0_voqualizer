"""Regression: downstream Wyoming handler errors must not fail submit ACK."""
import asyncio
import importlib


class FakeBridge:
    async def handle_text_envelope(self, **kwargs):
        raise AttributeError("'str' object has no attribute 'text'")


def _make_handler():
    mod = importlib.import_module('api.ws_wyoming')
    cls = getattr(mod, 'WyomingWs', None) or getattr(mod, 'WsWyoming')
    handler = cls.__new__(cls)
    handler._bridge = FakeBridge()
    handler._interface_id = 'web'
    handler._pending_outbound_payload = b''
    handler.sent = []
    async def emit_to(sid, event, data, **kwargs):
        handler.sent.append((sid, event, data, kwargs))
    handler.emit_to = emit_to
    return handler


def test_handler_error_is_emitted_as_wyoming_events_not_failed_ack():
    handler = _make_handler()
    result = asyncio.run(handler._handle_event({
        'type': 'voqualizer-text-prompt',
        'event_data': {'text': 'hi', 'generation_id': 'g-test'},
        'payload_length': 0,
    }, 'sid-test'))
    payload = result.as_result(handler_id='test', fallback_correlation_id='cid')
    assert payload['ok'] is True
    assert payload['data']['replies'] == 2
    events = [item[2] for item in handler.sent]
    assert events[0]['type'] == 'error'
    assert events[0]['data']['code'] == 'wyoming_handler_error'
    assert events[1]['type'] == 'voqualizer-response-final'
    assert events[1]['data']['ok'] is False
    assert events[1]['data']['generation_id'] == 'g-test'
