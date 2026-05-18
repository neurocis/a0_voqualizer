# A0 Voqualizer Asterisk audio-fork bridge

Reference Asterisk bridge for the `a0_voqualizer` protocol.

A7.4 acceptance target:

- Working sample with dialplan/config.
- Fork Asterisk call audio to a bridge process.
- Convert telephony PCM16 8 kHz audio to Voqualizer PCM16 16 kHz.
- Frame PCM16/16k audio with the A2 4-byte header:
  - `uint16 seq` in network byte order
  - `uint16 tsMs` in network byte order
  - PCM16 payload
- Forward framed audio to Voqualizer as `voqualizer_audio_chunk` with the per-session `bearer_token`.
- Receive streamed `voqualizer_tts_chunk` PCM16/16k audio.
- Resample PCM16 16 kHz back to PCM16 8 kHz for Asterisk playback/injection.

## Files

- `bridge.py` — dependency-light Python bridge reference implementation.
- `config/extensions.conf` — sample dialplan using `AudioSocket()` to fork call audio.
- `config/audiosocket.conf` — sample AudioSocket service configuration.
- `config/pjsip.conf` — minimal local endpoint example.

## Runtime integration sketch

Asterisk has several possible media-fork approaches depending on deployment and
version. This sample uses Asterisk AudioSocket because it is simple to reason
about and maps naturally to framed PCM audio:

1. Asterisk dialplan answers the call.
2. `AudioSocket()` connects to the bridge service.
3. The bridge receives 8 kHz signed linear audio frames.
4. The bridge converts/resamples to PCM16/16k and forwards Voqualizer frames.
5. The bridge receives Voqualizer TTS PCM16/16k and converts it to PCM16/8k for
   return audio.

The included `bridge.py` keeps transport concerns injected so normal pytest does
not require Asterisk, a SIP endpoint, a live A0 backend, real credentials, or a
network connection.

## Voqualizer auth reminder

A0 authenticates the Socket.IO connection. Voqualizer then issues a per-session
`bearer_token` from `voqualizer_ready`. The bridge connects to the Voqualizer Socket.IO handler `plugins/a0_voqualizer/ws_voqualizer`.
The bridge must attach this token to all
session-bound operations, including `voqualizer_audio_chunk` and
`voqualizer_control`.

## Sample dialplan

See `config/extensions.conf` for a complete sample context:

```asterisk
[voqualizer-demo]
exten => 7000,1,NoOp(A0 Voqualizer AudioSocket demo)
 same => n,Answer()
 same => n,AudioSocket(${UUID()},127.0.0.1:9092)
 same => n,Hangup()
```

## Production notes

This is a reference bridge. Production deployments should add TLS where
appropriate, robust reconnect/backoff, call authorization, structured logging,
and a higher-quality resampler if required by the target audio path.
