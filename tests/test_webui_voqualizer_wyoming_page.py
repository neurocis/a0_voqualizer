"""Static markers test for the W18 Wyoming-based standalone page.

The new page lives alongside the legacy webui/voqualizer.html, which remains
in-tree for reference per the breaking-rewrite plan.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / 'webui' / 'voqualizer-wyoming.html'
LEGACY = ROOT / 'webui' / 'voqualizer-legacy-reference.html'


def test_new_wyoming_page_exists():
    assert NEW.exists()


def test_legacy_standalone_page_preserved():
    assert LEGACY.exists()


def test_new_page_uses_only_wyoming_protocol():
    src = NEW.read_text()
    for forbidden in (
        'voqualizer_init',
        'voqualizer_user_text',
        'voqualizer_audio_chunk',
        'voqualizer_tts_chunk',
        'ack_fallback',
        'conversation-mode.js',
    ):
        assert forbidden not in src, forbidden
    for required in (
        '/plugins/a0_voqualizer/webui/wyoming/wyoming-ws-client.js',
        'createWyomingWsClient',
        "event:transcript",
        "event:voqualizer-response-start",
        "event:voqualizer-response-chunk",
        "event:voqualizer-response-final",
        "event:audio-start",
        "event:audio-chunk",
        "event:audio-stop",
        "submitText",
        "beginAudio",
        "sendAudioChunk",
        "endAudio",
        "cancelTts",
    ):
        assert required in src, required


def test_new_page_exposes_debug_snapshot():
    src = NEW.read_text()
    assert 'window.voqualizerWyomingDebug' in src
    assert 'client.snapshot()' in src
    assert 'beforeunload' in src


def test_new_page_filters_stale_generations():
    src = NEW.read_text()
    assert 'function isCurrent(ev)' in src
    assert 'client.isCurrentGeneration' in src
    assert 'if (!isCurrent(ev)) return;' in src


def test_new_page_has_interface_selector_and_discovery():
    src = NEW.read_text()
    assert 'voq-wyoming-interface' in src
    assert "action: 'interfaces'" in src
    assert '/api/plugins/a0_voqualizer/wyoming_ws' in src
    assert 'a0_voqualizer_wyoming_interface' in src


def test_new_page_uses_w35_csrf_cache_bust():
    src = NEW.read_text()
    assert 'w56-canonical-wyoming-2026-06-08-1' in src


def test_new_page_exposes_smoke_diagnostics():
    src = NEW.read_text()
    assert 'id="voq-wyoming-smoke"' in src
    assert 'id="voq-wyoming-diagnostics"' in src
    assert 'window.voqualizerWyomingSmoke' in src
    assert "action: 'smoke'" in src
    assert '/api/plugins/a0_voqualizer/wyoming_status' in src
    assert 'client.snapshot()' in src
    assert 'w56-canonical-wyoming-2026-06-08-1' in src


def test_new_page_exposes_setup_init_validate_start_controls():
    src = NEW.read_text()
    for marker in (
        'id="voq-wyoming-setup"',
        'id="voq-wyoming-ctxid"',
        'id="voq-wyoming-init-config"',
        'id="voq-wyoming-validate"',
        'id="voq-wyoming-start"',
        "action: 'init_config'",
        "action: 'validate'",
        "action: 'start'",
        'window.voqualizerWyomingInitConfig',
        'window.voqualizerWyomingValidate',
        'window.voqualizerWyomingStart',
        'w56-canonical-wyoming-2026-06-08-1',
    ):
        assert marker in src, marker


def test_new_page_exposes_live_checklist_controls():
    src = NEW.read_text()
    for marker in (
        'id="voq-wyoming-checklist"',
        "action: 'checklist'",
        'window.voqualizerWyomingChecklist',
        'runLiveChecklist',
        'w56-canonical-wyoming-2026-06-08-1',
    ):
        assert marker in src, marker


def test_new_page_has_always_visible_checklist_button():
    src = NEW.read_text()
    assert 'id="voq-wyoming-checklist-main"' in src
    assert 'handleChecklistClick' in src
    assert 'checklistMainBtn.addEventListener' in src
    assert 'w56-canonical-wyoming-2026-06-08-1' in src


def test_new_page_exposes_readiness_snapshot_helper():
    src = NEW.read_text()
    for marker in (
        "action: 'readiness'",
        'runReadinessSnapshot',
        'window.voqualizerWyomingReadiness',
        'ready_for_browser',
        'w56-canonical-wyoming-2026-06-08-1',
    ):
        assert marker in src, marker


def test_new_page_auto_configures_current_chat_for_functional_web_ui():
    src = NEW.read_text()
    for marker in (
        'getCurrentA0ContextId',
        "action: 'web_configure'",
        'configureWebInterfaceFromCurrentContext',
        'connectWithAutoSetup',
        'window.voqualizerWyomingConfigureWeb',
        'w56-canonical-wyoming-2026-06-08-1',
    ):
        assert marker in src, marker
