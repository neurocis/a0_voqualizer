import { callJsonApi } from '/js/api.js';

const PAGE_VERSION = 'm2-context-picker';
const ADMIN_ENDPOINT = 'plugins/a0_voqualizer/voqualizer_admin';
const SELECTED_CONTEXT_STORAGE_KEY = 'a0_voqualizer.standalone.selected_context_id';

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
  if (queryValue) return queryValue;

  try {
    const stored = globalThis.localStorage?.getItem(SELECTED_CONTEXT_STORAGE_KEY);
    if (stored) return stored;
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

async function loadContextPicker(select) {
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
    setPageStatus(contexts.length ? `Selected ${selectedContextId}` : 'No contexts found', contexts.length ? 'ready' : 'empty');
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
    return [];
  }
}

function bindContextPicker(select) {
  if (!select) return;
  select.addEventListener('change', () => {
    const selectedContextId = select.value || '';
    persistSelectedContextId(selectedContextId);
    if (globalThis.__voqualizer_page) {
      globalThis.__voqualizer_page.selectedContextId = selectedContextId;
      globalThis.__voqualizer_page.lastContextChangeAt = Date.now();
      const contexts = globalThis.__voqualizer_page.contexts || [];
      for (const ctx of contexts) {
        ctx.active = ctx.id === selectedContextId;
      }
    }
    setPageStatus(selectedContextId ? `Selected ${selectedContextId}` : 'No context selected', selectedContextId ? 'ready' : 'empty');
  });
}

function initVoqualizerPage() {
  const root = document.querySelector('[data-voqualizer-page="standalone"]');
  const prompt = document.getElementById('voq-prompt-input');
  const settings = document.getElementById('voq-settings-button');
  const contextSelect = document.getElementById('voq-context-select');

  globalThis.__voqualizer_page = {
    version: PAGE_VERSION,
    loadedAt: Date.now(),
    route: '/plugins/a0_voqualizer/webui/voqualizer.html',
    milestone: 2,
    standalone: true,
    adminEndpoint: ADMIN_ENDPOINT,
    selectedContextStorageKey: SELECTED_CONTEXT_STORAGE_KEY,
    contexts: [],
    selectedContextId: '',
    contextsLoading: false,
    contextError: '',
  };

  if (!root) return;
  root.dataset.ready = 'true';

  if (prompt) {
    prompt.addEventListener('input', () => autosizePrompt(prompt));
    autosizePrompt(prompt);
  }

  if (settings) {
    settings.addEventListener('click', () => {
      globalThis.__voqualizer_page.lastSettingsClickAt = Date.now();
    });
  }

  bindContextPicker(contextSelect);
  void loadContextPicker(contextSelect);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initVoqualizerPage, { once: true });
} else {
  initVoqualizerPage();
}

export {
  ADMIN_ENDPOINT,
  PAGE_VERSION,
  SELECTED_CONTEXT_STORAGE_KEY,
  fetchContexts,
  initVoqualizerPage,
  normalizeContext,
  normalizeContexts,
};
