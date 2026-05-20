"""Source-level tests for the Voqualizer Conversational/PTT store."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
CM = PLUGIN / 'webui' / 'conversation-mode.js'


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

