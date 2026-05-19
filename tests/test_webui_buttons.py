"""Source-level tests for the dedicated Voqualizer chat-input buttons."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
EXT = PLUGIN / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-buttons.html'
OLD_EXT = PLUGIN / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-button-overrides.html'


def test_old_override_extension_removed():
    assert not OLD_EXT.exists(), 'old override extension must be removed'


def test_new_extension_file_exists():
    assert EXT.exists(), 'voqualizer-buttons.html missing'


def test_extension_renders_status_pill_and_two_dedicated_buttons():
    s = EXT.read_text()
    assert 'id="voqualizer-status-pill"' in s
    assert 'id="voqualizer-speaker-button"' in s
    assert 'id="voqualizer-mic-button"' in s
    for marker in ('Voq: Off', 'Voq: Listening', 'Voq: Push-to-talk', 'TTS muted', 'Voq: Connecting', 'Voq: Stopping', 'Voq: Error'):
        assert marker in s, f'missing status marker {marker!r}'



def test_extension_orders_controls_speaker_mic_status_pill():
    s = EXT.read_text()
    speaker = s.index('id="voqualizer-speaker-button"')
    mic = s.index('id="voqualizer-mic-button"')
    pill = s.index('id="voqualizer-status-pill"')
    assert speaker < mic < pill, 'expected [Voq Speaker] [Voq Mic] [Voq Status Pill] order'

def test_extension_does_not_intercept_a0_native_buttons():
    s = EXT.read_text()
    # Must not capture-phase override A0's own buttons.
    assert 'microphone-button' not in s
    assert 'stop-speech' not in s
    assert "getElementById('microphone-button')" not in s
    assert "getElementById('stop-speech')" not in s
    assert 'stopImmediatePropagation' not in s
    assert 'capture: true' not in s


def test_extension_uses_tap_hold_threshold_and_state_classes():
    s = EXT.read_text()
    assert 'TAP_HOLD_THRESHOLD_MS' in s
    assert '250' in s
    for marker in (
        'voqualizer-idle',
        'voqualizer-tts-off',
        'voqualizer-active',
        'voqualizer-ptt',
        'voqualizer-connecting',
        'voqualizer-error',
    ):
        assert marker in s, f'missing class marker {marker!r}'


def test_extension_uses_dynamic_labels_and_tooltips():
    s = EXT.read_text()
    for marker in (
        'speakerLabel(ttsOff)',
        'micLabel(s)',
        'setAttribute(\'aria-label\'',
        'setAttribute(\'title\'',
        'Voqualizer TTS is on. Click to mute TTS for this chat.',
        'Voqualizer TTS is muted. Click to enable TTS for this chat.',
        'Voqualizer mic off. Tap for conversation. Hold for push-to-talk.',
        'Voqualizer listening. Tap to stop. Hold for push-to-talk finalization.',
        'Voqualizer push-to-talk active. Release to send final.',
        'Voqualizer error. Tap to retry.',
        'Voqualizer stopping…',
        'lastTransitionReason',
    ):
        assert marker in s, f'missing dynamic label marker {marker!r}'


def test_extension_uses_transition_notices():
    s = EXT.read_text()
    for marker in (
        'flashNotice(text)',
        'Conversation mode on',
        'Conversation mode off',
        'Push-to-talk: release to send',
        'TTS muted for this chat',
        'TTS enabled for this chat',
    ):
        assert marker in s, f'missing notice marker {marker!r}'


def test_extension_uses_pointer_and_keyboard_handlers():
    s = EXT.read_text()
    for marker in (
        '@pointerdown="onPointerDown',
        '@pointerup="onPointerUp',
        '@pointercancel="onPointerCancel',
        '@keydown="onKey',
        '@keyup="onKey',
        "event.key !== ' '",
        "event.key !== 'Enter'",
    ):
        assert marker in s, f'missing handler marker {marker!r}'


def test_extension_aria_markers_present():
    s = EXT.read_text()
    assert 'aria-live="polite"' in s
    assert 'aria-pressed' in s
    assert 'aria-label="Voqualizer microphone' in s
    assert 'aria-label="Voqualizer speaker' in s
