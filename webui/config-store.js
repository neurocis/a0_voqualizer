/*
 * A0 Voqualizer provider configuration store.
 *
 * No-build browser ES module for the in-plugin settings/config page. It talks
 * to the existing A1.5 admin REST endpoint and keeps provider CRUD fully
 * client-side until save(), where the merged provider/default overlay is sent
 * through the schema-validating `save` action.
 */

export const ADMIN_ENDPOINT = '/api/plugins/a0_voqualizer/voqualizer_admin';
export const PROVIDER_SIDES = ['asr', 'tts'];

const ASR_PROVIDER_TYPES = ['mock', 'whisper', 'faster-whisper', 'openai', 'openai-compatible', 'localai'];
const TTS_PROVIDER_TYPES = ['mock', 'piper', 'openai', 'openai-compatible', 'localai'];

function nowMs() {
  return Date.now();
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function providerTypesFor(side) {
  return side === 'tts' ? TTS_PROVIDER_TYPES : ASR_PROVIDER_TYPES;
}

function makeProviderName(side) {
  return `${side}-provider-${nowMs().toString(36)}`;
}

function defaultProvider(side) {
  if (side === 'tts') {
    return {
      name: makeProviderName(side),
      type: 'mock',
      enabled: true,
      voice: 'mock',
      options: {},
    };
  }
  return {
    name: makeProviderName(side),
    type: 'mock',
    enabled: true,
    language: 'en',
    options: {},
  };
}

function providerKey(provider) {
  return provider && typeof provider.name === 'string' ? provider.name : '';
}

function normalizeProvider(side, provider = {}) {
  const types = providerTypesFor(side);
  const options = provider.options && typeof provider.options === 'object' && !Array.isArray(provider.options) ? provider.options : {};
  const normalized = {
    ...provider,
    name: String(provider.name || makeProviderName(side)).trim(),
    type: String(provider.type || 'mock').trim(),
    enabled: provider.enabled !== false,
    options,
  };
  // Providers can be edited either through top-level fields or advanced
  // options JSON. Keep top-level fields authoritative when present, but lift
  // common keys from options so endpoint/model/key/voice/etc. are always
  // visible and editable in the Providers UI.
  for (const key of ['endpoint', 'base_url', 'model', 'api_key_env', 'voice', 'format', 'response_format', 'sample_rate', 'speed', 'language', 'streaming']) {
    if ((normalized[key] == null || normalized[key] === '') && options[key] != null && options[key] !== '') {
      normalized[key] = options[key];
    }
  }
  if (!types.includes(normalized.type)) {
    normalized.type = 'mock';
  }
  if (side === 'asr' && !normalized.language) {
    normalized.language = 'en';
  }
  if (side === 'tts' && !normalized.voice) {
    normalized.voice = 'mock';
  }
  for (const key of ['endpoint', 'base_url', 'model', 'api_key_env', 'voice', 'format', 'response_format', 'sample_rate', 'speed']) {
    if (normalized[key] === '') {
      delete normalized[key];
    }
  }
  return normalized;
}

function normalizeConfig(config = {}) {
  const asrProviders = Array.isArray(config.asr && config.asr.providers) ? config.asr.providers : [];
  const ttsProviders = Array.isArray(config.tts && config.tts.providers) ? config.tts.providers : [];
  const asr = asrProviders.length ? asrProviders.map((provider) => normalizeProvider('asr', provider)) : [defaultProvider('asr')];
  const tts = ttsProviders.length ? ttsProviders.map((provider) => normalizeProvider('tts', provider)) : [defaultProvider('tts')];
  return {
    ...clone(config || {}),
    asr: {
      ...(config.asr || {}),
      providers: asr,
      default: config.asr && asr.some((provider) => provider.name === config.asr.default) ? config.asr.default : asr[0].name,
    },
    tts: {
      ...(config.tts || {}),
      providers: tts,
      default: config.tts && tts.some((provider) => provider.name === config.tts.default) ? config.tts.default : tts[0].name,
    },
  };
}

function validateProvider(side, provider, peers) {
  if (!provider.name || !/^[A-Za-z0-9_.-]+$/.test(provider.name)) {
    return 'Provider name must use only letters, numbers, underscore, dot, or dash.';
  }
  if (!providerTypesFor(side).includes(provider.type)) {
    return `Unsupported ${side.toUpperCase()} provider type: ${provider.type}`;
  }
  const duplicates = peers.filter((candidate) => candidate.name === provider.name).length;
  if (duplicates > 1) {
    return `Duplicate provider name: ${provider.name}`;
  }
  return '';
}

export function overlayFromConfig(config) {
  return {
    asr: {
      default: config.asr.default,
      providers: config.asr.providers,
    },
    tts: {
      default: config.tts.default,
      providers: config.tts.providers,
    },
  };
}

export function createVoqualizerConfigStore(options = {}) {
  const endpoint = options.endpoint || ADMIN_ENDPOINT;
  const fetchImpl = options.fetch || globalThis.fetch;
  const listeners = new Set();
  const initialConfig = normalizeConfig(options.initialConfig || {});
  const state = {
    loading: false,
    saving: false,
    dirty: false,
    config: initialConfig,
    loadedOverlay: overlayFromConfig(initialConfig),
    testResults: {},
    events: [],
    error: null,
  };

  function snapshot() {
    return {
      ...state,
      config: clone(state.config),
      testResults: clone(state.testResults),
      events: clone(state.events),
    };
  }

  function notify() {
    const copy = snapshot();
    for (const listener of listeners) {
      listener(copy);
    }
  }

  function setState(patch) {
    Object.assign(state, patch);
    notify();
  }

  function computeDirty() {
    return !sameJson(overlayFromConfig(state.config), state.loadedOverlay);
  }

  function markDirty() {
    setState({ dirty: computeDirty() });
  }

  function appendEvent(event, payload = {}) {
    state.events.push({ event, ts: nowMs(), payload });
    if (state.events.length > 100) {
      state.events.splice(0, state.events.length - 100);
    }
    notify();
  }

  async function admin(action, payload = {}) {
    if (!fetchImpl) {
      throw new Error('fetch is required for Voqualizer config admin calls');
    }
    const response = await fetchImpl(endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...payload }),
    });
    if (!response.ok) {
      throw new Error(`Admin request failed: HTTP ${response.status}`);
    }
    const data = await response.json();
    if (data && data.ok === false && action !== 'test_provider') {
      throw new Error(data.message || data.code || `${action} failed`);
    }
    appendEvent(`admin:${action}`, data);
    return data;
  }

  async function load() {
    setState({ loading: true, error: null });
    try {
      const data = await admin('config');
      const loadedConfig = normalizeConfig(data.config || {});
      setState({
        config: loadedConfig,
        loadedOverlay: overlayFromConfig(loadedConfig),
        loading: false,
        dirty: false,
      });
      return data;
    } catch (error) {
      setState({ loading: false, error: error.message || String(error) });
      throw error;
    }
  }

  function providers(side) {
    return state.config[side].providers;
  }

  function setDefault(side, name) {
    if (!PROVIDER_SIDES.includes(side)) {
      throw new Error(`Unsupported provider side: ${side}`);
    }
    if (!providers(side).some((provider) => provider.name === name)) {
      throw new Error(`Unknown ${side.toUpperCase()} provider: ${name}`);
    }
    state.config[side].default = name;
    markDirty();
    appendEvent('provider:default', { side, name });
  }

  function addProvider(side, seed = {}) {
    if (!PROVIDER_SIDES.includes(side)) {
      throw new Error(`Unsupported provider side: ${side}`);
    }
    const provider = normalizeProvider(side, { ...defaultProvider(side), ...seed });
    let suffix = 2;
    const baseName = provider.name;
    while (providers(side).some((candidate) => candidate.name === provider.name)) {
      provider.name = `${baseName}-${suffix}`;
      suffix += 1;
    }
    providers(side).push(provider);
    if (!state.config[side].default) {
      state.config[side].default = provider.name;
    }
    markDirty();
    appendEvent('provider:add', { side, name: provider.name });
    return provider;
  }

  function updateProvider(side, originalName, patch = {}) {
    if (!PROVIDER_SIDES.includes(side)) {
      throw new Error(`Unsupported provider side: ${side}`);
    }
    const list = providers(side);
    const index = list.findIndex((provider) => provider.name === originalName);
    if (index < 0) {
      throw new Error(`Unknown ${side.toUpperCase()} provider: ${originalName}`);
    }
    const updated = normalizeProvider(side, { ...list[index], ...patch });
    const validation = validateProvider(side, updated, [...list.slice(0, index), updated, ...list.slice(index + 1)]);
    if (validation) {
      throw new Error(validation);
    }
    const oldName = list[index].name;
    list[index] = updated;
    if (state.config[side].default === oldName) {
      state.config[side].default = updated.name;
    }
    markDirty();
    appendEvent('provider:update', { side, oldName, name: updated.name });
    return updated;
  }

  function removeProvider(side, name) {
    if (!PROVIDER_SIDES.includes(side)) {
      throw new Error(`Unsupported provider side: ${side}`);
    }
    const list = providers(side);
    if (list.length <= 1) {
      throw new Error(`At least one ${side.toUpperCase()} provider is required`);
    }
    const index = list.findIndex((provider) => provider.name === name);
    if (index < 0) {
      throw new Error(`Unknown ${side.toUpperCase()} provider: ${name}`);
    }
    list.splice(index, 1);
    if (state.config[side].default === name) {
      state.config[side].default = list[0].name;
    }
    markDirty();
    appendEvent('provider:remove', { side, name });
  }

  async function save() {
    setState({ saving: true, error: null });
    try {
      for (const side of PROVIDER_SIDES) {
        for (const provider of providers(side)) {
          const validation = validateProvider(side, provider, providers(side));
          if (validation) {
            throw new Error(validation);
          }
        }
      }
      const data = await admin('save', { overlay: overlayFromConfig(state.config) });
      const savedConfig = normalizeConfig(data.config || state.config);
      setState({
        config: savedConfig,
        loadedOverlay: overlayFromConfig(savedConfig),
        saving: false,
        dirty: false,
      });
      return data;
    } catch (error) {
      setState({ saving: false, error: error.message || String(error) });
      throw error;
    }
  }

  async function testProvider(side, name) {
    if (!PROVIDER_SIDES.includes(side)) {
      throw new Error(`Unsupported provider side: ${side}`);
    }
    if (!providers(side).some((provider) => provider.name === name)) {
      throw new Error(`Unknown ${side.toUpperCase()} provider: ${name}`);
    }
    const key = `${side}:${name}`;
    state.testResults[key] = { ok: null, message: 'running', ts: nowMs() };
    notify();
    const data = await admin('test_provider', { side, name });
    state.testResults[key] = {
      ok: data.ok !== false,
      code: data.code || '',
      message: data.message || (data.ok === false ? 'failed' : 'ok'),
      ts: nowMs(),
      response: data,
    };
    notify();
    return data;
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(snapshot());
    return () => listeners.delete(listener);
  }

  return {
    getState: snapshot,
    subscribe,
    load,
    save,
    addProvider,
    updateProvider,
    removeProvider,
    setDefault,
    testProvider,
    overlayFromConfig: () => overlayFromConfig(state.config),
    providerTypesFor,
  };
}
