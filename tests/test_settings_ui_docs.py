from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_HTML = ROOT / "webui" / "config.html"
PROVIDERS_HTML = ROOT / "webui" / "providers.html"
README = ROOT / "README.md"
SKILL = ROOT / "SKILL.md"
EXAMPLES_README = ROOT / "examples" / "README.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_a84_artifacts_exist():
    assert CONFIG_HTML.is_file()
    assert PROVIDERS_HTML.is_file()
    assert README.is_file()
    assert SKILL.is_file()
    assert EXAMPLES_README.is_file()


def test_config_html_is_alpine_settings_modal_fragment():
    """webui/config.html must be a valid A0 Settings modal Alpine fragment so
    that the standard A0 plugin Settings modal renders non-empty content for
    a0_voqualizer. It is loaded as <x-component> inside an Alpine modal context
    that exposes `config` and `context` (see /a0/webui/components/plugins/).
    """
    text = read(CONFIG_HTML)
    # Alpine modal fragment scaffolding
    assert 'x-data' in text
    assert 'x-if="config"' in text
    # Plugin identity / title
    assert 'A0 Voqualizer' in text
    # config.* bindings driven by default_config.yaml keys (nested)
    for binding in [
        'x-model="config.behavior.barge_in"',
        'x-model="config.behavior.auto_spawn_context"',
        'x-model="config.behavior.sentence_chunking"',
        'x-model="config.asr.default"',
        'x-model="config.tts.default"',
        'x-model="config.protocol.default_input_codec"',
        'x-model="config.protocol.default_output_codec"',
        'x-model.number="config.protocol.heartbeat_interval_seconds"',
        'x-model.number="config.protocol.session_resume_window_seconds"',
        'x-model.number="config.limits.max_concurrent_sessions"',
        'x-model.number="config.limits.max_session_seconds"',
        'x-model.number="config.limits.max_audio_chunk_kb"',
        'x-model.number="config.limits.max_text_chunk_chars"',
        'x-model.number="config.limits.audio_queue_max_frames"',
    ]:
        assert binding in text
    # Cross-links: both the providers editor and the live tester open in their
    # own tab/window as full standalone pages (previously providers was a
    # modal launched via openModal()).
    assert 'href="/plugins/a0_voqualizer/webui/providers.html"' in text
    assert 'href="/plugins/a0_voqualizer/webui/tester.html"' in text
    assert 'target="_blank"' in text
    assert 'rel="noopener noreferrer"' in text
    assert "openModal('/plugins/a0_voqualizer/webui/providers.html')" not in text
    assert "openModal('/plugins/a0_voqualizer/webui/tester.html')" not in text


def test_config_html_is_not_a_standalone_html_page():
    """Regression guard: the Settings modal expects a fragment, not a full
    standalone page with module imports. A standalone page with module imports
    is what caused the modal to render empty.
    """
    text = read(CONFIG_HTML)
    assert '<!doctype html>' not in text.lower()
    assert 'createVoqualizerConfigStore' not in text
    assert "from './config-store.js'" not in text


def test_providers_panel_is_polished_and_links_docs():
    """The polished A6.3 / A8.4 standalone provider editor now ships as
    webui/providers.html and remains accessible from the Settings modal via an
    Open Providers editor button.
    """
    text = read(PROVIDERS_HTML)
    assert 'id="settings-panel"' in text
    assert 'data-artifact="A8.4-settings-panel"' in text
    assert 'main { width: 1860px; max-width: 100%; margin: 0 auto;' in text
    assert "A0 Voqualizer Settings" in text
    assert "settings-summary" in text
    assert "settings-doc-links" in text
    for marker in [
        "Open tester + diagnostics",
        "Error taxonomy",
        "Client portability",
        "Plugin README",
        "Skill card",
        "Examples README",
        "Security review",
        "Load test report",
        "/api/plugins/a0_voqualizer/voqualizer_admin",
        "bearer_token",
        "test_provider",
    ]:
        assert marker in text


def test_readme_documents_current_v010_features_and_constraints():
    text = read(README)
    for marker in [
        "M1–M8.4",
        "plugins/a0_voqualizer/ws_voqualizer",
        "/api/plugins/a0_voqualizer/voqualizer_admin",
        "per-session `bearer_token`",
        "webui/config.html",
        "webui/providers.html",
        "webui/tester.html",
        "docs/protocol/errors.md",
        "docs/security/review.md",
        "docs/performance/load-test-32-sessions.md",
        "docs/clients/portability.md",
        "examples/README.md",
        "Normal pytest is deterministic",
    ]:
        assert marker in text
    assert "A2 audio frame" in text
    assert "PCM16/16k" in text or "pcm16/16k" in text


def test_skill_card_documents_usage_protocol_and_references():
    text = read(SKILL)
    for marker in [
        "a0_voqualizer",
        "plugins/a0_voqualizer/ws_voqualizer",
        "bearer_token",
        "voqualizer_ready",
        "voqualizer_audio_chunk",
        "voqualizer_user_text",
        "voqualizer_control",
        "4-byte A2 header",
        "docs/protocol/errors.md",
        "examples/README.md",
        "webui/config.html",
        "webui/providers.html",
        "webui/tester.html",
    ]:
        assert marker in text
    assert "twilio media streams" in text.lower()
    assert "asterisk audio fork" in text.lower()


def test_examples_readme_indexes_all_reference_clients_and_protocol_contract():
    text = read(EXAMPLES_README)
    for marker in [
        "ios-swift/",
        "android-kotlin/",
        "twilio-media-streams/",
        "asterisk-audiofork/",
        "plugins/a0_voqualizer/ws_voqualizer",
        "voqualizer_init",
        "voqualizer_ready",
        "bearer_token",
        "voqualizer_audio_chunk",
        "voqualizer_user_text",
        "voqualizer_control",
        "A2 4-byte header",
        "voqualizer_error",
        "../webui/tester-store.js",
        "../docs/clients/portability.md",
    ]:
        assert marker in text


def test_examples_readme_preserves_no_external_runtime_constraints():
    text = read(EXAMPLES_README)
    for marker in [
        "iOS or Android simulators",
        "Xcode",
        "Android SDK",
        "Gradle",
        "Twilio accounts",
        "Asterisk installation",
        "Node dependency install",
        "external network",
        "live A0 backend",
    ]:
        assert marker in text


WEBUI_TESTER_HTML = ROOT / "webui" / "tester.html"


def test_modal_loaded_pages_use_absolute_module_imports():
    """Regression guard: pages opened via openModal() are loaded as blob: URLs,
    so relative ES module imports like './foo.js' fail to resolve. Both
    tester.html and providers.html must import their stores via the absolute
    plugin-served path (matching the working _memory plugin convention).
    """
    tester_text = WEBUI_TESTER_HTML.read_text(encoding="utf-8")
    providers_text = PROVIDERS_HTML.read_text(encoding="utf-8")

    assert "from '/plugins/a0_voqualizer/webui/tester-store.js'" in tester_text
    assert "from './tester-store.js'" not in tester_text

    assert "from '/plugins/a0_voqualizer/webui/config-store.js'" in providers_text
    assert "from './config-store.js'" not in providers_text


def test_modal_loaded_pages_defer_dom_init_until_attached():
    """Regression guard: A0's openModal() loads inline <script type=\"module\">
    blocks BEFORE appending the modal body's HTML nodes (see
    /a0/webui/js/components.js#importComponent). Calling document.getElementById
    at module top-level therefore returns null, producing
    \"Cannot set properties of null (setting 'textContent')\".
    Both tester.html and providers.html must wait for their DOM to be attached
    before initializing.
    """
    tester_text = WEBUI_TESTER_HTML.read_text(encoding="utf-8")
    providers_text = PROVIDERS_HTML.read_text(encoding="utf-8")

    for text in (tester_text, providers_text):
        assert "waitForDomAndInit" in text
        assert "requestAnimationFrame" in text
