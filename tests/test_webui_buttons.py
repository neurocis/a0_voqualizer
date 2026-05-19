"""Source-level tests for the dedicated Voqualizer chat-input buttons."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
EXT = PLUGIN / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-buttons.html'
OLD_EXT = PLUGIN / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-button-overrides.html'


def test_old_override_extension_removed():
    assert not OLD_EXT.exists(), 'old override extension must be removed'


def test_new_extension_file_exists():
    assert EXT.exists(), 'voqualizer-buttons.html missing'


def test_extension_renders_two_dedicated_buttons():
    s = EXT.read_text()
    assert 'id="voqualizer-speaker-button"' in s
    assert 'id="voqualizer-mic-button"' in s


def test_extension_does_not_intercept_a0_native_buttons():
    s = EXT.read_text()
    # Must not capture-phase override A0's own buttons.
    assert "getElementById('microphone-button')" not in s
    assert "getElementById('stop-speech')" not in s
    assert 'stopImmediatePropagation' not in s
    assert 'capture: true' not in s


def test_extension_uses_tap_hold_threshold_and_state_classes():
    s = EXT.read_text()
    assert 'TAP_HOLD_THRESHOLD_MS' in s
    assert '250' in s
    assert 'voqualizer-tts-off' in s
    assert 'voqualizer-active' in s
    assert 'voqualizer-ptt' in s
    assert 'voqualizer-connecting' in s
    assert 'voqualizer-error' in s


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
    assert 'aria-pressed' in s
    assert 'aria-label="Voqualizer microphone' in s
    assert 'aria-label="Voqualizer speaker' in s
