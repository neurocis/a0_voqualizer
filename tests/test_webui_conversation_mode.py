"""Source-level tests for the Voqualizer Conversational/PTT store."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
CM = PLUGIN / 'webui' / 'conversation-mode.js'


def test_conversation_mode_file_exists():
    assert CM.exists(), 'webui/conversation-mode.js missing'


def test_store_exposes_state_machine_and_helpers():
    s = CM.read_text()
    for marker in (
        "Alpine.store('voqualizer'",
        'STATE_IDLE',
        'STATE_CONNECTING',
        'STATE_CONVERSATIONAL',
        'STATE_PTT_ACTIVE',
        'STATE_ERROR',
        'TAP_HOLD_THRESHOLD_MS',
        'currentContextId',
        'a0_voqualizer.tts_enabled.',
        'onTap',
        'onHoldStart',
        'onHoldEnd',
        'set_tts_enabled',
        '_sendFinalFrame',
        'is_final: true',
    ):
        assert marker in s, f'missing marker {marker!r} in conversation-mode.js'


def test_tap_hold_threshold_is_250ms():
    s = CM.read_text()
    assert 'TAP_HOLD_THRESHOLD_MS = 250' in s


def test_per_context_session_storage_key():
    s = CM.read_text()
    assert "TTS_PREF_PREFIX = 'a0_voqualizer.tts_enabled.'" in s
    assert 'sessionStorage' in s
