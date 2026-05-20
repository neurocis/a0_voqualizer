"""Source-level tests for the dedicated Voqualizer chat-input buttons."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
EXT = PLUGIN / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-buttons.html'
OLD_EXT = PLUGIN / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-button-overrides.html'


def source() -> str:
    return EXT.read_text(encoding='utf-8')


def test_old_override_extension_removed():
    assert not OLD_EXT.exists(), 'old override extension must be removed'


def test_new_extension_file_exists():
    assert EXT.exists(), 'voqualizer-buttons.html missing'


def test_extension_renders_two_dedicated_buttons_without_status_pill():
    s = source()
    assert 'id="voqualizer-speaker-button"' in s
    assert 'id="voqualizer-mic-button"' in s
    assert 'voqualizer-status-pill' not in s
    assert 'aria-live="polite"' not in s
    assert 'Voq: Off' not in s
    assert 'Voq: Listening' not in s
    assert 'Voq: Push-to-talk' not in s


def test_extension_orders_controls_speaker_then_mic_only():
    s = source()
    speaker = s.index('id="voqualizer-speaker-button"')
    mic = s.index('id="voqualizer-mic-button"')
    assert speaker < mic, 'expected [Voq Speaker] [Voq Mic] order'
    assert 'voqualizer-status-pill' not in s


def test_extension_does_not_intercept_a0_native_buttons():
    s = source()
    assert 'microphone-button' not in s
    assert 'stop-speech' not in s
    assert "getElementById('microphone-button')" not in s
    assert "getElementById('stop-speech')" not in s
    assert 'stopImmediatePropagation' not in s
    assert 'capture: true' not in s


def test_extension_uses_tap_hold_threshold_and_state_classes():
    s = source()
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
    s = source()
    for marker in (
        'speakerLabel(ttsOff)',
        'micLabel(s)',
        "setAttribute('aria-label'",
        "setAttribute('title'",
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


def test_extension_uses_pointer_and_keyboard_handlers():
    s = source()
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
    s = source()
    assert 'aria-pressed' in s
    assert 'aria-label="Voqualizer mic off.' in s
    assert 'aria-label="Voqualizer TTS is on.' in s
