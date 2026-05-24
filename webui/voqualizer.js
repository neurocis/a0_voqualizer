const PAGE_VERSION = 'm1-static-shell';

function autosizePrompt(textarea) {
  if (!textarea) return;
  textarea.style.height = 'auto';
  textarea.style.height = `${Math.min(textarea.scrollHeight, window.innerHeight * 0.34)}px`;
}

function initVoqualizerPage() {
  const root = document.querySelector('[data-voqualizer-page="standalone"]');
  const prompt = document.getElementById('voq-prompt-input');
  const settings = document.getElementById('voq-settings-button');

  globalThis.__voqualizer_page = {
    version: PAGE_VERSION,
    loadedAt: Date.now(),
    route: '/plugins/a0_voqualizer/webui/voqualizer.html',
    milestone: 1,
    standalone: true,
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
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initVoqualizerPage, { once: true });
} else {
  initVoqualizerPage();
}

export { initVoqualizerPage, PAGE_VERSION };
