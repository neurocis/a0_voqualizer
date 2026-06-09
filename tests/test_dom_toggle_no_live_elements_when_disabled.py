"""DOM integration toggle should not place live Voqualizer elements in main chat DOM when disabled."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-buttons.html'
WYOMING = ROOT / 'extensions' / 'webui' / 'chat-input-box-end' / 'voqualizer-wyoming-buttons.html'
CANONICAL = ROOT / 'webui' / 'voqualizer.html'
JS = ROOT / 'webui' / 'voqualizer.js'


def test_legacy_main_dom_buttons_are_template_gated_until_toggle_enabled():
    src = LEGACY.read_text()
    assert 'domIntegrationEnabled: false' in src
    assert '<template x-if="domIntegrationEnabled">' in src
    assert "domIntegrationEnabled = true" in src
    assert 'side-dom-toggle-2026-06-08-4' in src
    # Buttons remain in inert template source only; x-init returns before enabling when disabled.
    disabled_branch = src[src.index('if (data && data.enabled === false)'):src.index('try { document.body.classList.add')]
    assert 'domIntegrationEnabled = true' not in disabled_branch
    assert 'init();' not in disabled_branch


def test_wyoming_main_dom_buttons_are_template_gated_and_client_loads_after_toggle():
    src = WYOMING.read_text()
    assert 'domIntegrationEnabled: false' in src
    assert '<template x-if="domIntegrationEnabled">' in src
    assert 'this.domIntegrationEnabled = true' in src
    assert '_loadWyomingDomClientFactory' in src
    assert 'side-dom-toggle-2026-06-08-4' in src
    first_script_lines = '\n'.join(src.split('<script type="module">', 1)[1].splitlines()[:8])
    assert "import { createWyomingWsClient }" not in first_script_lines
    assert "from '/plugins/a0_voqualizer/webui/wyoming/wyoming-ws-client.js" not in first_script_lines
    disabled_branch = src[src.index('if (domStatus && domStatus.enabled === false)'):src.index('} catch (_) { /* fall through')]
    assert 'this.domIntegrationEnabled = true' not in disabled_branch
    assert '_loadWyomingDomClientFactory' not in disabled_branch


def test_standalone_pages_keep_voqualizer_controls_regardless_main_dom_toggle():
    html = CANONICAL.read_text()
    js = JS.read_text()
    for marker in ('voqualizer-speaker-button', 'voqualizer-mic-button', 'voq-send-button', 'voq-prompt-clear'):
        assert marker in html
    assert 'w59-ui-bindings-safe-wyoming-2026-06-08-1' in html
    assert 'loadWyomingWsClientFactory' in js


def test_main_dom_extension_disabled_path_does_not_remove_core_wrappers():
    combined = LEGACY.read_text() + '\n' + WYOMING.read_text()
    assert "closest('x-component')" not in combined
    assert "closest?.('x-component')" not in combined
    assert '$el.remove()' not in combined
    assert 'this.$root?.remove' not in combined
