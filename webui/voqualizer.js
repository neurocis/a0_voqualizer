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

const PAGE_VERSION = 'm6-polish-final';
const ADMIN_ENDPOINT = 'plugins/a0_voqualizer/voqualizer_admin';
const MESSAGE_ENDPOINT = 'message_async';
const POLL_ENDPOINT = 'poll';
const VOQUALIZER_HANDLER = 'plugins/a0_voqualizer/ws_voqualizer';
const SELECTED_CONTEXT_STORAGE_KEY = 'a0_voqualizer.standalone.selected_context_id';
const TTS_ENABLED_STORAGE_KEY = 'a0_voqualizer.standalone.tts_enabled';
const ASR_ENABLED_STORAGE_KEY = 'a0_voqualizer.standalone.asr_enabled';
const ASR_SUBMIT_MODE = 'frontend_prompt';
// Uses the shared voqualizer-mic-processor AudioWorklet from voqualizer-audio.js
const BARGE_IN_LEVEL_THRESHOLD = 0.05;
const POLL_INTERVAL_MS = 700;
const POLL_HARD_TIMEOUT_MS = 120000;

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

async function fetchContexts() {
  const result = await callJsonApi(ADMIN_ENDPOINT, { action: 'contexts' });
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
  button.setAttribute('aria-disabled', button.disabled ? 'true' : 'false');
  button.setAttribute('title', busy ? 'Waiting for assistant response' : !hasContext ? 'Select a context before sending' : !hasText ? 'Type a prompt or use ASR' : 'Send prompt');
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
  return callJsonApi(POLL_ENDPOINT, { context: contextId, log_from: logFrom });
}

async function runPollLoop(state, contextId, submissionId) {
  const started = Date.now();
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
          if (item.type === 'response') sawResponse = true;
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
    if (Date.now() - started > POLL_HARD_TIMEOUT_MS) {
      renderErrorRow(state, 'Agent did not respond within the timeout window.');
      break;
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
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.isSubmitting = true;
    globalThis.__voqualizer_page.lastSubmitId = messageId;
  }
  setPageStatus('Sending…', 'loading');
  updateSendButton(state);
  renderUserBubble(state, { id: messageId, text });
  prompt.value = '';
  autosizePrompt(prompt);
  try {
    const result = await callJsonApi(MESSAGE_ENDPOINT, { text, context: contextId, message_id: messageId });
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
    autosizePrompt(prompt);
    updateSendButton(state);
  });
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
    const selectedContextId = renderContexts(select, contexts, readSelectedContextHint());
    page.contexts = contexts;
    page.selectedContextId = selectedContextId;
    page.contextsLoading = false;
    page.contextError = '';
    page.contextCount = contexts.length;
    state.lastLogVersion = 0;
    tts.contextId = selectedContextId;
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
    socket.on('disconnect', () => { tts.ready = false; updateTtsButton(); });
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
  if (!bytes || !bytes.byteLength) return;
  if (codec === 'pcm16/16k' || codec === 'pcm16/24k') {
    playPcmChunk(bytes, sampleRate, utteranceId);
  } else {
    bufferEncodedChunk(bytes, codec, utteranceId, !!(data.is_final || data.final));
  }
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
  source.start(startAt);
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
  void speakText(String(item.content || ''), { utteranceId });
}

function bindTtsButton() {
  const button = document.getElementById('voq-tts-button');
  if (!button) return;
  button.addEventListener('click', () => {
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
    updateTtsButton();
  });
  updateTtsButton();
}

let pageState = null;

function setPageStateRef(state) {
  pageState = state;
}

function updateAsrButton() {
  const button = document.getElementById('voq-asr-button');
  if (!button) return;
  let state = 'off';
  if (asr.lastError) state = 'error';
  else if (asr.starting) state = 'requesting';
  else if (asr.capturing && asr.lastPartialText) state = 'transcribing';
  else if (asr.capturing) state = 'listening';
  button.dataset.asrState = state;
  button.setAttribute('aria-pressed', asr.enabled ? 'true' : 'false');
  button.setAttribute('aria-label', asr.enabled ? 'Microphone input (on)' : 'Microphone input (off)');
  button.setAttribute('title', state === 'requesting' ? 'Requesting microphone permission…' : state === 'listening' ? 'Listening — click to stop' : state === 'transcribing' ? 'Transcribing speech — click to stop' : state === 'error' ? 'Microphone error — click to retry' : 'Microphone off — click to start speech recognition');
}

function sessionEnvelope() {
  return { session_id: tts.sessionId, bearer_token: tts.bearerToken };
}

function handleVu(message) {
  asr.lastVuLevel = Number(message?.level || 0);
  maybeLocalBargeIn(message);
}

function maybeLocalBargeIn(vu) {
  if (!asr.capturing) return;
  if (asr.bargedThisUtterance) return;
  if (!tts.enabled) return;
  if (!tts.tracker || tts.tracker.activePlaybackSources.size === 0) return;
  const level = Number(vu?.level || 0);
  const peak = Number(vu?.peak || 0);
  if (level < BARGE_IN_LEVEL_THRESHOLD && peak < BARGE_IN_LEVEL_THRESHOLD) return;
  asr.bargedThisUtterance = true;
  cancelInflightTts();
}

async function ensureWorklet(ctx) {
  if (ctx[' __voq_worklet_loaded__']) return;
  await ctx.audioWorklet.addModule(WORKLET_URL);
  ctx[' __voq_worklet_loaded__'] = true;
}

async function startAsrCapture() {
  if (asr.capturing || asr.starting) return;
  if (!globalThis.isSecureContext && globalThis.location?.protocol !== 'file:') {
    asr.lastError = 'Microphone requires HTTPS';
    updateAsrButton();
    return;
  }
  const contextId = globalThis.__voqualizer_page?.selectedContextId || '';
  if (!contextId) {
    asr.lastError = 'Select a context first';
    updateAsrButton();
    return;
  }
  asr.starting = true;
  asr.lastError = '';
  asr.bargedThisUtterance = false;
  updateAsrButton();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    asr.mediaStream = stream;
    const ctx = ensureAudioContext();
    if (!ctx) throw new Error('AudioContext unavailable');
    await ensureWorklet(ctx);
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, WORKLET_PROCESSOR);
    const gain = ctx.createGain();
    gain.gain.value = 0;
    source.connect(node);
    node.connect(gain);
    gain.connect(ctx.destination);
    asr.mediaSource = source;
    asr.workletNode = node;
    asr.monitorGain = gain;
    node.port.onmessage = (event) => {
      const m = event.data || {};
      if (m.type === 'vu') { handleVu(m); return; }
      if (m.type !== 'audio') return;
      if (!asr.capturing || asr.muted) return;
      if (!tts.socket || !tts.socket.connected || !tts.sessionId) return;
      const payload = audioChunkPayload(m.seq || 0, m.tsMs || 0, m.pcm16);
      try {
        tts.socket.emit(VOQUALIZER_HANDLER, { ...sessionEnvelope(), ...payload });
      } catch (_err) {}
    };
    node.port.postMessage({ type: 'setEnabled', enabled: true });
    // Re-init session with ASR enabled.
    tts.ready = false;
    await initVoqSession(contextId);
    asr.capturing = true;
    asr.starting = false;
    asr.inputBeforeCapture = document.getElementById('voq-prompt-input')?.value || '';
    persistAsrEnabled(true);
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.asrEnabled = true;
      globalThis.__voqualizer_page.asrCapturing = true;
    }
    setPageStatus('Listening…', 'loading');
    updateAsrButton();
  } catch (error) {
    asr.lastError = error?.message || String(error) || 'mic error';
    asr.starting = false;
    asr.enabled = false;
    persistAsrEnabled(false);
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.asrEnabled = false;
      globalThis.__voqualizer_page.asrCapturing = false;
      globalThis.__voqualizer_page.asrLastError = asr.lastError;
    }
    await stopAsrCapture({ silent: true });
    updateAsrButton();
  }
}

async function stopAsrCapture({ silent = false } = {}) {
  asr.capturing = false;
  asr.starting = false;
  try { asr.workletNode?.port.postMessage({ type: 'setEnabled', enabled: false }); } catch (_err) {}
  try { asr.workletNode?.disconnect(); } catch (_err) {}
  try { asr.monitorGain?.disconnect(); } catch (_err) {}
  try { asr.mediaSource?.disconnect(); } catch (_err) {}
  if (asr.mediaStream) {
    for (const track of asr.mediaStream.getTracks()) {
      try { track.stop(); } catch (_err) {}
    }
  }
  asr.mediaStream = null;
  asr.workletNode = null;
  asr.mediaSource = null;
  asr.monitorGain = null;
  asr.lastPartialText = '';
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.asrCapturing = false;
    globalThis.__voqualizer_page.asrLastPartialText = '';
  }
  // Re-init session with ASR disabled so server stops expecting frames.
  if (!silent && tts.socket && tts.socket.connected) {
    const contextId = globalThis.__voqualizer_page?.selectedContextId || '';
    if (contextId) {
      tts.ready = false;
      try { await initVoqSession(contextId); } catch (_err) {}
    }
  }
  updateAsrButton();
}

function handleAsrPartial(payload) {
  if (!asr.capturing) return;
  const data = (payload && payload.data) || payload || {};
  const text = String(data.text || '').trim();
  if (!text) return;
  asr.lastPartialText = text;
  if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.asrLastPartialText = text;
  const input = document.getElementById('voq-prompt-input');
  if (input) {
    if (input.value === asr.inputBeforeCapture || input.value === '' || input.dataset.voqAsrGhost === 'true') {
      input.value = text;
      input.dataset.voqAsrGhost = 'true';
    }
  }
  updateAsrButton();
}

async function handleAsrFinal(payload) {
  if (!asr.capturing) return;
  const data = (payload && payload.data) || payload || {};
  const text = String(data.text || '').trim();
  asr.lastFinalText = text;
  asr.lastFinalAt = Date.now();
  asr.lastPartialText = '';
  asr.bargedThisUtterance = false;
  if (globalThis.__voqualizer_page) {
    globalThis.__voqualizer_page.asrLastFinalText = text;
    globalThis.__voqualizer_page.asrLastFinalAt = asr.lastFinalAt;
    globalThis.__voqualizer_page.asrLastPartialText = '';
  }
  updateAsrButton();
  if (!text) return;
  await routeAsrFinal(text);
}

async function routeAsrFinal(text) {
  if (!pageState) return;
  if (pageState.isSubmitting) return;
  const input = document.getElementById('voq-prompt-input');
  if (input) {
    input.value = text;
    delete input.dataset.voqAsrGhost;
    autosizePrompt(input);
  }
  asr.inputBeforeCapture = '';
  await submitPrompt(pageState);
}

function handleAudioAck(payload) {
  const data = (payload && payload.data) || payload || {};
  if (globalThis.__voqualizer_page) {
    if (typeof data.queued === 'number') globalThis.__voqualizer_page.asrAudioQueued = data.queued;
    if (typeof data.emitted === 'number') globalThis.__voqualizer_page.asrAudioEmitted = data.emitted;
  }
}

function bindAsrButton() {
  const button = document.getElementById('voq-asr-button');
  if (!button) return;
  button.addEventListener('click', async () => {
    if (asr.enabled || asr.capturing) {
      asr.enabled = false;
      persistAsrEnabled(false);
      if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.asrEnabled = false;
      await stopAsrCapture();
    } else {
      asr.enabled = true;
      ensureAudioContext();
      if (globalThis.__voqualizer_page) globalThis.__voqualizer_page.asrEnabled = true;
      await startAsrCapture();
    }
    updateAsrButton();
  });
  updateAsrButton();
}

function initVoqualizerPage() {
  const root = document.querySelector('[data-voqualizer-page="standalone"]');
  const settings = document.getElementById('voq-settings-button');
  const contextSelect = document.getElementById('voq-context-select');

  globalThis.__voqualizer_page = {
    version: PAGE_VERSION,
    loadedAt: Date.now(),
    route: '/plugins/a0_voqualizer/webui/voqualizer.html',
    milestone: 6,
    standalone: true,
    adminEndpoint: ADMIN_ENDPOINT,
    messageEndpoint: MESSAGE_ENDPOINT,
    pollEndpoint: POLL_ENDPOINT,
    voqualizerHandler: VOQUALIZER_HANDLER,
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
    ttsEnabled: tts.enabled,
    sessionId: '',
    lastTtsAt: 0,
    lastTtsError: '',
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

  setPageStateRef(state);
  bindPromptInput(state);
  bindContextPicker(state, contextSelect);
  bindTtsButton();
  bindAsrButton();
  bindTranscriptControls();
  void loadContextPicker(state, contextSelect);

  document.addEventListener('visibilitychange', () => {
    if (document.hidden && asr.capturing) {
      void stopAsrCapture({ silent: true });
    }
  });

  globalThis.addEventListener('beforeunload', () => {
    if (asr.capturing) { void stopAsrCapture({ silent: true }); }
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
