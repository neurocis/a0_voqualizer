# a0_voqualizer

**🎙️ Real-time streaming voice ↔ agent ↔ voice for Agent Zero.**

`a0_voqualizer` is a full-duplex WebSocket bridge plugin that streams
microphone or telephony audio into Agent Zero, returns streaming ASR partials
and finals, streams agent token deltas, and synthesizes streamed TTS audio back
to the client. It supports barge-in, codec negotiation, session resume,
bounded-queue backpressure, per-session bearer-token authorization, browser
settings/tester UIs, and portable mobile/VoIP reference clients.

## Current status

M1–M8.4 functionality is implemented, and v0.1.0 release-candidate packaging is in place:

- WebSocket protocol handler: `plugins/a0_voqualizer/ws_voqualizer`
- REST admin endpoint: `/api/plugins/a0_voqualizer/voqualizer_admin`
- ASR/TTS adapters with deterministic mock providers for tests
- A5.5 per-session `bearer_token` semantics
- In-plugin tester, provider settings panel, and diagnostics overlay
- iOS, Android, Twilio Media Streams, and Asterisk reference examples
- Security review, deterministic codec fuzz tests, 32-session load harness, and
  stable error taxonomy/telemetry

Normal pytest is deterministic and requires no backend restart, real
credentials, external network, model downloads, live browser, live LLM/provider
calls, platform SDKs, telephony accounts, Node install, Asterisk install, or
live A0 backend.

## Quick start

1. Enable the plugin in Agent Zero.
2. Open the **A0 Voqualizer Settings** modal — this is the standard A0 plugin
   Settings modal, populated from `webui/config.html` (behavior toggles,
   default providers, protocol defaults, runtime limits).
3. Click **Open Providers editor** to load the polished provider editor at
   `webui/providers.html` for ASR/TTS provider CRUD and **Test provider**.
4. Click **Open tester** to launch `webui/tester.html`.
5. In the tester, click **Connect**, then **Start microphone**.

The tester captures PCM16/16k microphone audio through `audio-worklet.js`, frames
it with the A2 4-byte header in `tester-store.js`, sends
`voqualizer_audio_chunk` with the issued `bearer_token`, renders ASR/agent
responses, plays streamed PCM16 TTS, and shows latency/frame diagnostics.

## WebSocket protocol

Client → server:

- `voqualizer_init` — handshake, provider/codec negotiation, session binding.
- `voqualizer_audio_chunk` — A2-framed binary audio plus per-session token.
- `voqualizer_user_text` — direct text/TTS smoke path.
- `voqualizer_control` — `mute`, `unmute`, `barge_in`, `end_session`, `resume`.
- `voqualizer_ping` — heartbeat.

Server → client:

- `voqualizer_ready` — capabilities, negotiated providers/codecs, `session_id`,
  and per-session `bearer_token`.
- `voqualizer_asr_partial` / `voqualizer_asr_final` — streaming transcript.
- `voqualizer_agent_delta` / `voqualizer_agent_response_final` — agent output.
- `voqualizer_tts_chunk` / `voqualizer_tts_done` — streamed TTS audio.
- `voqualizer_error` — stable A8.3 error taxonomy.
- `voqualizer_pong` — heartbeat reply.

### A2 audio frame

`voqualizer_audio_chunk` payloads carry a 4-byte network-order header followed
by codec payload bytes:

| Bytes | Field |
|---|---|
| 0–1 | `uint16 seq` |
| 2–3 | `uint16 ts_ms` |
| 4… | audio payload |

Default portable codec path is `pcm16/16k`. Telephony examples transcode
µ-law/8k or signed-linear PCM16/8k to/from PCM16/16k.

## Configuration and settings panel

- Defaults live in `default_config.yaml`.
- Runtime overlay lives in `config.json` when saved.
- `webui/config.html` is the standard A0 plugin **Settings modal** fragment
  (Alpine `<x-component>` with `x-model` bindings for behavior, default
  providers, protocol defaults, and runtime limits).
- `webui/providers.html` is the polished standalone **Providers editor** for
  ASR/TTS provider CRUD, defaults, and **Test provider** buttons.
- `webui/config-store.js` calls the same-origin admin endpoint with
  `credentials: 'same-origin'` for provider load/save/test.

## Documentation

- `docs/security/review.md` — A8.1 security review.
- `docs/performance/load-test-32-sessions.md` — A8.2 load-test report.
- `docs/protocol/errors.md` — A8.3 stable error taxonomy + telemetry.
- `docs/clients/portability.md` — M7 client portability matrix.
- `examples/README.md` — example-client index.
- `CHANGELOG.md` — v0.1.0 release notes.

## Examples

- `examples/ios-swift/` — iOS Swift reference client.
- `examples/android-kotlin/` — Android Kotlin reference client.
- `examples/twilio-media-streams/` — Node.js Twilio bridge.
- `examples/asterisk-audiofork/` — Python Asterisk AudioSocket bridge.

## Layout

```text
a0_voqualizer/
├── api/                       # WS endpoint + REST admin
├── helpers/                   # auth, codec, frame, jitter, registry, telemetry
├── webui/                     # settings panel, tester, AudioWorklet
├── examples/                  # iOS, Android, Twilio, Asterisk
├── docs/                      # security, performance, protocol, client docs
├── tools/                     # deterministic load harness
└── tests/                     # pytest suites
```

## License

MIT — see `LICENSE`.
