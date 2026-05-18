---
name: a0_voqualizer
description: Real-time full-duplex WebSocket ASR/TTS bridge for Agent Zero. Use this skill when the user wants to talk to A0 with voice from browser, mobile, Twilio, Asterisk, or another PCM/VoIP client.
triggers:
  - voqualizer
  - voice bridge
  - real-time voice
  - streaming voice
  - websocket asr
  - websocket tts
  - voice agent
  - talk to A0
  - voip bridge
  - twilio media streams
  - asterisk audio fork
---

# a0_voqualizer

## What this plugin does

`a0_voqualizer` provides a full-duplex streaming voice loop for Agent Zero:

- captures client audio as A2-framed `voqualizer_audio_chunk` events;
- streams ASR partials/finals back to clients;
- injects final transcripts into an A0 `AgentContext`;
- streams agent token deltas;
- synthesizes and streams TTS chunks;
- supports barge-in, mute/unmute, session resume, bounded queues, and stable
  `voqualizer_error` codes.

The WebSocket handler is `plugins/a0_voqualizer/ws_voqualizer` and session-bound
operations require the per-session `bearer_token` issued by `voqualizer_ready`.

## When to use

Use this skill when the user wants:

- browser microphone voice chat with A0;
- mobile voice clients for iOS or Android;
- a Twilio Media Streams or Asterisk bridge;
- full-duplex speech with barge-in instead of turn-only chat;
- diagnostics for latency, audio frames, ASR, agent deltas, or TTS playback;
- provider configuration for ASR/TTS.

Do **not** use this for one-off recorded-file transcription; use a transcription
plugin/workflow instead.

## How to use

1. Open the **A0 Voqualizer Settings** modal — the standard A0 plugin Settings
   modal is populated from the Alpine fragment in `webui/config.html` (behavior
   toggles, default ASR/TTS provider, protocol defaults, runtime limits).
2. Click **Open Providers editor** in that modal, or open
   `webui/providers.html` directly, for full ASR/TTS provider CRUD and
   **Test provider** through the same-origin admin endpoint.
3. Use provider test buttons to validate provider wiring.
4. Open the tester (`webui/tester.html`) for microphone capture, VU, frame
   inspector, event log, ASR/agent rendering, and streamed TTS playback.
5. For external clients, follow the client contracts in:
   - `docs/clients/portability.md`
   - `examples/README.md`
   - platform-specific example directories.

## Important protocol reminders

- Call `voqualizer_init` first.
- Store `bearer_token` from `voqualizer_ready`.
- Attach `bearer_token` to `voqualizer_audio_chunk`, `voqualizer_user_text`, and
  `voqualizer_control`.
- Frame audio with a 4-byte A2 header: network-order `uint16 seq` +
  network-order `uint16 ts_ms`, then payload bytes.
- Prefer `pcm16/16k` for portable clients; telephony bridges may transcode
  µ-law/8k or signed-linear PCM16/8k.
- Branch on stable error `code`, not human-readable `message`.

## References

- `README.md` — current feature overview and quick start.
- `docs/protocol/errors.md` — stable error taxonomy and A0-standard telemetry.
- `docs/security/review.md` — security review.
- `docs/performance/load-test-32-sessions.md` — 32-session load-test report.
- `docs/clients/portability.md` — browser/mobile/VoIP portability matrix.
- `examples/README.md` — example index.
