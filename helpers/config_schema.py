"""JSON Schema for a0_voqualizer runtime configuration.

Validates the merged config (default_config.yaml ∪ config.json) at plugin load
and at every config CRUD operation through the REST admin endpoint.

The schema is intentionally permissive about provider type-specific extras
(extra fields allowed) so individual adapters can carry their own configuration
without schema churn. The contract enforced here is:

- `asr` and `tts` each have a `providers` list + a `default` name pointing into
  it.
- Every provider has a non-empty `name` and a recognized `type`.
- Codec lists are non-empty and contain only recognized codec identifiers.
- Numeric limits are positive integers within sane bounds.
"""

from __future__ import annotations

# Recognized provider types (extendable via PR + tests)
ASR_PROVIDER_TYPES = ("whisper", "openai", "openai-compatible", "mock")
TTS_PROVIDER_TYPES = ("piper", "openai", "openai-compatible", "mock")

# Codec identifiers — flat namespace `<format>/<rate>` where rate may be omitted
# for format-only codecs (opus, webm-opus, mp3).
INPUT_CODECS = (
    "pcm16/8k", "pcm16/16k", "pcm16/24k",
    "opus", "webm-opus",
    "mulaw/8k", "alaw/8k",
)
OUTPUT_CODECS = (
    "pcm16/16k", "pcm16/24k",
    "opus", "mp3",
    "mulaw/8k",
)

CONFIG_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "a0_voqualizer config",
    "type": "object",
    "required": ["asr", "tts", "protocol", "limits", "behavior"],
    "additionalProperties": True,
    "properties": {
        "asr": {
            "type": "object",
            "required": ["providers", "default"],
            "properties": {
                "providers": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "type"],
                        "additionalProperties": True,
                        "properties": {
                            "name": {"type": "string", "minLength": 1, "pattern": r"^[a-zA-Z0-9_\-]+$"},
                            "type": {"type": "string", "enum": list(ASR_PROVIDER_TYPES)},
                        },
                    },
                },
                "default": {"type": "string", "minLength": 1},
            },
        },
        "tts": {
            "type": "object",
            "required": ["providers", "default"],
            "properties": {
                "providers": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "type"],
                        "additionalProperties": True,
                        "properties": {
                            "name": {"type": "string", "minLength": 1, "pattern": r"^[a-zA-Z0-9_\-]+$"},
                            "type": {"type": "string", "enum": list(TTS_PROVIDER_TYPES)},
                        },
                    },
                },
                "default": {"type": "string", "minLength": 1},
            },
        },
        "protocol": {
            "type": "object",
            "required": [
                "input_codecs", "output_codecs",
                "default_input_codec", "default_output_codec",
                "heartbeat_interval_seconds", "session_resume_window_seconds",
            ],
            "properties": {
                "input_codecs": {
                    "type": "array", "minItems": 1,
                    "items": {"type": "string", "enum": list(INPUT_CODECS)},
                },
                "output_codecs": {
                    "type": "array", "minItems": 1,
                    "items": {"type": "string", "enum": list(OUTPUT_CODECS)},
                },
                "default_input_codec": {"type": "string", "enum": list(INPUT_CODECS)},
                "default_output_codec": {"type": "string", "enum": list(OUTPUT_CODECS)},
                "heartbeat_interval_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
                "session_resume_window_seconds": {"type": "integer", "minimum": 0, "maximum": 3600},
            },
        },
        "limits": {
            "type": "object",
            "required": [
                "max_session_seconds", "max_audio_chunk_kb",
                "max_text_chunk_chars", "max_concurrent_sessions",
                "audio_queue_max_frames",
            ],
            "properties": {
                "max_session_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
                "max_audio_chunk_kb": {"type": "integer", "minimum": 1, "maximum": 1024},
                "max_text_chunk_chars": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
                "max_concurrent_sessions": {"type": "integer", "minimum": 1, "maximum": 10_000},
                "audio_queue_max_frames": {"type": "integer", "minimum": 1, "maximum": 100_000},
            },
        },
        "behavior": {
            "type": "object",
            "required": ["barge_in", "auto_spawn_context", "sentence_chunking"],
            "properties": {
                "barge_in": {"type": "boolean"},
                "auto_spawn_context": {"type": "boolean"},
                "sentence_chunking": {"type": "boolean"},
                "asr_final_silence_ms": {"type": "number", "minimum": 100, "maximum": 10000},
            },
        },
    },
}


__all__ = [
    "CONFIG_SCHEMA",
    "ASR_PROVIDER_TYPES",
    "TTS_PROVIDER_TYPES",
    "INPUT_CODECS",
    "OUTPUT_CODECS",
]
