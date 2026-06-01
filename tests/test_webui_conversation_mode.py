"""Source-level tests for the Voqualizer Conversational/PTT store."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
CM = PLUGIN / 'webui' / 'conversation-mode.js'
CONVERSATION_MODE = CM
AUDIO_LIB = PLUGIN / 'webui' / 'lib' / 'voqualizer-audio.js'


def test_conversation_mode_file_exists():
    assert CM.exists(), 'webui/conversation-mode.js missing'


def test_store_exposes_state_machine_and_helpers():
    s = CM.read_text()
    for marker in (
        "Alpine.store('voqualizer'",
        'STATE_IDLE',
        'STATE_CONNECTING',
        'STATE_CONVERSATIONAL',
        'STATE_PTT_ACTIVE',
        'STATE_ERROR',
        'TAP_HOLD_THRESHOLD_MS',
        'currentContextId',
        'a0_voqualizer.tts_enabled.',
        'onTap',
        'onHoldStart',
        'onHoldEnd',
        'set_tts_enabled',
        '_sendFinalFrame',
        'is_final: true',
    ):
        assert marker in s, f'missing marker {marker!r} in conversation-mode.js'


def test_tap_hold_threshold_is_250ms():
    s = CM.read_text()
    assert 'TAP_HOLD_THRESHOLD_MS = 250' in s


def test_per_context_session_storage_key():
    s = CM.read_text()
    assert "TTS_PREF_PREFIX = 'a0_voqualizer.tts_enabled.'" in s
    assert 'sessionStorage' in s


def test_connection_flapping_hardening_markers_present():
    s = CM.read_text()
    for marker in (
        '__a0VoqualizerConversationStore',
        'globalThis.__voqualizer_conversation',
        'desiredMode',
        "DESIRED_IDLE = 'idle'",
        "DESIRED_CONVERSATIONAL = 'conversational'",
        "DESIRED_PTT = 'ptt'",
        'connectionGeneration',
        '_isGenerationCurrent(generation)',
        'stale_generation_ignored',
        'intentionalDisconnect',
        'socket_reconnect_ignored',
        'pendingContextId',
        'pendingContextSince',
        'contextChangeDebounceMs',
        'CONTEXT_CHANGE_DEBOUNCE_MS = 850',
        'context_changed',
        'normalizeContextCandidate',
        "text !== '[object Object]'",
        'lastTransitionReason',
        'lastConnectPhase',
        'lastDisconnectReason',
        'lastSocketEvent',
        'debugSnapshot()',
    ):
        assert marker in s, f'missing flapping hardening marker {marker!r}'


def test_register_store_reuses_existing_singleton():
    s = CM.read_text()
    assert "Alpine.store('voqualizer'" in s
    assert 'if (globalThis.__a0VoqualizerConversationStore)' in s
    assert 'return existing' in s
    assert 'globalThis.__a0VoqualizerConversationStore = store' in s


def test_socket_and_async_lifecycle_are_guarded_by_intent_and_generation():
    s = CM.read_text()
    for marker in (
        'const generation = this._beginLifecycle',
        'this.desiredMode === DESIRED_IDLE',
        'this.intentionalDisconnect || this.desiredMode === DESIRED_IDLE',
        "socket.on('connect'",
        "socket.on('reconnect'",
        "socket.on('reconnect_attempt'",
        "socket.on('disconnect'",
        "socket.on('connect_error'",
        'socket.disconnect()',
    ):
        assert marker in s, f'missing lifecycle guard marker {marker!r}'


def test_tts_playback_diagnostics_and_base64_decode_markers():
    s = CM.read_text()
    for marker in (
        'bytesFromTtsPayload',
        'lastTtsEnabledSent',
        'lastTtsControlAck',
        'lastTtsChunkAt',
        'lastTtsDoneAt',
        'lastTtsChunkBytes',
        'lastTtsUtteranceId',
        'lastTtsSkipReason',
        'lastPlaybackStartAt',
        'lastPlaybackStopReason',
        'lastAgentFinalAt',
        'lastAgentFinalText',
        'lastFinalFrameSentAt',
        'lastFinalFrameReason',
        'lastAudioSeqSent',
        'lastAsrFinalText',
        'lastAsrFinalUtteranceId',
        "socket.on('voqualizer_agent_response_final'",
        "socket.on('voqualizer_asr_final'",
        '_handleTtsChunk(payload)',
        "ctx.state === 'suspended'",
        'ctx.resume',
    ):
        assert marker in s, f'missing TTS diagnostic marker {marker!r}'


def test_tts_init_and_control_send_enabled_truth_to_backend():
    s = CM.read_text()
    assert 'tts: { enabled: this.isTtsEnabled() }' in s
    assert 'this.lastTtsEnabledSent = this.isTtsEnabled()' in s
    assert 'this.lastTtsEnabledSent = enabled' in s
    assert "action: 'set_tts_enabled'" in s
    assert 'this.lastTtsControlAck = this._unwrapPayload(ack)' in s



def test_gui_tts_playback_uses_shared_parity_helpers():
    src = CONVERSATION_MODE.read_text()
    audio_src = AUDIO_LIB.read_text()

    # The in-GUI controller should share the same TTS payload/codecs/scheduling
    # primitives as the tester path instead of having a divergent one-off PCM path.
    for marker in (
        'normalizeTtsCodec',
        'ttsSampleRate',
        'rememberPlaybackSource',
        'bytesFromTtsPayload',
        'alignPcm16Bytes',
        'pcm16ToFloat32',
        '_playbackTail',
        'lastPlaybackStartAt',
        'lastTtsChunkBytes',
        'ttsChunkCount',
        'ttsDoneCount',
        'agentFinalCount',
        'asrFinalCount',
    ):
        assert marker in src

    for marker in (
        'export function normalizeTtsCodec',
        'export function ttsSampleRate',
        'export function rememberPlaybackSource',
        'export function repairRiffWaveHeader',
        'export function concatAudioBytes',
    ):
        assert marker in audio_src

    assert "codec === 'pcm'" in audio_src
    assert "sampleRate === 24000 ? 'pcm16/24k'" in audio_src
    assert 'source.start(startAt)' in src
    assert 'this._playbackTail = startAt + buffer.duration' in src
    assert 'clearPcm16Carry(carryMap, utteranceId)' in src

def test_conversation_mode_tracks_mic_vu_for_gui_meter():
    s = CM.read_text()
    for marker in (
        'micVuLevel',
        'micVuPeak',
        'micVuRms',
        'micVuClipped',
        'lastMicVuAt',
        '_handleMicVu(vu',
        'onVu: (vu) => this._handleMicVu(vu)',
        '_resetMicVu(reason',
        'maybeLocalBargeInFromMic(vu, tracker)',
    ):
        assert marker in s, f'missing mic VU store marker {marker!r}'

def test_conversation_mode_tracks_speech_detected_until_utterance_end():
    s = CM.read_text()
    for marker in (
        'MIC_SPEECH_ACTIVE_THRESHOLD',
        'micSpeechActive',
        'micSpeechStartedAt',
        'mic_speech_detected',
        'level >= MIC_SPEECH_ACTIVE_THRESHOLD',
        'peak >= MIC_SPEECH_ACTIVE_THRESHOLD',
        'this.micSpeechActive = false;',
        '_handleAsrFinal(payload)',
        '_sendFinalFrame(generation',
    ):
        assert marker in s, f'missing speech-active lifecycle marker {marker!r}'

def test_conversation_mode_cools_down_speech_detected_after_final():
    s = CM.read_text()
    for marker in (
        'MIC_SPEECH_FINAL_COOLDOWN_MS',
        'micSpeechCooldownUntil',
        '_clearMicSpeech(reason',
        'Date.now() >= (this.micSpeechCooldownUntil || 0)',
        "_clearMicSpeech('asr_final_received')",
        "_clearMicSpeech('final_frame_sent')",
        "_clearMicSpeech('agent_final_received')",
    ):
        assert marker in s, f'missing speech final cooldown marker {marker!r}'

def test_conversation_mode_clears_speech_detected_after_vu_silence():
    s = CM.read_text()
    for marker in (
        'MIC_SPEECH_SILENCE_CLEAR_MS',
        'micSpeechLastActiveAt',
        'isAboveSpeechThreshold',
        'MIC_SPEECH_SILENCE_CLEAR_MS',
        "_clearMicSpeech('mic_silence_detected', 0)",
    ):
        assert marker in s, f'missing speech silence clear marker {marker!r}'



def test_conversation_mode_can_send_direct_tts_from_rendered_response_fallback():
    s = CM.read_text()
    for marker in (
        'speakText(text, options = {})',
        "voqualizer_user_text",
        "source: 'webui_rendered_response_fallback'",
        'lastDirectTtsText',
        'lastDirectTtsAt',
        'lastDirectTtsAck',
        'lastDirectTtsError',
        'directTtsCount',
        'ack_timeout',
    ):
        assert marker in s, f'missing direct TTS fallback marker {marker!r}'



def test_gui_asr_partials_populate_prompt_and_final_submits():
    s = CM.read_text()
    for marker in (
        "asr_submit_mode: 'frontend_prompt'",
        "socket.on('voqualizer_asr_partial'",
        '_handleAsrPartial(payload)',
        '_writeAsrPromptDraft(text, \'partial\')',
        '_submitPromptFromAsr(text)',
        '_promptElement()',
        '_setPromptValue(el, draft)',
        "new Event('input', { bubbles: true })",
        'asrPromptDraftOwned',
        'lastAsrPartialPromptAt',
        'lastAsrFinalPromptAt',
        'lastPromptSubmitAt',
        'lastPromptSubmitText',
        'lastPromptSubmitSkipReason',
        "prompt_not_owned",
        "send_control_missing",
    ):
        assert marker in s, f'missing GUI ASR prompt marker {marker!r}'


def test_gui_asr_ack_fallback_mirrors_prompt_without_autosubmit():
    source = CONVERSATION_MODE.read_text()
    assert "asr_submit_mode: 'context_bridge'" in source
    assert '_handleAudioAckForAsr(data)' in source
    assert 'data.asr_last_final_text' in source
    assert "audio_ack_final" in source
    assert "audio_ack_partial" in source
    assert "does not click Send" in source
    assert "auto-submitting here would risk duplicate prompts" in source
    assert 'lastAsrPromptSource' in source
    assert 'lastAsrPromptMirrorAt' in source


def test_gui_asr_prompt_targets_a0_chat_input_store():
    source = CONVERSATION_MODE.read_text()
    assert "textarea#chat-input" in source
    assert "Alpine.store('chatInput')" in source
    assert 'chatInput.message = value' in source
    assert 'lastPromptElementSelector' in source


def test_gui_asr_display_mirror_clears_after_context_bridge_final():
    s = CONVERSATION_MODE.read_text()
    for marker in (
        '_scheduleAsrPromptMirrorClear',
        '_clearAsrPromptMirror',
        'context_bridge_final_blank_populate',
        'context_bridge_submitted',
        'lastAsrPromptClearAt',
        'lastAsrPromptClearScheduledAt',
        'lastAsrPromptClearReason',
        'lastAsrPromptGraceClearDelayMs',
    ):
        assert marker in s, f'missing ASR prompt clear marker {marker!r}'


def test_gui_asr_grace_clear_detects_final_ack_sources():
    s = CONVERSATION_MODE.read_text()
    assert "mirrorSource.includes('final')" in s
    assert "mirrorKind.includes('final')" in s
    assert 'audio_ack_final' in s
    assert 'lastAsrPromptClearScheduledAt' in s


def test_gui_asr_ack_final_directly_schedules_grace_clear():
    s = CONVERSATION_MODE.read_text()
    assert "const mirrored = this._mirrorAsrTextToPrompt(finalText, 'final', 'audio_ack_final');" in s
    assert "if (mirrored && this.asrPromptSubmissionMode === 'context_bridge_display_only')" in s
    assert "this._scheduleAsrPromptMirrorClear('context_bridge_final_blank_populate');" in s


def test_gui_asr_final_ack_uses_simple_clear_scheduler():
    s = CONVERSATION_MODE.read_text()
    assert '_scheduleFinalAsrPromptMirrorClear' in s
    assert "this._scheduleFinalAsrPromptMirrorClear('context_bridge_final_blank_populate');" in s
    assert "this._scheduleFinalAsrPromptMirrorClear('ack_final_duplicate_blank_populate');" in s
    assert 'this._publishDebug?.();' in s


def test_gui_asr_clear_uses_blank_populate_path():
    s = CONVERSATION_MODE.read_text()
    assert "const isClearDraft = String(kind || '').toLowerCase().includes('clear');" in s
    assert "const ok = this._writeAsrPromptDraft('', 'clear');" in s
    assert 'context_bridge_final_blank_populate' in s
    assert 'blank_populate_failed' in s
    clear_block = s[s.find('_clearAsrPromptMirror'):s.find('_scheduleFinalAsrPromptMirrorClear')]
    assert 'current.trim()' not in clear_block
    assert 'draft.trim()' not in clear_block


def test_gui_asr_clear_scheduling_uses_ownership_not_mode_string():
    s = CONVERSATION_MODE.read_text()
    schedule_block = s[s.find('_scheduleFinalAsrPromptMirrorClear'):s.find('_scheduleAsrPromptMirrorClear')]
    assert "asrPromptSubmissionMode !== 'context_bridge_display_only'" not in schedule_block
    assert 'if (!this.asrPromptDraftOwned) return false;' in schedule_block
    assert 'this._scheduleAsrPromptMirrorClear' in schedule_block
    assert "if (isFinalMirror && this.asrPromptDraftOwned)" in s
    assert "if (mirrored && this.asrPromptDraftOwned)" in s


def test_gui_asr_clear_has_due_at_and_ack_tick_fallback():
    s = CONVERSATION_MODE.read_text()
    assert 'lastAsrPromptClearDueAt' in s
    assert '_maybeClearAsrPromptMirror' in s
    assert "this._maybeClearAsrPromptMirror('ack_tick_due');" in s
    assert 'Date.now() < dueAt' in s
    assert 'this.lastAsrPromptClearDueAt = this.lastAsrPromptClearScheduledAt + delay;' in s
    assert "isPartialMirror && !Number(this.lastAsrPromptClearDueAt || 0)" in s


def test_gui_asr_final_ack_immediately_blank_populates_after_mirror():
    s = CONVERSATION_MODE.read_text()
    assert "const mirrored = this._mirrorAsrTextToPrompt(finalText, 'final', 'audio_ack_final');" in s
    assert "this._clearAsrPromptMirror('audio_ack_final_blank_populate');" in s
    assert "this._clearAsrPromptMirror('ack_final_duplicate_blank_populate');" in s
    ack_block = s[s.find('const finalText = String(data.asr_last_final_text'):s.find('const partialText = String(data.asr_last_partial_text')]
    assert "this._scheduleFinalAsrPromptMirrorClear('context_bridge_final_blank_populate');" not in ack_block
    assert "this._scheduleAsrPromptMirrorClear('context_bridge_final_blank_populate');" not in ack_block
    assert "const ok = this._writeAsrPromptDraft('', 'clear');" in s


def test_rendered_response_observer_tts_fallback_extension_exists():
    ext = PLUGIN / 'extensions' / 'webui' / 'initFw_end' / 'voqualizer-response-observer.js'
    assert ext.exists(), 'rendered response observer TTS fallback extension missing'
    source = ext.read_text(encoding='utf-8')
    for marker in (
        'installVoqualizerResponseObserver',
        'MutationObserver',
        "document.querySelectorAll('.message-agent-response')",
        "Alpine?.store?.('voqualizer')",
        'store.speakText',
        'gui-observed-response-',
        '__voqualizer_response_observer',
        'data-voqualizer-tts-spoken',
        'a0_voqualizer.observed_response.',
    ):
        assert marker in source, f'missing rendered response observer marker {marker!r}'


def test_direct_tts_repairs_missing_passive_session_before_emit():
    source = CM.read_text(encoding='utf-8')
    for marker in (
        "direct_tts_repair_passive_session",
        "!this._socket || !this.bearerToken || !this.sessionId",
        "await this._ensurePassiveTtsSession('direct_tts_repair_passive_session')",
        "this._beginLifecycle(DESIRED_TTS, 'direct_tts_requested')",
    ):
        assert marker in source, f'missing direct TTS passive repair marker {marker!r}'


def test_rendered_response_observer_only_marks_successful_speech_as_spoken():
    ext = PLUGIN / 'extensions' / 'webui' / 'initFw_end' / 'voqualizer-response-observer.js'
    source = ext.read_text(encoding='utf-8')
    assert 'if (ack && ack.ok === false)' in source
    assert "state.lastError = String(ack.code || ack.reason || 'speak_failed')" in source
    assert source.index('if (ack && ack.ok === false)') < source.index('markSpoken(responseId)')


def test_tts_push_delivery_debug_markers_exist():
    source = CM.read_text(encoding='utf-8')
    for marker in (
        'lastRawTtsPushEvent',
        'lastRawTtsPushAt',
        'lastRawTtsPushKeys',
        'lastRawTtsPushDataKeys',
        "_recordRawTtsPush('voqualizer_tts_chunk', payload)",
        "_recordRawTtsPush('voqualizer_tts_done', payload)",
    ):
        assert marker in source, f'missing TTS push diagnostic marker {marker!r}'


def test_direct_tts_ack_chunk_fallback_markers_exist():
    source = CM.read_text(encoding='utf-8')
    for marker in (
        'lastAckTtsFallbackAt',
        'lastAckTtsFallbackChunks',
        'lastAckTtsFallbackReason',
        '_handleAckTtsFallback(value)',
        'Array.isArray(value.tts_chunks)',
        "this._handleTtsChunk(chunk)",
        "this._handleTtsDone(data.tts_done)",
    ):
        assert marker in source, f'missing direct TTS ack fallback marker {marker!r}'


def test_conversation_mode_defensive_json_and_asr_dedupe():
    content = CM.read_text()
    assert "async function parseJsonResponseSafely" in content
    assert "returned non-JSON response" in content
    assert "preview=" in content
    assert "_shouldDropDuplicateAsrFinal" in content
    assert "duplicate_asr_final_ignored" in content
    assert "_recentAsrFinals: new Map()" in content
