# A0 Voqualizer Android Kotlin demo client

Reference Android/Kotlin client for the `a0_voqualizer` protocol.

This example demonstrates the portable Android responsibilities for A7.2:

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
- Run full duplex: outbound microphone capture can continue while inbound TTS audio is played.

## Files

- `app/src/main/java/com/a0/voqualizerdemo/VoqualizerClient.kt` — protocol/client reference implementation.
- `app/src/main/java/com/a0/voqualizerdemo/MainActivity.kt` — minimal Jetpack Compose demo UI.
- `app/src/main/AndroidManifest.xml` — network + microphone permissions.

## Transport integration

The demo keeps Socket.IO behind the small `VoqualizerTransport` interface so normal
plugin pytest does not require Gradle, Android SDK, an emulator, a live backend,
or a network connection.

A production app can implement `VoqualizerTransport` using the Socket.IO Java/Kotlin client:

1. Connect to the A0 base URL.
2. Set Socket.IO auth to include:

   ```kotlin
   mapOf("handlers" to listOf("plugins/a0_voqualizer/ws_voqualizer"))
   ```

3. Bridge `emitWithAck(event, payload)` to the Socket.IO acknowledgement callback.
4. Forward server events to `VoqualizerClient.handleEvent(event, payload)`.

## Minimal usage

```kotlin
val transport = YourSocketIoTransport("https://a0.example")
val client = VoqualizerClient(transport)
client.connect(sessionId = "android-demo")
client.startFullDuplex()
```

## Security/auth reminder

The A0 HTTP/session/CSRF login flow authenticates the Socket.IO connection itself.
Voqualizer then issues a session-scoped `bearer_token` from `voqualizer_ready`.
Do not send audio, text, or control events without this token.
