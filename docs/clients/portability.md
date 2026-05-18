# A0 Voqualizer protocol portability matrix

This document summarizes how the `a0_voqualizer` protocol maps across the
reference clients and bridges delivered in M7.

## Scope

M7 proves that the Voqualizer wire protocol is portable beyond the bundled
browser tester:

| Artifact | Client / bridge | Path | Status |
|---|---|---|---|
| A7.1 | iOS Swift demo client | `examples/ios-swift/` | implemented |
| A7.2 | Android Kotlin demo client | `examples/android-kotlin/` | implemented |
| A7.3 | Twilio Media Streams bridge | `examples/twilio-media-streams/` | implemented |
| A7.4 | Asterisk audio-fork bridge | `examples/asterisk-audiofork/` | implemented |

Normal pytest for these examples is source-level only. It does not require iOS or
Android simulators, Xcode, Android SDK, Gradle, Twilio, Asterisk, SIP endpoints,
Node dependency installation, real credentials, network, a live A0 backend, live
LLM calls, or live ASR/TTS providers.

## Common protocol contract

All clients use the final `voqualizer_*` event names and the same session model:

1. Connect to the A0 Socket.IO endpoint with handler auth selecting:
   `plugins/a0_voqualizer/ws_voqualizer`.
2. Send `voqualizer_init`.
3. Store the per-session `bearer_token` from `voqualizer_ready`.
4. Attach `bearer_token` to session-bound operations:
   - `voqualizer_audio_chunk`
   - `voqualizer_user_text`
   - `voqualizer_control`
5. Encode outbound audio frames with the A2 4-byte frame header:
   - `uint16 seq` in network byte order
   - `uint16 tsMs` in network byte order
   - codec payload after the header
6. Handle the core response stream:
   - `voqualizer_asr_partial`
   - `voqualizer_asr_final`
   - `voqualizer_agent_delta`
   - `voqualizer_agent_response_final`
   - `voqualizer_tts_chunk`
   - `voqualizer_tts_done`
   - `voqualizer_error`

## Portability matrix

| Capability | Browser WebUI | iOS Swift | Android Kotlin | Twilio Media Streams | Asterisk audio-fork |
|---|---|---|---|---|---|
| Transport abstraction | A0 page `window.io` Socket.IO | `VoqualizerSocketTransport` | `VoqualizerTransport` | injected `voqualizerTransport` + Twilio socket | `VoqualizerTransport` + `AsteriskAudioSink` |
| Handler auth | `plugins/a0_voqualizer/ws_voqualizer` | same | same | same | same |
| Session init | `voqualizer_init` | `voqualizer_init` | `voqualizer_init` | `voqualizer_init` | `voqualizer_init` |
| Bearer-token enforcement | stores `bearer_token`; sends on audio/text/control | same | same | same | same |
| Input audio source | browser `AudioWorklet` mic | `AVAudioEngine` mic | `AudioRecord` mic | Twilio `media.payload` µ-law/8k | Asterisk AudioSocket signed-linear/8k |
| Input conversion | Float32 mic → PCM16/16k | downmix/resample → PCM16/16k | mono PCM16/16k capture | µ-law/8k → PCM16/8k → PCM16/16k | slin/8k → PCM16/16k |
| A2 frame header | `DataView.setUint16(..., false)` | manual big-endian bytes | `ByteBuffer.order(BIG_ENDIAN)` | `Buffer.writeUInt16BE` | `struct.pack("!HH", ...)` |
| Audio event | `voqualizer_audio_chunk` | same | same | same | same |
| Text/TTS smoke | `voqualizer_user_text` | `sendText()` | `sendText()` | not primary call path | not primary call path |
| Barge-in/control | `voqualizer_control` | `control("barge_in")` | `control("barge_in")` | stop/control path | `end_session()` control path |
| ASR rendering | partials/finals in tester UI | SwiftUI state | Compose state | not primary bridge UI | not primary bridge UI |
| Agent rendering | deltas/final in tester UI | SwiftUI state | Compose state | not primary bridge UI | not primary bridge UI |
| TTS playback/return | Web Audio PCM16 playback | `AVAudioPlayerNode` PCM16 playback | `AudioTrack` PCM16 playback | PCM16/16k → PCM16/8k → µ-law/8k Twilio `media` | PCM16/16k → PCM16/8k sink audio |
| Diagnostics | latency timers, frame inspector, event log | event log state | event log state | source-testable bridge helpers | source-testable bridge helpers |
| Normal pytest requirement | no browser/backend | no Xcode/simulator | no SDK/Gradle/emulator | no Node install/Twilio/network | no Asterisk/SIP/network |

## Codec and sample-rate notes

The default portable path is PCM16 mono at 16 kHz for Voqualizer audio. Telephony
bridges adapt their native media format to that path:

| Source system | Native media | Voqualizer media | Return media |
|---|---|---|---|
| Browser | Float32 Web Audio mic | PCM16/16k | PCM16/16k Web Audio playback |
| iOS | AVAudioEngine float PCM | PCM16/16k | PCM16/16k AVAudioPlayerNode |
| Android | AudioRecord PCM16 | PCM16/16k | PCM16/16k AudioTrack |
| Twilio | base64 µ-law/8k | PCM16/16k | base64 µ-law/8k |
| Asterisk | signed-linear PCM16/8k sample | PCM16/16k | signed-linear PCM16/8k sink |

The reference telephony bridges use deterministic linear resampling so the sample
code remains dependency-light. Production bridges can swap in higher-quality DSP
while preserving the same protocol framing and bearer-token behavior.

## A5.5 bearer-token checklist

Every portable client must satisfy this checklist:

- `voqualizer_init` is the only operation allowed before the token is issued.
- `voqualizer_ready.bearer_token` is stored per live session.
- `voqualizer_audio_chunk` includes `bearer_token`.
- `voqualizer_user_text` includes `bearer_token` when implemented.
- `voqualizer_control` includes `bearer_token`.
- Tokens are not shared between sessions.
- Reconnect/resume should reuse the token issued for the resumed session when the
  server returns it.

## A2 frame checklist

Every audio-producing client/bridge must produce the same frame shape:

```text
byte 0..1: uint16 sequence number, network byte order
byte 2..3: uint16 timestamp milliseconds, network byte order
byte 4..n: codec payload, normally PCM16/16k for M7 examples
```

Reference implementations:

| Client / bridge | Frame encoder marker |
|---|---|
| Browser | `framePcm16()` in `webui/tester-store.js` |
| iOS | `VoqualizerFrame.encoded()` |
| Android | `VoqualizerFrame.encode()` |
| Twilio | `encodeVoqualizerFrame()` |
| Asterisk | `encode_voqualizer_frame()` |

## Recommended implementation order for new clients

1. Implement Socket.IO handler auth and `voqualizer_init`.
2. Store and attach `bearer_token` for session-bound operations.
3. Implement the A2 frame encoder and send a silent PCM16/16k frame.
4. Add microphone capture and sample-rate conversion.
5. Render ASR partial/final and agent delta/final events.
6. Add streamed TTS playback or telephony return-audio conversion.
7. Add diagnostics: event log, frame inspection, and latency timers.

## Known constraints

- The reference examples intentionally avoid bundling platform package managers or
  generated project files when that would make normal pytest depend on external
  SDKs or dependency downloads.
- The Twilio and Asterisk examples are bridge references, not hosted services.
  Deployment-specific TLS, authentication, call routing, reconnection, logging,
  and production DSP are left to the operator.
- PCM16/16k is the common denominator for M7 portability. Additional codecs can
  be negotiated later without changing the bearer-token or A2 framing contract.
