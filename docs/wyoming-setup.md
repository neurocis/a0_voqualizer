# Voqualizer Wyoming setup guide

The Wyoming rewrite exposes canonical Wyoming TCP interfaces as the authoritative interface. Browser and DOM clients use the Socket.IO Wyoming bridge as secondary Wyoming clients.

Legacy `api/ws_voqualizer.py`, legacy standalone UI, and legacy DOM assets remain in-tree for reference only.

## 1. Create a concrete interface config

Use the CLI initializer:

```bash
cd /a0/usr/plugins/a0_voqualizer
python3 tools/wyoming_init_config.py \
  --ctxid REAL_AGENT_ZERO_CTXID \
  --interface hero \
  --name "Hero" \
  --bind-host 127.0.0.1 \
  --bind-port 10701
```

This writes `config/wyoming_interfaces.json` by default.

Safety behavior:

- placeholder ctxIDs such as `REPLACE_WITH_*` are rejected;
- existing configs are preserved unless `--overwrite` is passed;
- each configured interface maps 1:1 to exactly one Agent Zero ctxID.

## 2. Validate config

CLI:

```bash
python3 tools/wyoming_smoke.py --config config/wyoming_interfaces.json
```

Admin API:

```json
{"action":"validate"}
```

POST to:

```text
/api/plugins/a0_voqualizer/wyoming_status
```

## 3. Start runtime

Via admin API:

```json
{"action":"start"}
```

The plugin startup hook also attempts to start the runtime when `config/wyoming_interfaces.json` exists and validates cleanly.

## 4. Smoke test TCP describe/info

```bash
python3 tools/wyoming_smoke.py \
  --config config/wyoming_interfaces.json \
  --interface hero \
  --tcp-describe
```

Expected successful TCP probe shape includes:

```json
{
  "tcp_describe": {
    "ok": true,
    "type": "info"
  }
}
```

## 5. Browser/DOM diagnostics

Standalone Wyoming page exposes:

- `window.voqualizerWyomingDebug()`
- `window.voqualizerWyomingSmoke()`
- `window.voqualizerWyomingValidate()`
- `window.voqualizerWyomingInitConfig()`
- `window.voqualizerWyomingStart()`

DOM main-UI Wyoming extension exposes:

- `window.voqualizerWyomingDomDebug()`
- `window.voqualizerWyomingDomSmoke()`
- `window.voqualizerWyomingDomValidate()`
- `window.voqualizerWyomingDomStart()`

## 6. Browser WebSocket bridge

Browser clients use the shared client:

```text
/plugins/a0_voqualizer/webui/wyoming/wyoming-ws-client.js
```

It speaks Wyoming events through the framework Socket.IO handler using:

- `wyoming_init`
- `wyoming_event`
- `wyoming_payload`
- `wyoming_close`

Client-supplied `ctxid` and `interface_id` are stripped server-side. The configured interface ctxID remains authoritative.

## 7. External Wyoming clients

External devices should connect to the configured TCP host/port, for example:

```text
127.0.0.1:10701
```

Use Wyoming `describe` first and expect an `info` response.


## 8. Live checklist runner

After creating config, run the checklist without TCP first:

```bash
python3 tools/wyoming_live_checklist.py --config config/wyoming_interfaces.json --interface hero
```

After starting the runtime, include TCP describe:

```bash
python3 tools/wyoming_live_checklist.py --config config/wyoming_interfaces.json --interface hero --tcp-describe
```

The checklist reports per-step JSON for config load, enabled interface presence,
real ctxID validation, TCP `describe/info`, provider status availability, and
next recommended actions.

The same checklist is available through the admin endpoint:

```json
{"action":"checklist","interface_id":"hero","tcp_describe":false}
```


## Browser live checklist helpers

The standalone Wyoming page exposes:

```js
await window.voqualizerWyomingChecklist({ tcpDescribe: false })
```

The DOM main UI extension exposes:

```js
await window.voqualizerWyomingDomChecklist({ tcpDescribe: false })
```

Use `tcpDescribe: true` only after the Wyoming runtime has started and the TCP
interface is expected to be listening.


Both the standalone Wyoming page and DOM main UI extension include visible
checklist buttons. They call the same admin `action=checklist` path as the CLI
and debug globals.
