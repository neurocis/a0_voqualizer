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


### W18 Wyoming-based standalone page scaffold

Implemented scaffold:

- new `webui/voqualizer-wyoming.html` is a minimal standalone page built on top of the W17 `WyomingWsClient` adapter;
- it lives alongside legacy `webui/voqualizer.html` which remains in-tree as the reference implementation per the breaking-rewrite plan;
- supports text submit, mic capture sample loop, TTS audio playback via Wyoming `audio-*` events, and response text streaming;
- selects the Wyoming interface via `?interface=<id>` query parameter; the interface boundary picks the fixed Agent Zero ctxID server-side.

Next implementation step: W19 will re-point the DOM main-UI ASR/TTS extensions (`extensions/webui/chat-input-box-end/voqualizer-buttons.html` and the parts of `webui/conversation-mode.js` they consume) onto the same `WyomingWsClient` adapter, again without removing the legacy assets.


### W19 DOM main UI Wyoming extension scaffold

Implemented scaffold:

- new `extensions/webui/chat-input-box-end/voqualizer-wyoming-buttons.html` adds mic/speaker buttons to the main A0 chat input row, driven by the W17 `WyomingWsClient` adapter;
- legacy `extensions/webui/chat-input-box-end/voqualizer-buttons.html` remains in-tree for reference per the breaking-rewrite plan; both can coexist;
- Wyoming interface mapping is sourced from a `data-wyoming-interface` attribute (or `?wyoming=` query param fallback), so different chat contexts map 1:1 to different interfaces with fixed ctxID server-side;
- recognized transcripts are fed into the main chat textarea so existing send semantics are reused without coupling to the retired custom websocket protocol;
- TTS playback uses a dedicated AudioContext fed from Wyoming `audio-*` events.

Next implementation step: W20 will bind the W12 A0 provider adapter factories to real plugin ASR/context/TTS providers and wire them into `WyomingVoqualizerPipeline` for live interfaces.


### W20 Live A0 provider binding

Implemented scaffold:

- new `helpers/wyoming_live_providers.py` composes the W12 adapter factories with the plugin config loader and the real A0 ASR/TTS provider helpers;
- `build_live_asr_factory(cfg)` and `build_live_tts_factory(cfg)` resolve the configured default provider (Whisper, OpenAI, OpenAI-compatible, LocalAI, Piper, etc.) and gracefully fall back to the mock providers when construction fails;
- `bind_live_providers_to_runtime(interface, ...)` returns a `WyomingInterfaceRuntime` with a fully wired `WyomingVoqualizerPipeline` installed via the new `install_into(runtime)` helper;
- the default prompt submitter still echoes for safety; the framework `/ws` Wyoming handler can override the submitter with a real Agent Zero context submission callable;
- the W11 dependency bootstrap remains responsible for installing the actual ASR/TTS dependencies; this module never assumes anything beyond the existing helper surface.

Next implementation step: W21 will wire the W14/W16 admin/WS handlers to call `bind_live_providers_to_runtime` for live Wyoming interfaces, then run cross-client interop validation against Home Assistant Wyoming and the `wyoming.net` reference.


### W21 Runtime live binding + status surface

Implemented scaffold:

- `helpers/wyoming_runtime.py` now imports `bind_live_providers_to_runtime` from `helpers/wyoming_live_providers.py`;
- on `start()`, each enabled interface's scaffold runtime is replaced with a pipeline-installed live runtime (ASR/prompt/TTS adapters bound to the configured A0 providers via the W12 factories);
- `api/wyoming_status.py` now surfaces `live_providers` in its status payload via `live_provider_status()`, so the admin endpoint reports the configured provider names/types alongside runtime/interface state;
- legacy `api/ws_voqualizer.py` and the original DOM/standalone web UIs remain in-tree for reference per the breaking-rewrite plan.

Next implementation step: cross-client interop validation against Home Assistant Wyoming integration and the OHF/`wyoming.net` reference clients, plus optional retirement of the legacy assets once the new pipeline is validated end-to-end.


### W22 Interop/smoke validation checklist

Added smoke fixture:

- `config/wyoming_interfaces.smoke.example.json` documents a single local Wyoming interface (`hero-smoke`) bound 1:1 to a placeholder Agent Zero ctxID.

Manual smoke checklist:

1. Copy `config/wyoming_interfaces.smoke.example.json` to `config/wyoming_interfaces.json` and replace `REPLACE_WITH_REAL_CTXID` with a real ctxID.
2. Restart or call the admin `wyoming_status` endpoint with `action=start`.
3. Verify runtime status reports `running=true`, interface id `hero-smoke`, bind port `10701`, and `live_providers`.
4. TCP describe/info: connect with a Wyoming-capable TCP client and send `describe`; expect `info` for `hero-smoke` with fixed ctxID metadata.
5. Socket.IO wyoming_init: browser/DOM clients connect to `/ws` with handler `plugins/a0_voqualizer/ws_wyoming`, emit `wyoming_init {interface_id:"hero-smoke"}`, and receive `info`.
6. text prompt: send Wyoming `voqualizer-text-prompt` and verify assistant start/chunk/final events and authoritative Wyoming `audio-start/audio-chunk/audio-stop` TTS events.
7. ASR path: send `audio-start/audio-chunk/audio-stop`; verify `transcript` and false-positive filtering/dedupe.
8. External interop: validate event framing against Home Assistant Wyoming expectations and the OHF/`wyoming.net` client/reference shapes.

Known caveat:

- The default prompt submitter is still a safe echo until the host Agent Zero context submitter is injected. ASR/TTS provider factories are live-bound, but true agent response text requires the framework submitter hook.


### W23 Smoke diagnostic runner

Implemented:

- `tools/wyoming_smoke.py` validates a Wyoming interface config and prints a JSON diagnostic report;
- report includes configured/enabled interfaces, fixed ctxID bindings, bind host/port, and `live_provider_status()` output;
- optional `--tcp-describe` performs a canonical Wyoming `describe` -> `info` round-trip against a selected enabled interface;
- no retired `voqualizer_*` websocket protocol names are used.

Example:

```bash
cd /a0/usr/plugins/a0_voqualizer
python3 tools/wyoming_smoke.py --config config/wyoming_interfaces.json
python3 tools/wyoming_smoke.py --config config/wyoming_interfaces.json --interface hero-smoke --tcp-describe
```

Next implementation step: add a real Agent Zero context prompt submitter injection so `voqualizer-text-prompt` can produce true assistant responses instead of the current safe echo submitter.


### W24 Agent Zero context prompt submitter hook

Implemented:

- `helpers/wyoming_a0_prompt_submitter.py` bridges Wyoming prompt events into the fixed Agent Zero ctxID;
- tries canonical Agent Zero context shapes defensively;
- `build_agent_context_submitter(allow_echo_fallback=True)` is now the default submitter in `wyoming_live_providers.py`;
- safe echo remains only as fallback when live framework context API is unavailable;
- old `api/ws_voqualizer.py` remains reference-only.


### W25 Agent Zero response streaming submitter

Implemented:

- `helpers/wyoming_a0_prompt_submitter.py` now exposes `stream_to_agent_context(...)`;
- live prompt submission tries Agent Zero streaming method shapes (`stream`, `stream_async`, `communicate_stream`, `message_stream`, `submit_stream`, `ask_stream`);
- chunks flow into existing `WyomingPromptAdapter` response chunk/final handling and then authoritative Wyoming TTS;
- `wyoming_live_providers.py` now defaults to `build_agent_context_submitter(..., stream=True)` and reports `agent_context_streaming_with_echo_fallback`;
- if live streaming is unavailable, the submitter falls back to single final text, then safe echo only if framework access fails.


### W26 browser client diagnostics

Implemented:

- shared `webui/wyoming/wyoming-ws-client.js` now exposes `snapshot()` diagnostics;
- counters include connect attempts, init ACKs, events/payload bytes in/out, last event types, last generation id, and last error;
- standalone Wyoming page exposes `window.voqualizerWyomingDebug()`;
- DOM main UI Wyoming extension exposes `window.voqualizerWyomingDomDebug()`;
- both browser surfaces close the Wyoming client defensively on unload where practical.


### W27 Wyoming browser control cancellation

Implemented:

- `WyomingVoqualizerPipeline` now handles browser `voqualizer-control` actions;
- `cancel`, `cancel_tts`, `barge_in`, `stop`, and `stop_tts` normalize to the interface-scoped Wyoming cancel path;
- unsupported control actions return `error` with `unsupported_control_action`;
- this fixes the W17/W18/W19 `cancelTts()` helper path while preserving the canonical Wyoming event stream.


### W28 browser stale-generation guards

Implemented:

- shared browser client now counts `stale_generation_drops`;
- standalone Wyoming page ignores stale response/audio events using `client.isCurrentGeneration(...)`;
- DOM main UI Wyoming extension ignores stale audio events before playback;
- diagnostics snapshots expose the stale-drop counter for field debugging.


### W29 Wyoming browser interface discovery

Implemented:

- `api/wyoming_ws.py` now supports `action=interfaces` and returns browser-safe interface descriptors;
- descriptors include id/name/enabled/running/bind/capability fields but never expose ctxID;
- standalone Wyoming page has an interface selector backed by `/api/plugins/a0_voqualizer/wyoming_ws`;
- selected interface persists to `localStorage`;
- DOM main UI Wyoming extension uses the same persisted interface fallback when no `data-wyoming-interface` or `?wyoming=` is supplied.


### W30 runtime started flag alignment

Implemented:

- `WyomingVoqualizerRuntime` now mirrors `running` into `_started`;
- `api/ws_wyoming.py` accepts either `runtime.running` or `_started` when allowing `wyoming_init`;
- this fixes a live Socket.IO bridge init gap where the runtime could be started but rejected by the WS handler;
- tests cover the mirrored lifecycle flag and handler source check.


### W31 Wyoming length-field compatibility

Implemented:

- Wyoming encoder continues to emit canonical `payload_length`;
- decoder accepts both `payload_length` and `data_length` as aliases for following binary bytes;
- protocol errors now mention both field names for malformed length metadata;
- this improves interop tolerance with browser/proxy clients while preserving deterministic canonical output.


### W32 shared/admin smoke diagnostics

Implemented:

- added `helpers/wyoming_smoke_diagnostics.py` as shared smoke-report logic;
- CLI `tools/wyoming_smoke.py` now delegates to the shared helper;
- `api/wyoming_status.py` supports `action=smoke` with optional `interface_id`, `tcp_describe`, and `timeout`;
- smoke reports validate interface config, report fixed ctxID bindings, live provider status, and optional TCP `describe`/`info` round-trip.


### W33 WS handler runtime lookup repair

Implemented:

- `api/ws_wyoming.py` now resolves the Wyoming runtime directly through plugin `hooks.get_wyoming_runtime()`;
- removed fragile import of a nonexistent/private `_get_runtime` symbol from `api/wyoming_status.py`;
- strengthened handler tests so this import/lookup regression is caught.


### W34 browser ACK envelope normalization

Implemented:

- shared browser Wyoming client now normalizes framework `WsResult` ACK envelopes;
- supports direct `{ok,data,error}` result items, aggregated `{results:[...]}`, nested `{data:{results:[...]}}`, and direct data payloads;
- `wyoming_init` now reads normalized `info`, preventing browser clients from missing init info when the framework wraps handler returns;
- `sendEvent()` returns normalized ACK data while preserving error propagation.


### W35 browser CSRF handshake alignment

Implemented:

- shared Wyoming browser WS client now fetches a CSRF token before Socket.IO connect;
- uses `/webui/js/api.js#getCsrfToken()` when available, with `/api/csrf_token` as a safe fallback;
- Socket.IO auth now uses an async callback so `csrf_token` and `handlers` are always supplied together;
- standalone and DOM Wyoming clients received cache-bust bumps so hard refresh loads the repaired client.


### W36 browser smoke diagnostics UX

Implemented:

- standalone Wyoming page now has a `smoke` diagnostics button;
- diagnostics call `/api/plugins/a0_voqualizer/wyoming_status` with `action=smoke`;
- page exposes `window.voqualizerWyomingSmoke()` and expands `window.voqualizerWyomingDebug()` to include client snapshot plus rendered diagnostics;
- DOM main-UI Wyoming extension exposes `window.voqualizerWyomingDomSmoke()` and stores the latest smoke report;
- cache-busts bumped for standalone and DOM clients.


### W37 runtime config validation guard

Implemented:

- runtime validates enabled Wyoming interfaces before binding TCP ports;
- placeholder ctxIDs such as `REPLACE_WITH_*`, `PLACEHOLDER`, `TODO`, and `CTXID_HERE` block startup;
- duplicate enabled bind endpoints and duplicate interface ids are reported;
- `hooks.validate_wyoming_config()` exposes validation for admin/browser diagnostics;
- `api/wyoming_status.py` supports `action=validate`;
- status payloads include validation results so browser smoke UX can show config problems before live startup.


### W38 safe config initializer

Implemented:

- `helpers/wyoming_config_init.py` creates a concrete `config/wyoming_interfaces.json` from explicit ctxID/interface input;
- placeholder ctxIDs are rejected before writing;
- existing config files are preserved unless `overwrite=true` is supplied;
- `api/wyoming_status.py` supports `action=init_config` for admin/setup flows;
- the initializer writes one enabled Wyoming interface bound 1:1 to the provided Agent Zero ctxID.


### W40 DOM setup/status diagnostics

Implemented:

- DOM main-UI Wyoming extension now exposes `window.voqualizerWyomingDomValidate()` and `window.voqualizerWyomingDomStart()`;
- connect failures trigger validation so missing config/placeholder ctxID errors can be copied from the browser console;
- DOM debug snapshot now includes last smoke and validation diagnostics;
- DOM client cache-bust updated for setup/status diagnostics.


### W41 standalone setup/status controls

Implemented:

- standalone Wyoming page exposes setup controls for ctxID/interface initialization;
- setup calls `wyoming_status` actions `init_config`, `validate`, and `start`;
- connect failures trigger validation display so placeholder/missing config errors are visible in-browser;
- debug globals now include `window.voqualizerWyomingInitConfig()`, `window.voqualizerWyomingValidate()`, and `window.voqualizerWyomingStart()`.


### W42 CLI config initializer

Implemented:

- `tools/wyoming_init_config.py` provides a browser-independent setup path for external Wyoming clients/admins;
- requires explicit `--ctxid` and writes one enabled Wyoming interface by default;
- refuses placeholder ctxIDs and existing configs unless `--overwrite` is passed;
- emits JSON reports with `ok`, `config_path`, interface id, ctxID, and bind endpoint.

Example:

```bash
python3 tools/wyoming_init_config.py --ctxid REAL_CTXID --interface hero --bind-host 127.0.0.1 --bind-port 10701
python3 tools/wyoming_smoke.py --config config/wyoming_interfaces.json --interface hero --tcp-describe
```


### W43 setup guide and project checkpoint

Implemented:

- `docs/wyoming-setup.md` documents CLI setup, admin validation/start actions, TCP smoke diagnostics, browser/DOM debug globals, and external Wyoming client entry points;
- project checkpoint files updated under `/a0/usr/projects/a0-voqualizer/`;
- documentation preserves the 1:1 interface → ctxID rule and legacy-reference-only policy.


### W44 README Wyoming rewrite notice

Implemented:

- README now advertises the breaking Wyoming rewrite status near the top;
- README points operators to `docs/wyoming-setup.md`, CLI config initializer, smoke diagnostics, admin endpoint, browser bridge, standalone page, and DOM extension;
- README explicitly keeps the legacy custom websocket assets as reference-only while the Wyoming protocol is authoritative.


### W45 live checklist runner

Implemented:

- `tools/wyoming_live_checklist.py` summarizes local live-validation steps as JSON;
- checks config load, enabled interfaces, placeholder ctxIDs, optional TCP `describe/info`, and provider status availability;
- setup guide now documents the checklist before/after runtime start.


### W46 admin checklist action

Implemented:

- shared `helpers/wyoming_live_checklist.py` powers both CLI and admin diagnostics;
- `tools/wyoming_live_checklist.py` now delegates to the shared helper;
- `api/wyoming_status.py` supports `action=checklist` with optional `interface_id`, `tcp_describe`, and `timeout`;
- setup docs document the admin checklist payload.


### W47 browser checklist diagnostics

Implemented:

- standalone Wyoming page includes a live-checklist setup button;
- standalone debug surface exposes `window.voqualizerWyomingChecklist({tcpDescribe})`;
- DOM main UI extension exposes `window.voqualizerWyomingDomChecklist({tcpDescribe})` and records the last checklist in debug output;
- both use the W46 admin `action=checklist` instead of duplicating validation logic in browser JavaScript.


### W48 visible checklist controls

Implemented:

- standalone Wyoming page now has an always-visible header checklist button in addition to setup-panel diagnostics;
- DOM main UI Wyoming extension now has a visible checklist button;
- both controls call the W46 shared admin checklist path and retain debug globals.


### W49 readiness snapshot

Implemented:

- shared `helpers/wyoming_readiness.py` combines runtime status, validation, checklist, TCP probe status, and live provider status;
- admin `wyoming_status` supports `action=readiness`;
- standalone and DOM Wyoming browser surfaces expose readiness debug helpers and use readiness for visible checklist status.


### W50 checkpoint status update

Implemented:

- refreshed `/a0/usr/projects/a0-voqualizer/STATUS.md` with current W0-W49 status, diagnostics surfaces, and next live-validation phase;
- refreshed `/a0/usr/projects/a0-voqualizer/PLAN.md` with W51-W55 candidate milestones;
- documented that retained legacy assets remain reference-only while Wyoming TCP/bridge paths are the active rewrite surfaces.


### W51 live smoke capture tool

Implemented:

- `tools/wyoming_live_smoke_capture.py` captures validation, readiness, optional smoke, and optional TCP describe diagnostics in one JSON bundle;
- the tool is browser/auth independent and explicitly reports that framework runtime state must be confirmed via admin `action=readiness`;
- added deterministic tests covering placeholder detection and CLI output.


### Side quest: DOM-only ASR/TTS integration toggle

Implemented:

- `default_config.yaml` adds `wyoming.dom_integration.enabled: true`;
- `helpers/wyoming_dom_settings.py` resolves the toggle from `config.json` then `default_config.yaml` with a safe `True` fallback;
- admin `wyoming_status` supports `action="dom_integration"` for read and `{"action":"dom_integration","enabled":false}` for write;
- Settings panel (`webui/config.html`) exposes a "Wyoming DOM integration" section with an x-model bound checkbox;
- DOM main UI Wyoming extension (`extensions/webui/chat-input-box-end/voqualizer-wyoming-buttons.html`) checks the toggle in `init()` and hides itself + reports `wyoming: DOM ASR/TTS disabled` when off;
- standalone Wyoming page, Wyoming TCP runtime, providers, admin diagnostics, and legacy reference assets remain unaffected.


### Side quest repair: Settings Save path for DOM-only toggle

Issue observed: the DOM-only ASR/TTS toggle appeared in setup, but saving from
the standard A0 plugin Settings modal did not persist reliably.

Root cause: `hooks.py` was loaded by A0 via `helpers.modules.import_module()` /
`spec_from_file_location()`, so it had no package context. The Wyoming lifecycle
rewrite had introduced a relative import (`from .helpers.wyoming_runtime ...`) at
module import time. That import failure happened during `call_plugin_hook()` and
blocked the standard `get_plugin_config` / `save_plugin_config` path used by the
Settings modal.

Repair: `hooks.py` now registers the plugin-local `helpers/` directory as a
unique package named `a0_voqualizer_helpers` and imports
`a0_voqualizer_helpers.wyoming_runtime`. This avoids both the missing package
context problem and the framework top-level `helpers` namespace collision.

Validation: a live A0 framework probe confirmed the modal-equivalent flow:
load config → set `wyoming.dom_integration.enabled=false` → save config → reload
config → helper status sees `enabled=false`. The local config was restored to
`enabled=true` after the probe.


### Side quest follow-up: hide legacy DOM buttons too

Follow-up after the DOM-only toggle landed:

- Agent Zero auto-loads every file under
  `extensions/webui/chat-input-box-end/`, so both `voqualizer-buttons.html`
  (legacy custom protocol, kept for reference) AND
  `voqualizer-wyoming-buttons.html` (new Wyoming) render side by side.
- The initial side-quest toggle hid only the new Wyoming buttons, so the legacy
  buttons remained visible after the user disabled DOM ASR/TTS.
- Repair: legacy `voqualizer-buttons.html` now also queries the
  `wyoming_status` `dom_integration` admin action, hides itself, and skips its
  Alpine `init()` when `enabled` is `false`.
- Scope unchanged: standalone Voqualizer page, Wyoming TCP runtime, providers,
  and retained legacy reference files remain available; only the DOM main UI
  ASR/TTS surface is disabled when the toggle is off.


### Side quest follow-up 2: restore native buttons and remove DOM artifact

After the DOM-only toggle hid both Voqualizer DOM extensions:

- A0's native mic/speaker icons did not return because the legacy extension's
  CSS always hid `#microphone-button`, `#speaker-button`, etc., regardless of
  toggle state.
- An empty placeholder element remained below the chat input row because the
  legacy host element was only set to `display:none` instead of being removed,
  so its un-moved x-move-after host left a visible gap.

Repair:

- Native-button-hiding CSS is now gated on a `body.voqualizer-dom-active` class;
  the class is only set when the DOM ASR/TTS integration is actually enabled.
- When the toggle is OFF, both legacy and Wyoming host elements call
  `$el.remove()` so no empty extension placeholder remains in the layout.


### Side quest follow-up 3: remove extension wrapper artifact

The remaining artifact below the prompt input came from the framework extension
wrapper, not the visible Voqualizer button div. Plugin HTML is wrapped in an
`x-component` inside the `x-extension` slot; removing only the inner div left the
wrapper/style/script nodes behind, so the `chat-input-box-end` extension slot was
still non-empty and could occupy layout.

Repair:

- disabled legacy and Wyoming DOM paths now remove the closest `x-component`
  wrapper, falling back to the root element if needed;
- added defensive CSS to collapse any disabled wrapper that survives a cache or
  framework timing race.
