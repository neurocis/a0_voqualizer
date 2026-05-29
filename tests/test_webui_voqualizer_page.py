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
    assert 'id="voq-logout-button"' in html
    assert 'id="voq-chat"' in html
    assert 'aria-label="Voqualizer chat transcript"' in html
    assert 'id="voq-prompt-input"' in html
    assert 'aria-label="Prompt input / text window"' in html
    assert 'id="voq-send-button"' in html
    assert 'id="voqualizer-speaker-button"' in html
    assert 'id="voqualizer-mic-button"' in html


def test_voqualizer_page_action_order_is_prompt_send_mic_speaker(html=None):
    from pathlib import Path
    html = html if html is not None else Path(HTML).read_text()
    speaker_i = html.index('id="voqualizer-speaker-button"')
    mic_i = html.index('id="voqualizer-mic-button"')
    send_i = html.index('id="voq-send-button"')
    assert speaker_i < mic_i < send_i


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
    assert "m8-tts-retry-narrow" in js
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
    # M8: conversation-mode.js is now an approved import; only the legacy
    # main-WebGUI coupling tokens remain forbidden.
    for forbidden in [
        "tester-store.js",
        "Alpine.store('chats'",
        "Alpine.store('chatInput'",
        "globalThis.sendMessage",
        "voqualizer-response-observer",
        "set_messages_after_loop",
        "/js/tts-service.js",
        "/js/stt-service.js",
    ]:
        assert forbidden not in js, forbidden



def test_voqualizer_asr_wiring_m8_parity():
    js = read(JS)
    # M8: ASR is now driven by createVoqualizerStore() from conversation-mode.js.
    for token in [
        "createVoqualizerStore",
        "TAP_HOLD_THRESHOLD_MS",
        "STATE_CONVERSATIONAL",
        "STATE_PTT_ACTIVE",
        "STATE_CONNECTING",
        "STATE_STOPPING",
        "STATE_ERROR",
        "/plugins/a0_voqualizer/webui/conversation-mode.js",
        "voqualizer-mic-button",
        "voqualizer-speaker-button",
        "voqualizer-active",
        "voqualizer-ptt",
        "voqualizer-connecting",
        "voqualizer-error",
        "voqualizer-speech-detected",
        "voqualizer-vu-clipped",
        "voqualizer-tts-off",
        "--voqualizer-vu-level",
        "--voqualizer-vu-opacity",
        "voqualizer_asr_final",
        "submitPrompt(pageState)",
        "setContextId",
        "onAsrFinal",
    ]:
        assert token in js, token
    html = read(HTML)
    assert "voqualizer-buttons-row" in html


def test_voqualizer_asr_no_legacy_imports():
    js = read(JS)
    # M8: conversation-mode.js is now an approved import; only the legacy
    # main-WebGUI coupling tokens and tester-store.js remain forbidden.
    for forbidden in [
        "tester-store.js",
        "Alpine.store('chats'",
        "Alpine.store('chatInput'",
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
    assert 'aria-label="Voqualizer TTS is on. Click to mute TTS for this chat."' in html
    assert 'aria-label="Voqualizer mic off. Tap for conversation. Hold for push-to-talk."' in html
    assert "aria-pressed" in js
    assert "aria-disabled" in js
    assert "setAttribute('title'" in js or 'setAttribute("title"' in js
    assert "Voqualizer TTS is on. Click to mute TTS for this chat." in js
    assert "Voqualizer mic off. Tap for conversation. Hold for push-to-talk." in js


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
    js = read(JS)
    assert "lastSettingsClickAt" in js
    assert "lastLogoutClickAt" in js


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

def test_voqualizer_send_indicator_present():
    js = read(JS)
    for token in [
        'sendIndicator',
        'setSendIndicatorState',
        'resetSendIndicatorOnInteraction',
        "dataset.sendState",
        'sendIndicatorState',
    ]:
        assert token in js, token
    css = (Path(__file__).resolve().parents[1] / 'webui' / 'voqualizer.css').read_text()
    for token in [
        'data-send-state="processing"',
        'data-send-state="success"',
        'voq-send-pulse',
    ]:
        assert token in css, token

def test_voqualizer_tts_ack_fallback_playback_present():
    js = read(JS)
    for token in [
        'function handleTtsAckFallback',
        'handleTtsAckFallback(ack, id)',
        'ack.tts_chunks',
        'ack.delivery_fallback',
        'lastAckTtsFallbackAt',
        'lastAckTtsFallbackChunks',
        'lastAckTtsFallbackReason',
        'lastAckTtsPushedEmitCount',
        'lastAckTtsSenderPresent',
        'handleTtsChunk(data)',
        'handleTtsWordPlan(ack.tts_word_plan)',
        'handleTtsDone(ack.tts_done)',
    ]:
        assert token in js, token

def test_voqualizer_tts_enabled_ring_present():
    js = read(JS)
    css = read(CSS)
    for token in [
        'voqualizer-tts-on',
        'data-tts-enabled',
        "speaker.classList.toggle('voqualizer-tts-on'",
    ]:
        assert token in js, token
    for token in [
        '.voqualizer-speaker.voqualizer-tts-on',
        'data-tts-enabled="true"',
        'rgba(34, 197, 94',
    ]:
        assert token in css, token

def test_voqualizer_preloads_last_monologue_result():
    js = read(JS)
    html = read(HTML)
    css = read(CSS)
    for token in [
        "PRELOAD_MONOLOGUE_LOG_FROM",
        "function renderPreloadedResponseBubble",
        "async function preloadLastMonologueResult",
        "pollOnce(contextId, PRELOAD_MONOLOGUE_LOG_FROM)",
        "item.type === 'response'",
        "lastMonologuePreloadAt",
        "lastMonologuePreloadFound",
        "lastMonologuePreloadLogId",
        "lastMonologuePreloadTextLength",
        "lastMonologuePreloadError",
        "dataset.preloaded = 'true'",
        "No previous monologue result for this context yet.",
        "Loading latest monologue result…",
    ]:
        assert token in js, token
    assert "m8-tts-retry-narrow-2026-05-29-48" in html
    assert "m8-tts-retry-narrow-2026-05-29-48" in css

def test_voqualizer_submit_feedback_before_optional_voq_init():
    js = read(JS)
    assert "m8-tts-retry-narrow" in js
    assert "lastSubmitUiEchoAt" in js
    assert "lastSubmitVoqInitError" in js
    assert "Do not block visible submit feedback" in js
    assert "const voqInitPromise = (warmVoqSessionForContext(contextId, 'submit')" in js
    assert "renderUserBubble(state, { id: messageId, text });" in js
    assert js.index("renderUserBubble(state, { id: messageId, text });") < js.index("const voqInitPromise = (warmVoqSessionForContext(contextId, 'submit')")

def test_voqualizer_preload_warms_realtime_tts_session():
    js = read(JS)
    html = read(HTML)
    css = read(CSS)
    for token in [
        "function warmVoqSessionForContext",
        "warmVoqSessionForContext(contextId, 'monologue_preload')",
        "warmVoqSessionForContext(contextId, 'submit')",
        "lastVoqWarmupAt",
        "lastVoqWarmupReadyAt",
        "lastVoqWarmupContextId",
        "lastVoqWarmupReason",
        "lastVoqWarmupSessionId",
        "lastVoqWarmupReady",
        "lastVoqWarmupError",
        "removes the cold-start cost",
    ]:
        assert token in js, token
    assert "m8-tts-retry-narrow-2026-05-29-48" in html
    assert "m8-tts-retry-narrow-2026-05-29-48" in css


def test_voqualizer_send_button_circular_and_brighter_pulse():
    css = read(CSS)
    js = read(JS)
    for token in [
        '#voq-send-button.voq-action-button {',
        'border-radius: 50%;',
        'rgba(96, 165, 250',
    ]:
        assert token in css, token
    assert 'POLL_HARD_TIMEOUT_MS' not in js
    assert 'did not respond within the timeout window' not in js


def test_voqualizer_actions_portrait_thirds_present():
    css = read(CSS)
    js = read(JS)
    for token in [
        '@media (orientation: portrait)',
        '.voq-actions[data-wrapped="true"]',
        'grid-template-columns: repeat(3',
    ]:
        assert token in css, token
    for token in [
        'function updateActionsWrapped',
        "actions.setAttribute('data-wrapped', 'true')",
        'actionsWrapped',
        'orientationchange',
    ]:
        assert token in js, token


def test_voqualizer_send_success_fires_on_response():
    js = read(JS)
    css = read(CSS)
    for token in [
        "sendIndicator.wasBusy = false",
        "setSendIndicatorState('success')",
        "M8: response seen",
        "voq-send-success",
    ]:
        assert token in js, token
    for token in [
        '.voq-action-button.voq-send-success',
        '.voq-action-button.voq-send-success[disabled]',
    ]:
        assert token in css, token


def test_voqualizer_send_success_is_solid_not_pulsing():
    css = read(CSS)
    assert 'animation: none !important;' in css
    assert '.voq-action-button.voq-send-success' in css
    assert 'box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.6) !important;' in css


def test_voqualizer_send_resets_on_prompt_interaction():
    js = read(JS)
    for token in [
        "button.classList.remove('voq-send-success')",
        "sendIndicator.state === 'success' || sendIndicator.wasBusy",
        "prompt.addEventListener('click', () => { resetSendIndicatorOnInteraction(); updateSendButton(state); });",
        "prompt.addEventListener('focus', () => { resetSendIndicatorOnInteraction(); updateSendButton(state); });",
    ]:
        assert token in js, token


def test_voqualizer_topbar_has_fullscreen_button():
    html = read(HTML)
    js = read(JS)
    css = read(CSS)
    assert 'id="voq-fullscreen-button"' in html
    assert 'id="voq-refresh-button"' not in html
    assert 'id="voq-settings-button"' not in html
    assert 'function bindFullscreenButton' in js
    assert 'document.fullscreenElement' in js
    assert '.voq-fullscreen-button' in css


def test_voqualizer_context_burger_menu_present():
    html = read(HTML)
    js = read(JS)
    css = read(CSS)
    for token in ['id="voq-context-menu-button"', 'id="voq-context-menu"', 'class="voq-context-menu-wrap"', 'hidden aria-hidden="true"']:
        assert token in html, token
    for token in ['function renderContextMenu', 'function openContextMenu', 'function closeContextMenu', 'function bindContextMenuButton', 'li.dataset.active']:
        assert token in js, token
    for token in ['.voq-context-menu-wrap', '.voq-context-menu', '.voq-context-menu-button[aria-expanded="true"]']:
        assert token in css, token


def test_voqualizer_topbar_order_fullscreen_before_burger():
    html = read(HTML)
    fs = html.index('id="voq-fullscreen-button"')
    bg = html.index('id="voq-context-menu-button"')
    assert fs < bg


def test_voqualizer_send_button_never_disabled():
    js = read(JS)
    assert "button.disabled = false" in js
    assert "button.setAttribute('aria-disabled', 'false')" in js
    assert "Processing — click to send another prompt" in js
    assert "A stale busy/submitting latch should not" in js
    assert "if (state.isSubmitting) {" in js
    assert "resetSendIndicatorOnInteraction();" in js
    assert "select.disabled = false" in js


def test_voqualizer_conversation_store_invokes_asr_final_callback():
    conv = read(ROOT / "webui" / "conversation-mode.js")
    js = read(JS)
    assert "asr_submit_mode: this._onAsrFinal ? 'frontend_prompt' : 'context_bridge'" in conv
    assert "this._onAsrFinal(text, data)" in conv
    assert "asr_final_callback_error" in conv
    assert "onAsrFinal: (text) => { void routeStoreAsrFinal(text); }" in js
    assert "await submitPrompt(pageState)" in js


def test_voqualizer_asr_ack_final_fallback_submits_standalone():
    conv = read(ROOT / "webui" / "conversation-mode.js")
    assert "this._onAsrFinal(finalText, data)" in conv
    assert "asr_ack_final_callback_error" in conv
    assert "frontend_prompt mode" in conv
    assert "voqualizer_asr_final" in conv
    assert "DOM extension/context_bridge path" in conv


def test_voqualizer_asr_mic_requires_session_before_capture():
    conv = read(ROOT / "webui" / "conversation-mode.js")
    assert "mic_wait_for_voqualizer_session" in conv
    assert "mic_start_without_session_token" in conv
    assert "lastAudioFrameDropReason" in conv
    assert "audioFramesDropped" in conv
    assert "bearer_token_missing" in conv


def test_voqualizer_mobile_asr_debug_copy_button():
    html = read(HTML)
    js = read(JS)
    css = read(CSS)
    for token in ['id="voq-asr-debug-button"', 'bug_report', 'Copy ASR debug state']:
        assert token in html, token
    for token in ['function buildAsrDebugLines', 'function copyAsrDebugLines', 'function bindAsrDebugButton', '===VOQ_ASR_LINES===', 'lastAsrDebugCopyAt', 'frames_sent=', 'ack_final=']:
        assert token in js, token
    for token in ['.voq-asr-debug-button', 'data-copied']:
        assert token in css, token


def test_voqualizer_send_response_clears_submitting_latch():
    js = read(JS)
    assert "Once the final response has arrived" in js
    assert "state.isSubmitting = false;" in js
    assert "globalThis.__voqualizer_page.isSubmitting = false;" in js
    assert "incorrectly flip the send icon back to processing" in js


def test_voqualizer_standalone_suppresses_main_context_polling():
    conv = read(ROOT / "webui" / "conversation-mode.js")
    js = read(JS)
    assert "_suppressContextPolling: !!options.suppressContextPolling" in conv
    assert "if (!this._suppressContextPolling)" in conv
    assert "suppressContextPolling: true" in js
    assert "suppressTts: true" in js
    assert "onAsrFinal: (text) => { void routeStoreAsrFinal(text); }" in js


def test_voqualizer_asr_vad_sensitivity_and_debug_ack():
    ws = read(ROOT / "api" / "ws_voqualizer.py")
    js = read(JS)
    assert 'asr_speech_rms", 0.01' in ws
    assert 'asr_vad_last_rms' in ws
    assert 'asr_vad_peak_rms' in ws
    assert 'asr_vad_speech_rms' in ws
    assert 'asr_vad_buffered_chunks' in ws
    assert 'vad_rms=' in js
    assert 'vad_threshold=' in js


def test_voqualizer_asr_vad_threshold_matches_normalized_mobile_audio():
    ws = read(ROOT / "api" / "ws_voqualizer.py")
    assert 'asr_speech_rms", 0.01' in ws
    assert 'asr_vad_peak_rms' in ws
    assert 'asr_vad_speech_rms' in ws


def test_voqualizer_asr_debug_hidden_and_hamburger_long_press_toggle():
    html = read(HTML)
    js = read(JS)
    css = read(CSS)
    assert 'id="voq-asr-debug-button"' in html
    assert 'hidden' in html
    for token in ['function setAsrDebugVisible', 'function toggleAsrDebugVisible', 'hamburger_double_tap', 'doubleTapFired', 'DOUBLE_TAP_MS', 'asrDebugVisible', 'lastAsrDebugToggleAt']:
        assert token in js, token
    for token in ['.voq-asr-debug-button[hidden]', 'data-debug-visible']:
        assert token in css, token


def test_voqualizer_asr_debug_double_tap_toggle():
    js = read(JS)
    for token in ['hamburger_double_tap', 'doubleTapFired', 'DOUBLE_TAP_MS', 'double tap to show debug button', 'double tap to hide debug button']:
        assert token in js, token
    assert 'hamburger_long_press' not in js
    assert 'longPressTimer' not in js


def test_voqualizer_header_uses_context_friendly_name():
    html = read(HTML)
    js = read(JS)
    assert 'id="voq-header-context-name"' in html
    for token in ['function contextLabelForId', 'function updateHeaderContextName', 'headerContextName', 'lastHeaderContextNameAt']:
        assert token in js, token
    assert "match.name || match.label || match.id" in js
    assert 'updateHeaderContextName(selectedContextId)' in js


def test_voqualizer_bottom_actions_order_tts_mic_send():
    html = read(HTML)
    speaker_i = html.index('id="voqualizer-speaker-button"')
    mic_i = html.index('id="voqualizer-mic-button"')
    send_i = html.index('id="voq-send-button"')
    assert speaker_i < mic_i < send_i


def test_voqualizer_context_menu_is_ten_percent_less_width():
    css = read(CSS)
    assert 'min-width: 271px;' in css
    assert 'width: max-content;' in css
    assert 'max-width: min(82vw, 397px);' in css


def test_voqualizer_header_drops_ctxid_from_title_name():
    js = read(JS)
    assert "if (match) return match.name || match.label || match.id || 'Voqualizer';" in js
    assert "if (match) return match.label || match.name || match.id || 'Voqualizer';" not in js


def test_voqualizer_tts_audio_unlock_handlers():
    js = read(JS)
    for token in ['function resumeAudioContext', 'function installTtsAudioUnlockHandlers', 'lastAudioResumeAt', 'lastAudioResumeReason', 'lastAudioResumeError']:
        assert token in js, token
    for token in ["resumeAudioContext('speakText')", "ensureAudioContext('playPcmChunk')", "document.addEventListener('pointerdown'", "document.addEventListener('touchstart'", "document.addEventListener('keydown'"]:
        assert token in js, token


def test_voqualizer_tts_debug_clipboard_button():
    html = read(HTML)
    js = read(JS)
    css = read(CSS)
    for token in ['id="voq-tts-debug-button"', 'Copy TTS debug state', 'copy tts debug']:
        assert token in html, token
    for token in ['function buildTtsDebugLines', 'function copyTtsDebugLines', '===VOQ_TTS_LINES===', 'lastTtsDebugCopyAt', 'lastDirectTtsAck', 'lastAckTtsFallbackChunks', 'lastPlaybackStartAt', 'lastAudioResumeAt']:
        assert token in js, token
    for token in ['.voq-tts-debug-button', '.voq-tts-debug-button[hidden]', 'double tap to show debug buttons']:
        assert token in (css + js), token


def test_voqualizer_tts_trigger_diagnostics_and_fallback_id():
    js = read(JS)
    for token in ['function stableTextHash', 'lastTtsTriggerAt', 'lastTtsSkipReason', 'lastTtsSpeakQueuedAt', 'lastTtsTriggerFallbackId']:
        assert token in js, token
    for token in ['missing_content', 'not_response:', 'duplicate', 'fallback-${type}-${content.length}-${stableTextHash(content)}']:
        assert token in js, token
    assert "void speakText(content, { utteranceId });" in js


def test_voqualizer_tts_init_diagnostics():
    js = read(JS)
    for token in ['lastTtsInitStartAt', 'lastTtsInitReadyAt', 'lastTtsInitError', 'lastTtsSpeakEntryAt', 'lastTtsSpeakSkipReason', 'lastDirectTtsAckAt']:
        assert token in js, token
    for token in ['init_start=', 'speak_entry=', 'ack_at=', 'init_failed', 'connect failed:']:
        assert token in js, token


def test_voqualizer_tts_uses_ws_namespace_direct_events():
    js = read(JS)
    assert "io('/ws'" in js
    assert "socket.emit('voqualizer_init', payload" in js
    assert "tts.socket.emit('voqualizer_user_text'" in js
    assert "tts.socket.emit('voqualizer_control'" in js
    assert "socket.emit(VOQUALIZER_HANDLER, { event: 'voqualizer_init'" not in js
    assert "tts.socket.emit(VOQUALIZER_HANDLER, {" not in js
    assert "m8-tts-retry-narrow" in js


def test_voqualizer_tts_reconnects_stale_session_and_retries():
    js = read(JS)
    assert "m8-tts-retry-narrow" in js
    assert "tts.socket && tts.socket.connected" in js
    assert "stale_session_reconnect" in js
    assert "direct_tts_ack_timeout" in js
    assert "lastDirectTtsRetryAt" in js
    assert "lastDirectTtsAttempt" in js
    assert "await emitDirectTts(2)" in js


def test_voqualizer_tts_retry_does_not_double_speak():
    js = read(JS)
    assert "m8-tts-retry-narrow" in js
    assert "ackHasChunks" in js
    assert "shouldRetry" in js
    assert "direct_tts_ack_timeout" in js
    assert "NO_SESSION|SESSION_NOT_ACTIVE" in js
