## [Unreleased]
- Fixed OpenAI-compatible TTS numeric timeout handling by converting configured seconds to `aiohttp.ClientTimeout`.
- Added durable TTS route diagnostics and fallback final-response routing from active session context ids when bridge binding lookup misses.
- Reordered Providers settings ASR timing fields so ASR Pre-roll appears before Silence-to-Final.
- Fixed Providers settings ASR timing field wiring markers so Silence-to-Final and ASR Pre-roll remain distinct and cannot appear/save as flipped fields.
- Added per-ASR-provider `asr_preroll_ms` configuration with Providers UI editing and websocket negotiation, defaulting to the current 600 ms leading-ring window.
- Switched ASR utterance starts to an always-on 600 ms leading audio ring so early speech frames are merged even when VAD detects speech late, improving preservation of unique first tokens like Alpha/Pineapple.
- Fixed ASR utterance state reset to reuse the complete state factory after each final, preventing later utterances from losing pre-roll/diagnostic fields after the first successful ASR.
- Hardened ASR utterance construction by copying the leading pre-roll ring exactly once at speech start and exposing pre-roll/segment metadata for first-word-loss diagnostics.
- Added live TTS/ASR pipeline diagnostics and playback hardening: in-GUI TTS now decodes base64 audio fallback, records TTS/playback/agent-final state in `window.__voqualizer_conversation`, emits clearer TTS skip reasons, and tags batch-ASR utterances with generation metadata to suppress stale short leading finals that split first words into separate prompts.
- Hardened the in-GUI Voqualizer conversation controller against connection flapping with a singleton Alpine store, desired-mode tracking, generation guards for stale async work, intentional-disconnect reconnect suppression, debounced context-switch detection, and DevTools diagnostics via `window.__voqualizer_conversation`.
- Moved the dedicated Voqualizer status pill to the right of the Voqualizer mic button so the far-right chat controls read `[Voq Speaker] [Voq Mic] [Voq Status Pill]` while preserving all existing tap/hold, TTS toggle, and dynamic label behavior.
- Improved dedicated in-GUI Voqualizer button clarity: added a compact `Voq:` status pill, dynamic tooltips/ARIA labels for Speaker and Mic modes, stronger visual state cues for idle/listening/PTT/connecting/error/TTS-muted, and short non-intrusive transition notices without changing button behavior or touching A0 native controls.
- Switched in-GUI Voqualizer controls from overriding A0’s native Mic/Speaker (commit 802095c) to two dedicated new circular buttons (`#voqualizer-speaker-button`, `#voqualizer-mic-button`) placed at the far right of the chat-input action row. A0’s original Mic and Speaker buttons are no longer touched. The new Speaker button toggles Voqualizer TTS per context (dim cue, sessionStorage-persisted, live `voqualizer_control set_tts_enabled`); the new Mic button supports quick-tap to toggle Conversational Mode and click-hold (≥ 250 ms) Push-to-Talk with explicit `is_final` on release, plus Space/Enter keyboard support. Backend `tts_enabled` flow is unchanged.
- In-GUI Voqualizer controls now repurpose A0’s Speaker and Mic buttons: Speaker toggles Voqualizer TTS per context (persisted in sessionStorage, dim cue when off), Mic quick-tap (<250 ms) toggles Conversational Mode, click-hold (≥250 ms) is Push-to-Talk and sends an explicit end-of-utterance final on release; PTT overlays an active conversational session (stays connected on release) and disconnects when released from idle; context refocus/switch stops mic, disconnects and resets. Backend now accepts `tts.enabled` in `voqualizer_init` and a `voqualizer_control { action: "set_tts_enabled", enabled }` event, guarded by a new `session.tts_enabled` flag honored in `helpers/agent_finalizer.py` and `helpers/sentence_chunker.py`. Shared audio helpers extracted to `webui/lib/voqualizer-audio.js`; new `webui/conversation-mode.js` Alpine store and `extensions/webui/chat-input-box-end/voqualizer-button-overrides.html` provide the UI integration. The in-plugin tester remains independent.
- Tester now defaults the context picker to the configured Hero ContextID when the `a0_superordinates` plugin is installed and Hero Mode is enabled, regardless of which A0 chat is currently focused.
- Tester current-context defaulting now checks A0 global `getContext()` first so the active AIme/Hero chat is selected before connect.
- Tester now defaults the context picker to the currently selected A0 chat context (for example Hero mode) when opened from the A0 UI.
- ASR quality hardening now preserves first words with utterance pre-roll, suppresses repeated no-speech filler finals, forwards Whisper quality options, and surfaces first-frame/ASR request diagnostics in the tester.
- Providers UI now hides side-inapplicable fields: ASR cards only show ASR timing, while TTS cards keep voice/speed and force PCM 24 kHz output without editable format/sample-rate fields.
- Providers settings now expose `Silence to Final (ms)` as a per-ASR-provider tuning control, defaulting to 1000 ms.
- Final ASR transcript handling now strips repeated leading Whisper silence hallucinations like `Thank you. Thank you.` when real prompt text follows.
- Tester mic capture now uses a muted Web Audio monitor path instead of connecting the worklet directly to speakers, reducing echo-fed ASR hallucinations.
- Tester now performs local mic-VAD barge-in: detected speech stops queued browser TTS immediately and then notifies backend `barge_in`.
- Increased default ASR final-silence threshold from 800 ms to 1000 ms to reduce premature utterance finalization.
- Tester playback now stops queued Web Audio sources immediately when `voqualizer_tts_done(cancelled=true, reason=barge_in)` arrives.
- Automatic barge-in now cancels active streaming TTS on detected user speech and emits an immediate cancelled `voqualizer_tts_done`.
- ASR final injections now explicitly write the prefixed `{ASR: provider}` prompt to the target context visible chat log before `communicate()`.
- Reworked structured response TTS compromise: stream only extracted `text` field content while suppressing envelope keys and normalize final flush.
- Deferred streaming TTS for structured JSON/tool responses so final TTS speaks only normalized `tool_args.text` instead of envelope keys or Markdown.
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
