"""Tests for a0_voqualizer config schema + loader (A1.2).

Run:
    pytest tests/test_config_schema.py -v
from inside /a0/usr/plugins/a0_voqualizer/.
"""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

# Make the plugin importable when running pytest from the plugin dir or repo root.
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from helpers import registry  # noqa: E402
from helpers.config_schema import (  # noqa: E402
    CONFIG_SCHEMA,
    INPUT_CODECS,
    OUTPUT_CODECS,
    ASR_PROVIDER_TYPES,
    TTS_PROVIDER_TYPES,
)


# Fixtures

@pytest.fixture
def default_config() -> dict:
    """Load the actual default_config.yaml shipped with the plugin."""
    return registry.load_config(apply_overlay=False)


@pytest.fixture
def tmp_paths(tmp_path):
    """Per-test isolated default+overlay paths."""
    return {
        "default": str(tmp_path / "default.yaml"),
        "overlay": str(tmp_path / "config.json"),
    }


def _write_yaml(path: str, data: dict) -> None:
    import yaml
    with open(path, "w") as f:
        yaml.safe_dump(data, f)


def _minimal_valid_config() -> dict:
    """A minimal config that passes schema + semantic checks."""
    return {
        "asr": {
            "providers": [{"name": "mock-asr", "type": "mock"}],
            "default": "mock-asr",
        },
        "tts": {
            "providers": [{"name": "mock-tts", "type": "mock"}],
            "default": "mock-tts",
        },
        "protocol": {
            "input_codecs": ["pcm16/16k"],
            "output_codecs": ["pcm16/16k"],
            "default_input_codec": "pcm16/16k",
            "default_output_codec": "pcm16/16k",
            "heartbeat_interval_seconds": 15,
            "session_resume_window_seconds": 30,
        },
        "limits": {
            "max_session_seconds": 1800,
            "max_audio_chunk_kb": 64,
            "max_text_chunk_chars": 4000,
            "max_concurrent_sessions": 32,
            "audio_queue_max_frames": 256,
        },
        "behavior": {
            "barge_in": True,
            "auto_spawn_context": True,
            "sentence_chunking": True,
        },
    }


# Schema sanity

class TestSchemaSanity:
    def test_codec_lists_nonempty(self):
        assert INPUT_CODECS
        assert OUTPUT_CODECS

    def test_provider_type_lists_nonempty(self):
        assert ASR_PROVIDER_TYPES
        assert TTS_PROVIDER_TYPES

    def test_schema_is_dict(self):
        assert isinstance(CONFIG_SCHEMA, dict)
        assert CONFIG_SCHEMA["type"] == "object"
        assert "asr" in CONFIG_SCHEMA["properties"]
        assert "tts" in CONFIG_SCHEMA["properties"]


# Default config loads + validates

class TestDefaultConfig:
    def test_default_config_loads(self, default_config):
        assert isinstance(default_config, dict)

    def test_default_config_has_all_sections(self, default_config):
        for section in ("asr", "tts", "protocol", "limits", "behavior"):
            assert section in default_config, f"missing section: {section}"

    def test_default_config_default_providers_resolve(self, default_config):
        asr_names = [p["name"] for p in default_config["asr"]["providers"]]
        tts_names = [p["name"] for p in default_config["tts"]["providers"]]
        assert default_config["asr"]["default"] in asr_names
        assert default_config["tts"]["default"] in tts_names

    def test_default_codecs_in_lists(self, default_config):
        proto = default_config["protocol"]
        assert proto["default_input_codec"] in proto["input_codecs"]
        assert proto["default_output_codec"] in proto["output_codecs"]

    def test_validate_default_directly(self, default_config):
        # Should not raise
        registry.validate_config(default_config)


# Schema rejections

class TestSchemaRejections:
    def test_missing_asr_section(self):
        cfg = _minimal_valid_config()
        del cfg["asr"]
        with pytest.raises(registry.ConfigError):
            registry.validate_config(cfg)

    def test_empty_providers_list(self):
        cfg = _minimal_valid_config()
        cfg["asr"]["providers"] = []
        with pytest.raises(registry.ConfigError):
            registry.validate_config(cfg)

    def test_unknown_asr_type(self):
        cfg = _minimal_valid_config()
        cfg["asr"]["providers"][0]["type"] = "telepathy"
        with pytest.raises(registry.ConfigError):
            registry.validate_config(cfg)

    def test_unknown_tts_type(self):
        cfg = _minimal_valid_config()
        cfg["tts"]["providers"][0]["type"] = "smoke-signals"
        with pytest.raises(registry.ConfigError):
            registry.validate_config(cfg)

    def test_unknown_input_codec(self):
        cfg = _minimal_valid_config()
        cfg["protocol"]["input_codecs"] = ["pcm32/192k"]
        cfg["protocol"]["default_input_codec"] = "pcm32/192k"
        with pytest.raises(registry.ConfigError):
            registry.validate_config(cfg)

    def test_invalid_name_pattern(self):
        cfg = _minimal_valid_config()
        cfg["asr"]["providers"][0]["name"] = "has spaces!"
        cfg["asr"]["default"] = "has spaces!"
        with pytest.raises(registry.ConfigError):
            registry.validate_config(cfg)

    def test_limit_out_of_bounds(self):
        cfg = _minimal_valid_config()
        cfg["limits"]["max_session_seconds"] = 0
        with pytest.raises(registry.ConfigError):
            registry.validate_config(cfg)

    def test_behavior_wrong_type(self):
        cfg = _minimal_valid_config()
        cfg["behavior"]["barge_in"] = "yes"
        with pytest.raises(registry.ConfigError):
            registry.validate_config(cfg)


# Semantic rejections

class TestSemanticChecks:
    def test_asr_default_not_in_providers(self):
        cfg = _minimal_valid_config()
        cfg["asr"]["default"] = "nonexistent"
        with pytest.raises(registry.ConfigError) as exc:
            registry.validate_config(cfg)
        assert exc.value.path == "asr/default"

    def test_tts_default_not_in_providers(self):
        cfg = _minimal_valid_config()
        cfg["tts"]["default"] = "nonexistent"
        with pytest.raises(registry.ConfigError) as exc:
            registry.validate_config(cfg)
        assert exc.value.path == "tts/default"

    def test_default_input_codec_not_in_list(self):
        cfg = _minimal_valid_config()
        cfg["protocol"]["input_codecs"] = ["pcm16/16k"]
        cfg["protocol"]["default_input_codec"] = "opus"
        with pytest.raises(registry.ConfigError) as exc:
            registry.validate_config(cfg)
        assert exc.value.path == "protocol/default_input_codec"

    def test_duplicate_asr_provider_names(self):
        cfg = _minimal_valid_config()
        cfg["asr"]["providers"] = [
            {"name": "dup", "type": "mock"},
            {"name": "dup", "type": "mock"},
        ]
        cfg["asr"]["default"] = "dup"
        with pytest.raises(registry.ConfigError):
            registry.validate_config(cfg)


# Loader: defaults + overlay

class TestLoader:
    def test_load_defaults_only(self, tmp_paths):
        base = _minimal_valid_config()
        _write_yaml(tmp_paths["default"], base)
        cfg = registry.load_config(
            default_path=tmp_paths["default"],
            runtime_path=tmp_paths["overlay"],
            apply_overlay=False,
        )
        assert cfg == base

    def test_overlay_replaces_scalar(self, tmp_paths):
        base = _minimal_valid_config()
        _write_yaml(tmp_paths["default"], base)
        overlay = {"limits": {"max_concurrent_sessions": 64}}
        with open(tmp_paths["overlay"], "w") as f:
            json.dump(overlay, f)
        cfg = registry.load_config(
            default_path=tmp_paths["default"],
            runtime_path=tmp_paths["overlay"],
        )
        assert cfg["limits"]["max_concurrent_sessions"] == 64
        # Other limits preserved
        assert cfg["limits"]["max_session_seconds"] == 1800

    def test_overlay_replaces_list_entirely(self, tmp_paths):
        """Lists from overlay replace base lists wholesale (no element merging)."""
        base = _minimal_valid_config()
        _write_yaml(tmp_paths["default"], base)
        overlay = {
            "asr": {
                "providers": [
                    {"name": "only-one", "type": "openai-compatible"},
                ],
                "default": "only-one",
            }
        }
        with open(tmp_paths["overlay"], "w") as f:
            json.dump(overlay, f)
        cfg = registry.load_config(
            default_path=tmp_paths["default"],
            runtime_path=tmp_paths["overlay"],
        )
        assert len(cfg["asr"]["providers"]) == 1
        assert cfg["asr"]["providers"][0]["name"] == "only-one"

    def test_overlay_missing_is_defaults(self, tmp_paths):
        base = _minimal_valid_config()
        _write_yaml(tmp_paths["default"], base)
        # No overlay file written
        cfg = registry.load_config(
            default_path=tmp_paths["default"],
            runtime_path=tmp_paths["overlay"],
        )
        assert cfg == base

    def test_corrupt_overlay_raises(self, tmp_paths):
        base = _minimal_valid_config()
        _write_yaml(tmp_paths["default"], base)
        with open(tmp_paths["overlay"], "w") as f:
            f.write("not json {{{")
        with pytest.raises(registry.ConfigError):
            registry.load_config(
                default_path=tmp_paths["default"],
                runtime_path=tmp_paths["overlay"],
            )

    def test_overlay_breaking_validation_raises(self, tmp_paths):
        base = _minimal_valid_config()
        _write_yaml(tmp_paths["default"], base)
        overlay = {"asr": {"default": "ghost-provider"}}
        with open(tmp_paths["overlay"], "w") as f:
            json.dump(overlay, f)
        with pytest.raises(registry.ConfigError) as exc:
            registry.load_config(
                default_path=tmp_paths["default"],
                runtime_path=tmp_paths["overlay"],
            )
        assert exc.value.path == "asr/default"


# save_overlay roundtrip

class TestSaveOverlay:
    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        # Point registry's defaults at a temp file
        base = _minimal_valid_config()
        d = str(tmp_path / "default.yaml")
        r = str(tmp_path / "config.json")
        _write_yaml(d, base)
        monkeypatch.setattr(registry, "DEFAULT_CONFIG_PATH", d)
        monkeypatch.setattr(registry, "RUNTIME_CONFIG_PATH", r)

        overlay = {"limits": {"max_text_chunk_chars": 8000}}
        registry.save_overlay(overlay)

        assert os.path.exists(r)
        with open(r) as f:
            saved = json.load(f)
        assert saved == overlay

        cfg = registry.load_config()
        assert cfg["limits"]["max_text_chunk_chars"] == 8000

    def test_save_invalid_overlay_rejected(self, tmp_path, monkeypatch):
        base = _minimal_valid_config()
        d = str(tmp_path / "default.yaml")
        r = str(tmp_path / "config.json")
        _write_yaml(d, base)
        monkeypatch.setattr(registry, "DEFAULT_CONFIG_PATH", d)
        monkeypatch.setattr(registry, "RUNTIME_CONFIG_PATH", r)

        bad_overlay = {"behavior": {"barge_in": "definitely"}}
        with pytest.raises(registry.ConfigError):
            registry.save_overlay(bad_overlay)
        assert not os.path.exists(r)


def test_behavior_accepts_asr_final_silence_ms_source_marker():
    from pathlib import Path
    schema = Path('/a0/usr/plugins/a0_voqualizer/helpers/config_schema.py').read_text()
    default = Path('/a0/usr/plugins/a0_voqualizer/default_config.yaml').read_text()
    assert 'asr_final_silence_ms' in schema
    assert 'asr_final_silence_ms: 1000' in default
