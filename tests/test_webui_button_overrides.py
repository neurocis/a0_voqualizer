"""Source-level tests for the in-GUI Voqualizer button overrides extension."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
EXT = PLUGIN / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-button-overrides.html'


def test_extension_file_exists():
    assert EXT.exists(), 'voqualizer-button-overrides.html missing'


def test_extension_intercepts_mic_and_speaker_with_alpine_root():
    s = EXT.read_text()
    for marker in (
        "x-data=\"a0VoqualizerButtonOverrides()\"",
        "data-a0-voqualizer-button-overrides=\"1\"",
        "getElementById('microphone-button')",
        "getElementById('stop-speech')",
        'pointerdown',
        'pointerup',
        'pointercancel',
        'stopImmediatePropagation',
        'TAP_HOLD_THRESHOLD_MS',
        '250',
        'voqualizer-tts-off',
        'voqualizer-ptt',
        'voqualizer-active',
        'voqualizer-connecting',
        'aria-pressed',
        'capture: true',
    ):
        assert marker in s, f'missing marker {marker!r} in voqualizer-button-overrides.html'
