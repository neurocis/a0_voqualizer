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
} from '/plugins/a0_voqualizer/webui/conversation-mode.js';
// ASR finals from the store's socket (voqualizer_asr_final) and partials
// (voqualizer_asr_partial) are routed back into submitPrompt(pageState) via
// the store's onAsrFinal hook so the M3/M4/M5/M7 typed-prompt + /poll +
// cx-stream + word-highlight pipeline remains the single submission path.
let voqStore = null;

const PAGE_VERSION = 'm8-icons-bigger';
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
  btn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    if (menu.hidden) openContextMenu(); else closeContextMenu();
  });
  document.addEventListener('click', (ev) => {
    if (menu.hidden) return;
    if (ev.target === btn || btn.contains(ev.target) || menu.contains(ev.target)) return;
    closeContextMenu();
  });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeContextMenu(); });
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
  button.disabled = busy || !hasContext || !hasText;
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
  button.setAttribute('aria-disabled', button.disabled ? 'true' : 'false');
  button.setAttribute('title', busy ? 'Processing — waiting for assistant response' : sendIndicator.state === 'success' ? 'Response complete' : !hasContext ? 'Select a context before sending' : !hasText ? 'Type a prompt or use ASR' : 'Send prompt');
  if (select) {
    select.disabled = busy || (select.disabled === true && !hasContext);
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
    updateSendButton(state);
    setPageStatus(sawResponse ? 'Response complete' : 'Idle', sawResponse ? 'ready' : 'empty');
  }
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
  if (state.isSubmitting) return;
  ensureAudioContext();
  const messageId = generateMessageId();
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
    const result = await callJsonApiWithDiagnostics(MESSAGE_ENDPOINT, { text, context: contextId, message_id: messageId }, 'message_async');
    void voqInitPromise;
    if (!result) throw new Error('empty response from message_async');
    setPageStatus('Awaiting response…', 'loading');
    await runPollLoop(state, contextId, messageId);
  } catch (error) {
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

function ensureAudioContext() {
  if (!tts.audioContext) {
    try {
      const Ctx = globalThis.AudioContext || globalThis.webkitAudioContext;
      if (Ctx) tts.audioContext = new Ctx();
    } catch (_err) {}
  }
  if (tts.audioContext && tts.audioContext.state === 'suspended') {
    try { tts.audioContext.resume(); } catch (_err) {}
  }
  return tts.audioContext;
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
    const socket = io('/', {
      transports: ['websocket', 'polling'],
      withCredentials: true,
      auth: (cb) => cb({ csrf_token: csrf, handlers: [VOQUALIZER_HANDLER] }),
    });
    tts.socket = socket;
    socket.on('voqualizer_tts_chunk', handleTtsChunk);
    socket.on('voqualizer_tts_done', handleTtsDone);
    socket.on('voqualizer_asr_partial', handleAsrPartial);
    socket.on('voqualizer_asr_final', handleAsrFinal);
    socket.on('voqualizer_audio_ack', handleAudioAck);
    socket.on('voqualizer_error', handleVoqError);
    socket.on('voqualizer_cx_stream_start', handleCxStreamStart);
    socket.on('voqualizer_cx_token', handleCxToken);
    socket.on('voqualizer_cx_stream_final', handleCxStreamFinal);
    socket.on('voqualizer_cx_stream_error', handleCxStreamError);
    socket.on('voqualizer_tts_word_plan', handleTtsWordPlan);
    socket.on('disconnect', () => {
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
  if (tts.ready && tts.contextId === contextId && tts.sessionId) return tts;
  const socket = await connectVoq();
  const sessionId = generateMessageId();
  const payload = {
    session_id: sessionId,
    context_id: contextId,
    barge_in: !!asr.enabled,
    asr: { enabled: !!asr.enabled },
    tts: { enabled: !!tts.enabled },
    asr_submit_mode: ASR_SUBMIT_MODE,
  };
  const ready = await new Promise((resolve, reject) => {
    let settled = false;
    const timeout = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error('voqualizer_init timeout'));
    }, 10000);
    socket.emit(VOQUALIZER_HANDLER, { event: 'voqualizer_init', data: payload }, (ack) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (ack && ack.event === 'voqualizer_ready') resolve(ack);
      else if (ack && ack.error) reject(new Error(ack.error.message || 'voqualizer_init failed'));
      else resolve(ack || {});
    });
  });
  tts.sessionId = ready.session_id || sessionId;
  tts.bearerToken = ready.bearer_token || '';
  tts.contextId = contextId;
  tts.ready = true;
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
  if (!trimmed) return;
  if (!tts.enabled) return;
  const contextId = tts.contextId || (globalThis.__voqualizer_page?.selectedContextId || '');
  if (!contextId) return;
  ensureAudioContext();
  try {
    await initVoqSession(contextId);
  } catch (error) {
    tts.lastError = `init failed: ${error?.message || error}`;
    updateTtsButton();
    return;
  }
  const id = utteranceId || `voq-utt-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  tts.lastSpeakAt = Date.now();
  tts.lastError = '';
  try {
    tts.socket.emit(VOQUALIZER_HANDLER, {
      event: 'voqualizer_user_text',
      data: {
        session_id: tts.sessionId,
        bearer_token: tts.bearerToken,
        utterance_id: id,
        text: trimmed,
      },
    }, (ack) => {
      handleTtsAckFallback(ack, id);
      if (ack && ack.error) {
        tts.lastError = ack.error.message || 'speak failed';
        updateTtsButton();
      }
    });
  } catch (error) {
    tts.lastError = `speak failed: ${error?.message || error}`;
  }
  updateTtsButton();
}

function cancelInflightTts() {
  if (tts.tracker) tts.tracker.stopAllPlayback();
  if (tts.socket && tts.socket.connected && tts.sessionId) {
    try {
      tts.socket.emit(VOQUALIZER_HANDLER, {
        event: 'voqualizer_control',
        data: {
          session_id: tts.sessionId,
          bearer_token: tts.bearerToken,
          action: 'cancel_tts',
        },
      });
    } catch (_err) {}
  }
  updateTtsButton();
}

function disconnectVoq() {
  cancelInflightTts();
  if (tts.socket) {
    try {
      tts.socket.emit(VOQUALIZER_HANDLER, {
        event: 'voqualizer_control',
        data: {
          session_id: tts.sessionId,
          bearer_token: tts.bearerToken,
          action: 'end_session',
        },
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

function handleTtsChunk(payload) {
  if (!tts.enabled) return;
  const data = (payload && payload.data) || payload || {};
  const utteranceId = safeString(data.utterance_id || data.utteranceId || 'default');
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
  if (!ack || ack.error || !tts.enabled) return;
  const chunks = Array.isArray(ack.tts_chunks) ? ack.tts_chunks : [];
  if (!chunks.length) return;
  const utteranceId = safeString(ack.utterance_id || fallbackUtteranceId || 'default');
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
  const ctx = ensureAudioContext();
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
  if (!tts.enabled) return;
  if (!item || item.type !== 'response' || !item.id) return;
  if (tts.spokenResponseIds.has(item.id)) return;
  tts.spokenResponseIds.add(item.id);
  const utteranceId = `voq-resp-${item.id}`;
  registerWordPlanBubble(utteranceId, item.id);
  void speakText(String(item.content || ''), { utteranceId });
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
  if (pageState.isSubmitting) return;
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
          tts.socket.emit(VOQUALIZER_HANDLER, {
            event: 'voqualizer_control',
            data: {
              session_id: tts.sessionId,
              bearer_token: tts.bearerToken,
              action: 'set_tts_enabled',
              enabled: !!tts.enabled,
            },
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
  bindVoqualizerButtons();
  bindTranscriptControls();
  bindFullscreenButton();
  bindContextMenuButton();
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
