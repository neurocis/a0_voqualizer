# Voqualizer Wyoming Migration

Status: W0/W1 build-out started.

## Breaking-change rule

Voqualizer is being rewritten around Wyoming protocol compatibility. The old custom
browser/WebSocket protocol is not a compatibility target and should be retired:

- `voqualizer_init`
- `voqualizer_text_prompt`
- `voqualizer_audio_chunk`
- `voqualizer_tts_chunk`
- ACK/direct/fallback TTS playback paths
- standalone `conversation-mode.js` coupling

## Foundational design decisions

1. Voqualizer exposes real Wyoming-compatible interfaces for any compatible
   client/device/app, not only browsers.
2. Each Wyoming interface maps 1:1 to exactly one A0 `ctxID`.
3. Multiple Wyoming interfaces may be active concurrently.
4. Context selection is an interface/config concern, not a client session concern.
5. Browser/mobile web UI is only one client. If it needs WebSocket transport, that
   bridge must preserve Wyoming event envelopes and must not define the protocol.

## Interface model

Each configured interface has:

```json
{
  "id": "hero",
  "name": "Hero",
  "ctxid": "abc123",
  "enabled": true,
  "bind_host": "0.0.0.0",
  "bind_port": 10701,
  "capabilities": {
    "asr": true,
    "tts": true,
    "assistant_text": true,
    "barge_in": true
  }
}
```

A Wyoming client connects to the interface endpoint and is implicitly bound to
that interface's `ctxID`.

## Event compatibility direction

Use standard Wyoming events where available:

- `describe` / `info` for capability negotiation.
- `audio-start`, `audio-chunk`, `audio-stop` for audio transport.
- `transcript` for ASR result text where compatible.
- `synthesize` for TTS requests where compatible.
- `error` for protocol/provider/runtime errors.

Voqualizer-specific needs that may require documented extension events:

- typed/context prompt submission;
- assistant response start/delta/final;
- active generation/barge-in cancellation;
- current spoken word / word timing.

Extension events must still use Wyoming event framing and metadata conventions.

## Migration milestones

### W0 — Compatibility discovery/spec

Deliverables:

- confirm canonical Wyoming frame format from references;
- standard-vs-extension event matrix;
- 1:1 interface-to-ctxID routing spec;
- generic client compatibility notes;
- browser bridge constraints.

### W1 — Protocol core

Deliverables:

- `helpers/wyoming_protocol.py` event/frame helpers;
- unit tests for event round-trip and malformed input;
- no dependency on old Voqualizer socket event names.

### W2 — Interface manager/server

Deliverables:

- interface config loader;
- one enabled Wyoming listener per interface or compatible binding strategy;
- per-interface session state bound to fixed ctxID;
- `describe`/`info` behavior.

### W3 — ASR over Wyoming

Deliverables:

- inbound Wyoming audio events to ASR provider;
- transcript events out;
- ASR ignore list retained;
- duplicate final suppression per utterance/generation/text hash.

### W4 — Prompt/assistant text over Wyoming

Deliverables:

- typed/ASR prompt extension event if no standard equivalent exists;
- A0 context submission using interface ctxID;
- response start/delta/final events;
- collapsed response-tool headline/body rendering before emitting display text.

### W5 — Authoritative TTS over Wyoming

Deliverables:

- one TTS stream per active generation;
- old ACK/direct/custom pushed TTS removed;
- monotonic chunk sequence;
- strict stale-generation drop;
- barge-in cancels only that interface's current generation.

### W6 — Browser/mobile UI rewrite

Deliverables:

- `webui/voqualizer.js` rewritten as Wyoming client/bridge client;
- interface picker replaces context picker;
- no `conversation-mode.js` import;
- no old `voqualizer_*` events;
- no ACK fallback playback.

### W7 — Interop validation

Deliverables:

- protocol fixture tests;
- Home Assistant/OHF/wyoming.net compatibility checklist;
- manual smoke tests for generic client and browser client.


### W2.5/W3 TCP server binding scaffold

Implemented scaffold:

- `read_event_from_stream` / `write_event_to_stream` for asyncio stream framing;
- `WyomingTcpServer` for one generic Wyoming TCP client connection per interface runtime;
- `WyomingTcpInterfaceManager` for concurrently active interface listeners;
- tests proving `describe` returns interface-bound `info` and closes sessions cleanly.

Next implementation step: replace the temporary prompt echo handler with real A0
context submission and add standard ASR/TTS event adapters.
