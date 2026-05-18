# A8.3 Voqualizer error taxonomy and telemetry

Artifact: **A8.3 — Error taxonomy + telemetry**

Acceptance: **Stable `voqualizer_error` codes; logging via A0 standard**

## Stable error-event shape

Asynchronous failures are emitted to clients as `voqualizer_error` events using
this JSON-safe shape:

```json
{
  "event": "voqualizer_error",
  "code": "BAD_AUDIO_CHUNK",
  "message": "human-readable summary",
  "recoverable": true,
  "category": "audio",
  "severity": "warning",
  "session_id": "optional-session-id",
  "details": {}
}
```

Synchronous request/ack failures continue to use A0 `WsResult.error`, but the
`error.code` values are drawn from the same stable taxonomy.

## Stable public codes

| Code | Category | Typical surface | Meaning |
|---|---|---|---|
| `UNKNOWN_EVENT` | protocol | `WsResult.error` | Unknown `voqualizer_*` event. |
| `HANDLER_ERROR` | internal | `WsResult.error` | Unexpected handler exception. |
| `BAD_REQUEST` | validation | `WsResult.error` | Invalid request payload, provider, codec, text, or control action. |
| `AUTH_REQUIRED` | auth | `WsResult.error` | Missing/wrong per-session bearer token. |
| `NO_SESSION` | session | `WsResult.error` | Session-bound operation before init or after removal. |
| `REGISTRY_FULL` | rate_limit | `WsResult.error` | Concurrent session limit reached. |
| `BAD_AUDIO_CHUNK` | audio | `WsResult.error` | Malformed A2 frame or codec conversion failure. |
| `ASR_PROVIDER_UNSUPPORTED` | asr | `WsResult.error` | Unsupported ASR provider type. |
| `ASR_PROVIDER_NOT_FOUND` | asr | `WsResult.error` | Configured ASR provider missing. |
| `ASR_UNAVAILABLE` | asr | `voqualizer_error` / `WsResult.error` | ASR provider unavailable. |
| `ASR_HTTP_ERROR` | asr | `voqualizer_error` / `WsResult.error` | Hosted ASR HTTP error. |
| `ASR_BAD_RESPONSE` | asr | `voqualizer_error` / `WsResult.error` | Hosted ASR malformed response. |
| `BAD_ASR_AUDIO` | asr | `WsResult.error` | ASR adapter rejected audio. |
| `BAD_ASR_CONFIG` | asr | `WsResult.error` | ASR provider config invalid. |
| `TTS_PROVIDER_UNSUPPORTED` | tts | `WsResult.error` | Unsupported TTS provider type. |
| `TTS_PROVIDER_NOT_FOUND` | tts | `WsResult.error` | Configured TTS provider missing. |
| `TTS_UNAVAILABLE` | tts | `voqualizer_error` / `WsResult.error` | TTS provider unavailable. |
| `TTS_UNSUPPORTED_CODEC` | tts | `voqualizer_error` / `WsResult.error` | TTS provider cannot produce requested codec. |
| `TTS_SYNTHESIS_FAILED` | tts | `voqualizer_error` / `WsResult.error` | Local TTS synthesis failed. |
| `TTS_TRANSPORT_ERROR` | tts | `voqualizer_error` / `WsResult.error` | Hosted TTS transport failed. |
| `TTS_HTTP_ERROR` | tts | `voqualizer_error` / `WsResult.error` | Hosted TTS HTTP error. |
| `TTS_BAD_RESPONSE` | tts | `voqualizer_error` / `WsResult.error` | Hosted TTS malformed response. |
| `TTS_CANCELLED` | tts | `voqualizer_tts_done`/future error | TTS cancelled, usually by barge-in. |
| `TTS_FINALIZATION_ERROR` | tts | `voqualizer_error` | Final-response TTS finalization failed. |
| `CONTEXT_BRIDGE_ERROR` | context | `voqualizer_error` | Generic AgentContext bridge failure. |
| `CONTEXT_BRIDGE_UNAVAILABLE` | context | `voqualizer_error` | AgentContext runtime unavailable. |
| `CONTEXT_BRIDGE_BAD_REQUEST` | context | `voqualizer_error` | Context bridge input invalid. |

## A0 standard logging

`helpers/error_taxonomy.py` provides `log_voqualizer_error()` for A0-standard
telemetry. It logs via `helpers.print_style.PrintStyle` with a stable prefix:

```text
voqualizer_error code=BAD_AUDIO_CHUNK category=audio session_id=s1 operation=voqualizer_audio_chunk message=...
```

The log line is deliberately grep-friendly and token-safe:

- includes `code`, `category`, optional `session_id`, optional `operation`, and
  message;
- uses detail keys instead of dumping arbitrary payloads;
- does not log raw audio frames or per-session bearer tokens.

## Client guidance

Clients should branch on `code`, not `message`. `message` remains human-readable
and may change. `category`, `severity`, and `recoverable` are advisory fields for
UI/diagnostics.

## Normal pytest constraints

The taxonomy and telemetry tests are deterministic and require No backend restart,
real credentials, external network, model downloads, live browser, live
LLM/provider calls, platform SDKs, telephony accounts, Node install, Asterisk
install, or live A0 backend.
