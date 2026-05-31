import { callJsonApi } from '/js/api.js';
import {
  bytesFromTtsPayload,
  concatAudioBytes,
  repairRiffWaveHeader,
  normalizeTtsCodec,
  ttsSampleRate,
  pcm16ToFloat32,
  alignPcm16Bytes,
  clearPcm16Carry,
  createPlaybackTracker,
  rememberPlaybackSource,
  framePcm16,
  audioChunkPayload,
  WORKLET_URL,
  WORKLET_PROCESSOR,
} from '/plugins/a0_voqualizer/webui/lib/voqualizer-audio.js';
// M8: ASR/mic + speaker-button state machine parity is delegated to the same
// store the in-DOM voqualizer-buttons.html extension uses, so the standalone
// page reproduces tap/hold/PTT/VU/speech-detected/TTS-toggle behavior exactly.
import {
  createVoqualizerStore,
  TAP_HOLD_THRESHOLD_MS,
  STATE_IDLE,
  STATE_CONNECTING,
  STATE_CONVERSATIONAL,
  STATE_PTT_ACTIVE,
  STATE_TTS_READY,
  STATE_STOPPING,
  STATE_ERROR,
} from '/plugins/a0_voqualizer/webui/conversation-mode.js?v=m8-realtime-ws-prompt-2026-05-30-58';
// ASR finals from the store's socket (voqualizer_asr_final) and partials
// (voqualizer_asr_partial) are routed back into submitPrompt(pageState) via
// the store's onAsrFinal hook so the M3/M4/M5/M7 typed-prompt + /poll +
// cx-stream + word-highlight pipeline remains the single submission path.
let voqStore = null;

const PAGE_VERSION = 'm8-realtime-ws-prompt';
const STORE_IMPORT_CACHE = 'store_import_cache=m8-realtime-ws-prompt-2026-05-30-58';
const ADMIN_ENDPOINT = 'plugins/a0_voqualizer/voqualizer_admin';
const MESSAGE_ENDPOINT = 'plugins/a0_voqualizer/voqualizer_message_async';
const POLL_ENDPOINT = 'poll';
const VOQUALIZER_HANDLER = 'plugins/a0_voqualizer/ws_voqualizer';
const SELECTED_CONTEXT_STORAGE_KEY = 'a0_voqualizer.standalone.selected_context_id';
const TTS_ENABLED_STORAGE_KEY = 'a0_voqualizer.standalone.tts_enabled';
const ASR_ENABLED_STORAGE_KEY = 'a0_voqualizer.standalone.asr_enabled';
const ASR_SUBMIT_MODE = 'frontend_prompt';
// Uses the shared voqualizer-mic-processor AudioWorklet from voqualizer-audio.js
const BARGE_IN_LEVEL_THRESHOLD = 0.05;
const POLL_INTERVAL_MS = 700;
const PRELOAD_MONOLOGUE_LOG_FROM = 0;

const tts = {
  socket: null,
  sessionId: '',
  bearerToken: '',
  contextId: '',
  connecting: null,
  ready: false,
  enabled: loadTtsEnabled(),
  tracker: createPlaybackTracker(),
  pcm16CarryMap: new Map(),
  audioContext: null,
  playbackTail: 0,
  encodedBuffers: new Map(),
  spokenResponseIds: new Set(),
  lastError: '',
  lastSpeakAt: 0,
  activeDirectUtteranceId: '',
  acceptedTtsUtteranceIds: new Set(),
  livePushSinceSubmit: false,
  lastLivePushAt: 0,
  lastLivePushUtteranceId: '',
  processingHeartbeatTimer: 0,
  processingHeartbeatSubmissionId: '',
  processingHeartbeatStartedAt: 0,
  processingHeartbeatLastAt: 0,
  processingHeartbeatCount: 0,
};

const asr = {
  enabled: loadAsrEnabled(),
  capturing: false,
  starting: false,
  muted: false,
  lastPartialText: '',
  lastFinalText: '',
  lastFinalAt: 0,
  lastError: '',
  inputBeforeCapture: '',
  bargedThisUtterance: false,
  mediaStream: null,
  workletNode: null,
  mediaSource: null,
  monitorGain: null,
  lastVuLevel: 0,
};

const cx = {
  enabledByCapability: false,
  streamsBySubmitId: new Map(),
  bubblesByStreamId: new Map(),
  streamsByStreamId: new Map(),
  lastSeqByStreamId: new Map(),
  finalByStreamId: new Set(),
  reconciledLogIds: new Set(),
  lastEventAt: 0,
  lastEvent: '',
  lastError: '',
};

const wordPlan = {
  plansByUtteranceId: new Map(),
  bubblesByUtteranceId: new Map(),
  spansByUtteranceId: new Map(),
  activeIndexByUtteranceId: new Map(),
  playbackStartByUtteranceId: new Map(),
  endedByUtteranceId: new Set(),
  rafId: 0,
};


function setProcessingHeartbeatDebug(fields = {}) {
  const page = globalThis.__voqualizer_page;
  if (!page) return;
  Object.assign(page, fields);
}

function stopProcessingHeartbeat(reason = 'stopped') {
  if (tts.processingHeartbeatTimer) {
    clearInterval(tts.processingHeartbeatTimer);
    tts.processingHeartbeatTimer = 0;
  }
  if (tts.processingHeartbeatSubmissionId || tts.processingHeartbeatStartedAt) {
    setProcessingHeartbeatDebug({
      processingHeartbeatActive: false,
      processingHeartbeatStoppedAt: Date.now(),
      processingHeartbeatStopReason: reason,
      processingHeartbeatSubmissionId: tts.processingHeartbeatSubmissionId,
      processingHeartbeatCount: tts.processingHeartbeatCount,
    });
  }
  tts.processingHeartbeatSubmissionId = '';
}

function startProcessingHeartbeat(submissionId) {
  stopProcessingHeartbeat('restart');
  if (!tts.enabled || !submissionId) {
    setProcessingHeartbeatDebug({
      processingHeartbeatActive: false,
      processingHeartbeatSkipReason: !tts.enabled ? 'tts_disabled' : 'missing_submission',
    });
    return;
  }
  tts.processingHeartbeatSubmissionId = submissionId;
  tts.processingHeartbeatStartedAt = Date.now();
  tts.processingHeartbeatLastAt = 0;
  tts.processingHeartbeatCount = 0;
  setProcessingHeartbeatDebug({
    processingHeartbeatActive: true,
    processingHeartbeatStartedAt: tts.processingHeartbeatStartedAt,
    processingHeartbeatStoppedAt: 0,
    processingHeartbeatStopReason: '',
    processingHeartbeatSubmissionId: submissionId,
    processingHeartbeatCount: 0,
    processingHeartbeatSkipReason: '',
  });
  tts.processingHeartbeatTimer = setInterval(() => {
    if (!tts.enabled || tts.livePushSinceSubmit || tts.processingHeartbeatSubmissionId !== submissionId) {
      stopProcessingHeartbeat(tts.livePushSinceSubmit ? 'streaming_response_started' : 'inactive');
      return;
    }
    tts.processingHeartbeatLastAt = Date.now();
    tts.processingHeartbeatCount += 1;
    setProcessingHeartbeatDebug({
      processingHeartbeatActive: true,
      processingHeartbeatLastAt: tts.processingHeartbeatLastAt,
      processingHeartbeatCount: tts.processingHeartbeatCount,
    });
    void speakText('processing', { utteranceId: `voq-processing-${submissionId}-${tts.processingHeartbeatCount}` });
  }, 3000);
}

function cxActiveStreamCount() {
  let count = 0;
  for (const streamId of cx.streamsByStreamId.keys()) {
    if (!cx.finalByStreamId.has(streamId)) count += 1;
  }
  return count;
}

function clearCxStreamState({ keepCapability = true } = {}) {
  cx.streamsBySubmitId.clear();
  cx.bubblesByStreamId.clear();
  cx.streamsByStreamId.clear();
  cx.lastSeqByStreamId.clear();
  cx.finalByStreamId.clear();
  cx.reconciledLogIds.clear();
  cx.lastEventAt = 0;
  cx.lastEvent = '';
  cx.lastError = '';
  if (!keepCapability) cx.enabledByCapability = false;
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.cxLastEvent = '';
    globalThis.__voqualizer_page.cxLastStreamId = '';
    globalThis.__voqualizer_page.cxLastSeq = 0;
    globalThis.__voqualizer_page.cxLastError = '';
    globalThis.__voqualizer_page.cxActiveStreamCount = 0;
  }
}

function updateCxActiveStreamDebug() {
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.cxActiveStreamCount = cxActiveStreamCount();
  }
}

function loadTtsEnabled() {
  try {
    const stored = globalThis.localStorage?.getItem(TTS_ENABLED_STORAGE_KEY);
    if (stored === null || stored === undefined) return true;
    return stored === 'true';
  } catch (_err) {
    return true;
  }
}

function persistTtsEnabled(value) {
  try {
    globalThis.localStorage?.setItem(TTS_ENABLED_STORAGE_KEY, value ? 'true' : 'false');
  } catch (_err) {}
}

function loadAsrEnabled() {
  try {
    const stored = globalThis.localStorage?.getItem(ASR_ENABLED_STORAGE_KEY);
    return stored === 'true';
  } catch (_err) {
    return false;
  }
}

function persistAsrEnabled(value) {
  try {
    globalThis.localStorage?.setItem(ASR_ENABLED_STORAGE_KEY, value ? 'true' : 'false');
  } catch (_err) {}
}

function stableTextHash(text) {
  const value = safeString(text);
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function safeString(value) {
  return value === undefined || value === null ? '' : String(value);
}

function autosizePrompt(textarea) {
  if (!textarea) return;
  textarea.style.height = 'auto';
  textarea.style.height = `${Math.min(textarea.scrollHeight, window.innerHeight * 0.34)}px`;
}

function readSelectedContextHint() {
  const params = new URLSearchParams(globalThis.location?.search || '');
  const queryValue = params.get('ctxid') || params.get('context_id') || params.get('contextId');
  if (queryValue) return safeString(queryValue).trim();
  try {
    const stored = globalThis.localStorage?.getItem(SELECTED_CONTEXT_STORAGE_KEY);
    if (stored) return safeString(stored).trim();
  } catch (_err) {}
  try {
    if (typeof globalThis.getContext === 'function') {
      return safeString(globalThis.getContext()).trim();
    }
  } catch (_err) {}
  return '';
}

async function fetchHeroDefaultContextId() {
  // Best-effort: if the a0_superordinates plugin is installed and Hero Mode
  // is enabled with a designated Hero context, return that ctxid so the
  // standalone page can default-select the Hero's chat. Silently no-op when
  // the plugin/endpoint is unavailable or Hero Mode is Disabled.
  try {
    const { callJsonApi } = await import('/js/api.js');
    const result = await callJsonApi(
      'plugins/a0_superordinates/superordinate_config',
      {},
    );
    if (!result || result.ok === false) return '';
    const hero = safeString(result.hero_mode_designated_hero || '').trim();
    if (!hero) return '';
    if (hero.toLowerCase() === 'disabled') return '';
    return hero;
  } catch (_err) {
    return '';
  }
}

function persistSelectedContextId(contextId) {
  try {
    if (contextId) {
      globalThis.localStorage?.setItem(SELECTED_CONTEXT_STORAGE_KEY, contextId);
    } else {
      globalThis.localStorage?.removeItem(SELECTED_CONTEXT_STORAGE_KEY);
    }
  } catch (_err) {}
}

function normalizeContext(ctx) {
  const id = safeString(ctx?.id).trim();
  if (!id) return null;
  const name = safeString(ctx?.name || id).trim() || id;
  const kind = safeString(ctx?.type || 'chat').trim() || 'chat';
  return {
    id,
    name,
    label: `${name} (${id})`,
    kind,
    active: false,
    parentId: safeString(ctx?.parent_id).trim(),
    lastMessage: safeString(ctx?.last_message),
    createdAt: ctx?.created_at || '',
  };
}

function normalizeContexts(contexts) {
  return (Array.isArray(contexts) ? contexts : [])
    .map(normalizeContext)
    .filter(Boolean)
    .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

async function callJsonApiWithDiagnostics(endpoint, data, stage) {
  const page = globalThis.__voqualizer_page;
  if (page) {
    page.lastApiStage = stage || endpoint;
    page.lastApiEndpoint = endpoint;
    page.lastApiPayload = data ? JSON.parse(JSON.stringify(data)) : null;
    page.lastApiError = '';
    page.lastApiErrorAt = 0;
  }
  try {
    const result = await callJsonApi(endpoint, data);
    if (page) {
      page.lastApiResult = result ? JSON.parse(JSON.stringify(result)) : result;
      page.lastApiOkAt = Date.now();
    }
    return result;
  } catch (error) {
    const message = error?.message || String(error);
    if (page) {
      page.lastApiError = message.slice(0, 2000);
      page.lastApiErrorAt = Date.now();
      page.lastApiResult = null;
    }
    throw error;
  }
}

async function fetchContexts() {
  const result = await callJsonApiWithDiagnostics(ADMIN_ENDPOINT, { action: 'contexts' }, 'contexts');
  if (!result || result.ok === false) {
    throw new Error(result?.message || result?.code || 'contexts request failed');
  }
  return normalizeContexts(result.contexts || []);
}

function setSelectPlaceholder(select, label, { disabled = true } = {}) {
  if (!select) return;
  select.innerHTML = '';
  const option = document.createElement('option');
  option.value = '';
  option.textContent = label;
  option.selected = true;
  select.appendChild(option);
  select.disabled = disabled;
}


function contextLabelForId(contextId) {
  const page = globalThis.__voqualizer_page;
  const contexts = Array.isArray(page?.contexts) ? page.contexts : [];
  const match = contexts.find((ctx) => ctx.id === contextId);
  if (match) return match.name || match.label || match.id || 'Voqualizer';
  const select = document.getElementById('voq-context-select');
  const option = select ? [...select.options].find((opt) => opt.value === contextId) : null;
  return option?.textContent || contextId || 'Voqualizer';
}

function updateHeaderContextName(contextId = '') {
  const label = document.getElementById('voq-header-context-name');
  if (!label) return;
  const page = globalThis.__voqualizer_page;
  const selected = contextId || page?.selectedContextId || document.getElementById('voq-context-select')?.value || '';
  const friendlyName = contextLabelForId(selected);
  label.textContent = friendlyName || 'Voqualizer';
  label.setAttribute('title', friendlyName || 'Voqualizer');
  if (page) {
    page.headerContextName = friendlyName || 'Voqualizer';
    page.lastHeaderContextNameAt = Date.now();
  }
}

function renderContexts(select, contexts, selectedContextId) {
  if (!select) return '';
  select.innerHTML = '';
  if (!contexts.length) {
    setSelectPlaceholder(select, 'No contexts found', { disabled: true });
    return '';
  }
  const ids = new Set(contexts.map((ctx) => ctx.id));
  const nextSelected = selectedContextId && ids.has(selectedContextId) ? selectedContextId : contexts[0].id;
  if (selectedContextId && !ids.has(selectedContextId)) {
    persistSelectedContextId('');
  }
  for (const ctx of contexts) {
    const option = document.createElement('option');
    option.value = ctx.id;
    option.textContent = ctx.label;
    option.dataset.name = ctx.name;
    option.dataset.kind = ctx.kind;
    option.selected = ctx.id === nextSelected;
    select.appendChild(option);
    ctx.active = ctx.id === nextSelected;
  }
  select.disabled = false;
  select.value = nextSelected;
  persistSelectedContextId(nextSelected);
  return nextSelected;
}

function setPageStatus(message, level = 'info') {
  // Suppress decorative status lines requested to be hidden from the page UI.
  const _msg = typeof message === 'string' ? message : '';
  if (
    _msg === 'Response complete' ||
    _msg.indexOf('Selected ') === 0 ||
    _msg.indexOf('Loading contexts') === 0
  ) {
    return;
  }
  const text = message || 'Ready';
  const root = document.querySelector('[data-voqualizer-page="standalone"]');
  if (root) {
    root.dataset.status = level;
    root.dataset.statusMessage = text;
  }
  const status = document.getElementById('voq-status');
  if (status) {
    status.textContent = text;
    status.dataset.level = level;
  }
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.lastStatus = text;
    globalThis.__voqualizer_page.lastStatusLevel = level;
  }
}

function generateMessageId() {
  try {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  } catch (_err) {}
  return `voq-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function transcriptElement() {
  return document.getElementById('voq-chat');
}

function clearEmptyState() {
  const chat = transcriptElement();
  if (!chat) return;
  const empty = chat.querySelector('.voq-empty-state');
  if (empty) empty.remove();
}

function isNearBottom(chat) {
  if (!chat) return true;
  return chat.scrollHeight - chat.scrollTop - chat.clientHeight < 96;
}

function updateJumpLatest() {
  const chat = transcriptElement();
  const button = document.getElementById('voq-jump-latest');
  if (!chat || !button) return;
  button.hidden = isNearBottom(chat);
}

function scrollTranscriptToBottom() {
  const chat = transcriptElement();
  if (!chat) return;
  chat.scrollTop = chat.scrollHeight;
  updateJumpLatest();
}

function maybeAutoScroll(chat, wasNearBottom) {
  if (!chat) return;
  if (wasNearBottom) chat.scrollTop = chat.scrollHeight;
  updateJumpLatest();
}


function updateActionsWrapped() {
  const actions = document.querySelector('[data-voqualizer-page="standalone"] .voq-actions');
  if (!actions) return;
  // Measure on a fresh non-grid layout to avoid feedback loops.
  const prev = actions.getAttribute('data-wrapped') || '';
  if (prev === 'true') actions.removeAttribute('data-wrapped');
  const rects = Array.from(actions.children)
    .filter((el) => el.offsetParent !== null)
    .map((el) => el.getBoundingClientRect());
  let wrapped = false;
  if (rects.length >= 2) {
    const baseTop = Math.round(rects[0].top);
    wrapped = rects.some((r) => Math.round(r.top) - baseTop > 4);
  }
  if (wrapped) actions.setAttribute('data-wrapped', 'true');
  else actions.removeAttribute('data-wrapped');
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.actionsWrapped = wrapped;
    globalThis.__voqualizer_page.actionsWrappedAt = Date.now();
  }
}


function buildAsrDebugLines() {
  const c = globalThis.__voqualizer_conversation || {};
  const p = globalThis.__voqualizer_page || {};
  const ack = c.lastAudioAck || {};
  const input = document.getElementById('voq-prompt-input');
  const assets = Array.from(document.querySelectorAll('script[src],link[href]'))
    .map((element) => element.src || element.href)
    .filter((url) => /voqualizer|conversation-mode/.test(url));
  const lines = [
    '===VOQ_ASR_LINES===',
    `cache_ok=${assets.some((url) => url.includes('m8-tts-processing-heartbeat-2026-05-29-57'))}`,
    `page_version=${p.version}`,
    `state=${c.state} desired=${c.desiredMode} phase=${c.lastConnectPhase} reason=${c.lastTransitionReason}`,
    `session=${!!c.sessionId} token=${!!c.bearerToken} capturing=${c.capturing}`,
    `vu=${c.micVuLevel} peak=${c.micVuPeak} rms=${c.micVuRms} speech=${c.micSpeechActive}`,
    `frames_sent=${c.audioFramesSent} frames_dropped=${c.audioFramesDropped} drop_reason=${c.lastAudioFrameDropReason}`,
    `ack_at=${c.lastAudioAckAt} ack_error=${c.lastAudioAckError}`,
    `ack_event=${ack.event} ack_code=${ack.code} ack_msg=${ack.message}`,
    `ack_emitted=${ack.emitted} ack_queued=${ack.queued}`,
    `vad_rms=${ack.asr_vad_last_rms} vad_peak=${ack.asr_vad_peak_rms} vad_threshold=${ack.asr_vad_speech_rms} vad_speech=${ack.asr_vad_has_speech} vad_ms=${ack.asr_vad_speech_ms} vad_chunks=${ack.asr_vad_buffered_chunks}`,
    `ack_partial=${JSON.stringify(ack.asr_last_partial_text || '')}`,
    `ack_final=${JSON.stringify(ack.asr_last_final_text || '')}`,
    `store_partial=${JSON.stringify(c.lastAsrPartialText || '')}`,
    `store_final=${JSON.stringify(c.lastAsrFinalText || '')}`,
    `ack_store_final=${JSON.stringify(c.lastAckAsrFinalText || '')}`,
    `submit_at=${c.lastPromptSubmitAt} submit_skip=${c.lastPromptSubmitSkipReason}`,
    `page_final=${JSON.stringify(p.asrLastFinalText || '')}`,
    `prompt=${JSON.stringify(input?.value || '')}`,
  ];
  return lines.join('\n');
}

async function copyAsrDebugLines() {
  const text = buildAsrDebugLines();
  const button = document.getElementById('voq-asr-debug-button');
  let copied = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch (_error) {}
  if (!copied) {
    try {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', 'readonly');
      area.style.position = 'fixed';
      area.style.left = '-9999px';
      document.body.appendChild(area);
      area.select();
      copied = document.execCommand('copy');
      area.remove();
    } catch (_error) {}
  }
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.lastAsrDebugCopyAt = Date.now();
    globalThis.__voqualizer_page.lastAsrDebugCopyOk = copied;
    globalThis.__voqualizer_page.lastAsrDebugLines = text;
  }
  if (button) {
    button.dataset.copied = copied ? 'true' : 'false';
    button.setAttribute('title', copied ? 'ASR debug copied' : 'ASR debug ready — copy failed, long-press/select from console if needed');
    setTimeout(() => {
      button.dataset.copied = 'false';
      button.setAttribute('title', 'Copy ASR debug state');
    }, 1800);
  }
  setPageStatus(copied ? 'ASR debug copied' : 'ASR debug captured', copied ? 'ready' : 'warn');
  return text;
}

function summarizeTtsAckForDebug(ack) {
  if (!ack || typeof ack !== 'object') return 'null';
  const summary = {
    event: ack.event || '',
    ok: ack.ok,
    error: ack.error ? (ack.error.message || ack.error.code || String(ack.error)) : '',
    delivery_fallback: ack.delivery_fallback || '',
    chunks: ack.chunks,
    tts_chunks: Array.isArray(ack.tts_chunks) ? ack.tts_chunks.length : ack.tts_chunks,
    pushed_emit_count: ack.pushed_emit_count,
    pushed_done_emit_count: ack.pushed_done_emit_count,
    sender_present: ack.sender_present,
    has_tts_done: !!ack.tts_done,
    has_tts_word_plan: !!ack.tts_word_plan,
    utterance_id: ack.utterance_id || '',
  };
  return JSON.stringify(summary);
}

function buildTtsDebugLines() {
  const p = globalThis.__voqualizer_page || {};
  const speaker = document.getElementById('voqualizer-speaker-button');
  const assets = Array.from(document.querySelectorAll('script[src],link[href]'))
    .map((element) => element.src || element.href)
    .filter((url) => /voqualizer|conversation-mode/.test(url));
  const ack = p.lastDirectTtsAck || null;
  const lines = [
    '===VOQ_TTS_LINES===',
    `cache_ok=${assets.some((url) => url.includes('m8-tts-processing-heartbeat-2026-05-29-57'))}`,
    `page_version=${p.version}`,
    `tts_enabled=${p.ttsEnabled} button_pressed=${speaker?.getAttribute('aria-pressed')} data_enabled=${speaker?.getAttribute('data-tts-enabled')}`,
    `button_class=${JSON.stringify(speaker?.className || '')}`,
    `socket_ready=${p.ttsReady} session=${p.ttsSessionId || ''} context=${p.selectedContextId || ''}`,
    `ws_prompt_transport=${p.promptSubmitTransport || ''} ws_prompt_ack=${p.lastWsPromptAckAt || 0} ws_prompt_error=${p.lastWsPromptError || ''}`,
    `init_start=${p.lastTtsInitStartAt || 0} init_ready=${p.lastTtsInitReadyAt || 0} init_context=${p.lastTtsInitContextId || ''} init_error=${p.lastTtsInitError || ''}`,
    `speak_entry=${p.lastTtsSpeakEntryAt || 0} speak_entry_len=${p.lastTtsSpeakEntryTextLength || 0} speak_skip=${p.lastTtsSpeakSkipReason || ''}`,
    `last_trigger_at=${p.lastTtsTriggerAt || 0} trigger_type=${p.lastTtsTriggerType || ''} trigger_id=${p.lastTtsTriggerItemId || ''} trigger_fallback=${p.lastTtsTriggerFallbackId || ''} trigger_len=${p.lastTtsTriggerTextLength || 0} skip=${p.lastTtsSkipReason || ''}`,
    `queued_at=${p.lastTtsSpeakQueuedAt || 0} queued_utt=${p.lastTtsSpeakQueuedUtteranceId || ''}`,
    `processing_heartbeat=${!!p.processingHeartbeatActive} processing_count=${p.processingHeartbeatCount || 0} processing_stop=${p.processingHeartbeatStopReason || ''}`,
    `last_speak_at=${p.lastTtsSpeakAt || p.lastDirectTtsAt || 0} ack_at=${p.lastDirectTtsAckAt || 0} ack_type=${p.lastDirectTtsAckRawType || ''} last_error=${p.lastTtsError || p.lastError || ''}`,
    `ack_fallback_at=${p.lastAckTtsFallbackAt || 0} ack_chunks=${p.lastAckTtsFallbackChunks || 0} ack_reason=${p.lastAckTtsFallbackReason || ''}`,
    `ack_suppressed_at=${p.lastAckTtsFallbackSuppressedAt || 0} ack_suppressed_chunks=${p.lastAckTtsFallbackSuppressedChunks || 0} ack_suppressed_reason=${p.lastAckTtsFallbackSuppressedReason || ''}`,
    `ack_pushed=${p.lastAckTtsPushedEmitCount || 0} ack_sender=${p.lastAckTtsSenderPresent}`,
    `live_push_utt=${p.lastLivePushedTtsUtteranceId || ''} live_push_at=${p.lastLivePushedTtsAt || 0} live_push_source=${p.lastLivePushedTtsSource || ''}`,
    `chunk_count=${p.ttsChunkCount || 0} chunk_at=${p.lastTtsChunkAt || 0} chunk_bytes=${p.lastTtsChunkBytes || 0} chunk_codec=${p.lastTtsChunkCodec || ''} chunk_rate=${p.lastTtsChunkSampleRate || ''}`,
    `playback_at=${p.lastPlaybackStartAt || 0} playback_ms=${p.lastPlaybackDurationMs || 0} playback_utt=${p.lastPlaybackUtteranceId || ''} playback_error=${p.lastPlaybackError || ''}`,
    `audio_state=${p.audioContextState || ''} audio_create=${p.lastAudioContextCreateAt || 0} audio_create_reason=${p.lastAudioContextCreateReason || ''} audio_create_error=${p.lastAudioContextError || ''}`,
    `audio_resume=${p.lastAudioResumeAt || 0} audio_resume_reason=${p.lastAudioResumeReason || ''} audio_resume_error=${p.lastAudioResumeError || ''}`,
    `stale_socket_event=${p.lastStaleTtsSocketEvent || ''} stale_socket_at=${p.lastStaleTtsSocketEventAt || 0}`,
    `last_ack=${summarizeTtsAckForDebug(ack)}`,
  ];
  return lines.join('\n');
}

async function copyTtsDebugLines() {
  const text = buildTtsDebugLines();
  const button = document.getElementById('voq-tts-debug-button');
  let copied = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch (_error) {}
  if (!copied) {
    try {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', 'readonly');
      area.style.position = 'fixed';
      area.style.left = '-9999px';
      document.body.appendChild(area);
      area.select();
      copied = document.execCommand('copy');
      area.remove();
    } catch (_error) {}
  }
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.lastTtsDebugCopyAt = Date.now();
    globalThis.__voqualizer_page.lastTtsDebugCopyOk = copied;
    globalThis.__voqualizer_page.lastTtsDebugLines = text;
  }
  if (button) {
    button.dataset.copied = copied ? 'true' : 'false';
    button.setAttribute('title', copied ? 'TTS debug copied' : 'TTS debug ready — copy failed, long-press/select from console if needed');
    setTimeout(() => {
      button.dataset.copied = 'false';
      button.setAttribute('title', 'Copy TTS debug state');
    }, 1800);
  }
  setPageStatus(copied ? 'TTS debug copied' : 'TTS debug captured', copied ? 'ready' : 'warn');
  return text;
}


function setAsrDebugVisible(visible, reason = 'manual') {
  const button = document.getElementById('voq-asr-debug-button');
  const ttsButton = document.getElementById('voq-tts-debug-button');
  const menuButton = document.getElementById('voq-context-menu-button');
  const isVisible = !!visible;
  if (button) {
    button.hidden = !isVisible;
    button.setAttribute('aria-hidden', isVisible ? 'false' : 'true');
  }
  if (ttsButton) {
    ttsButton.hidden = !isVisible;
    ttsButton.setAttribute('aria-hidden', isVisible ? 'false' : 'true');
  }
  if (menuButton) {
    menuButton.dataset.debugVisible = isVisible ? 'true' : 'false';
    menuButton.setAttribute('title', isVisible ? 'Select Voqualizer context — double tap to hide debug buttons' : 'Select Voqualizer context — double tap to show debug buttons');
  }
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.asrDebugVisible = isVisible;
    globalThis.__voqualizer_page.lastAsrDebugToggleAt = Date.now();
    globalThis.__voqualizer_page.lastAsrDebugToggleReason = reason;
  }
  setPageStatus(isVisible ? 'Debug buttons shown' : 'Debug buttons hidden', 'ready');
}

function toggleAsrDebugVisible(reason = 'hamburger_double_tap') {
  const button = document.getElementById('voq-asr-debug-button');
  setAsrDebugVisible(!(button && !button.hidden), reason);
}

function bindAsrDebugButton() {
  const button = document.getElementById('voq-asr-debug-button');
  const ttsButton = document.getElementById('voq-tts-debug-button');
  if (button) button.addEventListener('click', () => { void copyAsrDebugLines(); });
  if (ttsButton) ttsButton.addEventListener('click', () => { void copyTtsDebugLines(); });
}

function bindFullscreenButton() {
  const btn = document.getElementById('voq-fullscreen-button');
  if (!btn) return;
  const sync = () => {
    const active = !!document.fullscreenElement;
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    btn.setAttribute('title', active ? 'Exit fullscreen' : 'Toggle fullscreen');
    btn.setAttribute('aria-label', active ? 'Exit fullscreen' : 'Toggle fullscreen');
    const icon = btn.querySelector('.voq-material');
    if (icon) icon.textContent = active ? 'fullscreen_exit' : 'fullscreen';
    if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.fullscreenActive = active;
  };
  btn.addEventListener('click', async () => {
    try {
      if (!document.fullscreenElement) {
        const root = document.documentElement;
        if (root.requestFullscreen) await root.requestFullscreen();
      } else if (document.exitFullscreen) {
        await document.exitFullscreen();
      }
    } catch (err) {
      if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.fullscreenError = err?.message || String(err);
    }
    sync();
  });
  document.addEventListener('fullscreenchange', sync);
  sync();
}

function bindRefreshButton(state) {
  const btn = document.getElementById('voq-refresh-button');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const contextId = globalThis.__voqualizer_page?.selectedContextId || '';
    if (!contextId) return;
    btn.dataset.busy = 'true';
    try {
      const chat = transcriptElement();
      if (chat) chat.innerHTML = '<div class="voq-empty-state">Reloading latest monologue result\u2026</div>';
      if (state?.transcriptIds) state.transcriptIds.clear();
      state.lastLogVersion = 0;
      if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.lastLogVersion = 0;
      await preloadLastMonologueResult(state, contextId);
      if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.lastRefreshAt = Date.now();
    } catch (err) {
      if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.lastRefreshError = err?.message || String(err);
    } finally {
      btn.dataset.busy = 'false';
    }
  });
}

function renderContextMenu(contexts, selectedContextId) {
  const menu = document.getElementById('voq-context-menu');
  if (!menu) return;
  menu.innerHTML = '';
  if (!contexts || contexts.length === 0) {
    const li = document.createElement('li');
    li.textContent = 'No contexts available';
    li.setAttribute('aria-disabled', 'true');
    menu.appendChild(li);
    return;
  }
  for (const ctx of contexts) {
    const li = document.createElement('li');
    li.textContent = ctx.label || ctx.name || ctx.id;
    li.setAttribute('role', 'option');
    li.dataset.contextId = ctx.id;
    li.dataset.active = ctx.id === selectedContextId ? 'true' : 'false';
    li.setAttribute('aria-selected', ctx.id === selectedContextId ? 'true' : 'false');
    li.tabIndex = 0;
    const handle = () => {
      const sel = document.getElementById('voq-context-select');
      if (sel) {
        sel.value = ctx.id;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      }
      closeContextMenu();
    };
    li.addEventListener('click', handle);
    li.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); handle(); } });
    menu.appendChild(li);
  }
}
function openContextMenu() {
  const menu = document.getElementById('voq-context-menu');
  const btn = document.getElementById('voq-context-menu-button');
  if (!menu || !btn) return;
  menu.hidden = false;
  btn.setAttribute('aria-expanded', 'true');
}
function closeContextMenu() {
  const menu = document.getElementById('voq-context-menu');
  const btn = document.getElementById('voq-context-menu-button');
  if (!menu || !btn) return;
  menu.hidden = true;
  btn.setAttribute('aria-expanded', 'false');
}
function bindContextMenuButton() {
  const btn = document.getElementById('voq-context-menu-button');
  const menu = document.getElementById('voq-context-menu');
  if (!btn || !menu) return;
  let lastTapAt = 0;
  let doubleTapFired = false;
  const DOUBLE_TAP_MS = 320;
  btn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    if (doubleTapFired) {
      doubleTapFired = false;
      return;
    }
    const now = Date.now();
    if (now - lastTapAt <= DOUBLE_TAP_MS) {
      lastTapAt = 0;
      doubleTapFired = true;
      toggleAsrDebugVisible('hamburger_double_tap');
      if (!menu.hidden) closeContextMenu();
      return;
    }
    lastTapAt = now;
    if (menu.hidden) openContextMenu(); else closeContextMenu();
  });
  document.addEventListener('click', (ev) => {
    if (menu.hidden) return;
    if (ev.target === btn || btn.contains(ev.target) || menu.contains(ev.target)) return;
    closeContextMenu();
  });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeContextMenu(); });
  setAsrDebugVisible(false, 'init_hidden');
}
function bindTranscriptControls() {
  const chat = transcriptElement();
  const button = document.getElementById('voq-jump-latest');
  if (chat) chat.addEventListener('scroll', updateJumpLatest, { passive: true });
  if (button) button.addEventListener('click', scrollTranscriptToBottom);
  updateJumpLatest();
}

function createBubble({ id, role, content, kind }) {
  const bubble = document.createElement('article');
  bubble.className = `voq-bubble voq-bubble--${role}`;
  bubble.dataset.bubbleId = id;
  bubble.dataset.role = role;
  bubble.setAttribute('role', role === 'error' ? 'alert' : 'article');
  bubble.setAttribute('aria-label', role === 'user' ? 'You' : role === 'assistant' ? 'Assistant' : role === 'error' ? 'Error' : 'System');
  if (kind) bubble.dataset.kind = kind;
  const body = document.createElement('div');
  body.className = 'voq-bubble-body';
  body.textContent = safeString(content);
  bubble.appendChild(body);
  return bubble;
}

function renderUserBubble(state, { id, text }) {
  const chat = transcriptElement();
  if (!chat) return;
  clearEmptyState();
  const wasNearBottom = isNearBottom(chat);
  const bubble = createBubble({ id: `user-${id}`, role: 'user', content: text });
  chat.appendChild(bubble);
  state.transcriptIds.set(`user-${id}`, bubble);
  maybeAutoScroll(chat, wasNearBottom);
}

function renderOrUpdateLogBubble(state, item) {
  const chat = transcriptElement();
  if (!chat) return;
  clearEmptyState();
  const wasNearBottom = isNearBottom(chat);
  const key = `log-${item.id}`;
  const existing = state.transcriptIds.get(key);
  const role = item.type === 'response' ? 'assistant' : item.type === 'agent' ? 'assistant' : 'system';
  const content = item.content ?? '';
  // M7.3: reconcile with a live cx-stream bubble so a single assistant bubble is shown.
  if (!existing && (item.type === 'response' || item.type === 'agent')) {
    const cxBubble = cxBubbleForLogItem(item);
    if (cxBubble) {
      const body = cxBubble.querySelector('.voq-bubble-body');
      if (body) body.textContent = safeString(content);
      cxBubble.dataset.kind = item.type;
      cxBubble.dataset.final = item.type === 'response' ? 'true' : 'false';
      cxBubble.dataset.streaming = 'false';
      cxBubble.dataset.reconciledLogId = item.id;
      const streamId = cxBubble.dataset.cxStreamId || '';
      if (streamId) cx.reconciledLogIds.add(`${streamId}::${item.id}`);
      state.transcriptIds.set(key, cxBubble);
      maybeAutoScroll(chat, wasNearBottom);
      if (item.type === 'response') maybeSpeakResponse(item);
      return;
    }
  }
  if (existing) {
    const body = existing.querySelector('.voq-bubble-body');
    if (body) body.textContent = safeString(content);
    existing.dataset.kind = item.type;
    existing.dataset.final = item.type === 'response' ? 'true' : 'false';
  } else {
    const bubble = createBubble({ id: key, role, content, kind: item.type });
    bubble.dataset.final = item.type === 'response' ? 'true' : 'false';
    chat.appendChild(bubble);
    state.transcriptIds.set(key, bubble);
  }
  maybeAutoScroll(chat, wasNearBottom);
  if (item.type === 'response') {
    maybeSpeakResponse(item);
  }
}


function renderPreloadedResponseBubble(state, item) {
  const chat = transcriptElement();
  if (!chat || !item) return;
  chat.innerHTML = '';
  const key = `preload-${item.id || 'last-response'}`;
  const bubble = createBubble({
    id: key,
    role: 'assistant',
    content: item.content ?? '',
    kind: item.type || 'response',
  });
  bubble.dataset.final = 'true';
  bubble.dataset.preloaded = 'true';
  chat.appendChild(bubble);
  if (state?.transcriptIds) {
    state.transcriptIds.clear();
    state.transcriptIds.set(key, bubble);
    if (item.id) state.transcriptIds.set(`log-${item.id}`, bubble);
  }
  scrollTranscriptToBottom();
}


function warmVoqSessionForContext(contextId, reason = 'preload') {
  const page = globalThis.__voqualizer_page;
  if (!contextId) return null;
  if (page) {
    page.lastVoqWarmupAt = Date.now();
    page.lastVoqWarmupContextId = contextId;
    page.lastVoqWarmupReason = reason;
    page.lastVoqWarmupError = '';
  }
  const promise = initVoqSession(contextId).then((result) => {
    if (page) {
      page.lastVoqWarmupReadyAt = Date.now();
      page.lastVoqWarmupSessionId = tts.sessionId || result?.sessionId || '';
      page.lastVoqWarmupReady = true;
    }
    return result;
  }).catch((error) => {
    if (page) {
      page.lastVoqWarmupReady = false;
      page.lastVoqWarmupError = error?.message || String(error);
    }
    return null;
  });
  return promise;
}

async function preloadLastMonologueResult(state, contextId) {
  const page = globalThis.__voqualizer_page;
  if (!state || !contextId) return;
  const requestId = generateMessageId();
  state.lastPreloadRequestId = requestId;
  if (page) {
    page.lastMonologuePreloadAt = Date.now();
    page.lastMonologuePreloadContextId = contextId;
    page.lastMonologuePreloadFound = false;
    page.lastMonologuePreloadError = '';
  }
  // Warm the optional realtime/TTS Socket.IO session while the previous
  // monologue result is being fetched. This removes the cold-start cost from
  // the next prompt without blocking the transcript preload.
  void warmVoqSessionForContext(contextId, 'monologue_preload');
  try {
    const snapshot = await pollOnce(contextId, PRELOAD_MONOLOGUE_LOG_FROM);
    if (state.lastPreloadRequestId !== requestId) return;
    const logs = Array.isArray(snapshot?.logs) ? snapshot.logs : [];
    const lastResponse = [...logs].reverse().find((item) => item && item.type === 'response' && safeString(item.content).trim());
    if (typeof snapshot?.log_version === 'number') {
      state.lastLogVersion = snapshot.log_version;
      if (page) page.lastLogVersion = snapshot.log_version;
    }
    if (lastResponse) {
      renderPreloadedResponseBubble(state, lastResponse);
      if (page) {
        page.lastMonologuePreloadFound = true;
        page.lastMonologuePreloadLogId = lastResponse.id || '';
        page.lastMonologuePreloadTextLength = safeString(lastResponse.content).length;
      }
    } else {
      const chat = transcriptElement();
      if (chat) {
        chat.innerHTML = '<div class="voq-empty-state">No previous monologue result for this context yet.</div>';
      }
      if (state.transcriptIds) state.transcriptIds.clear();
      if (page) {
        page.lastMonologuePreloadFound = false;
        page.lastMonologuePreloadLogId = '';
        page.lastMonologuePreloadTextLength = 0;
      }
    }
  } catch (error) {
    if (state.lastPreloadRequestId !== requestId) return;
    if (page) page.lastMonologuePreloadError = error?.message || String(error);
  }
}

function renderErrorRow(state, message) {
  const chat = transcriptElement();
  if (!chat) return;
  clearEmptyState();
  const wasNearBottom = isNearBottom(chat);
  const id = `error-${Date.now()}`;
  const bubble = createBubble({ id, role: 'error', content: message, kind: 'error' });
  chat.appendChild(bubble);
  state.transcriptIds.set(id, bubble);
  maybeAutoScroll(chat, wasNearBottom);
}

const sendIndicator = { state: 'idle', wasBusy: false };

function setSendIndicatorState(next) {
  sendIndicator.state = next;
  const button = document.getElementById('voq-send-button');
  if (button) button.dataset.sendState = next;
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.sendIndicatorState = next;
  }
}

function resetSendIndicatorOnInteraction() {
  // Always clear the success cue + wasBusy latch on any prompt interaction so
  // the send button returns to its normal idle / enabled-if-text state.
  if (sendIndicator.state === 'success' || sendIndicator.wasBusy) {
    setSendIndicatorState('idle');
    sendIndicator.wasBusy = false;
    const button = document.getElementById('voq-send-button');
    if (button) {
      button.classList.remove('voq-send-success');
      button.dataset.sendState = 'idle';
    }
  }
}

function updateSendButton(state) {
  const button = document.getElementById('voq-send-button');
  const select = document.getElementById('voq-context-select');
  const prompt = document.getElementById('voq-prompt-input');
  if (!button) return;
  const hasContext = !!(globalThis.__voqualizer_page?.selectedContextId);
  const hasText = !!prompt?.value.trim();
  const busy = !!state.isSubmitting;
  // M8: never disable the send button. A stale busy/submitting latch should not
  // trap the UI; clicks remain available and submitPrompt decides whether there
  // is enough input/context to send.
  button.disabled = false;
  button.dataset.busy = busy ? 'true' : 'false';
  if (busy) {
    setSendIndicatorState('processing');
    sendIndicator.wasBusy = true;
  } else if (sendIndicator.wasBusy) {
    setSendIndicatorState('success');
    sendIndicator.wasBusy = false;
  } else {
    button.dataset.sendState = sendIndicator.state;
  }
  // Keep success styling visible even when the button is disabled (e.g. when
  // the prompt has been cleared after submit) so the user still sees the
  // green completion cue until they next interact with the prompt.
  button.classList.toggle('voq-send-success', sendIndicator.state === 'success');
  button.setAttribute('aria-disabled', 'false');
  button.setAttribute('title', busy ? 'Processing — click to send another prompt' : sendIndicator.state === 'success' ? 'Response complete — type or click to send again' : !hasContext ? 'Select a context before sending' : !hasText ? 'Type a prompt or use ASR' : 'Send prompt');
  if (select) {
    select.disabled = false;
    const selected = select.selectedOptions && select.selectedOptions[0];
    select.setAttribute('title', selected ? selected.textContent || 'Select Voqualizer context' : 'Select Voqualizer context');
  }
}

function updateTtsButton() {
  const button = document.getElementById('voq-tts-button');
  if (!button) return;
  let state = 'idle';
  if (!tts.enabled) state = 'off';
  else if (tts.tracker.activePlaybackSources.size > 0) state = 'speaking';
  else if (tts.lastError) state = 'error';
  button.dataset.ttsState = state;
  button.setAttribute('aria-pressed', tts.enabled ? 'true' : 'false');
  button.setAttribute('aria-label', tts.enabled ? 'Speak responses (on)' : 'Speak responses (off)');
  button.setAttribute('title', state === 'off' ? 'TTS off — click to speak assistant responses' : state === 'speaking' ? 'Speaking — click to stop and mute' : state === 'error' ? 'TTS error — click to retry/enable' : 'TTS on — click to mute assistant responses');
}

async function pollOnce(contextId, logFrom) {
  return callJsonApiWithDiagnostics(POLL_ENDPOINT, { context: contextId, log_from: logFrom }, 'poll');
}

async function runPollLoop(state, contextId, submissionId) {
  let sawResponse = false;
  while (state.activeSubmissionId === submissionId) {
    let snapshot;
    try {
      snapshot = await pollOnce(contextId, state.lastLogVersion || 0);
    } catch (error) {
      renderErrorRow(state, `Poll failed: ${error?.message || error}`);
      break;
    }
    if (snapshot && Array.isArray(snapshot.logs)) {
      for (const item of snapshot.logs) {
        if (!item || !item.id) continue;
        if (item.type === 'user') continue;
        if (item.type === 'agent' || item.type === 'response') {
          renderOrUpdateLogBubble(state, item);
          if (item.type === 'response') {
            sawResponse = true;
            // M8: response seen — flip the send indicator to success right
            // away so the green state appears at completion, independent of
            // whether log_progress_active has had time to transition to false.
            if (sendIndicator.wasBusy) {
              setSendIndicatorState('success');
              sendIndicator.wasBusy = false;
            }
            // Once the final response has arrived, the visible submit lifecycle
            // is complete even if /poll keeps reconciling backend progress.
            // Otherwise the first prompt interaction can see stale isSubmitting
            // and incorrectly flip the send icon back to processing.
            state.isSubmitting = false;
            if (globalThis.__voqualizer_page) {
              globalThis.__voqualizer_page.isSubmitting = false;
            }
            setPageStatus('Response complete', 'ready');
          }
        }
      }
      if (typeof snapshot.log_version === 'number') {
        state.lastLogVersion = snapshot.log_version;
      }
      if (snapshot.deselect_chat) {
        setPageStatus('Context was deselected by server.', 'warn');
        break;
      }
      if (sawResponse && snapshot.log_progress_active === false) break;
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
  if (state.activeSubmissionId === submissionId) {
    state.activeSubmissionId = '';
    state.isSubmitting = false;
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.isSubmitting = false;
      globalThis.__voqualizer_page.lastLogVersion = state.lastLogVersion;
    }
    stopProcessingHeartbeat(sawResponse ? 'response_complete' : 'poll_idle');
    updateSendButton(state);
    setPageStatus(sawResponse ? 'Response complete' : 'Idle', sawResponse ? 'ready' : 'empty');
  }
}


async function submitPromptOverVoqSession(text, contextId, messageId) {
  const page = globalThis.__voqualizer_page;
  if (page) {
    page.lastWsPromptAttemptAt = Date.now();
    page.lastWsPromptMessageId = messageId;
    page.lastWsPromptContextId = contextId;
    page.lastWsPromptError = '';
  }
  try {
    await initVoqSession(contextId);
  } catch (error) {
    if (page) page.lastWsPromptError = `init_failed: ${error?.message || error}`;
    throw error;
  }
  if (!tts.socket || !tts.socket.connected || !tts.sessionId || !tts.bearerToken) {
    const err = new Error('voqualizer websocket session not ready');
    if (page) page.lastWsPromptError = err.message;
    throw err;
  }
  const ack = await new Promise((resolve, reject) => {
    let settled = false;
    const timeout = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error('voqualizer_text_prompt timeout'));
    }, 10000);
    tts.socket.emit('voqualizer_text_prompt', {
      session_id: tts.sessionId,
      bearer_token: tts.bearerToken,
      context: contextId,
      context_id: contextId,
      message_id: messageId,
      generation_id: messageId,
      text,
    }, (response) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (response && response.error) reject(new Error(response.error.message || 'voqualizer_text_prompt failed'));
      else resolve(response || {});
    });
  });
  if (page) {
    page.lastWsPromptAckAt = Date.now();
    page.lastWsPromptAckEvent = ack && ack.event || '';
    page.lastWsPromptAckStatus = ack && ack.status || '';
  }
  return ack;
}

async function submitPrompt(state) {
  const prompt = document.getElementById('voq-prompt-input');
  if (!prompt) return;
  const text = prompt.value.trim();
  if (!text) return;
  const contextId = globalThis.__voqualizer_page?.selectedContextId || '';
  if (!contextId) {
    renderErrorRow(state, 'Select a context before sending.');
    return;
  }
  if (state.isSubmitting) {
    resetSendIndicatorOnInteraction();
  }
  ensureAudioContext();
  cancelInflightTts('new_prompt');
  const messageId = generateMessageId();
  tts.livePushSinceSubmit = false;
  tts.lastLivePushAt = 0;
  tts.lastLivePushUtteranceId = '';
  startProcessingHeartbeat(messageId);
  state.isSubmitting = true;
  state.activeSubmissionId = messageId;
  cx.streamsBySubmitId.set(messageId, { contextId, started: Date.now(), streamId: '' });
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.isSubmitting = true;
    globalThis.__voqualizer_page.lastSubmitId = messageId;
    globalThis.__voqualizer_page.lastSubmitUiEchoAt = Date.now();
    globalThis.__voqualizer_page.lastSubmitVoqInitError = '';
  }
  setPageStatus('Sending…', 'loading');
  updateSendButton(state);
  renderUserBubble(state, { id: messageId, text });
  prompt.value = '';
  autosizePrompt(prompt);
  // Do not block visible submit feedback on optional realtime/TTS socket setup.
  // Socket.IO load/connect/init can take a noticeable moment on mobile; the
  // typed prompt submit path is authoritative, so show the echo immediately
  // and attach realtime cx/TTS in parallel when available.
  const voqInitPromise = (warmVoqSessionForContext(contextId, 'submit') || Promise.resolve(null)).catch((err) => {
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastSubmitVoqInitError = err?.message || String(err);
    }
  });
  try {
    let result;
    try {
      result = await submitPromptOverVoqSession(text, contextId, messageId);
      if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.promptSubmitTransport = 'websocket';
    } catch (wsError) {
      if (globalThis.__voqualizer_page) {
        globalThis.__voqualizer_page.promptSubmitTransport = 'http_fallback';
        globalThis.__voqualizer_page.lastWsPromptError = wsError?.message || String(wsError);
      }
      result = await callJsonApiWithDiagnostics(MESSAGE_ENDPOINT, { text, context: contextId, message_id: messageId }, 'message_async');
    }
    void voqInitPromise;
    if (!result) throw new Error('empty response from prompt submit');
    setPageStatus('Awaiting response…', 'loading');
    await runPollLoop(state, contextId, messageId);
  } catch (error) {
    stopProcessingHeartbeat('send_failed');
    renderErrorRow(state, `Send failed: ${error?.message || error}`);
    state.isSubmitting = false;
    state.activeSubmissionId = '';
    if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.isSubmitting = false;
    setPageStatus('Send failed', 'error');
    updateSendButton(state);
  }
}

function bindPromptInput(state) {
  const prompt = document.getElementById('voq-prompt-input');
  const button = document.getElementById('voq-send-button');
  if (!prompt) return;
  prompt.addEventListener('input', () => {
    resetSendIndicatorOnInteraction();
    autosizePrompt(prompt);
    updateSendButton(state);
  });
  prompt.addEventListener('focus', () => { resetSendIndicatorOnInteraction(); updateSendButton(state); });
  prompt.addEventListener('click', () => { resetSendIndicatorOnInteraction(); updateSendButton(state); });
  prompt.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    if (event.shiftKey) return;
    event.preventDefault();
    void submitPrompt(state);
  });
  if (button) button.addEventListener('click', () => void submitPrompt(state));
  autosizePrompt(prompt);
  updateSendButton(state);
}

async function loadContextPicker(state, select) {
  const page = globalThis.__voqualizer_page;
  if (!select || !page) return [];
  setSelectPlaceholder(select, 'Loading contexts…', { disabled: true });
  page.contextsLoading = true;
  page.contextError = '';
  setPageStatus('Loading contexts…', 'loading');
  try {
    const contexts = await fetchContexts();
    let initialHint = readSelectedContextHint();
    if (!initialHint) {
      const heroHint = await fetchHeroDefaultContextId();
      if (heroHint && contexts.some((ctx) => ctx.id === heroHint)) {
        initialHint = heroHint;
        page.heroDefaultApplied = true;
        page.heroDefaultContextId = heroHint;
      } else {
        page.heroDefaultApplied = false;
        page.heroDefaultContextId = heroHint || '';
      }
    } else {
      page.heroDefaultApplied = false;
      page.heroDefaultContextId = '';
    }
    const selectedContextId = renderContexts(select, contexts, initialHint);
    page.contexts = contexts;
    page.selectedContextId = selectedContextId;
    page.contextsLoading = false;
    page.contextError = '';
    page.contextCount = contexts.length;
    state.lastLogVersion = 0;
    tts.contextId = selectedContextId;
    if (selectedContextId) void preloadLastMonologueResult(state, selectedContextId);
    renderContextMenu(contexts, selectedContextId);
    updateHeaderContextName(selectedContextId);
    setPageStatus(contexts.length ? `Selected ${selectedContextId}` : 'No contexts found', contexts.length ? 'ready' : 'empty');
    updateSendButton(state);
    return contexts;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    setSelectPlaceholder(select, 'Contexts unavailable', { disabled: true });
    page.contexts = [];
    page.selectedContextId = '';
    page.contextsLoading = false;
    page.contextError = message;
    page.contextCount = 0;
    updateHeaderContextName('');
    setPageStatus('Voqualizer backend unavailable or plugin disabled.', 'error');
    updateSendButton(state);
    return [];
  }
}

function bindContextPicker(state, select) {
  if (!select) return;
  select.addEventListener('change', () => {
    const selectedContextId = select.value || '';
    persistSelectedContextId(selectedContextId);
    state.lastLogVersion = 0;
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.selectedContextId = selectedContextId;
      globalThis.__voqualizer_page.lastContextChangeAt = Date.now();
      globalThis.__voqualizer_page.lastLogVersion = 0;
      const contexts = globalThis.__voqualizer_page.contexts || [];
      for (const ctx of contexts) ctx.active = ctx.id === selectedContextId;
    }
    handleContextChange(selectedContextId);
    const page = globalThis.__voqualizer_page;
    if (page && Array.isArray(page.contexts)) renderContextMenu(page.contexts, selectedContextId);
    updateHeaderContextName(selectedContextId);
    const chat = transcriptElement();
    if (chat) chat.innerHTML = '<div class="voq-empty-state">Loading latest monologue result…</div>';
    if (state.transcriptIds) state.transcriptIds.clear();
    if (selectedContextId) void preloadLastMonologueResult(state, selectedContextId);
    setPageStatus(selectedContextId ? `Selected ${selectedContextId}` : 'No context selected', selectedContextId ? 'ready' : 'empty');
    updateSendButton(state);
  });
}

function handleContextChange(newContextId) {
  cancelInflightTts();
  if (asr.capturing) {
    void stopAsrCapture({ silent: true });
  }
  tts.spokenResponseIds.clear();
  clearCxStreamState({ keepCapability: true });
  clearAllWordHighlights();
  tts.pcm16CarryMap.clear();
  tts.encodedBuffers.clear();
  if (tts.contextId !== newContextId) {
    disconnectVoq();
  }
  tts.contextId = newContextId;
}

function ensureAudioContext(reason = 'ensure') {
  if (!tts.audioContext) {
    try {
      const Ctx = globalThis.AudioContext || globalThis.webkitAudioContext;
      if (Ctx) tts.audioContext = new Ctx();
      if (globalThis.__voqualizer_page) {
        globalThis.__voqualizer_page.lastAudioContextCreateAt = Date.now();
        globalThis.__voqualizer_page.lastAudioContextCreateReason = reason;
      }
    } catch (error) {
      if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.lastAudioContextError = error?.message || String(error);
    }
  }
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.audioContextState = tts.audioContext ? tts.audioContext.state : '';
  }
  return tts.audioContext;
}

async function resumeAudioContext(reason = 'resume') {
  const ctx = ensureAudioContext(reason);
  if (!ctx) return null;
  try {
    if (ctx.state === 'suspended') await ctx.resume();
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastAudioResumeAt = Date.now();
      globalThis.__voqualizer_page.lastAudioResumeReason = reason;
      globalThis.__voqualizer_page.lastAudioResumeError = '';
      globalThis.__voqualizer_page.audioContextState = ctx.state;
    }
  } catch (error) {
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastAudioResumeAt = Date.now();
      globalThis.__voqualizer_page.lastAudioResumeReason = reason;
      globalThis.__voqualizer_page.lastAudioResumeError = error?.message || String(error);
      globalThis.__voqualizer_page.audioContextState = ctx.state;
    }
  }
  return ctx;
}

function installTtsAudioUnlockHandlers() {
  if (globalThis.__voqualizerTtsAudioUnlockInstalled) return;
  globalThis.__voqualizerTtsAudioUnlockInstalled = true;
  const unlock = (reason) => { void resumeAudioContext(reason); };
  document.addEventListener('pointerdown', () => unlock('pointerdown'), { passive: true, capture: true });
  document.addEventListener('touchstart', () => unlock('touchstart'), { passive: true, capture: true });
  document.addEventListener('keydown', () => unlock('keydown'), { passive: true, capture: true });
}

async function loadSocketIo() {
  if (globalThis.io) return globalThis.io;
  try {
    const mod = await import('/vendor/socket.io.esm.min.js');
    return mod.io || mod.default || globalThis.io;
  } catch (error) {
    tts.lastError = `socket.io load failed: ${error?.message || error}`;
    throw error;
  }
}

async function fetchCsrfTokenSafe() {
  try {
    const api = await import('/js/api.js');
    if (typeof api.getCsrfToken === 'function') return await api.getCsrfToken();
  } catch (_err) {}
  return '';
}

async function connectVoq() {
  if (tts.socket && tts.socket.connected) return tts.socket;
  if (tts.connecting) return tts.connecting;
  tts.connecting = (async () => {
    const io = await loadSocketIo();
    const csrf = await fetchCsrfTokenSafe();
    const socket = io('/ws', {
      transports: ['websocket', 'polling'],
      withCredentials: true,
      auth: (cb) => cb({ csrf_token: csrf, handlers: [VOQUALIZER_HANDLER] }),
    });
    if (tts.socket && tts.socket !== socket) {
      try { tts.socket.removeAllListeners && tts.socket.removeAllListeners(); } catch (_err) {}
      try { tts.socket.disconnect && tts.socket.disconnect(); } catch (_err) {}
    }
    tts.socket = socket;
    const activeSocketOnly = (handler, eventName) => (payload) => {
      if (socket !== tts.socket) {
        if (globalThis.__voqualizer_page) {
          globalThis.__voqualizer_page.lastStaleTtsSocketEventAt = Date.now();
          globalThis.__voqualizer_page.lastStaleTtsSocketEvent = eventName;
        }
        return;
      }
      handler(payload);
    };
    socket.on('voqualizer_tts_chunk', activeSocketOnly(handleTtsChunk, 'voqualizer_tts_chunk'));
    socket.on('voqualizer_tts_done', activeSocketOnly(handleTtsDone, 'voqualizer_tts_done'));
    socket.on('voqualizer_asr_partial', activeSocketOnly(handleAsrPartial, 'voqualizer_asr_partial'));
    socket.on('voqualizer_asr_final', activeSocketOnly(handleAsrFinal, 'voqualizer_asr_final'));
    socket.on('voqualizer_audio_ack', activeSocketOnly(handleAudioAck, 'voqualizer_audio_ack'));
    socket.on('voqualizer_error', activeSocketOnly(handleVoqError, 'voqualizer_error'));
    socket.on('voqualizer_cx_stream_start', activeSocketOnly(handleCxStreamStart, 'voqualizer_cx_stream_start'));
    socket.on('voqualizer_cx_token', activeSocketOnly(handleCxToken, 'voqualizer_cx_token'));
    socket.on('voqualizer_cx_stream_final', activeSocketOnly(handleCxStreamFinal, 'voqualizer_cx_stream_final'));
    socket.on('voqualizer_cx_stream_error', activeSocketOnly(handleCxStreamError, 'voqualizer_cx_stream_error'));
    socket.on('voqualizer_tts_word_plan', activeSocketOnly(handleTtsWordPlan, 'voqualizer_tts_word_plan'));
    socket.on('disconnect', () => {
      if (socket !== tts.socket) return;
      tts.ready = false;
      clearAllWordHighlights();
      clearCxStreamState({ keepCapability: true });
      updateTtsButton();
      if (globalThis.__voqualizer_page) {
        globalThis.__voqualizer_page.lastRealtimeDisconnectAt = Date.now();
        globalThis.__voqualizer_page.activeWordUtteranceId = '';
        globalThis.__voqualizer_page.activeWordIndex = -1;
      }
    });
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('socket connect timeout')), 8000);
      socket.on('connect', () => { clearTimeout(timeout); resolve(); });
      socket.on('connect_error', (err) => { clearTimeout(timeout); reject(err); });
    });
    return socket;
  })();
  try {
    return await tts.connecting;
  } finally {
    tts.connecting = null;
  }
}

async function initVoqSession(contextId) {
  if (tts.ready && tts.contextId === contextId && tts.sessionId && tts.bearerToken && tts.socket && tts.socket.connected) return tts;
  if (tts.ready && (!tts.socket || !tts.socket.connected || !tts.bearerToken)) {
    tts.ready = false;
    tts.sessionId = '';
    tts.bearerToken = '';
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastTtsInitError = 'stale_session_reconnect';
      globalThis.__voqualizer_page.ttsReady = false;
    }
  }
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.lastTtsInitStartAt = Date.now();
    globalThis.__voqualizer_page.lastTtsInitContextId = contextId;
    globalThis.__voqualizer_page.lastTtsInitError = '';
    globalThis.__voqualizer_page.ttsReady = false;
  }
  let socket;
  try {
    socket = await connectVoq();
  } catch (error) {
    const message = error?.message || String(error);
    tts.lastError = `connect failed: ${message}`;
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastTtsInitError = tts.lastError;
      globalThis.__voqualizer_page.lastTtsError = tts.lastError;
    }
    throw error;
  }
  const sessionId = generateMessageId();
  const payload = {
    session_id: sessionId,
    context_id: contextId,
    barge_in: !!asr.enabled,
    asr: { enabled: !!asr.enabled },
    tts: { enabled: !!tts.enabled },
    asr_submit_mode: ASR_SUBMIT_MODE,
  };
  let ready;
  try {
    ready = await new Promise((resolve, reject) => {
      let settled = false;
      const timeout = setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(new Error('voqualizer_init timeout'));
      }, 10000);
      socket.emit('voqualizer_init', payload, (ack) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        if (ack && ack.event === 'voqualizer_ready') resolve(ack);
        else if (ack && ack.error) reject(new Error(ack.error.message || 'voqualizer_init failed'));
        else resolve(ack || {});
      });
    });
  } catch (error) {
    const message = error?.message || String(error);
    tts.lastError = `init failed: ${message}`;
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastTtsInitError = tts.lastError;
      globalThis.__voqualizer_page.lastTtsError = tts.lastError;
      globalThis.__voqualizer_page.ttsReady = false;
    }
    throw error;
  }
  tts.sessionId = ready.session_id || sessionId;
  tts.bearerToken = ready.bearer_token || '';
  tts.contextId = contextId;
  tts.ready = true;
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.ttsReady = true;
    globalThis.__voqualizer_page.ttsSessionId = tts.sessionId;
    globalThis.__voqualizer_page.lastTtsInitReadyAt = Date.now();
    globalThis.__voqualizer_page.lastTtsInitError = '';
  }
  cx.enabledByCapability = !!(ready.capabilities && ready.capabilities.cx_stream);
  const wordPlanCap = !!(ready.capabilities && ready.capabilities.tts_word_plan);
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.cxStreamCapability = cx.enabledByCapability;
    globalThis.__voqualizer_page.wordPlanCapability = wordPlanCap;
    globalThis.__voqualizer_page.protocolVersion = ready.capabilities && ready.capabilities.protocol_version || '';
  }
  return tts;
}

async function speakText(text, { utteranceId } = {}) {
  const trimmed = safeString(text).trim();
  const page = globalThis.__voqualizer_page;
  if (page) {
    page.lastTtsSpeakEntryAt = Date.now();
    page.lastTtsSpeakEntryTextLength = trimmed.length;
    page.lastTtsSpeakEntryUtteranceId = utteranceId || '';
    page.lastTtsSpeakSkipReason = '';
  }
  if (!trimmed) { if (page) page.lastTtsSpeakSkipReason = 'empty_text'; return; }
  if (!tts.enabled) { if (page) page.lastTtsSpeakSkipReason = 'tts_disabled'; return; }
  const contextId = tts.contextId || (globalThis.__voqualizer_page?.selectedContextId || '');
  if (!contextId) { if (page) page.lastTtsSpeakSkipReason = 'missing_context'; return; }
  await resumeAudioContext('speakText');
  try {
    await initVoqSession(contextId);
  } catch (error) {
    tts.lastError = `init failed: ${error?.message || error}`;
    if (page) {
      page.lastTtsError = tts.lastError;
      page.lastTtsSpeakSkipReason = 'init_failed';
    }
    updateTtsButton();
    return;
  }
  const id = utteranceId || `voq-utt-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  tts.activeDirectUtteranceId = id;
  tts.acceptedTtsUtteranceIds.add(id);
  tts.lastSpeakAt = Date.now();
  tts.lastError = '';
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.lastTtsSpeakAt = tts.lastSpeakAt;
    globalThis.__voqualizer_page.lastDirectTtsAt = tts.lastSpeakAt;
    globalThis.__voqualizer_page.lastDirectTtsTextLength = trimmed.length;
    globalThis.__voqualizer_page.lastTtsError = '';
    globalThis.__voqualizer_page.lastTtsSpeakSkipReason = '';
  }
  const emitDirectTts = async (attempt = 1) => new Promise((resolve) => {
    let settled = false;
    const finish = (ack) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (globalThis.__voqualizer_page) {
        globalThis.__voqualizer_page.lastDirectTtsAckAt = Date.now();
        globalThis.__voqualizer_page.lastDirectTtsAckRawType = typeof ack;
        globalThis.__voqualizer_page.lastDirectTtsAttempt = attempt;
      }
      handleTtsAckFallback(ack, id);
      if (ack && ack.error) {
        tts.lastError = ack.error.message || 'speak failed';
        if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.lastTtsError = tts.lastError;
        updateTtsButton();
      }
      resolve(ack);
    };
    const timeout = setTimeout(() => finish({ error: { message: 'direct_tts_ack_timeout' }, reason: 'direct_tts_ack_timeout' }), 20000);
    try {
      tts.socket.emit('voqualizer_user_text', {
        session_id: tts.sessionId,
        bearer_token: tts.bearerToken,
        utterance_id: id,
        text: trimmed,
      }, finish);
    } catch (error) {
      finish({ error: { message: `emit_failed: ${error?.message || error}` }, reason: 'emit_failed' });
    }
  });
  try {
    const ack = await emitDirectTts(1);
    const reason = safeString(ack?.reason || ack?.error?.message || '');
    const code = safeString(ack?.error?.code || ack?.code || '');
    const ackHasChunks = !!(ack && Array.isArray(ack.tts_chunks) && ack.tts_chunks.length);
    const shouldRetry = !ackHasChunks && tts.enabled && (
      reason === 'direct_tts_ack_timeout' ||
      /^(NO_SESSION|SESSION_NOT_ACTIVE)$/i.test(code) ||
      /session .* not active|send voqualizer_init before/i.test(reason)
    );
    if (shouldRetry) {
      tts.ready = false;
      try { if (tts.socket) tts.socket.disconnect(); } catch (_err) {}
      tts.socket = null;
      tts.sessionId = '';
      tts.bearerToken = '';
      if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.lastDirectTtsRetryAt = Date.now();
      await initVoqSession(contextId);
      await emitDirectTts(2);
    }
  } catch (error) {
    tts.lastError = `speak failed: ${error?.message || error}`;
    if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.lastTtsError = tts.lastError;
  }
  updateTtsButton();
}

function cancelInflightTts(reason = 'cancel') {
  if (reason !== 'processing_heartbeat') stopProcessingHeartbeat(`cancel:${reason}`);
  if (tts.tracker) tts.tracker.stopAllPlayback();
  if (tts.socket && tts.socket.connected && tts.sessionId) {
    try {
      tts.socket.emit('voqualizer_control', {
        session_id: tts.sessionId,
        bearer_token: tts.bearerToken,
        action: 'cancel_tts',
      });
    } catch (_err) {}
  }
  updateTtsButton();
}

function disconnectVoq() {
  cancelInflightTts();
  if (tts.socket) {
    try {
      tts.socket.emit('voqualizer_control', {
        session_id: tts.sessionId,
        bearer_token: tts.bearerToken,
        action: 'end_session',
      });
    } catch (_err) {}
    try { tts.socket.disconnect(); } catch (_err) {}
  }
  tts.socket = null;
  tts.sessionId = '';
  tts.bearerToken = '';
  tts.ready = false;
}

function handleVoqError(payload) {
  const data = (payload && payload.data) || payload || {};
  tts.lastError = safeString(data.message || data.code || 'voqualizer error');
  updateTtsButton();
}

function shouldAcceptTtsUtterance(utteranceId, source = 'chunk') {
  const id = safeString(utteranceId || '');
  const page = globalThis.__voqualizer_page;
  if (/^agent-sentence-/i.test(id)) {
    tts.livePushSinceSubmit = true;
    tts.lastLivePushAt = Date.now();
    tts.lastLivePushUtteranceId = id;
    tts.acceptedTtsUtteranceIds.add(id);
    if (page) {
      page.lastLivePushedTtsAt = tts.lastLivePushAt;
      page.lastLivePushedTtsUtteranceId = id;
      page.lastLivePushedTtsSource = source;
      page.lastTtsIgnoredUtteranceId = '';
      page.lastTtsIgnoredReason = '';
      page.lastTtsIgnoredSource = '';
    }
  }
  return true;
}
function handleTtsChunk(payload) {
  if (!tts.enabled) return;
  const data = (payload && payload.data) || payload || {};
  const utteranceId = safeString(data.utterance_id || data.utteranceId || 'default');
  if (!shouldAcceptTtsUtterance(utteranceId, 'chunk')) return;
  if (tts.tracker.cancelledTtsUtterances.has(utteranceId)) return;
  const codec = normalizeTtsCodec(data, payload);
  const sampleRate = ttsSampleRate(data, payload, codec);
  const bytes = bytesFromTtsPayload(payload);
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.ttsChunkCount = (Number(globalThis.__voqualizer_page.ttsChunkCount || 0) + 1);
    globalThis.__voqualizer_page.lastTtsChunkAt = Date.now();
    globalThis.__voqualizer_page.lastTtsChunkBytes = bytes && bytes.byteLength ? bytes.byteLength : 0;
    globalThis.__voqualizer_page.lastTtsChunkCodec = codec;
    globalThis.__voqualizer_page.lastTtsChunkSampleRate = sampleRate;
    globalThis.__voqualizer_page.lastTtsChunkUtteranceId = utteranceId;
    globalThis.__voqualizer_page.audioContextState = tts.audioContext ? tts.audioContext.state : '';
  }
  if (!bytes || !bytes.byteLength) return;
  if (codec === 'pcm16/16k' || codec === 'pcm16/24k') {
    playPcmChunk(bytes, sampleRate, utteranceId);
  } else {
    bufferEncodedChunk(bytes, codec, utteranceId, !!(data.is_final || data.final));
  }
}

function handleTtsAckFallback(ack, fallbackUtteranceId = '') {
  if (fallbackUtteranceId) tts.acceptedTtsUtteranceIds.add(safeString(fallbackUtteranceId));
  if (!ack || ack.error || !tts.enabled) return;
  const chunks = Array.isArray(ack.tts_chunks) ? ack.tts_chunks : [];
  if (!chunks.length) return;
  const utteranceId = safeString(ack.utterance_id || fallbackUtteranceId || 'default');
  if (tts.livePushSinceSubmit || (globalThis.__voqualizer_page?.lastLivePushedTtsAt && globalThis.__voqualizer_page?.lastSubmitUiEchoAt && globalThis.__voqualizer_page.lastLivePushedTtsAt >= globalThis.__voqualizer_page.lastSubmitUiEchoAt)) {
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastAckTtsFallbackSuppressedAt = Date.now();
      globalThis.__voqualizer_page.lastAckTtsFallbackSuppressedChunks = chunks.length;
      globalThis.__voqualizer_page.lastAckTtsFallbackSuppressedUtteranceId = utteranceId;
      globalThis.__voqualizer_page.lastAckTtsFallbackSuppressedReason = 'live_push_already_streamed';
      globalThis.__voqualizer_page.lastDirectTtsAck = {
        event: safeString(ack.event || ''),
        utterance_id: safeString(ack.utterance_id || fallbackUtteranceId || ''),
        chunks: Number(ack.chunks || 0),
        tts_chunks: chunks.length,
        suppressed: true,
        suppress_reason: 'live_push_already_streamed',
        pushed_emit_count: Number(ack.pushed_emit_count || 0),
        sender_present: !!ack.sender_present,
      };
    }
    updateTtsButton();
    return;
  }
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.lastAckTtsFallbackAt = Date.now();
    globalThis.__voqualizer_page.lastAckTtsFallbackChunks = chunks.length;
    globalThis.__voqualizer_page.lastAckTtsFallbackReason = ack.delivery_fallback || 'ack_chunks';
    globalThis.__voqualizer_page.lastAckTtsPushedEmitCount = Number(ack.pushed_emit_count || 0);
    globalThis.__voqualizer_page.lastAckTtsSenderPresent = !!ack.sender_present;
    globalThis.__voqualizer_page.lastDirectTtsAck = {
      event: safeString(ack.event || ''),
      utterance_id: safeString(ack.utterance_id || fallbackUtteranceId || ''),
      chunks: Number(ack.chunks || 0),
      tts_chunks: chunks.length,
      delivery_fallback: safeString(ack.delivery_fallback || ''),
      pushed_emit_count: Number(ack.pushed_emit_count || 0),
      pushed_done_emit_count: Number(ack.pushed_done_emit_count || 0),
      sender_present: !!ack.sender_present,
      has_tts_done: !!ack.tts_done,
      has_tts_word_plan: !!ack.tts_word_plan,
    };
  }
  for (const chunk of chunks) {
    const data = chunk && chunk.data ? { ...chunk.data } : { ...(chunk || {}) };
    if (!data.utterance_id && !data.utteranceId) data.utterance_id = utteranceId;
    handleTtsChunk(data);
  }
  if (ack.tts_word_plan) handleTtsWordPlan(ack.tts_word_plan);
  if (ack.tts_done) handleTtsDone(ack.tts_done);
  updateTtsButton();
}

function playPcmChunk(bytes, sampleRate, utteranceId) {
  const ctx = ensureAudioContext('playPcmChunk');
  if (!ctx) return;
  const aligned = alignPcm16Bytes(bytes, tts.pcm16CarryMap, utteranceId);
  if (!aligned.byteLength) return;
  const float = pcm16ToFloat32(aligned);
  if (!float.length) return;
  const buffer = ctx.createBuffer(1, float.length, sampleRate);
  buffer.copyToChannel(float, 0);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  const startAt = Math.max(ctx.currentTime + 0.01, tts.playbackTail);
  try {
    source.start(startAt);
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastPlaybackStartAt = Date.now();
      globalThis.__voqualizer_page.lastPlaybackStartAudioTime = startAt;
      globalThis.__voqualizer_page.lastPlaybackDurationMs = Math.round(buffer.duration * 1000);
      globalThis.__voqualizer_page.lastPlaybackUtteranceId = utteranceId;
      globalThis.__voqualizer_page.lastPlaybackError = '';
      globalThis.__voqualizer_page.audioContextState = ctx.state;
    }
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    tts.lastError = `playback failed: ${message}`;
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastPlaybackError = message;
      globalThis.__voqualizer_page.audioContextState = ctx.state;
    }
    updateTtsButton();
    return;
  }
  if (!wordPlan.playbackStartByUtteranceId.has(utteranceId)) {
    wordPlan.playbackStartByUtteranceId.set(utteranceId, startAt);
    ensureWordHighlightLoop();
  }
  tts.playbackTail = startAt + buffer.duration;
  rememberPlaybackSource(tts.tracker, utteranceId, source);
  updateTtsButton();
}

function bufferEncodedChunk(bytes, codec, utteranceId, isFinal) {
  const key = utteranceId || 'default';
  const existing = tts.encodedBuffers.get(key) || [];
  existing.push(bytes);
  tts.encodedBuffers.set(key, existing);
  if (isFinal) {
    const joined = concatAudioBytes(existing);
    const repaired = codec === 'wav' ? repairRiffWaveHeader(joined) : joined;
    tts.encodedBuffers.delete(key);
    const mime = codec === 'wav' ? 'audio/wav' : codec === 'mp3' ? 'audio/mpeg' : 'audio/ogg';
    const blob = new Blob([repaired], { type: mime });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.addEventListener('ended', () => { URL.revokeObjectURL(url); }, { once: true });
    try { audio.play(); } catch (_err) {}
  }
}

function handleTtsDone(payload) {
  const data = (payload && payload.data) || payload || {};
  const utteranceId = safeString(data.utterance_id || data.utteranceId || 'default');
  if (!shouldAcceptTtsUtterance(utteranceId, 'done')) return;
  if (data.cancelled || data.reason === 'barge_in') {
    tts.tracker.stopPlaybackForUtterance(utteranceId);
    finalizeWordHighlight(utteranceId, { cancelled: true });
  } else {
    finalizeWordHighlight(utteranceId, { cancelled: false });
  }
  clearPcm16Carry(tts.pcm16CarryMap, utteranceId);
  tts.encodedBuffers.delete(utteranceId);
  updateTtsButton();
}

function maybeSpeakResponse(item) {
  const page = globalThis.__voqualizer_page;
  const now = Date.now();
  const type = safeString(item?.type || '');
  const content = safeString(item?.content || item?.message || item?.text || '');
  const id = safeString(item?.id || item?.message_id || item?.log_id || '');
  const fallbackId = id || `fallback-${type}-${content.length}-${stableTextHash(content)}`;
  const recordSkip = (reason) => {
    if (page) {
      page.lastTtsTriggerAt = now;
      page.lastTtsTriggerType = type;
      page.lastTtsTriggerItemId = id;
      page.lastTtsTriggerFallbackId = fallbackId;
      page.lastTtsTriggerTextLength = content.length;
      page.lastTtsSkipReason = reason;
    }
  };
  if (!tts.enabled) { recordSkip('tts_disabled'); return; }
  if (!item) { recordSkip('missing_item'); return; }
  if (type !== 'response') { recordSkip(`not_response:${type || 'empty'}`); return; }
  if (!content.trim()) { recordSkip('missing_content'); return; }
  if (tts.livePushSinceSubmit || (page?.lastLivePushedTtsAt && page?.lastSubmitUiEchoAt && page.lastLivePushedTtsAt >= page.lastSubmitUiEchoAt)) {
    recordSkip('live_push_already_streamed');
    return;
  }
  if (tts.spokenResponseIds.has(fallbackId)) { recordSkip('duplicate'); return; }
  tts.spokenResponseIds.add(fallbackId);
  const utteranceId = `voq-resp-${fallbackId}`;
  if (page) {
    page.lastTtsTriggerAt = now;
    page.lastTtsTriggerType = type;
    page.lastTtsTriggerItemId = id;
    page.lastTtsTriggerFallbackId = fallbackId;
    page.lastTtsTriggerTextLength = content.length;
    page.lastTtsSkipReason = '';
    page.lastTtsSpeakQueuedAt = now;
    page.lastTtsSpeakQueuedUtteranceId = utteranceId;
  }
  registerWordPlanBubble(utteranceId, id || fallbackId);
  void speakText(content, { utteranceId });
}

function findOrCreateCxBubble(streamId, messageId) {
  if (!streamId) return null;
  const existing = cx.bubblesByStreamId.get(streamId);
  if (existing && existing.isConnected) return existing;
  const chat = transcriptElement();
  if (!chat) return null;
  clearEmptyState();
  const wasNearBottom = isNearBottom(chat);
  const bubble = createBubble({ id: `cx-${streamId}`, role: 'assistant', content: '', kind: 'response' });
  bubble.dataset.cxStreamId = streamId;
  bubble.dataset.messageId = messageId || '';
  bubble.dataset.final = 'false';
  bubble.dataset.streaming = 'true';
  chat.appendChild(bubble);
  cx.bubblesByStreamId.set(streamId, bubble);
  maybeAutoScroll(chat, wasNearBottom);
  return bubble;
}

function handleCxStreamStart(payload) {
  const data = (payload && payload.data) || payload || {};
  const streamId = safeString(data.stream_id);
  if (!streamId) return;
  const messageId = safeString(data.message_id);
  cx.streamsByStreamId.set(streamId, { messageId, contextId: safeString(data.context_id), startedAt: Date.now() });
  cx.lastSeqByStreamId.set(streamId, 0);
  cx.lastEvent = 'voqualizer_cx_stream_start';
  cx.lastEventAt = Date.now();
  stopProcessingHeartbeat('cx_stream_start');
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.cxLastEvent = cx.lastEvent;
    globalThis.__voqualizer_page.cxLastStreamId = streamId;
    globalThis.__voqualizer_page.cxActiveStreamCount = cxActiveStreamCount();
  }
  findOrCreateCxBubble(streamId, messageId);
}

function handleCxToken(payload) {
  const data = (payload && payload.data) || payload || {};
  const streamId = safeString(data.stream_id);
  if (!streamId) return;
  const seq = Number(data.seq || 0);
  const lastSeq = cx.lastSeqByStreamId.get(streamId) || 0;
  if (seq && seq <= lastSeq) return;
  if (seq) cx.lastSeqByStreamId.set(streamId, seq);
  const messageId = safeString(data.message_id);
  stopProcessingHeartbeat('cx_token');
  const bubble = findOrCreateCxBubble(streamId, messageId);
  if (!bubble) return;
  const chat = transcriptElement();
  const wasNearBottom = chat ? isNearBottom(chat) : false;
  const body = bubble.querySelector('.voq-bubble-body');
  if (!body) return;
  const fullText = safeString(data.text);
  const delta = safeString(data.delta);
  if (fullText) body.textContent = fullText;
  else if (delta) body.textContent = (body.textContent || '') + delta;
  bubble.dataset.streaming = 'true';
  cx.lastEvent = 'voqualizer_cx_token';
  cx.lastEventAt = Date.now();
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.cxLastEvent = cx.lastEvent;
    globalThis.__voqualizer_page.cxLastStreamId = streamId;
    globalThis.__voqualizer_page.cxLastSeq = seq;
  }
  if (chat) maybeAutoScroll(chat, wasNearBottom);
}

function handleCxStreamFinal(payload) {
  const data = (payload && payload.data) || payload || {};
  const streamId = safeString(data.stream_id);
  if (!streamId) return;
  cx.finalByStreamId.add(streamId);
  const bubble = cx.bubblesByStreamId.get(streamId);
  if (bubble) {
    const body = bubble.querySelector('.voq-bubble-body');
    const finalText = safeString(data.text);
    if (body && finalText) body.textContent = finalText;
    bubble.dataset.final = 'true';
    bubble.dataset.streaming = 'false';
  }
  cx.lastEvent = 'voqualizer_cx_stream_final';
  cx.lastEventAt = Date.now();
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.cxLastEvent = cx.lastEvent;
    globalThis.__voqualizer_page.cxLastStreamId = streamId;
    globalThis.__voqualizer_page.cxActiveStreamCount = cxActiveStreamCount();
  }
}

function handleCxStreamError(payload) {
  const data = (payload && payload.data) || payload || {};
  cx.lastError = safeString(data.message || data.code || 'cx stream error');
  cx.lastEvent = 'voqualizer_cx_stream_error';
  cx.lastEventAt = Date.now();
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.cxLastEvent = cx.lastEvent;
    globalThis.__voqualizer_page.cxLastError = cx.lastError;
  }
}

function cxBubbleForLogItem(item) {
  if (!item) return null;
  // Try to match by exact text first.
  const targetText = safeString(item.content);
  for (const [streamId, bubble] of cx.bubblesByStreamId.entries()) {
    if (!bubble || !bubble.isConnected) continue;
    const body = bubble.querySelector('.voq-bubble-body');
    if (!body) continue;
    if (cx.reconciledLogIds.has(`${streamId}::${item.id}`)) return bubble;
  }
  // Find most recent finalized stream that has not yet been reconciled.
  let best = null;
  for (const [streamId, bubble] of cx.bubblesByStreamId.entries()) {
    if (!bubble || !bubble.isConnected) continue;
    if (bubble.dataset.reconciledLogId) continue;
    if (!cx.finalByStreamId.has(streamId)) continue;
    best = { streamId, bubble };
  }
  return best ? best.bubble : null;
}


function registerWordPlanBubble(utteranceId, logId) {
  if (!utteranceId || !logId) return;
  const bubble = pageState && pageState.transcriptIds ? pageState.transcriptIds.get(`log-${logId}`) : null;
  if (bubble) wordPlan.bubblesByUtteranceId.set(utteranceId, bubble);
}

function bubbleForUtterance(utteranceId) {
  const existing = wordPlan.bubblesByUtteranceId.get(utteranceId);
  if (existing && existing.isConnected) return existing;
  // Fallback: utterance_id format voq-resp-{logId}
  const match = /^voq-resp-(.+)$/.exec(utteranceId || '');
  if (match && pageState && pageState.transcriptIds) {
    const bubble = pageState.transcriptIds.get(`log-${match[1]}`);
    if (bubble) {
      wordPlan.bubblesByUtteranceId.set(utteranceId, bubble);
      return bubble;
    }
  }
  return null;
}

function renderWordSpansInto(bubble, text, words) {
  if (!bubble || !Array.isArray(words) || !words.length) return [];
  const body = bubble.querySelector('.voq-bubble-body');
  if (!body) return [];
  body.textContent = '';
  const fragment = document.createDocumentFragment();
  const safeText = safeString(text);
  let cursor = 0;
  const spans = [];
  for (const word of words) {
    const start = Number(word.char_start || 0);
    const end = Number(word.char_end || start);
    if (start > cursor) fragment.appendChild(document.createTextNode(safeText.slice(cursor, start)));
    const span = document.createElement('span');
    span.className = 'voq-word';
    span.dataset.wordIndex = String(word.word_index);
    span.dataset.charStart = String(start);
    span.dataset.charEnd = String(end);
    span.textContent = safeText.slice(start, end) || safeString(word.word);
    fragment.appendChild(span);
    spans.push(span);
    cursor = end;
  }
  if (cursor < safeText.length) fragment.appendChild(document.createTextNode(safeText.slice(cursor)));
  body.appendChild(fragment);
  return spans;
}

function handleTtsWordPlan(payload) {
  const data = (payload && payload.data) || payload || {};
  const utteranceId = safeString(data.utterance_id || data.utteranceId);
  if (!utteranceId) return;
  const words = Array.isArray(data.words) ? data.words : [];
  if (!words.length) return;
  const alreadyEnded = wordPlan.endedByUtteranceId.has(utteranceId);
  const planData = { text: safeString(data.text), words, durationMs: Number(data.duration_ms || 0) };
  const bubble = bubbleForUtterance(utteranceId);
  if (!bubble) {
    wordPlan.plansByUtteranceId.set(utteranceId, planData);
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastTtsWordPlanEventAt = Date.now();
      globalThis.__voqualizer_page.lastWordPlanUtteranceId = utteranceId;
      globalThis.__voqualizer_page.lastWordPlanWordCount = words.length;
      globalThis.__voqualizer_page.lastWordPlanDurationMs = planData.durationMs;
    }
    return;
  }
  const spans = renderWordSpansInto(bubble, planData.text, words);
  wordPlan.spansByUtteranceId.set(utteranceId, spans);
  wordPlan.plansByUtteranceId.set(utteranceId, planData);
  wordPlan.activeIndexByUtteranceId.set(utteranceId, -1);
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.lastTtsWordPlanEventAt = Date.now();
    globalThis.__voqualizer_page.lastWordPlanUtteranceId = utteranceId;
    globalThis.__voqualizer_page.lastWordPlanWordCount = words.length;
    globalThis.__voqualizer_page.lastWordPlanDurationMs = planData.durationMs;
  }
  if (alreadyEnded) {
    for (const span of spans || []) span.classList.remove('voq-word--active');
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.lastLateWordPlanUtteranceId = utteranceId;
      globalThis.__voqualizer_page.lastLateWordPlanAt = Date.now();
      globalThis.__voqualizer_page.activeWordUtteranceId = '';
      globalThis.__voqualizer_page.activeWordIndex = -1;
    }
    return;
  }
  ensureWordHighlightLoop();
}

function setActiveWord(utteranceId, index) {
  const spans = wordPlan.spansByUtteranceId.get(utteranceId);
  if (!spans) return;
  const previous = wordPlan.activeIndexByUtteranceId.get(utteranceId);
  if (previous === index) return;
  if (previous != null && previous >= 0 && spans[previous]) spans[previous].classList.remove('voq-word--active');
  if (index >= 0 && spans[index]) spans[index].classList.add('voq-word--active');
  wordPlan.activeIndexByUtteranceId.set(utteranceId, index);
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.activeWordUtteranceId = index >= 0 ? utteranceId : '';
    globalThis.__voqualizer_page.activeWordIndex = index;
  }
}

function ensureWordHighlightLoop() {
  if (wordPlan.rafId) return;
  const tick = () => {
    wordPlan.rafId = 0;
    const ctx = tts.audioContext;
    if (!ctx) return;
    const now = ctx.currentTime;
    let pending = 0;
    for (const [utteranceId, startAt] of wordPlan.playbackStartByUtteranceId.entries()) {
      const plan = wordPlan.plansByUtteranceId.get(utteranceId);
      if (!plan) continue;
      const elapsedMs = Math.max(0, (now - startAt) * 1000);
      const words = plan.words;
      let active = -1;
      for (let i = 0; i < words.length; i++) {
        const w = words[i];
        const start = Number(w.start_ms || 0);
        const end = Number(w.end_ms || start);
        if (elapsedMs >= start && elapsedMs < end) { active = i; break; }
        if (elapsedMs >= end) active = i;
      }
      setActiveWord(utteranceId, active);
      const last = words[words.length - 1];
      const endMs = last ? Number(last.end_ms || 0) : 0;
      if (elapsedMs >= endMs && wordPlan.endedByUtteranceId.has(utteranceId)) {
        // playback considered done; let finalizeWordHighlight clean up
      } else {
        pending += 1;
      }
    }
    if (pending > 0) wordPlan.rafId = requestAnimationFrame(tick);
  };
  wordPlan.rafId = requestAnimationFrame(tick);
}

function finalizeWordHighlight(utteranceId, { cancelled } = {}) {
  if (!utteranceId) return;
  wordPlan.endedByUtteranceId.add(utteranceId);
  const spans = wordPlan.spansByUtteranceId.get(utteranceId);
  const plan = wordPlan.plansByUtteranceId.get(utteranceId);
  if (spans && plan && !cancelled) {
    setActiveWord(utteranceId, plan.words.length - 1);
  }
  // Brief delay before clearing the active class to avoid a flicker.
  setTimeout(() => {
    const lateSpans = wordPlan.spansByUtteranceId.get(utteranceId);
    if (lateSpans) for (const span of lateSpans) span.classList.remove('voq-word--active');
    wordPlan.activeIndexByUtteranceId.set(utteranceId, -1);
    wordPlan.playbackStartByUtteranceId.delete(utteranceId);
  }, cancelled ? 0 : 180);
}

function clearAllWordHighlights() {
  for (const spans of wordPlan.spansByUtteranceId.values()) {
    for (const span of spans) span.classList.remove('voq-word--active');
  }
  wordPlan.spansByUtteranceId.clear();
  wordPlan.activeIndexByUtteranceId.clear();
  wordPlan.playbackStartByUtteranceId.clear();
  wordPlan.endedByUtteranceId.clear();
  wordPlan.plansByUtteranceId.clear();
  wordPlan.bubblesByUtteranceId.clear();
  if (wordPlan.rafId) {
    cancelAnimationFrame(wordPlan.rafId);
    wordPlan.rafId = 0;
  }
}

let pageState = null;

function setPageStateRef(state) {
  pageState = state;
}

// M8: route ASR finals into the standalone submitPrompt(pageState) path so
// the M3/M4/M5/M7 typed-prompt + /poll + cx-stream + word-highlight pipeline
// stays the single source of truth for assistant responses on this page.
async function routeStoreAsrFinal(text) {
  if (!pageState) return;
  const input = document.getElementById('voq-prompt-input');
  const trimmed = String(text || '').trim();
  if (!trimmed) return;
  if (input) {
    input.value = trimmed;
    delete input.dataset.voqAsrGhost;
    autosizePrompt(input);
  }
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.asrLastFinalText = trimmed;
    globalThis.__voqualizer_page.asrLastFinalAt = Date.now();
  }
  await submitPrompt(pageState);
}

// Legacy aliases retained so other code paths (and source-token tests) keep
// matching the same identifiers the M5 implementation introduced. The store
// owns capture, so these are intentionally thin/no-ops.
function handleAsrPartial(payload) {
  const data = (payload && payload.data) || payload || {};
  const text = String(data.text || '').trim();
  if (!text) return;
  if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.asrLastPartialText = text;
}
async function handleAsrFinal(payload) {
  const data = (payload && payload.data) || payload || {};
  const text = String(data.text || '').trim();
  if (!text) return;
  await routeStoreAsrFinal(text);
}
async function routeAsrFinal(text) { await routeStoreAsrFinal(text); }
function handleAudioAck(payload) {
  const data = (payload && payload.data) || payload || {};
  if (globalThis.__voqualizer_page) {
    if (typeof data.queued === 'number') globalThis.__voqualizer_page.asrAudioQueued = data.queued;
    if (typeof data.emitted === 'number') globalThis.__voqualizer_page.asrAudioEmitted = data.emitted;
  }
}
function maybeLocalBargeIn(_vu) { /* store owns local barge-in */ }
// Compatibility stubs for any external caller that still expects the
// pre-M8 capture entry points. The store now owns mic acquisition, framing,
// VU metering, speech detection, finalization cooldown, and barge-in.
async function startAsrCapture() {
  try { await voqStore?.startConversational(); } catch (_err) {}
}
async function stopAsrCapture(_opts) {
  try { await voqStore?.stop('standalone_compat_stop'); } catch (_err) {}
}

// M8: speaker + mic button glue driven by createVoqualizerStore().
function bindVoqualizerButtons() {
  const speaker = document.getElementById('voqualizer-speaker-button');
  const mic = document.getElementById('voqualizer-mic-button');
  if (!speaker && !mic) return;

  // Suppress the store's own TTS path so it does not race the standalone
  // direct-TTS pipeline (which already owns word-plan + highlight). The
  // standalone page retains TTS-enabled state via persistTtsEnabled().
  voqStore = createVoqualizerStore({
    suppressTts: true,
    suppressContextPolling: true,
    onAsrFinal: (text) => { void routeStoreAsrFinal(text); },
  });
  try { voqStore.init(); } catch (err) { console.error('[voqualizer] store init', err); }

  // Push the standalone picker's current context into the store as soon as
  // it is known. The store also self-observes URL params + getContext().
  const initialContextId = globalThis.__voqualizer_page?.selectedContextId || '';
  if (initialContextId) {
    try { voqStore.setContextId(initialContextId, 'page_picker_init'); } catch (_err) {}
  }

  const STATES = { STATE_IDLE, STATE_CONNECTING, STATE_CONVERSATIONAL, STATE_PTT_ACTIVE, STATE_TTS_READY, STATE_STOPPING, STATE_ERROR };

  function speakerLabel(ttsOff) {
    return ttsOff
      ? 'Voqualizer TTS is muted. Click to enable TTS for this chat.'
      : 'Voqualizer TTS is on. Click to mute TTS for this chat.';
  }
  function micLabel(s) {
    if (s.state === STATES.STATE_CONNECTING) return 'Voqualizer connecting…';
    if (s.state === STATES.STATE_STOPPING) return 'Voqualizer stopping…';
    if (s.state === STATES.STATE_CONVERSATIONAL) return 'Voqualizer listening. Tap to stop. Hold for push-to-talk finalization.';
    if (s.state === STATES.STATE_PTT_ACTIVE) return 'Voqualizer push-to-talk active. Release to send final.';
    if (s.state === STATES.STATE_ERROR) return 'Voqualizer error. Tap to retry.';
    return 'Voqualizer mic off. Tap for conversation. Hold for push-to-talk.';
  }
  function visualClass(s) {
    if (s.state === STATES.STATE_CONNECTING || s.state === STATES.STATE_STOPPING) return 'voqualizer-connecting';
    if (s.state === STATES.STATE_CONVERSATIONAL) return 'voqualizer-active';
    if (s.state === STATES.STATE_PTT_ACTIVE) return 'voqualizer-ptt';
    if (s.state === STATES.STATE_ERROR) return 'voqualizer-error';
    return 'voqualizer-idle';
  }

  function sync() {
    const s = voqStore;
    if (!s) return;
    // The standalone speaker button is the source of truth for TTS-enabled
    // state on this page; the store's TTS path is suppressed. We still
    // surface state to the store for context-scoped toggling parity.
    const ttsOff = !tts.enabled;
    if (speaker) {
      speaker.classList.toggle('voqualizer-tts-off', ttsOff);
      speaker.classList.toggle('voqualizer-tts-on', !ttsOff);
      speaker.setAttribute('data-tts-enabled', String(!ttsOff));
      speaker.setAttribute('aria-pressed', String(!ttsOff));
      speaker.setAttribute('aria-label', speakerLabel(ttsOff));
      speaker.setAttribute('title', speakerLabel(ttsOff));
    }
    if (mic) {
      const cls = visualClass(s);
      mic.classList.toggle('voqualizer-idle', cls === 'voqualizer-idle');
      mic.classList.toggle('voqualizer-active', cls === 'voqualizer-active');
      mic.classList.toggle('voqualizer-ptt', cls === 'voqualizer-ptt');
      mic.classList.toggle('voqualizer-connecting', cls === 'voqualizer-connecting');
      mic.classList.toggle('voqualizer-error', cls === 'voqualizer-error');
      const vuLevel = Math.max(0, Math.min(1, Number(s.micVuLevel || 0) || 0));
      const vuActive = (s.state === STATES.STATE_CONVERSATIONAL || s.state === STATES.STATE_PTT_ACTIVE) && vuLevel > 0.01;
      mic.style.setProperty('--voqualizer-vu-level', String(vuLevel));
      mic.style.setProperty('--voqualizer-vu-opacity', vuActive ? '1' : '0');
      mic.classList.toggle('voqualizer-vu-clipped', !!s.micVuClipped);
      mic.classList.toggle('voqualizer-speech-detected', !!s.micSpeechActive);
      mic.setAttribute('data-voqualizer-vu-level', vuLevel.toFixed(2));
      mic.setAttribute('data-voqualizer-speech-active', String(!!s.micSpeechActive));
      mic.setAttribute('aria-pressed', String(s.state === STATES.STATE_CONVERSATIONAL || s.state === STATES.STATE_PTT_ACTIVE));
      const label = micLabel(s);
      mic.setAttribute('aria-label', label);
      mic.setAttribute('title', `${label} Last: ${s.lastTransitionReason || 'n/a'} / ${s.lastConnectPhase || 'n/a'}`);
    }
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.voqStoreState = s.state;
      globalThis.__voqualizer_page.voqStoreDesiredMode = s.desiredMode;
      globalThis.__voqualizer_page.voqStoreMicVuLevel = s.micVuLevel;
      globalThis.__voqualizer_page.voqStoreMicSpeechActive = !!s.micSpeechActive;
      globalThis.__voqualizer_page.voqStoreMicVuClipped = !!s.micVuClipped;
      globalThis.__voqualizer_page.voqStoreContextId = s.contextId;
    }
  }
  sync();
  setInterval(sync, 250);

  // Speaker = standalone TTS toggle (independent of store TTS path).
  if (speaker) {
    speaker.addEventListener('click', (event) => {
      event.preventDefault();
      tts.enabled = !tts.enabled;
      persistTtsEnabled(tts.enabled);
      if (!tts.enabled) {
        cancelInflightTts();
      } else {
        ensureAudioContext();
        const contextId = globalThis.__voqualizer_page?.selectedContextId || '';
        if (contextId) void initVoqSession(contextId).catch((err) => {
          tts.lastError = `init failed: ${err?.message || err}`;
        });
      }
      if (tts.socket && tts.socket.connected && tts.sessionId) {
        try {
          tts.socket.emit('voqualizer_control', {
            session_id: tts.sessionId,
            bearer_token: tts.bearerToken,
            action: 'set_tts_enabled',
            enabled: !!tts.enabled,
          });
        } catch (_err) {}
      }
      sync();
    });
  }

  // Mic = createVoqualizerStore tap/hold (TAP_HOLD_THRESHOLD_MS = 250).
  if (mic) {
    let holdTimer = 0;
    let holdActive = false;
    let pointerDownTs = 0;
    let keyDownActive = false;
    mic.addEventListener('pointerdown', (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      event.preventDefault();
      pointerDownTs = Date.now();
      holdActive = false;
      holdTimer = setTimeout(async () => {
        holdActive = true;
        try { await voqStore?.onHoldStart(); } catch (e) { console.error('[voqualizer] onHoldStart', e); }
      }, TAP_HOLD_THRESHOLD_MS);
    });
    const onPointerUp = async (event) => {
      if (event && event.preventDefault) event.preventDefault();
      clearTimeout(holdTimer);
      holdTimer = 0;
      const elapsed = Date.now() - pointerDownTs;
      try {
        if (holdActive || elapsed >= TAP_HOLD_THRESHOLD_MS) {
          await voqStore?.onHoldEnd();
        } else {
          await voqStore?.onTap();
        }
      } catch (e) { console.error('[voqualizer] tap/hold end', e); }
      holdActive = false;
    };
    mic.addEventListener('pointerup', onPointerUp);
    mic.addEventListener('pointercancel', () => { clearTimeout(holdTimer); holdTimer = 0; holdActive = false; });
    mic.addEventListener('pointerleave', () => { clearTimeout(holdTimer); holdTimer = 0; holdActive = false; });
    mic.addEventListener('keydown', async (event) => {
      if (event.key !== ' ' && event.key !== 'Enter') return;
      event.preventDefault();
      if (event.repeat) return;
      keyDownActive = true;
      mic.dispatchEvent(new PointerEvent('pointerdown', { button: 0, bubbles: false }));
    });
    mic.addEventListener('keyup', async (event) => {
      if (event.key !== ' ' && event.key !== 'Enter') return;
      event.preventDefault();
      if (!keyDownActive) return;
      keyDownActive = false;
      await onPointerUp({ preventDefault() {} });
    });
  }
}

// Legacy entry points kept as no-ops so external callers + tests that look
// for the historical identifiers still find them.
function bindTtsButton() { /* M8: replaced by bindVoqualizerButtons */ }
function bindAsrButton() { /* M8: replaced by bindVoqualizerButtons */ }
function updateAsrButton() { /* M8: store + sync() drive button visuals */ }
function sessionEnvelope() { return { session_id: tts.sessionId, bearer_token: tts.bearerToken }; }
function handleVu(_m) { /* store owns VU/speech */ }
async function ensureWorklet(ctx) {
  if (!ctx || ctx[' __voq_worklet_loaded__']) return;
  try { await ctx.audioWorklet.addModule(WORKLET_URL); ctx[' __voq_worklet_loaded__'] = true; } catch (_err) {}
}

function initVoqualizerPage() {
  const root = document.querySelector('[data-voqualizer-page="standalone"]');
  const settings = document.getElementById('voq-settings-button');
  const logout = document.getElementById('voq-logout-button');
  const contextSelect = document.getElementById('voq-context-select');

  globalThis.__voqualizer_page = {
    version: PAGE_VERSION,
    loadedAt: Date.now(),
    route: '/plugins/a0_voqualizer/webui/voqualizer.html',
    milestone: 7,
    wordPlanCapability: false,
    lastWordPlanUtteranceId: '',
    lastWordPlanWordCount: 0,
    lastWordPlanDurationMs: 0,
    lastTtsWordPlanEventAt: 0,
    lastLateWordPlanAt: 0,
    lastLateWordPlanUtteranceId: '',
    activeWordUtteranceId: '',
    activeWordIndex: -1,
    cxActiveStreamCount: 0,
    lastRealtimeDisconnectAt: 0,
    cxStreamCapability: false,
    protocolVersion: '',
    cxLastEvent: '',
    cxLastStreamId: '',
    cxLastSeq: 0,
    cxLastError: '',
    standalone: true,
    adminEndpoint: ADMIN_ENDPOINT,
    messageEndpoint: MESSAGE_ENDPOINT,
    pollEndpoint: POLL_ENDPOINT,
    voqualizerHandler: VOQUALIZER_HANDLER,
    lastApiStage: '',
    lastApiEndpoint: '',
    lastApiPayload: null,
    lastApiResult: null,
    lastApiOkAt: 0,
    lastApiError: '',
    lastApiErrorAt: 0,
    pollIntervalMs: POLL_INTERVAL_MS,
    selectedContextStorageKey: SELECTED_CONTEXT_STORAGE_KEY,
    ttsEnabledStorageKey: TTS_ENABLED_STORAGE_KEY,
    contexts: [],
    selectedContextId: '',
    headerContextName: 'Voqualizer',
    lastHeaderContextNameAt: 0,
    contextsLoading: false,
    contextError: '',
    contextCount: 0,
    isSubmitting: false,
    lastSubmitId: '',
    lastLogVersion: 0,
    lastMonologuePreloadAt: 0,
    lastMonologuePreloadContextId: '',
    lastMonologuePreloadFound: false,
    lastMonologuePreloadLogId: '',
    lastMonologuePreloadTextLength: 0,
    lastMonologuePreloadError: '',
    lastVoqWarmupAt: 0,
    lastVoqWarmupReadyAt: 0,
    lastVoqWarmupContextId: '',
    lastVoqWarmupReason: '',
    lastVoqWarmupSessionId: '',
    lastVoqWarmupReady: false,
    lastVoqWarmupError: '',
    ttsEnabled: tts.enabled,
    sessionId: '',
    lastTtsAt: 0,
    lastTtsError: '',
    lastAckTtsFallbackAt: 0,
    lastAckTtsFallbackChunks: 0,
    lastAckTtsFallbackReason: '',
    lastAckTtsPushedEmitCount: 0,
    lastAckTtsSenderPresent: false,
    lastDirectTtsAck: null,
    ttsChunkCount: 0,
    lastTtsChunkAt: 0,
    lastTtsChunkBytes: 0,
    lastTtsChunkCodec: '',
    lastTtsChunkSampleRate: 0,
    lastTtsChunkUtteranceId: '',
    lastPlaybackStartAt: 0,
    lastPlaybackStartAudioTime: 0,
    lastPlaybackDurationMs: 0,
    lastPlaybackUtteranceId: '',
    lastPlaybackError: '',
    audioContextState: '',
    lastStatus: 'Ready',
    lastStatusLevel: 'info',
    asrEnabledStorageKey: ASR_ENABLED_STORAGE_KEY,
    asrSubmitMode: ASR_SUBMIT_MODE,
    workletUrl: WORKLET_URL,
    asrEnabled: asr.enabled,
    asrCapturing: false,
    asrLastPartialText: '',
    asrLastFinalText: '',
    asrLastFinalAt: 0,
    asrLastError: '',
    asrAudioQueued: 0,
    asrAudioEmitted: 0,
  };

  const state = {
    transcriptIds: new Map(),
    isSubmitting: false,
    activeSubmissionId: '',
    lastLogVersion: 0,
  };

  if (!root) return;
  root.dataset.ready = 'true';

  if (settings) {
    settings.addEventListener('click', () => {
      globalThis.__voqualizer_page.lastSettingsClickAt = Date.now();
      setPageStatus('Opening Voqualizer provider settings…', 'info');
    });
  }

  if (logout) {
    logout.addEventListener('click', () => {
      globalThis.__voqualizer_page.lastLogoutClickAt = Date.now();
      setPageStatus('Logging out…', 'info');
    });
  }

  setPageStateRef(state);
  bindPromptInput(state);
  bindContextPicker(state, contextSelect);
  // M8: replaces former bindTtsButton() + bindAsrButton() with the
  // createVoqualizerStore()-driven mic + speaker glue so the standalone page
  // mirrors the in-DOM voqualizer-buttons.html behavior exactly.
  installTtsAudioUnlockHandlers();
  bindVoqualizerButtons();
  bindTranscriptControls();
  bindFullscreenButton();
  bindContextMenuButton();
  bindAsrDebugButton();
  updateActionsWrapped();
  if (typeof globalThis.ResizeObserver === 'function') {
    try {
      const ro = new ResizeObserver(() => updateActionsWrapped());
      const target = document.querySelector('[data-voqualizer-page="standalone"] .voq-composer');
      if (target) ro.observe(target);
    } catch (_err) {}
  }
  globalThis.addEventListener('resize', updateActionsWrapped);
  globalThis.addEventListener('orientationchange', updateActionsWrapped);
  void loadContextPicker(state, contextSelect);

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      try { void voqStore?.stop('visibility_hidden'); } catch (_err) {}
    }
  });

  globalThis.addEventListener('beforeunload', () => {
    try { void voqStore?.stop('beforeunload'); } catch (_err) {}
    disconnectVoq();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initVoqualizerPage, { once: true });
} else {
  initVoqualizerPage();
}

export {
  ADMIN_ENDPOINT,
  MESSAGE_ENDPOINT,
  PAGE_VERSION,
  POLL_ENDPOINT,
  POLL_INTERVAL_MS,
  PRELOAD_MONOLOGUE_LOG_FROM,
  SELECTED_CONTEXT_STORAGE_KEY,
  TTS_ENABLED_STORAGE_KEY,
  VOQUALIZER_HANDLER,
  cancelInflightTts,
  connectVoq,
  disconnectVoq,
  fetchContexts,
  handleTtsChunk,
  handleTtsDone,
  initVoqSession,
  initVoqualizerPage,
  maybeSpeakResponse,
  preloadLastMonologueResult,
  renderPreloadedResponseBubble,
  handleCxStreamStart,
  handleCxToken,
  handleCxStreamFinal,
  handleCxStreamError,
  handleTtsWordPlan,
  finalizeWordHighlight,
  clearAllWordHighlights,
  normalizeContext,
  normalizeContexts,
  speakText,
  submitPrompt,
  ASR_ENABLED_STORAGE_KEY,
  ASR_SUBMIT_MODE,
  BARGE_IN_LEVEL_THRESHOLD,
  startAsrCapture,
  stopAsrCapture,
  handleAsrPartial,
  handleAsrFinal,
  routeAsrFinal,
  maybeLocalBargeIn,
  updateJumpLatest,
  scrollTranscriptToBottom,
};
