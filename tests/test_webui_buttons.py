"""Source-level tests for the dedicated Voqualizer chat-input buttons."""
from pathlib import Path
import re

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

def test_extension_renders_mic_vu_meter_inside_mic_button():
    s = source()
    for marker in (
        'voqualizer-mic-vu',
        '--voqualizer-vu-level',
        '--voqualizer-vu-opacity',
        'data-voqualizer-vu-level',
        'micVuLevel',
        'micVuClipped',
        'voqualizer-vu-clipped',
    ):
        assert marker in s, f'missing mic VU marker {marker!r}'



def test_extension_mic_vu_meter_is_prominent():
    s = source()
    for marker in (
        'radial-gradient(circle',
        'drop-shadow(0 0 calc',
        'transform: scale(calc',
        '0.35 + (0.55 * var(--voqualizer-vu-level, 0))',
        '.voqualizer-mic.voqualizer-active',
        '.voqualizer-mic.voqualizer-ptt',
        '.voqualizer-mic.voqualizer-vu-clipped .voqualizer-mic-vu',
    ):
        assert marker in s, f'missing prominent VU marker {marker!r}'

def test_extension_turns_mic_glyph_red_when_speech_detected():
    s = source()
    for marker in (
        'voqualizer-speech-detected',
        'data-voqualizer-speech-active',
        'micSpeechActive',
        'color: #ff3b30',
        'text-shadow: 0 0 7px rgba(255, 59, 48',
    ):
        assert marker in s, f'missing speech-detected mic marker {marker!r}'

def test_extension_exposes_speech_cooldown_debug_marker():
    s = source()
    assert 'data-voqualizer-speech-cooldown-until' in s
    assert 'micSpeechCooldownUntil' in s

def test_extension_exposes_speech_last_active_debug_marker():
    s = source()
    assert 'data-voqualizer-speech-last-active-at' in s
    assert 'micSpeechLastActiveAt' in s



def test_rendered_response_tts_fallback_extension_exists():
    ext = PLUGIN / 'extensions' / 'webui' / 'set_messages_after_loop' / 'voqualizer-speak-response.js'
    assert ext.exists(), 'rendered response TTS fallback extension missing'
    s = ext.read_text(encoding='utf-8')
    for marker in (
        'speakVoqualizerRenderedResponses',
        "Alpine?.store?.('voqualizer')",
        'store.speakText',
        "voqualizer_user_text",
        'isPrimaryResponse',
        "String(args?.type || '') !== 'response'",
        "Number(args?.agentno || 0) > 0",
        'message-agent-response',
        'responseTextFromElement',
        'a0_voqualizer.spoken_response.',
        'sessionStorage',
    ):
        assert marker in s, f'missing rendered response TTS fallback marker {marker!r}'


def test_native_mic_hidden():
    src = BUTTONS_HTML.read_text()
    assert '#microphone-button' in src, 'must reference native mic button'
    assert '.a0-sup-prompt-speech' in src, 'must hide superordinates speech toggle'
    assert 'display: none' in src, 'must hide native mic button'

def test_voqualizer_buttons_inline_positioning():
    src = BUTTONS_HTML.read_text()
    assert 'x-move-after="#send-button"' in src, 'must move Voqualizer buttons directly after send button'
    assert 'display: inline-flex' in src, 'must render Voqualizer controls as inline flex buttons'
    assert 'padding-right: calc(36px * 2' not in src, 'must not pad chat button row for overlay placement'

    row_block = re.search(r'\.voqualizer-buttons-row\s*\{(?P<body>.*?)\n\s*\}', src, re.S)
    assert row_block, 'must define voqualizer button row CSS'
    assert 'position: absolute' not in row_block.group('body'), 'row must not use fragile absolute overlay positioning'

    host_block = re.search(r'\[id="chat-input-box-end"\]\s*\{(?P<body>.*?)\n\s*\}', src, re.S)
    assert host_block, 'must define chat-input-box-end host CSS'
    assert 'display: contents' in host_block.group('body'), 'extension host should disappear after x-move-after relocation'
    assert 'position: absolute' not in host_block.group('body'), 'extension host must not use overlay positioning'
