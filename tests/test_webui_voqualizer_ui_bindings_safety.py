"""W59: preserved Voqualizer UI must keep controls bindable while Wyoming loads lazily."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / 'webui' / 'voqualizer.html'
JS = ROOT / 'webui' / 'voqualizer.js'
CLIENT = ROOT / 'webui' / 'wyoming' / 'wyoming-ws-client.js'


def test_canonical_html_has_prompt_clear_and_original_controls():
    src = HTML.read_text()
    for marker in (
        'id="voq-prompt-clear"',
        'class="voq-prompt-clear"',
        'id="voq-prompt-input"',
        'id="voq-send-button"',
        'id="voqualizer-mic-button"',
        'id="voqualizer-speaker-button"',
        'w60-logout-next-2026-06-09-1',
    ):
        assert marker in src, marker


def test_voqualizer_js_does_not_top_level_import_wyoming_client():
    src = JS.read_text()
    first_lines = '\n'.join(src.splitlines()[:8])
    assert 'wyoming-ws-client.js' not in first_lines
    assert 'async function loadWyomingWsClientFactory' in src
    assert "await import('/plugins/a0_voqualizer/webui/wyoming/wyoming-ws-client.js?v=w60-logout-next-2026-06-09-1')" in src
    assert 'bindPromptInput(state)' in src
    assert 'bindVoqualizerButtons()' in src


def test_shared_wyoming_client_uses_existing_framework_import_paths():
    src = CLIENT.read_text()
    assert "from '/vendor/socket.io.esm.min.js'" in src
    assert "import('/js/api.js')" in src
    assert "from '/socket.io/socket.io.esm.min.js'" not in src
    assert "import('/webui/js/api.js')" not in src
