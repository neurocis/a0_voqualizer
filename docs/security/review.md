# A0 Voqualizer Security Review — A8.1

Artifact: **A8.1 — Security review**

PLAN.md acceptance areas:

- CSRF
- auth
- rate limits
- input validation
- codec fuzzing

This review covers the current `a0_voqualizer` implementation through M7. It is
written to be actionable for release hardening while preserving the established
project constraint that normal pytest must not require backend restart, real
credentials, external network, model downloads, live browser, live LLM/provider
calls, platform SDKs, telephony accounts, Node dependency installation, or a live
A0 backend.

## Reviewed surfaces

| Surface | Files / artifacts reviewed | Notes |
|---|---|---|
| WebSocket handler | `api/ws_voqualizer.py` | `voqualizer_init`, `voqualizer_audio_chunk`, `voqualizer_user_text`, `voqualizer_control`, ping, outbound events |
| REST admin | `api/voqualizer_admin.py` | provider/config/capabilities/status/save/test_provider actions |
| Session auth | `helpers/auth.py`, `helpers/session.py`, `helpers/registry.py` | per-session bearer token, resume, tombstones, concurrency limits |
| Protocol framing | `helpers/frame.py` | A2 4-byte header parser/writer |
| Codecs | `helpers/codec.py` | PCM16, G.711 µ-law/A-law, Opus shell-out, resampling |
| ASR/TTS providers | `helpers/asr/*`, `helpers/tts/*` | provider adapters, no secret/network requirement in tests |
| WebUI | `webui/*` | browser tester, config UI, diagnostics overlay |
| Examples | `examples/*`, `docs/clients/portability.md` | iOS, Android, Twilio, Asterisk reference clients |

## Executive summary

Current security posture is acceptable for the M8.1 review baseline:

- Framework-level A0 authentication/CSRF protects the Socket.IO handler and REST
  admin endpoint.
- Voqualizer adds A5.5 per-session bearer-token authorization for session-bound
  WebSocket operations.
- Session concurrency, queue backpressure, max session age, and provider/config
  schema validation provide baseline rate/abuse resistance.
- Protocol inputs are best-effort validated and recoverable failures are returned
  as stable `voqualizer_error` / `WsResult.error` payloads rather than uncaught
  exceptions.
- Codec helpers reject malformed PCM16 alignment, unsupported sample rates, bad
  codec strings, short frames, and unsupported codec names.
- Deterministic fuzz-style tests were added for codec/frame/protocol malformed
  inputs without requiring external fuzzing engines or runtime services.

Remaining recommendations are tracked below and can be scheduled for later M8
artifacts where appropriate.

## CSRF review

### Current controls

- `VoqualizerAdmin.requires_auth()` returns `True`, so the A0 REST admin endpoint
  is behind framework authentication/CSRF handling.
- `WsVoqualizer` is a framework WebSocket handler selected by Socket.IO handler
  auth (`plugins/a0_voqualizer/ws_voqualizer`). The project’s A1.4 live evidence
  confirmed the A0 login + CSRF cookie flow for Socket.IO.
- Browser WebUI examples rely on same-origin A0 pages:
  - `webui/config-store.js` uses `credentials: 'same-origin'` for admin REST.
  - `webui/tester-store.js` relies on the A0 page-provided authenticated
    Socket.IO client.

### Review result

No plugin-local CSRF bypass was found. The plugin does not expose an unauthenticated
standalone HTTP server, and examples are transport-injected/reference-only.

### Recommendations

- Keep admin actions on same-origin authenticated A0 APIs.
- If future static hosting moves outside the A0 origin, require explicit CSRF
  token plumbing rather than relying on ambient cookies.

## Auth review

### Current controls

- `helpers/auth.py` issues opaque per-session bearer tokens with
  `secrets.token_urlsafe(32)`.
- Tokens are stored in `BridgeSession.metadata` under
  `SESSION_TOKEN_METADATA_KEY`.
- `verify_session_bearer_token()` uses `secrets.compare_digest()`.
- Session-bound operations verify the token:
  - `voqualizer_audio_chunk`
  - `voqualizer_user_text`
  - `voqualizer_control`
- Accepted A5.5 tests cover:
  - token issuance from `voqualizer_ready`
  - token reuse on resume
  - rejection of wrong/missing tokens
  - cross-session token mismatch rejection
- M6/M7 browser/mobile/telephony clients document and attach the token to
  session-bound operations.

### Review result

Per-session authorization is correctly layered on top of framework-level A0 auth.
The main residual risk is accidental logging or UI exposure of bearer tokens.
The accepted WebUI displays only token status (`issued` / `not issued`), not the
raw token.

### Recommendations

- Continue redacting bearer tokens in logs, diagnostics, browser UI, and examples.
- Keep token scope per `BridgeSession`; do not allow global/plugin-level tokens to
  operate on voice sessions.
- Consider token rotation on long-lived sessions if sessions exceed the default
  `limits.max_session_seconds` in future deployments.

## Rate-limit and resource-control review

### Current controls

| Control | Current implementation |
|---|---|
| Concurrent sessions | `BridgeRegistry` enforces `limits.max_concurrent_sessions` |
| Session lifetime/idle GC | `limits.max_session_seconds` and registry GC semantics |
| Audio queue backpressure | `BridgeSession` bounded queue with oldest-dropped policy and metrics; configured by `limits.audio_queue_max_frames` / `audio_queue_max_frames` |
| Configured chunk limit | `limits.max_audio_chunk_kb` exists in `default_config.yaml` / schema |
| Text size limit | `limits.max_text_chunk_chars` exists in config/schema |
| Provider catalog validation | `voqualizer_init` validates provider names against config |
| Codec negotiation | `voqualizer_init` validates input/output codec against configured lists |

### Review result

The project has solid structural resource controls. Two configured limits are
identified for additional enforcement hardening:

- `limits.max_audio_chunk_kb` should be enforced directly in
  `voqualizer_audio_chunk` before decode/transcode work.
- `limits.max_text_chunk_chars` should be enforced directly in
  `voqualizer_user_text` before TTS/provider work.

Current tests and handlers validate malformed/empty audio and text, but direct
max-size rejection should be added in a follow-up hardening patch if not already
covered by framework request-size limits.

### Recommendations

- Add direct WS payload-size checks for audio frames and text requests.
- Consider per-session event-rate counters for `voqualizer_audio_chunk`,
  `voqualizer_user_text`, and `voqualizer_control` if exposed to untrusted
  clients beyond authenticated A0 users.
- Continue keeping ASR/TTS provider tests offline and mocked in normal pytest.

## Input-validation review

### Current controls

- `voqualizer_init` validates:
  - provider names
  - requested input/output codecs
  - non-empty `session_id`
  - registry capacity
- `voqualizer_audio_chunk` validates:
  - active session exists
  - bearer token is valid
  - frame payload can be extracted
  - A2 frame header decodes
  - negotiated codec converts to PCM16/16k
  - ASR provider errors are converted into recoverable protocol errors
- `voqualizer_user_text` validates:
  - active session exists
  - payload is an object/mapping
  - bearer token is valid
  - text is a non-empty string
- `voqualizer_control` validates:
  - action is non-empty
  - active session exists
  - bearer token is valid
  - action is one of known controls
- `api/voqualizer_admin.py` validates save overlays through schema-aware
  `registry.save_overlay()`.
- `helpers/frame.py` validates exact 4-byte headers, minimum frame size, and
  uint16 metadata bounds.
- `helpers/codec.py` validates codec strings, sample rates, PCM16 alignment, and
  unsupported codec names.

### Review result

Input validation is defensive and test-covered for common malformed payloads.
Additional direct max-size checks are recommended under rate limits.

### Recommendations

- Enforce configured max audio/text sizes at the WebSocket handler boundary.
- Keep unknown event handling non-throwing and recoverable.
- Keep provider-adapter failures converted to `voqualizer_error` rather than raw
  tracebacks.

## Codec fuzzing review

### Current controls

Existing codec/frame tests already cover:

- exact A2 4-byte header shape
- network byte order
- malformed and short frames
- unsupported sample rates
- PCM16 alignment errors
- codec round-trips for PCM16, µ-law, A-law, Opus where available
- empty payload behavior

A8.1 adds deterministic malformed-input fuzz coverage in
`tests/test_security_codec_fuzz.py`:

- random short/long byte strings against `decode_frame()`
- random invalid metadata against `pack_header()`
- random malformed codec strings against `parse_codec()`
- odd-length PCM16 rejection
- random invalid codec names against `convert_codec_to_pcm16()`
- bounded random bytes through G.711 µ-law/A-law decoders without external tools

### Review result

The deterministic fuzz-style tests provide useful regression coverage while
preserving normal pytest constraints. They are not a replacement for long-running
coverage-guided fuzzing, but they establish the expected non-crash behavior for
malformed codec/protocol inputs.

### Recommendations

- Consider adding optional offline fuzzing with Hypothesis or Atheris in a
  non-default test target if project policy later allows extra dependencies.
- Keep normal pytest deterministic and fast.

## Findings and recommendations

| ID | Area | Severity | Status | Recommendation |
|---|---|---:|---|---|
| A8.1-F1 | Auth | Low | Controlled | Continue redacting bearer tokens from logs/UI/examples |
| A8.1-F2 | Rate limits | Medium | Follow-up recommended | Enforce `limits.max_audio_chunk_kb` in `voqualizer_audio_chunk` |
| A8.1-F3 | Rate limits | Medium | Follow-up recommended | Enforce `limits.max_text_chunk_chars` in `voqualizer_user_text` |
| A8.1-F4 | Codec fuzzing | Low | Added deterministic tests | Optional deeper fuzzing can be non-default later |
| A8.1-F5 | CSRF | Low | Controlled by framework | Keep admin/WebUI same-origin or add explicit CSRF token plumbing |

## Acceptance checklist

- [x] CSRF reviewed.
- [x] Auth reviewed, including A5.5 per-session bearer-token semantics.
- [x] Rate limits and resource controls reviewed.
- [x] Input validation reviewed.
- [x] Codec fuzzing covered with deterministic pytest.
- [x] No backend restart required.
- [x] No real credentials, external network, model downloads, live browser,
      live LLM/provider calls, platform SDKs, telephony services, Node install,
      Asterisk install, or live A0 backend required for normal pytest.
