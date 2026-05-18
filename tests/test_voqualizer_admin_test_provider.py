from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
A0_ROOT = Path("/a0")
for candidate in (ROOT, A0_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

# The admin handler is normally loaded inside the A0 runtime where helpers.api
# exists. Keep this regression test deterministic/offline by providing the
# minimum ApiHandler seam before importing the plugin handler.
helpers_pkg = sys.modules.setdefault("helpers", types.ModuleType("helpers"))
# Preserve package semantics so imports such as `helpers.ws` still resolve from
# the local A0 runtime tree while this test injects only `helpers.api`.
helpers_pkg.__path__ = ["/a0/helpers"]
helpers_api = types.ModuleType("helpers.api")


class ApiHandler:  # pragma: no cover - import seam only
    pass


helpers_api.ApiHandler = ApiHandler
setattr(helpers_pkg, "api", helpers_api)
sys.modules["helpers.api"] = helpers_api

from api.voqualizer_admin import VoqualizerAdmin  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.asr import ASRError, ASRUnavailableError  # noqa: E402
from usr.plugins.a0_voqualizer.helpers.tts import TTSError, TTSUnavailableError  # noqa: E402


class FailingASRProvider:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.closed = False

    async def start(self) -> None:
        return None

    async def transcribe(self, *args: Any, **kwargs: Any) -> Any:
        raise self.exc

    async def close(self) -> None:
        self.closed = True


class FailingTTSProvider:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.started = False
        self.stopped = False

    @property
    def capabilities(self):
        class Caps:
            output_codecs = ("pcm16/16k",)
            sample_rates = (16000,)
        return Caps()

    async def start(self) -> None:
        self.started = True

    async def stream(self, request):
        raise self.exc
        yield  # pragma: no cover

    async def stop(self) -> None:
        self.stopped = True


def _config() -> dict[str, Any]:
    return {
        "asr": {
            "default": "mock-asr",
            "providers": [
                {"name": "mock-asr", "type": "mock", "language": "en", "streaming": True},
                {"name": "unavailable-asr", "type": "mock"},
                {"name": "http-asr", "type": "mock"},
            ],
        },
        "tts": {
            "default": "mock-tts",
            "providers": [
                {"name": "mock-tts", "type": "mock", "voice": "mock", "streaming": True},
                {"name": "unavailable-tts", "type": "mock"},
                {"name": "http-tts", "type": "mock"},
            ],
        },
    }


@pytest.fixture
def admin(monkeypatch):
    from usr.plugins.a0_voqualizer.helpers import registry

    monkeypatch.setattr(registry, "load_config", _config)
    return VoqualizerAdmin()


def test_admin_test_provider_mock_asr_success(admin):
    result = asyncio.run(admin._action_test_provider("asr", "mock-asr"))

    assert result["ok"] is True
    assert result["code"] == "OK"
    assert result["side"] == "asr"
    assert result["name"] == "mock-asr"
    assert result["type"] == "mock"
    assert isinstance(result["latency_ms"], int)
    assert result["transcript_preview"] == "mock transcript"
    assert result["details"]["sample_rate"] == 16000
    assert "NOT_IMPLEMENTED" not in result["message"]


def test_admin_test_provider_mock_tts_success(admin):
    result = asyncio.run(admin._action_test_provider("tts", "mock-tts"))

    assert result["ok"] is True
    assert result["code"] == "OK"
    assert result["side"] == "tts"
    assert result["name"] == "mock-tts"
    assert result["type"] == "mock"
    assert isinstance(result["latency_ms"], int)
    assert result["bytes_returned"] > 0
    assert result["codec"] == "pcm16/16k"
    assert result["sample_rate"] == 16000
    assert result["details"]["chunks_seen"] >= 1


def test_admin_test_provider_missing_asr_config_maps_unavailable(admin, monkeypatch):
    def fake_build(spec: Mapping[str, Any]):
        assert spec["name"] == "unavailable-asr"
        return FailingASRProvider(ASRUnavailableError("ASR dependency or API key missing", details={"api_key_env": "OPENAI_API_KEY"}))

    monkeypatch.setattr(admin, "_build_asr_provider", fake_build)
    result = asyncio.run(admin._action_test_provider("asr", "unavailable-asr"))

    assert result["ok"] is False
    assert result["code"] == "ASR_UNAVAILABLE"
    assert result["side"] == "asr"
    assert result["details"]["api_key_env"] == "OPENAI_API_KEY"


def test_admin_test_provider_missing_tts_config_maps_unavailable(admin, monkeypatch):
    def fake_build(spec: Mapping[str, Any]):
        assert spec["name"] == "unavailable-tts"
        return FailingTTSProvider(TTSUnavailableError("TTS dependency or API key missing", details={"api_key_env": "OPENAI_API_KEY"}))

    monkeypatch.setattr(admin, "_build_tts_provider", fake_build)
    result = asyncio.run(admin._action_test_provider("tts", "unavailable-tts"))

    assert result["ok"] is False
    assert result["code"] == "TTS_UNAVAILABLE"
    assert result["side"] == "tts"
    assert result["details"]["api_key_env"] == "OPENAI_API_KEY"


def test_admin_test_provider_http_errors_preserve_asr_and_tts_taxonomy(admin, monkeypatch):
    def fake_asr(spec: Mapping[str, Any]):
        return FailingASRProvider(ASRError("ASR HTTP 503", code="ASR_HTTP_ERROR", details={"status": 503}))

    def fake_tts(spec: Mapping[str, Any]):
        return FailingTTSProvider(TTSError("TTS HTTP 502", code="TTS_HTTP_ERROR", details={"status": 502}))

    monkeypatch.setattr(admin, "_build_asr_provider", fake_asr)
    asr_result = asyncio.run(admin._action_test_provider("asr", "http-asr"))
    assert asr_result["ok"] is False
    assert asr_result["code"] == "ASR_HTTP_ERROR"
    assert asr_result["details"]["status"] == 503

    monkeypatch.setattr(admin, "_build_tts_provider", fake_tts)
    tts_result = asyncio.run(admin._action_test_provider("tts", "http-tts"))
    assert tts_result["ok"] is False
    assert tts_result["code"] == "TTS_HTTP_ERROR"
    assert tts_result["details"]["status"] == 502


def test_admin_test_provider_unknown_provider_and_side_are_clear(admin):
    missing = asyncio.run(admin._action_test_provider("asr", "does-not-exist"))
    assert missing == {
        "ok": False,
        "code": "PROVIDER_NOT_FOUND",
        "side": "asr",
        "name": "does-not-exist",
        "message": "Unknown ASR provider: does-not-exist",
    }

    bad_side = asyncio.run(admin._action_test_provider("video", "mock-asr"))
    assert bad_side["ok"] is False
    assert bad_side["code"] == "INVALID_SIDE"
    assert bad_side["side"] == "video"
    assert "asr" in bad_side["message"]
    assert "tts" in bad_side["message"]
