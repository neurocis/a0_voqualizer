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


### W4 ASR adapter scaffold

Implemented scaffold:

- `WyomingAsrAdapter` handles `audio-start`, `audio-chunk`, and `audio-stop`;
- audio is scoped to the connected interface/session, whose ctxID is fixed by configuration;
- final transcript emits Wyoming `transcript` events with interface/session/ctxID metadata;
- false-positive ASR artifacts are ignored with a Wyoming-framed diagnostic event;
- duplicate finals are suppressed by utterance id or normalized text hash.

Next implementation step: wire `WyomingAsrAdapter` to the real Voqualizer/A0 ASR
provider and route final transcripts into the Wyoming prompt path.


### W5 prompt/assistant adapter scaffold

Implemented scaffold:

- `WyomingPromptAdapter` accepts `voqualizer-text-prompt` extension events;
- client-supplied context is ignored; interface ctxID is authoritative;
- incoming Wyoming `transcript` events can also enter the prompt path;
- assistant response start/chunk/final extension events are emitted with generation metadata;
- response-tool JSON envelopes are collapsed before final display text is emitted;
- cancel/barge-in advances generation state.

Next implementation step: replace the scaffold provider with real A0 context
submission/streaming and then attach authoritative Wyoming TTS output to response
chunks/finals.


### W6 authoritative TTS adapter scaffold

Implemented scaffold:

- `WyomingTtsAdapter` accepts standard `synthesize` and assistant final events;
- emits only Wyoming `audio-start`, `audio-chunk`, and `audio-stop` events;
- each stream is tagged with interface/session/ctxID/generation metadata;
- chunks use monotonic `chunk_seq`;
- cancel/barge-in advances generation and clears chunk state;
- old ACK/direct/custom websocket TTS paths are not used.

Next implementation step: wire the adapter to the real TTS provider and compose
prompt-response-final -> authoritative TTS events in the interface runtime.


### W7 pipeline composition scaffold

Implemented scaffold:

- `WyomingVoqualizerPipeline` composes ASR, prompt, and authoritative TTS adapters;
- `describe` returns interface info;
- ASR `audio-stop` transcript output can feed the fixed-ctxID prompt path;
- assistant final output can feed authoritative Wyoming TTS audio events;
- cancel/barge-in is scoped to one interface/session and advances generation;
- no old custom websocket protocol participates in this pipeline.

Next implementation step: wire this pipeline into each TCP interface runtime and
replace scaffold providers with real A0 ASR/prompt/TTS providers.


### W8 pipeline runtime wiring scaffold

Implemented scaffold:

- `WyomingInterfaceRuntime` can delegate all events to a composed pipeline;
- `build_wyoming_pipeline_runtime(interface)` creates one pipeline runtime for one interface ctxID;
- `build_wyoming_pipeline_manager(interfaces)` creates one runtime per configured interface;
- generic Wyoming TCP sessions can now be routed through ASR -> prompt -> authoritative TTS composition;
- context remains fixed at the interface boundary.

Next implementation step: expose config loading/startup helpers for enabled
interfaces and replace scaffold providers with real A0 ASR/context/TTS providers.


### W9 runtime bootstrap scaffold

Implemented scaffold:

- `WyomingVoqualizerRuntime` loads enabled Wyoming interfaces;
- every enabled interface receives a composed ASR -> prompt -> TTS pipeline runtime;
- `WyomingTcpInterfaceManager` owns one TCP server per enabled interface;
- runtime status reports configured/enabled interfaces, bind ports, and manager info;
- an example config documents the 1:1 interface-to-ctxID mapping.

Next implementation step: integrate runtime startup with plugin lifecycle/hooks and
replace scaffold providers with real A0 ASR/context/TTS adapters.


### W10 plugin lifecycle scaffold

Implemented scaffold:

- plugin hooks expose Wyoming runtime start/stop/status helpers;
- runtime startup is opt-in and only starts when `config/wyoming_interfaces.json` exists;
- missing config is non-fatal so placeholder ports are not opened accidentally;
- shutdown/uninstall stop Wyoming TCP servers cleanly;
- `api/wyoming_status.py` exposes status/start/stop actions for admin diagnostics.

Next implementation step: wire real A0 ASR/context/TTS providers into the pipeline
and decide the operational config source for interface definitions.


### W11 dependency bootstrap and metadata restoration

Implemented scaffold:

- preserved the original conservative dependency bootstrap inside the new Wyoming lifecycle hooks;
- dependency status remains written to `.dependency_status.json`;
- admin status now includes dependency status and a `bootstrap` action;
- plugin metadata now describes the breaking Wyoming TCP rewrite instead of the retired custom websocket bridge.

Next implementation step: replace scaffold ASR/prompt/TTS providers with real A0 adapters and decide whether old websocket API files should be deleted now or after provider wiring.


### W12 A0 provider adapter scaffold

Implemented scaffold:

- old `api/ws_voqualizer.py` and web UI assets remain in-tree for reference;
- `helpers/wyoming_a0_adapters.py` wraps pluggable A0 ASR/TTS providers and a prompt submitter;
- adapters feed the Wyoming ASR, prompt, and TTS pipeline without reusing old websocket events;
- adapter status reports which provider factories/submitter are configured.

Next implementation step: bind these adapter factories to the real plugin registry/config providers and compose them into `WyomingVoqualizerPipeline` for live interfaces.


### W13 Wyoming-over-WebSocket browser bridge scaffold

Implemented scaffold:

- `WyomingWsBridge` lets browser/mobile WebSocket clients talk to the Wyoming runtime;
- each WS session is bound 1:1 to one configured Wyoming interface and its fixed Agent Zero ctxID;
- client-supplied `ctxid` / `interface_id` are ignored, just like for native Wyoming TCP clients;
- text frames carry the Wyoming event envelope (JSON); optional following binary frame carries the event payload;
- Wyoming replies are sent back as a JSON envelope plus optional binary payload;
- old `api/ws_voqualizer.py` and old web UI assets remain in-tree for reference, but this bridge does not depend on them.

Next implementation step: wire `WyomingWsBridge` into a plugin API endpoint and
begin migrating the standalone Voqualizer web UI to use the bridge as just
another Wyoming client.


### W14 Wyoming WS bridge API endpoint scaffold

Implemented scaffold:

- `api/wyoming_ws.py` exposes the JSON status/diagnostics handler `WyomingWs`;
- a transport-agnostic helper `run_wyoming_ws_bridge_session(interface_id, recv, send)` lets a host WS layer mount a `WyomingWsBridge` session bound to one configured Wyoming interface;
- the handler supports `status` (default), `list`, and `describe` actions for diagnostics;
- if the runtime is not started, all actions return a clear not-started status instead of failing;
- old `api/ws_voqualizer.py` remains for reference and is not used by this endpoint.

Next implementation step: wire the host framework WS layer to call `run_wyoming_ws_bridge_session(...)` for the configured Wyoming WS route, then migrate the standalone Voqualizer web UI to use this endpoint as just another Wyoming client.


### W15 DOM main UI client migration plan

Scope note:

- in addition to the standalone Voqualizer web UI, this plugin also ships DOM main UI ASR/TTS extensions (notably `webui/conversation-mode.js` and the `voqualizer-mic-button` / `voqualizer-speaker-button` UI in extension HTML);
- those DOM extensions currently consume the retired custom Voqualizer WebSocket protocol;
- the breaking Wyoming rewrite will treat the DOM extensions as just another Wyoming WS bridge client, on equal footing with the standalone web UI and any external Wyoming-compatible client/device/app.

Planned DOM extension migration (kept in lockstep with standalone UI migration):

1. Add a small Wyoming WS client adapter under `webui/wyoming/` that exposes:
   - `connect(interfaceId)` to open the bridge socket;
   - `sendEvent(type, data, payload?)` for canonical Wyoming envelopes;
   - `onEvent(type, handler)` dispatcher;
   - generation/utterance tracking;
   - barge-in helpers.
2. Re-implement `conversation-mode.js` ASR/TTS state machine on top of that adapter, replacing the legacy `voqualizer_*` socket protocol.
3. Keep DOM mic/speaker UI markup and UX intact so visual parity (VU, idle/active state, idle bars, etc.) is preserved.
4. Decommission DOM dependencies on:
   - `voqualizer_init`
   - `voqualizer_user_text`
   - `voqualizer_audio_chunk`
   - `voqualizer_tts_chunk`
   - ACK fallback playback;
   - shared-store TTS suppression hacks that worked around the old transport.
5. Maintain old DOM/socket files in-tree for reference but stop depending on them at runtime.
6. Apply the same generation/cancellation rules used by the Wyoming pipeline so barge-in cancels stale generations and prevents duplicate playback.

Acceptance for DOM client migration:

- DOM main UI ASR/TTS extensions only speak Wyoming events to the new bridge endpoint;
- old custom socket protocol is fully unused from DOM code paths;
- visual UX (mic/speaker buttons, VU, idle bars) remains familiar;
- legacy code in `webui/voqualizer.js`, `webui/conversation-mode.js`, and `api/ws_voqualizer.py` remains for reference but is not loaded by the new path.


### W16 Wyoming WsHandler wiring

Implemented scaffold:

- new `api/ws_wyoming.py` `WsWyoming` handler mounts a `WyomingWsBridge` per Socket.IO connection;
- speaks a tiny Wyoming-only protocol surface: `wyoming_init`, `wyoming_event`, `wyoming_payload`, `wyoming_close`;
- binary payloads are carried as base64 in `payload_b64` (single shot) or streamed via `wyoming_payload` chunks paired with a previous `wyoming_event`;
- client-supplied `ctxid` / `interface_id` in event envelopes are stripped by `WyomingWsBridge.handle_text_envelope`;
- works under the existing framework `/ws` namespace with `requires_auth=True` and CSRF auto-following;
- old `api/ws_voqualizer.py` remains in-tree purely for reference.

Next implementation step: add the shared browser-side Wyoming WS client adapter and re-point the standalone Voqualizer web UI plus the DOM main UI ASR/TTS extensions onto it (W17–W19).


### W17 Shared browser-side Wyoming WS client adapter

Implemented scaffold:

- new `webui/wyoming/wyoming-ws-client.js` exposes `WyomingWsClient` / `createWyomingWsClient`;
- connects to A0's `/ws` Socket.IO namespace with handler id `plugins/a0_voqualizer/ws_wyoming`;
- speaks only Wyoming events: `wyoming_init`, `wyoming_event`, `wyoming_payload`, `wyoming_close`;
- helpers cover text prompt submission, audio capture lifecycle (`audio-start`/`audio-chunk`/`audio-stop`), TTS cancel, generation IDs, and `event:<type>` dispatch;
- payload bytes are carried as base64 in `payload_b64` matching the W16 handler contract;
- the adapter is framework-agnostic and reusable from the standalone Voqualizer web UI and from the DOM main UI ASR/TTS extensions.

Next implementation step: W18 will re-point the standalone Voqualizer web UI onto this adapter, and W19 will do the same for `webui/conversation-mode.js` and the DOM mic/speaker extension UI, while keeping legacy assets in-tree for reference.
