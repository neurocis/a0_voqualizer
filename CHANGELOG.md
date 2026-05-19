## [Unreleased]
- Final-response TTS now extracts only the response text section and normalizes Markdown into speech-friendly plain text.
- Added visible `{ASR: provider}` prefix for final ASR prompts injected into target A0 chat contexts.
- Added tester PCM16 carry-byte alignment so streamed raw PCM chunks split mid-sample do not desynchronize playback into static.
- Routed context-driven final assistant responses through the same reliable PCM/provider-default TTS path as direct tester text, including base64 audio fallback.
- Made Voqualizer admin handler robust against plugin-local `helpers` import shadowing during context-list diagnostics.
- Hardened tester context refresh to use A0 `callJsonApi` admin helper before fetch fallback so the context picklist populates reliably.
- Added tester context-id picklist backed by admin `contexts` action and final-ASR ContextBridge injection for selected A0 chat pipeline binding.
- Decoupled batch ASR transcription from the `voqualizer_audio_chunk` ack path so slow HTTP ASR calls cannot turn mic ingress into `HANDLER_ERROR`/timeout failures.
- Fixed Providers TTS smoke test to honor provider-configured `format` and `sample_rate` (e.g. `pcm` @ 24000 Hz) instead of forcing `pcm16/16k`, producing audible previews on Kokoro-style PCM endpoints.
- Added configurable TTS speed as a provider field used by smoke tests, tester/direct TTS, and agent-response TTS requests.
- Fixed slow PCM TTS playback from the tester by no longer forcing 16 kHz text-to-speech requests over provider-configured sample rates.
- Added raw PCM TTS smoke-preview playback, mapped Kokoro PCM defaults to 24 kHz live TTS requests, and contained Providers-page test-result layout expansion.
- Fixed encoded TTS playback by collecting full WAV/MP3/Opus streams before browser playback, adding a JSON-safe TTS audio fallback, and repairing Kokoro RIFF/WAVE preview headers when needed.
- Added audible TTS provider smoke-test previews and surfaced encoded WAV/MP3/Opus chunks with matching codecs for browser playback.
- Fixed Tester transcript rendering by unwrapping A0 Socket.IO event envelopes before updating ASR partial/final and agent response panels.
- Replaced the fixed 200ms batch-ASR trigger with an utterance buffer that emits ~1s partials and silence-finalized transcripts for OpenAI-compatible Whisper providers.
- Added a 200ms ASR aggregation buffer for batch/OpenAI-compatible Whisper providers so 20ms tester mic frames are transcribed as usable segments instead of empty per-frame results.
- Added a JSON-safe base64 fallback for tester audio frames so browser/A0 paths that strip nested binary attachments still reach ASR instead of `BAD_AUDIO_CHUNK`.

# Changelog

## Unreleased

### Fixed
- Fixed Providers page layout overflow after TTS tests by constraining result/event text and redacting large base64 blobs from the admin event log.
- Hardened tester audio transport and backend frame extraction so browser Socket.IO decoded binary shapes no longer fail with `BAD_AUDIO_CHUNK` before ASR.


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
