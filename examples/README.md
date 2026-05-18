# A0 Voqualizer examples

This directory contains reference clients and bridges for the shared
`a0_voqualizer` protocol. All examples preserve the same core contract:

1. Connect to `plugins/a0_voqualizer/ws_voqualizer`.
2. Send `voqualizer_init`.
3. Store `bearer_token` from `voqualizer_ready`.
4. Attach that token to `voqualizer_audio_chunk`, `voqualizer_user_text`, and
   `voqualizer_control`.
5. Frame outbound audio with the A2 4-byte header: network-order `uint16 seq`,
   network-order `uint16 ts_ms`, then audio payload.
6. Render ASR partial/final events, agent deltas/final responses, TTS chunks,
   `voqualizer_tts_done`, and stable `voqualizer_error` codes.

## Included examples

| Example | Path | Purpose |
|---|---|---|
| iOS Swift demo client | `ios-swift/` | AVAudioEngine capture/playback and SwiftUI reference UI. |
| Android Kotlin demo client | `android-kotlin/` | AudioRecord/AudioTrack capture/playback and Compose reference UI. |
| Twilio Media Streams bridge | `twilio-media-streams/` | Node.js µ-law/8k ↔ PCM16/16k bridge. |
| Asterisk audio-fork bridge | `asterisk-audiofork/` | Python AudioSocket signed-linear PCM16/8k ↔ PCM16/16k sample with dialplan/config. |

## Browser/WebUI reference

The bundled browser implementation lives in `../webui/` rather than this
examples directory:

- `../webui/audio-worklet.js` captures PCM16/16k and VU meter events.
- `../webui/tester-store.js` connects, frames audio, sends bearer-token-bound
  audio/text/control events, renders protocol events, and plays streamed TTS.
- `../webui/tester.html` provides the tester and diagnostics overlay.
- `../webui/config.html` is the standard A0 plugin **Settings modal** fragment.
- `../webui/providers.html` is the polished standalone **Providers editor** with ASR/TTS CRUD and provider test buttons.

## Testing constraints

Normal pytest validates these examples through deterministic source-level tests.
It does not require iOS or Android simulators, Xcode, Android SDK, Gradle,
Twilio accounts, Asterisk installation, SIP endpoints, Node dependency install,
external network, real credentials, live providers, or a live A0 backend.

See `../docs/clients/portability.md` for the full portability matrix.
