"""Regression: legacy DOM Voqualizer buttons must respect the DOM-only toggle.

Agent Zero auto-loads every HTML file under
extensions/webui/chat-input-box-end/ so both the legacy custom-protocol DOM
buttons and the new Wyoming DOM buttons render together. The side-quest toggle
only disables the DOM ASR/TTS integration; the standalone Voqualizer page,
Wyoming TCP runtime, providers, and retained legacy reference files remain
available. When the toggle is OFF, the legacy buttons must also hide and skip
their init() to actually take the user out of the DOM ASR/TTS surface.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "extensions" / "webui" / "chat-input-box-end" / "voqualizer-buttons.html"
NEW = ROOT / "extensions" / "webui" / "chat-input-box-end" / "voqualizer-wyoming-buttons.html"


def test_legacy_dom_buttons_check_dom_integration_toggle_before_init():
    src = LEGACY.read_text()
    for marker in (
        'data-voqualizer-dom-toggle-guard',
        "action: 'dom_integration'",
        '/api/plugins/a0_voqualizer/wyoming_status',
        'voqualizerLegacyDomIntegrationStatus',
        "$el.hidden = true",
        'return; // do not initialize legacy buttons',
        "document.body.classList.add('voqualizer-dom-active')",
        "body.voqualizer-dom-active #microphone-button",
        "[data-a0-voqualizer-buttons][data-wyoming-dom-disabled=\"true\"]",
    ):
        assert marker in src, marker


def test_new_wyoming_dom_buttons_still_respect_dom_integration_toggle():
    src = NEW.read_text()
    for marker in (
        "action: 'dom_integration'",
        '_fetchDomIntegrationStatus',
        'DOM ASR/TTS disabled',
        'data-wyoming-dom-disabled',
    ):
        assert marker in src, marker


def test_legacy_dom_buttons_do_not_remove_core_x_component_ancestor():
    src = LEGACY.read_text()
    assert "closest('x-component')" not in src
    assert '$el.remove()' not in src
    assert '$el.hidden = true' in src
