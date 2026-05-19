"""Verify tts_enabled flow in api/ws_voqualizer.py and helper guards."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
WS = PLUGIN / 'api' / 'ws_voqualizer.py'
SESSION = PLUGIN / 'helpers' / 'session.py'
FINAL = PLUGIN / 'helpers' / 'agent_finalizer.py'
CHUNKER = PLUGIN / 'helpers' / 'sentence_chunker.py'


def test_session_has_tts_enabled_field():
    assert 'tts_enabled: bool = True' in SESSION.read_text()


def test_ws_init_accepts_tts_enabled():
    s = WS.read_text()
    assert 'tts_enabled_init = tts_block.get("enabled")' in s
    assert 'session.tts_enabled = tts_enabled_init' in s


def test_ws_control_supports_set_tts_enabled():
    s = WS.read_text()
    assert 'set_tts_enabled' in s
    assert 'session.tts_enabled = enabled' in s
    assert 'cancel_in_flight_tts' in s


def test_agent_finalizer_respects_tts_enabled():
    assert 'tts_disabled' in FINAL.read_text()
    assert 'getattr(session, "tts_enabled"' in FINAL.read_text()


def test_sentence_chunker_respects_tts_enabled():
    assert 'tts_disabled' in CHUNKER.read_text()
    assert 'getattr(session, "tts_enabled"' in CHUNKER.read_text()
