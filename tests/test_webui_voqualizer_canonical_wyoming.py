"""W56: the canonical standalone Voqualizer page is the Wyoming UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / 'webui' / 'voqualizer.html'
WYOMING_ALIAS = ROOT / 'webui' / 'voqualizer-wyoming.html'
LEGACY_REF = ROOT / 'webui' / 'voqualizer-legacy-reference.html'


def test_canonical_voqualizer_page_is_wyoming_interface():
    src = CANONICAL.read_text()
    for marker in (
        '/plugins/a0_voqualizer/webui/wyoming/wyoming-ws-client.js',
        'createWyomingWsClient',
        "action: 'web_configure'",
        'configureWebInterfaceFromCurrentContext',
        'connectWithAutoSetup',
        'window.voqualizerWyomingConfigureWeb',
        'w56-canonical-wyoming-2026-06-08-1',
    ):
        assert marker in src, marker


def test_canonical_page_avoids_retired_custom_protocol():
    src = CANONICAL.read_text()
    for token in (
        'voqualizer_init',
        'voqualizer_user_text',
        'voqualizer_audio_chunk',
        'voqualizer_tts_chunk',
        'ack_fallback',
        'conversation-mode.js',
        '/message_async',
        '/poll',
    ):
        assert token not in src, token


def test_legacy_standalone_preserved_only_as_reference():
    assert LEGACY_REF.exists()
    legacy = LEGACY_REF.read_text()
    assert '/message_async' in legacy or '/poll' in legacy or 'conversation-mode' in legacy or 'voqualizer.js' in legacy
    assert WYOMING_ALIAS.exists()
