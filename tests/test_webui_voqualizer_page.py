from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "webui" / "voqualizer.html"
CSS = ROOT / "webui" / "voqualizer.css"
JS = ROOT / "webui" / "voqualizer.js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_voqualizer_page_files_exist():
    assert HTML.exists()
    assert CSS.exists()
    assert JS.exists()


def test_voqualizer_page_identity_and_assets():
    html = read(HTML)
    assert "<title>Voqualizer</title>" in html
    assert 'data-voqualizer-page="standalone"' in html
    assert '/plugins/a0_voqualizer/webui/voqualizer.css' in html
    assert '/plugins/a0_voqualizer/webui/voqualizer.js' in html
    assert 'Voqualizer:' in html
    assert 'Context:' not in html


def test_voqualizer_page_required_regions_and_controls():
    html = read(HTML)
    assert 'id="voq-context-select"' in html
    assert 'id="voq-settings-button"' in html
    assert 'aria-label="Voqualizer settings"' in html
    assert 'id="voq-chat"' in html
    assert 'aria-label="Voqualizer chat transcript"' in html
    assert 'id="voq-prompt-input"' in html
    assert 'aria-label="Prompt input / text window"' in html
    assert 'id="voq-send-button"' in html
    assert 'id="voq-tts-button"' in html
    assert 'id="voq-asr-button"' in html


def test_voqualizer_page_action_order_is_prompt_send_tts_asr():
    html = read(HTML)
    prompt_i = html.index('id="voq-prompt-input"')
    send_i = html.index('id="voq-send-button"')
    tts_i = html.index('id="voq-tts-button"')
    asr_i = html.index('id="voq-asr-button"')
    assert prompt_i < send_i < tts_i < asr_i


def test_voqualizer_page_avoids_main_webgui_observer_dependencies():
    combined = "\n".join(read(path) for path in (HTML, CSS, JS))
    forbidden = [
        "voqualizer-response-observer",
        "__voqualizer_response_observer",
        "set_messages_after_loop",
        "chat-input-box-end",
        "step-action-buttons",
        "kokoro_tts",
        "whisper_stt",
        "/js/tts-service.js",
        "/js/stt-service.js",
    ]
    for token in forbidden:
        assert token not in combined


def test_voqualizer_page_static_shell_debug_marker():
    js = read(JS)
    assert "m2-context-picker" in js
    assert "__voqualizer_page" in js
    assert "standalone: true" in js
    assert "milestone: 2" in js



def test_voqualizer_context_picker_wiring():
    js = read(JS)
    assert "import { callJsonApi } from '/js/api.js';" in js
    assert "plugins/a0_voqualizer/voqualizer_admin" in js
    assert "action: 'contexts'" in js
    assert "a0_voqualizer.standalone.selected_context_id" in js
    assert "function normalizeContext" in js
    assert "function normalizeContexts" in js
    assert "async function fetchContexts" in js
    assert "async function loadContextPicker" in js
    assert "Loading contexts…" in js
    assert "No contexts found" in js
    assert "Contexts unavailable" in js


def test_voqualizer_context_picker_debug_fields():
    js = read(JS)
    for token in [
        "adminEndpoint",
        "selectedContextStorageKey",
        "contextsLoading",
        "contextError",
        "contextCount",
        "selectedContextId",
    ]:
        assert token in js
