/**
 * Observe rendered A0 assistant responses and speak them through Voqualizer.
 *
 * Uses a per-node debounce so we only speak the response after streaming has
 * settled, and uses the stable message DOM id (when available) for dedup so
 * partial streaming text variants do not produce multiple speak attempts.
 */
const SPOKEN_KEY_PREFIX = 'a0_voqualizer.observed_response.';
const MAX_SPEAK_CHARS = 12000;
const OBSERVER_FLAG = '__a0VoqualizerResponseObserverInstalled';
const PENDING_ATTR = 'data-voqualizer-tts-pending';
const SPOKEN_ATTR = 'data-voqualizer-tts-spoken';
const STABILITY_DELAY_MS = 900;

const pending = new Map(); // responseId -> { node, timer, lastText, lastSeenAt, scheduledAt }

export default async function installVoqualizerResponseObserver() {
  if (globalThis[OBSERVER_FLAG]) return;
  globalThis[OBSERVER_FLAG] = true;

  const state = ensureObserverDebugState();
  state.installedAt = Date.now();

  const observer = new MutationObserver(() => {
    queueMicrotask(() => scanRenderedResponses('mutation'));
  });

  try {
    observer.observe(document.body || document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    state.observing = true;
  } catch (err) {
    state.lastError = err && err.message ? err.message : String(err || 'observer_failed');
  }

  globalThis.__a0VoqualizerResponseObserver = {
    observer,
    scan: () => scanRenderedResponses('manual'),
    pending,
  };
  scanRenderedResponses('install');
}

function scanRenderedResponses(reason = 'scan') {
  const state = ensureObserverDebugState();
  state.lastScanAt = Date.now();
  state.lastScanReason = reason;

  const store = globalThis.Alpine?.store?.('voqualizer');
  if (!store || typeof store.speakText !== 'function') {
    state.lastSkipReason = 'store_unavailable';
    return;
  }
  if (!store.isTtsEnabled?.()) {
    state.lastSkipReason = 'tts_disabled';
    return;
  }

  const nodes = Array.from(document.querySelectorAll('.message-agent-response'));
  state.lastNodeCount = nodes.length;
  for (const node of nodes) {
    considerNode(node, store);
  }
}

function considerNode(node, store) {
  const state = ensureObserverDebugState();
  if (!node) return;
  if (node.getAttribute?.(SPOKEN_ATTR) === '1') {
    state.lastSkipReason = 'already_spoken_attr';
    return;
  }

  const responseId = responseIdentity(node);
  if (alreadySpoken(responseId)) {
    node.setAttribute?.(SPOKEN_ATTR, '1');
    state.lastSkipReason = 'already_spoken';
    return;
  }

  const text = responseTextFromNode(node);
  if (!text) {
    state.lastSkipReason = 'empty_text';
    return;
  }

  let entry = pending.get(responseId);
  if (!entry) {
    entry = { node, timer: null, lastText: '', lastSeenAt: 0, scheduledAt: 0 };
    pending.set(responseId, entry);
  }

  // If text changed, reset the stability timer.
  if (text !== entry.lastText) {
    entry.lastText = text;
    entry.lastSeenAt = Date.now();
    entry.scheduledAt = Date.now() + STABILITY_DELAY_MS;
    state.lastSkipReason = 'debouncing';
    state.lastResponseId = responseId;
    state.lastText = text.slice(0, 160);
    if (entry.timer) clearTimeout(entry.timer);
    entry.timer = setTimeout(() => {
      pending.delete(responseId);
      speakStableResponse(responseId, node, entry.lastText, store);
    }, STABILITY_DELAY_MS);
  }
}

function speakStableResponse(responseId, node, text, store) {
  const state = ensureObserverDebugState();
  if (!text) return;
  if (alreadySpoken(responseId)) {
    node.setAttribute?.(SPOKEN_ATTR, '1');
    state.lastSkipReason = 'already_spoken_at_dispatch';
    return;
  }
  if (node.getAttribute?.(PENDING_ATTR) === '1') {
    state.lastSkipReason = 'already_pending';
    return;
  }

  node.setAttribute?.(PENDING_ATTR, '1');
  state.lastResponseId = responseId;
  state.lastText = text.slice(0, 160);
  state.lastSpeakAttemptAt = Date.now();
  state.speakAttemptCount += 1;

  Promise.resolve(store.speakText(text.slice(0, MAX_SPEAK_CHARS), {
    response_id: responseId,
    utterance_id: `gui-observed-response-${responseId}`,
  })).then((ack) => {
    node.removeAttribute?.(PENDING_ATTR);
    state.lastAck = ack || null;
    state.lastSpeakAckAt = Date.now();
    if (ack && ack.ok === false) {
      state.lastError = String(ack.code || ack.reason || 'speak_failed');
      return;
    }
    markSpoken(responseId);
    node.setAttribute?.(SPOKEN_ATTR, '1');
    state.lastError = '';
  }).catch((err) => {
    node.removeAttribute?.(PENDING_ATTR);
    state.lastError = err && err.message ? err.message : String(err || 'speak_failed');
  });
}

function responseIdentity(node) {
  // Prefer stable message-level identifiers that do not change during streaming.
  const message = node.closest?.('[data-message-id], [data-id], .message-container, .message, .msg');
  const raw = message?.dataset?.messageId
    || message?.dataset?.id
    || message?.id
    || node?.id
    || '';
  if (raw) return String(raw);
  // Fall back to DOM-position-based id so streaming text changes do not mint new ids.
  const container = node.closest?.('.message-container') || node.parentElement || node;
  const index = container ? Array.prototype.indexOf.call(container.parentNode?.children || [], container) : -1;
  return `dom-${index}-${node.tagName}`;
}

function responseTextFromNode(node) {
  const body = node.querySelector?.('.msg-content')
    || node.querySelector?.('.message-body')
    || node;
  const clone = body?.cloneNode?.(true);
  if (!clone) return '';
  for (const noisy of clone.querySelectorAll?.('script,style,.step-action-buttons,.document-response-file-cards,button') || []) {
    noisy.remove();
  }
  return normalizeSpeechText(clone.textContent || '');
}

function normalizeSpeechText(text) {
  return String(text || '')
    .replace(/\s+/g, ' ')
    .replace(/^\s*\{ASR:[^}]+}\s*/i, '')
    .trim();
}

function alreadySpoken(responseId) {
  const key = SPOKEN_KEY_PREFIX + responseId;
  try {
    if (sessionStorage.getItem(key)) return true;
  } catch {}
  const spoken = globalThis.__a0VoqualizerObservedResponses || new Set();
  globalThis.__a0VoqualizerObservedResponses = spoken;
  return spoken.has(responseId);
}

function markSpoken(responseId) {
  const key = SPOKEN_KEY_PREFIX + responseId;
  try { sessionStorage.setItem(key, '1'); } catch {}
  const spoken = globalThis.__a0VoqualizerObservedResponses || new Set();
  spoken.add(responseId);
  globalThis.__a0VoqualizerObservedResponses = spoken;
}

function ensureObserverDebugState() {
  const state = globalThis.__voqualizer_response_observer || {
    installedAt: 0,
    observing: false,
    lastScanAt: 0,
    lastScanReason: '',
    lastNodeCount: 0,
    lastSkipReason: '',
    lastResponseId: '',
    lastText: '',
    lastSpeakAttemptAt: 0,
    lastSpeakAckAt: 0,
    lastAck: null,
    lastError: '',
    speakAttemptCount: 0,
  };
  globalThis.__voqualizer_response_observer = state;
  return state;
}
