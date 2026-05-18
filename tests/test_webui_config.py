from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "webui" / "config-store.js"
# A6.3 / A8.4 standalone provider editor and provider-test buttons now ship as
# webui/providers.html so that webui/config.html can serve as the A0 Settings
# modal Alpine fragment (loaded as an <x-component>). The full standalone
# editor remains accessible from the Settings modal via an Open Providers editor
# button.
HTML = ROOT / "webui" / "providers.html"


def store_source() -> str:
    return STORE.read_text(encoding="utf-8")


def html_source() -> str:
    return HTML.read_text(encoding="utf-8")


def test_a63_artifacts_exist():
    assert STORE.is_file()
    assert HTML.is_file()


def test_config_store_uses_existing_admin_endpoint_and_actions():
    text = store_source()
    assert "/api/plugins/a0_voqualizer/voqualizer_admin" in text
    assert "admin('config')" in text
    assert "admin('save'" in text
    assert "admin('test_provider'" in text
    assert "credentials: 'same-origin'" in text
    assert "Content-Type': 'application/json'" in text


def test_config_store_supports_provider_crud_and_defaults():
    text = store_source()
    for symbol in [
        "addProvider",
        "updateProvider",
        "removeProvider",
        "setDefault",
        "normalizeProvider",
        "validateProvider",
        "overlayFromConfig",
    ]:
        assert symbol in text
    assert "At least one ${side.toUpperCase()} provider is required" in text
    assert "Duplicate provider name" in text


def test_config_store_knows_asr_and_tts_provider_types():
    text = store_source()
    for provider_type in ["whisper", "faster-whisper", "openai-compatible", "localai", "piper", "mock"]:
        assert provider_type in text
    assert "PROVIDER_SIDES = ['asr', 'tts']" in text


def test_config_store_has_provider_test_button_state_model():
    text = store_source()
    assert "testResults" in text
    assert "async function testProvider(side, name)" in text
    assert "state.testResults[key] = { ok: null, message: 'running'" in text
    assert "data.ok !== false" in text
    assert "response: data" in text


def test_config_html_renders_provider_crud_controls():
    text = html_source()
    for dom_id in [
        'id="load"',
        'id="save"',
        'id="add-asr"',
        'id="add-tts"',
        'id="asr-providers"',
        'id="tts-providers"',
        'id="provider-template"',
    ]:
        assert dom_id in text
    for klass in [
        "provider-name",
        "provider-type",
        "provider-default",
        "provider-enabled",
        "provider-options",
        "provider-test",
        "provider-delete",
    ]:
        assert klass in text


def test_config_html_binds_store_crud_save_and_test_provider_buttons():
    text = html_source()
    assert "createVoqualizerConfigStore" in text
    assert "store.load()" in text
    assert "store.save()" in text
    assert "store.addProvider('asr')" in text
    assert "store.addProvider('tts')" in text
    assert "store.updateProvider(side, originalName" in text
    assert "store.removeProvider(side, provider.name)" in text
    assert "store.setDefault(side, provider.name)" in text
    assert "store.testProvider(side, provider.name)" in text


def _function_body(text: str, name: str) -> str:
    start = text.index(f"function {name}")
    next_start = text.find("\n  function ", start + 1)
    if next_start == -1:
        next_start = text.find("\n  async function ", start + 1)
    return text[start: next_start if next_start != -1 else len(text)]


def test_config_store_dirty_tracking_recomputes_against_loaded_overlay():
    text = store_source()
    assert "loadedOverlay: overlayFromConfig(initialConfig)" in text
    assert "function sameJson(a, b)" in text
    assert "function computeDirty()" in text
    assert "return !sameJson(overlayFromConfig(state.config), state.loadedOverlay);" in text
    assert "function markDirty()" in text
    assert "setState({ dirty: computeDirty() });" in text


def test_config_store_structural_changes_mark_dirty_immediately():
    text = store_source()
    for fn in ["addProvider", "removeProvider", "updateProvider", "setDefault"]:
        body = _function_body(text, fn)
        assert "markDirty();" in body, fn
        assert "setState({ dirty: true });" not in body, fn


def test_config_store_revert_and_save_update_loaded_baseline():
    text = store_source()
    assert "loadedOverlay: overlayFromConfig(loadedConfig)" in text
    assert "loadedOverlay: overlayFromConfig(savedConfig)" in text
    assert "dirty: false" in _function_body(text, "save")
    assert "const data = await admin('save', { overlay: overlayFromConfig(state.config) });" in text



def test_providers_page_save_button_is_not_dirty_gated():
    text = html_source()
    assert "$('save').disabled = !state.dirty || state.saving" not in text
    assert "$('save').disabled = state.saving" in text
    assert "No unsaved changes" in text
    assert "Save will rewrite the current provider config" in text
    assert '<button id="save">Save providers</button>' in text


def test_config_store_save_always_uses_schema_validating_admin_path():
    text = store_source()
    save_body = _function_body(text, "save")
    assert "if (!state.dirty)" not in save_body
    assert "return;" not in save_body.split("const data = await admin('save'", 1)[0]
    assert "const data = await admin('save', { overlay: overlayFromConfig(state.config) });" in save_body
    assert "loadedOverlay: overlayFromConfig(savedConfig)" in save_body
    assert "dirty: false" in save_body
