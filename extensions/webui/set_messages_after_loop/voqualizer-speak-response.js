/**
 * Speak rendered A0 assistant responses through the active Voqualizer session.
 *
 * This is a browser-side fallback for live contexts where Python finalization
 * hooks do not reliably see the final visible response. It intentionally uses
 * the existing authenticated `voqualizer_user_text` WebSocket path, so TTS works
 * whether ASR is enabled or not.
 */
const SPOKEN_KEY_PREFIX = 'a0_voqualizer.spoken_response.';
const MAX_SPEAK_CHARS = 12000;

export default async function speakVoqualizerRenderedResponses(context) {
  const store = globalThis.Alpine?.store?.('voqualizer');
  if (!store || typeof store.speakText !== 'function') return;
  if (!store.isTtsEnabled?.()) return;
  if (!context?.results?.length || context.historyEmpty) return;

  for (const { args, result } of context.results) {
    if (!isPrimaryResponse(args, result)) continue;
    const responseId = responseIdentity(args);
    if (alreadySpoken(responseId)) continue;
    const text = responseTextFromElement(result.element);
    if (!text) continue;
    markSpoken(responseId);
    await store.speakText(text.slice(0, MAX_SPEAK_CHARS), {
      response_id: responseId,
      utterance_id: `gui-response-${responseId}`,
    });
  }
}

function isPrimaryResponse(args = {}, result = {}) {
  if (String(args?.type || '') !== 'response') return false;
  if (Number(args?.agentno || 0) > 0) return false;
  return Boolean(result?.element?.querySelector?.('.message-agent-response'));
}

function responseIdentity(args = {}) {
  return String(args?.id || args?.no || args?.timestamp || Date.now());
}

function responseTextFromElement(element) {
  const root = element?.querySelector?.('.message-agent-response') || element;
  const body = root?.querySelector?.('.msg-content')
    || root?.querySelector?.('.message-body')
    || root;
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
  } catch {
    // Ignore storage failures; in-memory marker below still helps per page.
  }
  const spoken = globalThis.__a0VoqualizerSpokenResponses || new Set();
  globalThis.__a0VoqualizerSpokenResponses = spoken;
  return spoken.has(responseId);
}

function markSpoken(responseId) {
  const key = SPOKEN_KEY_PREFIX + responseId;
  try { sessionStorage.setItem(key, '1'); } catch {}
  const spoken = globalThis.__a0VoqualizerSpokenResponses || new Set();
  spoken.add(responseId);
  globalThis.__a0VoqualizerSpokenResponses = spoken;
}
