/**
 * Observe rendered A0 assistant responses and speak them through Voqualizer.
 *
 * Waits for response completion (presence of `.step-action-buttons` inside
 * `.message-agent-response`) before speaking, with a long debounce timeout as
 * a safety net. Uses stable message DOM ids for dedup.
 */
const SPOKEN_KEY_PREFIX = 'a0_voqualizer.observed_response.';
const MAX_SPEAK_CHARS = 12000;
const OBSERVER_FLAG = '__a0VoqualizerResponseObserverInstalled';
const PENDING_ATTR = 'data-voqualizer-tts-pending';
const SPOKEN_ATTR = 'data-voqualizer-tts-spoken';
const FALLBACK_TIMEOUT_MS = 3500;   // safety net if no completion marker appears
const MIN_STABILITY_MS = 600;       // require this much DOM-quiet before speaking
const INSTALL_GRACE_MS = 3000;      // ignore responses observed during initial history render

const pending = new Map(); // responseId -> { node, lastText, lastSeenAt, stabilityTimer, fallbackTimer, scheduledAt }
const historicalNodes = new WeakSet(); // nodes that existed at install time -> never speak
const responseNodeIds = new WeakMap(); // fallback ids for nodes with no stable DOM/message id
let nextResponseNodeId = 1;

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

  // Snapshot existing .message-agent-response nodes by reference. Any node
  // that exists at install time is historical and must never be spoken, even
  // if its text mutates later (streaming render, collapse/expand, etc).
  // New nodes added later are naturally not in the WeakSet and will be
  // considered for speech. This is more robust than time-based grace windows.
  preMarkHistoricalResponses();
  state.armedAt = Date.now();
  scanRenderedResponses('armed');
}

function preMarkHistoricalResponses() {
  // On install, capture every already-rendered .message-agent-response node
  // by reference in a WeakSet. These nodes are historical — they must never
  // be spoken even if mutated later (DOM re-renders, streaming completion,
  // collapse/expand). New nodes added later are not in the WeakSet and will
  // be considered for speech normally.
  const nodes = Array.from(document.querySelectorAll('.message-agent-response'));
  for (const node of nodes) {
    historicalNodes.add(node);
    node.setAttribute?.(SPOKEN_ATTR, '1');
  }
  const state = ensureObserverDebugState();
  state.preMarkedHistoricalCount = nodes.length;
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
  state.lastConversationalSeen = !!store.conversational;

  const nodes = Array.from(document.querySelectorAll('.message-agent-response'));
  state.lastNodeCount = nodes.length;
  for (const node of nodes) {
    considerNode(node, store);
  }
}

function considerNode(node, store) {
  const state = ensureObserverDebugState();
  if (!node) return;
  // Historical node by reference - never speak it, regardless of text changes.
  if (historicalNodes.has(node)) {
    state.lastSkipReason = 'historical_node';
    return;
  }
  if (node.getAttribute?.(SPOKEN_ATTR) === '1') return;

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
    entry = {
      node,
      lastText: '',
      lastSeenAt: 0,
      firstSeenAt: Date.now(),
      stabilityTimer: null,
      fallbackTimer: null,
    };
    pending.set(responseId, entry);
    // Schedule a fallback in case completion marker never appears.
    entry.fallbackTimer = setTimeout(() => {
      const e = pending.get(responseId);
      if (!e) return;
      pending.delete(responseId);
      if (e.stabilityTimer) clearTimeout(e.stabilityTimer);
      const finalText = responseTextFromNode(e.node) || e.lastText;
      speakStableResponse(responseId, e.node, finalText, store, 'fallback_timeout');
    }, FALLBACK_TIMEOUT_MS);
  }

  const complete = isResponseComplete(node);
  state.lastCompleteSeen = complete;

  if (text !== entry.lastText) {
    entry.lastText = text;
    entry.lastSeenAt = Date.now();
    state.lastResponseId = responseId;
    state.lastText = text.slice(0, 160);
    state.lastSkipReason = complete ? 'complete_pending_debounce' : 'streaming_debounce';
    if (entry.stabilityTimer) clearTimeout(entry.stabilityTimer);
    // If the response is already "complete" by DOM marker, use a short stability
    // pause. If still streaming, keep waiting for either completion or fallback.
    if (complete) {
      entry.stabilityTimer = setTimeout(() => {
        const e = pending.get(responseId);
        if (!e) return;
        pending.delete(responseId);
        if (e.fallbackTimer) clearTimeout(e.fallbackTimer);
        const finalText = responseTextFromNode(e.node) || e.lastText;
        speakStableResponse(responseId, e.node, finalText, store, 'completion_marker');
      }, MIN_STABILITY_MS);
    }
    return;
  }

  // Same text observed again — if completion marker is now present and we have
  // not yet scheduled a stability timer, schedule one now.
  if (complete && !entry.stabilityTimer) {
    entry.stabilityTimer = setTimeout(() => {
      const e = pending.get(responseId);
      if (!e) return;
      pending.delete(responseId);
      if (e.fallbackTimer) clearTimeout(e.fallbackTimer);
      const finalText = responseTextFromNode(e.node) || e.lastText;
      speakStableResponse(responseId, e.node, finalText, store, 'completion_marker_late');
    }, MIN_STABILITY_MS);
  }
}

function isResponseComplete(node) {
  // A0 adds `.step-action-buttons` inside the message-agent-response (or its
  // surrounding message-container) only after the response is complete.
  if (node.querySelector?.('.step-action-buttons')) return true;
  const container = node.closest?.('.message-container');
  if (container && container.querySelector('.step-action-buttons')) return true;
  return false;
}

function speakStableResponse(responseId, node, text, store, reason) {
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
  state.lastSpeakReason = reason;
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
  const message = node.closest?.('[data-message-id], [data-id], .message-container, .message, .msg');
  const raw = message?.dataset?.messageId
    || message?.dataset?.id
    || message?.id
    || node?.id
    || '';
  if (raw) return String(raw);

  // Some A0 response nodes do not expose stable message ids. The previous
  // fallback used DOM sibling indexes (dom-${index}-${tagName}), but many
  // response nodes can report the same index as the DOM mutates/collapses.
  // That collapses all responses into one pending entry, so the observer may
  // dispatch old history/compaction text while the visible latest response is
  // skipped or times out. Assign a page-lifetime id by node reference instead.
  let assigned = responseNodeIds.get(node);
  if (!assigned) {
    assigned = `node-${nextResponseNodeId++}-${node.tagName || 'NODE'}`;
    responseNodeIds.set(node, assigned);
  }
  return assigned;
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
  // In-memory only: every page load is a fresh start so we never collide with
  // stale sessionStorage markers from earlier sessions or removed extensions.
  const spoken = globalThis.__a0VoqualizerObservedResponses || new Set();
  globalThis.__a0VoqualizerObservedResponses = spoken;
  return spoken.has(responseId);
}

function markSpoken(responseId) {
  // In-memory only.
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
    lastSpeakReason: '',
    lastAck: null,
    lastError: '',
    lastCompleteSeen: false,
    lastConversationalSeen: false,
    speakAttemptCount: 0,
    armedAt: 0,
    installGraceMs: INSTALL_GRACE_MS,
    preMarkedHistoricalCount: 0,
  };
  globalThis.__voqualizer_response_observer = state;
  return state;
}
