# Changelog

## Unreleased

- Tester reconnect now creates a fresh Voqualizer session after `end_session` instead of reusing an ended session id/token state.

- Providers page now exposes common provider settings (`endpoint`, `model`, `voice`, `api_key_env`, `format`, and `sample_rate`) as first-class editable fields instead of hiding them behind blank/advanced options JSON.

### Fixed
- Made the Providers page Save button always available except while a save request is in progress; dirty state is now informational only.
- Replaced the Providers page `test_provider` admin stub with real ASR/TTS smoke checks that return latency, provider metadata, pass/fail status, and M3/M4 error taxonomy codes.
- Fixed Providers page Save UX dirty tracking so structural provider changes immediately enable Save and reverting back to the loaded config clears the dirty state.

## v0.1.0 — 2026-05-15

Initial hardening-track release for `a0_voqualizer`.

### Added

- Full-duplex `plugins/a0_voqualizer/ws_voqualizer` protocol handler.
- Same-origin admin endpoint: `/api/plugins/a0_voqualizer/voqualizer_admin`.
- per-session `bearer_token` authorization for session-bound operations.
- A2 4-byte audio frame support with network-order `uint16 seq` and `uint16 ts_ms`.
- PCM16/G.711/Opus codec helpers and deterministic codec fuzz coverage.
- ASR provider adapters and deterministic mock ASR provider.
- TTS provider adapters and deterministic mock TTS provider.
- AgentContext binding and streamed agent token/final-response integration.
- Browser WebUI:
  - `webui/audio-worklet.js`
  - `webui/tester.html`
  - `webui/tester-store.js`
  - `webui/config.html`
  - `webui/providers.html`
  - `webui/config-store.js`
- Diagnostics overlay with latency timers, event log, and frame inspector.
- Client/bridge examples:
  - iOS Swift demo client
  - Android Kotlin demo client
  - Twilio Media Streams bridge
  - Asterisk audio-fork bridge with sample config
- Protocol portability matrix: `docs/clients/portability.md`.
- Security review and deterministic codec/protocol fuzz tests.
- 32 concurrent session load harness and metrics report.
- Stable error taxonomy and A0-standard telemetry logging.
- Updated README, SKILL.md, settings panel docs, and examples README.

### Release validation

- Normal pytest remains deterministic and offline.
- No backend restart, real credentials, external network, model downloads, live browser,
  live LLM/provider calls, platform SDKs, telephony accounts, Node install, Asterisk
  install, or live A0 backend are required for normal tests.
- Latest release-candidate regression: `pytest -q` passes.

### Installability

The plugin is installable through the standard A0 plugin mechanism:

- `plugin.yaml` declares `name: a0_voqualizer`, `version: 0.1.0`, metadata, and settings sections.
- `hooks.py` provides standard `install()` / `uninstall()` hooks.
- `requirements.txt` and `hooks.py` cover runtime dependency installation/status.
- `default_config.yaml` provides the default provider catalogue and runtime limits.
