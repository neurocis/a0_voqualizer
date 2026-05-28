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
    assert 'voqualizer.css?v=' in html
    assert 'voqualizer.js?v=' in html
    assert 'Voqualizer:' in html
    assert 'Context:' not in html


def test_voqualizer_mobile_version_marker_in_css():
    css = read(CSS)
    assert 'voq-mobile-version:' in css


def test_voqualizer_mobile_viewport_meta():
    html = read(HTML)
    assert 'width=device-width, initial-scale=1.0, maximum-scale=5.0' in html
    assert 'viewport-fit=cover' in html


def test_voqualizer_page_required_regions_and_controls():
    html = read(HTML)
    assert 'id="voq-context-select"' in html
    assert 'id="voq-settings-button"' in html
    assert 'id="voq-logout-button"' in html
    assert 'aria-label="Open Voqualizer provider settings"' in html
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
    assert "m7-word-highlight" in js
    assert "__voqualizer_page" in js
    assert "standalone: true" in js
    assert "milestone: 7" in js



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



def test_voqualizer_typed_prompt_path():
    js = read(JS)
    assert "const MESSAGE_ENDPOINT = 'plugins/a0_voqualizer/voqualizer_message_async';" in js
    assert "const POLL_ENDPOINT = 'poll';" in js
    assert "callJsonApiWithDiagnostics(MESSAGE_ENDPOINT, {" in js
    assert "context: contextId" in js
    assert "message_id: messageId" in js
    assert "callJsonApiWithDiagnostics(POLL_ENDPOINT, {" in js
    assert "log_from: logFrom" in js
    assert "function runPollLoop" in js
    assert "function submitPrompt" in js
    assert "function renderUserBubble" in js
    assert "function renderOrUpdateLogBubble" in js
    assert "renderErrorRow" in js
    combined = js + read(CSS)
    assert "voq-bubble--user" in combined
    assert "voq-bubble--assistant" in combined


def test_voqualizer_typed_prompt_no_main_gui_coupling():
    js = read(JS)
    for forbidden in [
        "Alpine.store('chats'",
        "globalThis.sendMessage",
        "set_messages_after_loop",
        "chat-input-box-end",
        "step-action-buttons",
        "voqualizer-response-observer",
        "/js/tts-service.js",
        "/js/stt-service.js",
    ]:
        assert forbidden not in js, forbidden



def test_voqualizer_tts_wiring():
    js = read(JS)
    assert "voqualizer-audio.js" in js
    assert "plugins/a0_voqualizer/ws_voqualizer" in js
    assert "a0_voqualizer.standalone.tts_enabled" in js
    assert "function connectVoq" in js
    assert "async function initVoqSession" in js
    assert "async function speakText" in js
    assert "function cancelInflightTts" in js
    assert "function disconnectVoq" in js
    assert "function handleTtsChunk" in js
    assert "function handleTtsDone" in js
    assert "function maybeSpeakResponse" in js
    assert "voqualizer_user_text" in js
    assert "voqualizer_init" in js
    assert "set_tts_enabled" in js
    assert "cancel_tts" in js


def test_voqualizer_tts_no_legacy_imports():
    js = read(JS)
    for forbidden in [
        "tester-store.js",
        "conversation-mode.js",
        "Alpine.store('chats'",
        "globalThis.sendMessage",
        "voqualizer-response-observer",
        "set_messages_after_loop",
    ]:
        assert forbidden not in js, forbidden



def test_voqualizer_asr_wiring():
    js = read(JS)
    for token in [
        "WORKLET_URL",
        "WORKLET_PROCESSOR",
        "voqualizer-mic-processor",
        "a0_voqualizer.standalone.asr_enabled",
        "asr_submit_mode",
        "frontend_prompt",
        "async function startAsrCapture",
        "async function stopAsrCapture",
        "function handleAsrPartial",
        "async function handleAsrFinal",
        "async function routeAsrFinal",
        "function maybeLocalBargeIn",
        "voqualizer_asr_partial",
        "voqualizer_asr_final",
        "audioChunkPayload",
        "framePcm16",
        "submitPrompt(pageState)",
    ]:
        assert token in js, token


def test_voqualizer_asr_no_legacy_imports():
    js = read(JS)
    for forbidden in [
        "tester-store.js",
        "conversation-mode.js",
        "Alpine.store('chats'",
        "globalThis.sendMessage",
        "voqualizer-response-observer",
        "set_messages_after_loop",
        "/js/stt-service.js",
    ]:
        assert forbidden not in js, forbidden



def test_voqualizer_accessibility_regions():
    html = read(HTML)
    assert 'role="log"' in html
    assert 'aria-relevant="additions text"' in html
    assert 'aria-atomic="false"' in html
    assert 'tabindex="0"' in html
    assert 'id="voq-status"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-describedby="voq-status' in html
    assert 'id="voq-input-help"' in html


def test_voqualizer_buttons_accessibility():
    html = read(HTML)
    js = read(JS)
    assert 'aria-label="Send prompt"' in html
    assert 'aria-label="Toggle text to speech"' in html
    assert 'aria-label="Toggle speech recognition"' in html
    assert "aria-pressed" in js
    assert "aria-disabled" in js
    assert "setAttribute('title'" in js or 'setAttribute("title"' in js
    assert "Speak responses (on)" in js
    assert "Microphone input (on)" in js


def test_voqualizer_status_and_jump_latest():
    html = read(HTML)
    js = read(JS)
    css = read(CSS)
    assert 'id="voq-status"' in html
    assert 'id="voq-jump-latest"' in html
    assert "function updateJumpLatest" in js
    assert "function scrollTranscriptToBottom" in js
    assert "function bindTranscriptControls" in js
    assert "Jump to latest" in html
    assert ".voq-jump-latest" in css
    assert 'role="status"' in html
    assert "lastStatus" in js
    assert "lastStatusLevel" in js


def test_voqualizer_responsive_polish_css():
    css = read(CSS)
    for token in [
        "font-size: 16px",
        "@media (max-width: 768px)",
        "@media (max-width: 480px)",
        "@media (prefers-reduced-motion: reduce)",
        "Common-denominator mobile readability pass",
        "Hide decorative status row",
        "font-size: 18px",
        "min-height: 2.75rem",
        "grid-template-columns: 1fr",
        "grid-template-columns: repeat(3, minmax(2.75rem, 1fr))",
        "padding-left: 0.5rem",
        "max-width: 100vw",
        "max-height: 30dvh",
        "overflow-x: hidden",
        "safe-area-inset-bottom",
        "position: sticky",
        ".voq-word",
        "font-size: inherit",
        ".voq-sr-only",
        ":focus-visible",
    ]:
        assert token in css, token


def test_voqualizer_settings_link_and_debug():
    html = read(HTML)
    js = read(JS)
    assert '/plugins/a0_voqualizer/webui/providers.html' in html
    assert "Open Voqualizer provider settings" in html
    assert "lastSettingsClickAt" in js
    assert "lastLogoutClickAt" in js
    assert "Opening Voqualizer provider settings" in js


def test_voqualizer_api_diagnostics():
    js = read(JS)
    assert "async function callJsonApiWithDiagnostics" in js
    assert "lastApiStage" in js
    assert "lastApiEndpoint" in js
    assert "lastApiPayload" in js
    assert "lastApiError" in js
    assert "message_async" in js
    assert "poll" in js


def test_voqualizer_message_async_proxy_endpoint():
    proxy = ROOT / "api" / "voqualizer_message_async.py"
    text = read(proxy)
    assert "class VoqualizerMessageAsync" in text
    assert "from agent import UserMessage" in text
    assert "context.communicate" in text
    assert "Message received." in text
    assert "requires_auth" in text


def test_voqualizer_logout_link_and_debug():
    html = read(HTML)
    js = read(JS)
    assert 'id="voq-logout-button"' in html
    assert 'href="/logout"' in html
    assert 'aria-label="Log out of Agent Zero"' in html
    assert '>logout<' in html
    assert "lastLogoutClickAt" in js
    assert "Logging out" in js



def test_voqualizer_cx_stream_handlers_present():
    js = (Path(__file__).resolve().parents[1] / "webui" / "voqualizer.js").read_text()
    for token in [
        "voqualizer_cx_stream_start",
        "voqualizer_cx_token",
        "voqualizer_cx_stream_final",
        "voqualizer_cx_stream_error",
        "handleCxStreamStart",
        "handleCxToken",
        "handleCxStreamFinal",
        "handleCxStreamError",
        "findOrCreateCxBubble",
        "cxBubbleForLogItem",
        "cxStreamCapability",
        "data-cx-stream-id" if False else "cxStreamId",
        "dataset.streaming",
    ]:
        assert token in js, token


def test_voqualizer_cx_stream_no_main_gui_coupling():
    js = (Path(__file__).resolve().parents[1] / "webui" / "voqualizer.js").read_text()
    for forbidden in [
        "Alpine.store('chats'",
        "globalThis.sendMessage",
        "set_messages_after_loop",
        "voqualizer-response-observer",
        "tester-store.js",
        "conversation-mode.js",
    ]:
        assert forbidden not in js, forbidden


def test_voqualizer_word_plan_handler_present():
    js = (Path(__file__).resolve().parents[1] / "webui" / "voqualizer.js").read_text()
    for token in [
        "voqualizer_tts_word_plan",
        "handleTtsWordPlan",
        "renderWordSpansInto",
        "finalizeWordHighlight",
        "clearAllWordHighlights",
        "wordPlanCapability",
        "voq-word--active",
        "playbackStartByUtteranceId",
        "registerWordPlanBubble",
        "requestAnimationFrame",
    ]:
        assert token in js, token
    css = (Path(__file__).resolve().parents[1] / "webui" / "voqualizer.css").read_text()
    for token in [".voq-word", ".voq-word--active"]:
        assert token in css, token

def test_voqualizer_realtime_cleanup_helpers_present():
    js = read(JS)
    for token in [
        'function clearCxStreamState',
        'function cxActiveStreamCount',
        'clearAllWordHighlights();',
        'lastRealtimeDisconnectAt',
        'cxActiveStreamCount',
        'clearCxStreamState({ keepCapability: true })',
    ]:
        assert token in js


def test_voqualizer_late_word_plan_guard_present():
    js = read(JS)
    for token in [
        'endedByUtteranceId.has(utteranceId)',
        'lastLateWordPlanAt',
        'lastLateWordPlanUtteranceId',
        'lastTtsWordPlanEventAt',
    ]:
        assert token in js


def test_voqualizer_active_word_debug_fields_present():
    js = read(JS)
    for token in [
        'activeWordUtteranceId',
        'activeWordIndex',
        'wordPlan.activeIndexByUtteranceId',
    ]:
        assert token in js


def test_voqualizer_hero_default_context_helper_present():
    js = read(JS)
    for token in [
        'async function fetchHeroDefaultContextId',
        'plugins/a0_superordinates/superordinate_config',
        'hero_mode_designated_hero',
        'heroDefaultApplied',
        'heroDefaultContextId',
    ]:
        assert token in js, token
