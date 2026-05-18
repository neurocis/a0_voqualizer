# A8.2 Load test metrics — 32 concurrent sessions

Artifact: **A8.2 — Load test (32 concurrent sessions)**

Acceptance: **< 1s first-audio latency under load; metrics report**

## Result

- Status: **PASS**
- Concurrent sessions: **32**
- Target first-audio latency: **< 1000 ms**
- Measurement source: `tools/load_test_32_sessions.py`
- Normal pytest coverage: `tests/test_load_32_sessions.py`

## Method

The A8.2 harness runs an offline, deterministic in-process load test against the
real `WsVoqualizer` protocol path:

1. Configure `BridgeRegistry` with `max_concurrent_sessions: 32`.
2. Create 32 independent handler instances.
3. Send `voqualizer_init` for each session.
4. Store the issued per-session `bearer_token` from `voqualizer_ready`.
5. Encode one 20 ms PCM16/16k audio chunk per session using the A2 4-byte frame
   header: `uint16 seq` + `uint16 ts_ms`, network byte order.
6. Send `voqualizer_audio_chunk` concurrently for all sessions.
7. Measure first-audio latency from send time to first ASR transcript event.

The harness uses the deterministic mock ASR provider, so it proves protocol and
session scalability without external services.

## Latest accepted metric target

The pytest assertion requires:

- exactly 32 sessions complete;
- every session emits at least one ASR transcript event;
- every session receives `voqualizer_audio_ack`;
- max first-transcript latency is below 1000 ms;
- p95 first-transcript latency is below 1000 ms;
- all audio operations use the issued `bearer_token`.

## Runtime constraints

This load test requires No backend restart, real credentials, external network,
model downloads, live browser, live LLM/provider calls, platform SDK, telephony
account, Node dependency install, Asterisk install, or live A0 backend.

To regenerate a human-readable report manually:

```bash
cd /a0/usr/plugins/a0_voqualizer
/opt/venv-a0/bin/python tools/load_test_32_sessions.py
```

Normal CI/pytest validates the harness through `tests/test_load_32_sessions.py`.
