# A0 Voqualizer iOS Swift demo client

Reference iOS client for the `a0_voqualizer` protocol.

This example demonstrates the portable client responsibilities for A7.1:

- Connect to the A0 Socket.IO endpoint with the Voqualizer handler selected:
  - `plugins/a0_voqualizer/ws_voqualizer`
- Send `voqualizer_init`.
- Store the per-session `bearer_token` returned by `voqualizer_ready`.
- Attach that bearer token to all session-bound operations:
  - `voqualizer_audio_chunk`
  - `voqualizer_user_text`
  - `voqualizer_control`
- Capture microphone PCM16 audio at 16 kHz.
- Frame each PCM16 chunk with the A2 4-byte header:
  - `uint16 seq` in network byte order
  - `uint16 tsMs` in network byte order
  - PCM16 payload
- Send framed microphone audio using `voqualizer_audio_chunk`.
- Render ASR partial/final and agent delta/final events.
- Play streamed `voqualizer_tts_chunk` PCM16 audio.

## Files

- `VoqualizerClient.swift` — protocol/client reference implementation.
- `ContentView.swift` — minimal SwiftUI demo UI.
- `Info.plist` — microphone permission string.

## Integration notes

This demo intentionally keeps the transport behind the small `VoqualizerSocketTransport`
protocol so it can be wired to the Socket.IO Swift client used by your app without
forcing that dependency into normal pytest or the plugin repository.

A production app can implement `VoqualizerSocketTransport` using `Socket.IO-Client-Swift`:

1. Connect to the A0 base URL.
2. Set Socket.IO auth to include:

   ```swift
   ["handlers": ["plugins/a0_voqualizer/ws_voqualizer"]]
   ```

3. Bridge `emitWithAck(_:_:completion:)` to Socket.IO's ack callback.
4. Forward server events to `VoqualizerClient.handleEvent(_:payload:)`.

## Minimal usage

```swift
let transport = YourSocketIOTransport(baseURL: URL(string: "https://a0.example")!)
let client = VoqualizerClient(transport: transport)
try await client.connect(sessionId: "ios-demo")
try await client.startFullDuplex()
```

The demo is full duplex: microphone capture can stream outbound `voqualizer_audio_chunk` frames while inbound `voqualizer_tts_chunk` audio is scheduled for playback.

## Security/auth reminder

The A0 HTTP/session/CSRF login flow authenticates the Socket.IO connection itself.
Voqualizer then issues a session-scoped `bearer_token` from `voqualizer_ready`.
Do not send audio, text, or control events without this token.
