# A0 Voqualizer Twilio Media Streams bridge

Reference Node.js bridge for Twilio Media Streams and the `a0_voqualizer`
protocol.

A7.3 acceptance target:

- Receive Twilio Media Streams WebSocket messages containing μ-law (`audio/x-mulaw`) 8 kHz audio.
- Decode μ-law 8 kHz to PCM16.
- Resample PCM16 8 kHz to PCM16 16 kHz.
- Frame PCM16/16k audio with the A2 4-byte header:
  - `uint16 seq` in network byte order
  - `uint16 tsMs` in network byte order
  - PCM16 payload
- Forward framed audio to Voqualizer as `voqualizer_audio_chunk` with the per-session `bearer_token`.
- Receive streamed `voqualizer_tts_chunk` PCM16/16k audio.
- Resample PCM16 16 kHz to PCM16 8 kHz.
- Encode μ-law 8 kHz and send Twilio `media` messages back to the call.

## Files

- `bridge.js` — reference bridge implementation and pure codec/resampling helpers.
- `package.json` — optional runtime dependencies for a real deployment.

## Runtime integration sketch

This example keeps the core bridge logic dependency-light and testable. For a real
deployment, install the optional runtime dependencies and wire the transport adapters:

```bash
cd examples/twilio-media-streams
npm install
A0_BASE_URL=https://a0.example \
A0_SESSION_COOKIE='...' \
node bridge.js
```

The bridge needs two WebSocket sides:

1. Twilio Media Streams inbound/outbound WebSocket.
2. A0 Socket.IO connection using handler auth:

```js
{ handlers: ['plugins/a0_voqualizer/ws_voqualizer'] }
```

After `voqualizer_init`, store `bearer_token` from `voqualizer_ready` and attach it
to all session-bound Voqualizer operations.

## Security/auth reminder

A0 HTTP/session/CSRF authenticates the Socket.IO connection. Voqualizer then issues
a session-scoped `bearer_token`; do not forward Twilio audio or controls without it.

## Codec note

Twilio Media Streams uses base64 μ-law at 8 kHz. Voqualizer's default streaming
input/output path is PCM16 at 16 kHz. This bridge includes deterministic pure-JS
μ-law and linear interpolation resampling helpers so the protocol conversion can be
reviewed and tested without ffmpeg, network, Twilio, or a live A0 backend.
