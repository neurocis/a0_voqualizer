import { callJsonApi } from '/js/api.js';

const PAGE_VERSION = 'm3-typed-prompt';
const ADMIN_ENDPOINT = 'plugins/a0_voqualizer/voqualizer_admin';
const MESSAGE_ENDPOINT = 'message_async';
const POLL_ENDPOINT = 'poll';
const SELECTED_CONTEXT_STORAGE_KEY = 'a0_voqualizer.standalone.selected_context_id';
const POLL_INTERVAL_MS = 700;
const POLL_HARD_TIMEOUT_MS = 120000;

function autosizePrompt(textarea) {
  if (!textarea) return;
  textarea.style.height = 'auto';
  textarea.style.height = `${Math.min(textarea.scrollHeight, window.innerHeight * 0.34)}px`;
}

function safeString(value) {
  return value === undefined || value === null ? '' : String(value);
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
  const root = document.querySelector('[data-voqualizer-page="standalone"]');
  if (!root) return;
  root.dataset.status = level;
  root.dataset.statusMessage = message || '';
}

function generateMessageId() {
  try {
    if (globalThis.crypto?.randomUUID) {
      return globalThis.crypto.randomUUID();
    }
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

function maybeAutoScroll(chat, wasNearBottom) {
  if (!chat) return;
  if (wasNearBottom) chat.scrollTop = chat.scrollHeight;
}

function createBubble({ id, role, content, kind }) {
  const bubble = document.createElement('article');
  bubble.className = `voq-bubble voq-bubble--${role}`;
  bubble.dataset.bubbleId = id;
  bubble.dataset.role = role;
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
  const disabled = busy || !hasContext || !hasText;
  button.disabled = disabled;
  button.dataset.busy = busy ? 'true' : 'false';
  if (select) select.disabled = busy || select.disabled === true && !hasContext;
}

async function pollOnce(contextId, logFrom) {
  return callJsonApi(POLL_ENDPOINT, {
    context: contextId,
    log_from: logFrom,
  });
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
        if (item.type === 'user') {
          continue;
        }
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
      if (sawResponse && snapshot.log_progress_active === false) {
        break;
      }
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
    const result = await callJsonApi(MESSAGE_ENDPOINT, {
      text,
      context: contextId,
      message_id: messageId,
    });
    if (!result) throw new Error('empty response from message_async');
    setPageStatus('Awaiting response…', 'loading');
    await runPollLoop(state, contextId, messageId);
  } catch (error) {
    renderErrorRow(state, `Send failed: ${error?.message || error}`);
    state.isSubmitting = false;
    state.activeSubmissionId = '';
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.isSubmitting = false;
    }
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
  if (button) {
    button.addEventListener('click', () => void submitPrompt(state));
  }
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
      for (const ctx of contexts) {
        ctx.active = ctx.id === selectedContextId;
      }
    }
    setPageStatus(selectedContextId ? `Selected ${selectedContextId}` : 'No context selected', selectedContextId ? 'ready' : 'empty');
    updateSendButton(state);
  });
}

function initVoqualizerPage() {
  const root = document.querySelector('[data-voqualizer-page="standalone"]');
  const settings = document.getElementById('voq-settings-button');
  const contextSelect = document.getElementById('voq-context-select');

  globalThis.__voqualizer_page = {
    version: PAGE_VERSION,
    loadedAt: Date.now(),
    route: '/plugins/a0_voqualizer/webui/voqualizer.html',
    milestone: 3,
    standalone: true,
    adminEndpoint: ADMIN_ENDPOINT,
    messageEndpoint: MESSAGE_ENDPOINT,
    pollEndpoint: POLL_ENDPOINT,
    pollIntervalMs: POLL_INTERVAL_MS,
    selectedContextStorageKey: SELECTED_CONTEXT_STORAGE_KEY,
    contexts: [],
    selectedContextId: '',
    contextsLoading: false,
    contextError: '',
    contextCount: 0,
    isSubmitting: false,
    lastSubmitId: '',
    lastLogVersion: 0,
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
    });
  }

  bindPromptInput(state);
  bindContextPicker(state, contextSelect);
  void loadContextPicker(state, contextSelect);
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
  fetchContexts,
  initVoqualizerPage,
  normalizeContext,
  normalizeContexts,
  submitPrompt,
};
