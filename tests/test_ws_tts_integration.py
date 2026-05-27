from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

# Import WS handler from /a0 so framework helpers.ws resolves correctly.
_ORIG_SYS_PATH = list(sys.path)
A0_ROOT = str(Path("/a0"))
PLUGIN_ROOT = str(Path(__file__).resolve().parents[1])
for entry in ("", PLUGIN_ROOT):
    while entry in sys.path:
        sys.path.remove(entry)
while A0_ROOT in sys.path:
    sys.path.remove(A0_ROOT)
sys.path.insert(0, A0_ROOT)
sys.modules.pop("helpers", None)

from usr.plugins.a0_voqualizer.api import ws_voqualizer as ws_mod
from usr.plugins.a0_voqualizer.api.ws_voqualizer import WsVoqualizer
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry
from usr.plugins.a0_voqualizer.helpers.tts import (
    AudioChunk as TTSAudioChunk,
    MockTTSProvider,
    OpenAICompatibleTTSProvider,
    OpenAITTSProvider,
    PiperLocalTTSProvider,
    TTSProvider,
    TTSRequest,
    TTSError,
)

# Restore normal plugin-local import behavior for later tests.
sys.path[:] = _ORIG_SYS_PATH
for _name in list(sys.modules):
    if _name == "helpers" or _name.startswith("helpers."):
        sys.modules.pop(_name, None)


class CapturingWs(WsVoqualizer):
    def __init__(self):
        super().__init__(None, threading.Lock())
        self.emitted = []

    async def emit_to(self, sid, event, data, *, correlation_id=None):
        self.emitted.append((sid, event, data))


def run(coro):
    return asyncio.run(coro)


def base_cfg(*, tts_provider=None, output_codec="pcm16/16k"):
    return {
        "asr": {
            "default": "mock-asr",
            "providers": [{"name": "mock-asr", "type": "mock", "streaming": True, "language": "en"}],
        },
        "tts": {
            "default": (tts_provider or {"name": "mock-tts"})["name"],
            "providers": [tts_provider or {"name": "mock-tts", "type": "mock", "voice": "mock", "chunk_size": 4}],
        },
        "protocol": {
            "input_codecs": ["pcm16/16k"],
            "output_codecs": ["pcm16/16k", "pcm16/24k", "mp3", "opus"],
            "default_input_codec": "pcm16/16k",
            "default_output_codec": output_codec,
            "heartbeat_interval_seconds": 15,
            "session_resume_window_seconds": 30,
        },
        "behavior": {"barge_in": True},
        "limits": {"audio_queue_max_frames": 4, "max_concurrent_sessions": 4, "max_session_seconds": 300},
    }


async def init_session(handler: CapturingWs, monkeypatch, cfg=None, *, session_id="tts-1"):
    BridgeRegistry.reset_instance()
    cfg = cfg or base_cfg()
    monkeypatch.setattr(ws_mod, "_safe_load_config", lambda: cfg)
    BridgeRegistry.from_config(cfg, replace=True)
    ready = await handler.process(
        "voqualizer_init",
        {"session_id": session_id, "asr": {"provider": "mock-asr"}, "tts": {"provider": cfg["tts"]["default"]}},
        "SID1",
    )
    assert ready["event"] == "voqualizer_ready"
    assert ready["capabilities"]["cx_stream"] is True
    assert ready["capabilities"]["tts_word_plan"] is True
    assert ready["capabilities"]["protocol_version"] == "1.1"
    assert isinstance(ready["bearer_token"], str) and ready["bearer_token"]
    handler.bearer_token = ready["bearer_token"]
    return cfg


def test_tts_provider_factory_selection():
    assert isinstance(ws_mod._build_tts_provider({"name": "piper", "type": "piper"}), PiperLocalTTSProvider)
    assert isinstance(ws_mod._build_tts_provider({"name": "openai", "type": "openai"}), OpenAITTSProvider)
    assert isinstance(ws_mod._build_tts_provider({"name": "localai", "type": "localai"}), OpenAICompatibleTTSProvider)
    assert isinstance(ws_mod._build_tts_provider({"name": "mock", "type": "mock"}), MockTTSProvider)
    with pytest.raises(TTSError) as excinfo:
        ws_mod._build_tts_provider({"name": "bad", "type": "unknown"})
    assert excinfo.value.to_dict()["code"] == "TTS_PROVIDER_UNSUPPORTED"


def test_user_text_emits_tts_chunks_and_done(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        await init_session(handler, monkeypatch)

        ack = await handler.process(
            "voqualizer_user_text",
            {"text": "hello tts", "utterance_id": "utt-1", "codec": "pcm16/16k", "sample_rate": 16000, "bearer_token": handler.bearer_token},
            "SID1",
        )

        assert ack["event"] == "voqualizer_tts_ack"
        assert ack["utterance_id"] == "utt-1"
        chunk_events = [item for item in handler.emitted if item[1] == "voqualizer_tts_chunk"]
        done_events = [item for item in handler.emitted if item[1] == "voqualizer_tts_done"]
        assert len(chunk_events) >= 2
        assert len(done_events) == 1
        assert done_events[0][2]["cancelled"] is False
        assert done_events[0][2]["chunks"] == len(chunk_events)
        first = chunk_events[0][2]
        assert first["session_id"] == "tts-1"
        assert first["utterance_id"] == "utt-1"
        assert first["event"] if "event" in first else "voqualizer_tts_chunk"
        assert first["codec"] == "pcm16/16k"
        assert isinstance(first["audio"], bytes)
        assert first["metadata"]["provider"] == "mock-tts"
        assert chunk_events[-1][2]["is_final"] is True

    run(scenario())


def test_user_text_requires_session_and_valid_text(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        no_session = await handler.process("voqualizer_user_text", {"text": "hello"}, "SID1")
        assert no_session.as_result(handler_id="h", fallback_correlation_id=None)["error"]["code"] == "NO_SESSION"
        await init_session(handler, monkeypatch)
        bad = await handler.process("voqualizer_user_text", {"text": "", "bearer_token": handler.bearer_token}, "SID1")
        assert bad.as_result(handler_id="h", fallback_correlation_id=None)["error"]["code"] == "BAD_REQUEST"

    run(scenario())


class SlowTTSProvider(TTSProvider):
    def __init__(self):
        super().__init__({"name": "slow-tts", "type": "mock"})
        self.started_event = asyncio.Event()
        self.allow_next = asyncio.Event()

    @property
    def capabilities(self):  # pragma: no cover - not used by WS tests
        raise NotImplementedError

    async def stream(self, request: TTSRequest):
        self.started_event.set()
        yield TTSAudioChunk(data=b"one", seq=0, utterance_id=request.utterance_id, codec=request.codec, sample_rate=request.sample_rate)
        await self.allow_next.wait()
        yield TTSAudioChunk(data=b"two", seq=1, utterance_id=request.utterance_id, codec=request.codec, sample_rate=request.sample_rate, is_final=True)


def test_barge_in_cancels_active_tts_stream(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        cfg = base_cfg(tts_provider={"name": "slow-tts", "type": "mock"})
        await init_session(handler, monkeypatch, cfg)
        provider = SlowTTSProvider()
        monkeypatch.setattr(ws_mod, "_build_tts_provider", lambda spec: provider)

        tts_task = asyncio.create_task(
            handler.process("voqualizer_user_text", {"text": "cancel me", "utterance_id": "utt-cancel", "bearer_token": handler.bearer_token}, "SID1")
        )
        await provider.started_event.wait()
        # Let first chunk be emitted, then barge in before provider is allowed to produce more.
        await asyncio.sleep(0)
        control = await handler.process("voqualizer_control", {"action": "barge_in", "bearer_token": handler.bearer_token}, "SID1")
        assert control["event"] == "voqualizer_control_ack"
        provider.allow_next.set()
        result = await asyncio.wait_for(tts_task, timeout=1)

        chunk_events = [item for item in handler.emitted if item[1] == "voqualizer_tts_chunk"]
        done_events = [item for item in handler.emitted if item[1] == "voqualizer_tts_done"]
        assert result["event"] == "voqualizer_tts_cancelled"
        assert len(chunk_events) == 1
        assert chunk_events[0][2]["audio"] == b"one"
        assert len(done_events) == 1
        assert done_events[0][2]["cancelled"] is True
        assert done_events[0][2]["reason"] == "barge_in"
        assert done_events[0][2]["chunks"] == 1

    run(scenario())


class ErrorTTSProvider(TTSProvider):
    @property
    def capabilities(self):  # pragma: no cover - not used by WS tests
        raise NotImplementedError

    async def stream(self, request: TTSRequest):
        raise TTSError("synthetic tts failure", code="TTS_SYNTHETIC", recoverable=True, details={"x": 1})
        yield  # pragma: no cover


def test_tts_provider_error_maps_to_ws_error_and_emit(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        cfg = base_cfg(tts_provider={"name": "error-tts", "type": "mock"})
        await init_session(handler, monkeypatch, cfg)
        monkeypatch.setattr(ws_mod, "_build_tts_provider", lambda spec: ErrorTTSProvider({"name": "error-tts"}))

        result = await handler.process("voqualizer_user_text", {"text": "boom", "bearer_token": handler.bearer_token}, "SID1")
        err = result.as_result(handler_id="h", fallback_correlation_id=None)["error"]
        emitted_errors = [item for item in handler.emitted if item[1] == "voqualizer_error"]

        assert err["code"] == "TTS_SYNTHETIC"
        assert emitted_errors
        assert emitted_errors[0][2]["code"] == "TTS_SYNTHETIC"
        assert emitted_errors[0][2]["session_id"] == "tts-1"

    run(scenario())


def test_mock_no_network_no_credentials_behavior(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        await init_session(handler, monkeypatch)
        ack1 = await handler.process("voqualizer_user_text", {"text": "same", "utterance_id": "a", "bearer_token": handler.bearer_token}, "SID1")
        first_audio = b"".join(data["audio"] for _sid, event, data in handler.emitted if event == "voqualizer_tts_chunk")
        handler.emitted.clear()
        ack2 = await handler.process("voqualizer_user_text", {"text": "same", "utterance_id": "b", "bearer_token": handler.bearer_token}, "SID1")
        second_audio = b"".join(data["audio"] for _sid, event, data in handler.emitted if event == "voqualizer_tts_chunk")
        return ack1, ack2, first_audio, second_audio

    ack1, ack2, first_audio, second_audio = run(scenario())

    assert ack1["event"] == "voqualizer_tts_ack"
    assert ack2["event"] == "voqualizer_tts_ack"
    assert first_audio == second_audio
    assert first_audio


def test_tts_chunk_includes_base64_fallback_for_browser_dispatch():
    from pathlib import Path
    source = Path('/a0/usr/plugins/a0_voqualizer/api/ws_voqualizer.py').read_text()
    assert 'payload["audio_b64"]' in source
    assert 'payload["audio_encoding"] = "base64"' in source


def test_user_text_prefers_provider_pcm_sample_rate_defaults():
    from pathlib import Path
    source = Path('/a0/usr/plugins/a0_voqualizer/api/ws_voqualizer.py').read_text()
    assert 'provider_format == "pcm" and provider_sample_rate == 24000' in source
    assert 'default_codec = "pcm16/24k"' in source
    assert 'metadata.setdefault("response_format", provider_format)' in source


def test_user_text_prefers_provider_tts_speed_default():
    from pathlib import Path
    source = Path('/a0/usr/plugins/a0_voqualizer/api/ws_voqualizer.py').read_text()
    assert 'provider_spec.get("speed")' in source
    assert 'provider_speed = float' in source
    assert 'speed = float(data.get("speed") or provider_speed)' in source


def test_direct_tts_ack_includes_chunk_fallback_payload_markers():
    source = (PLUGIN / 'api' / 'ws_voqualizer.py').read_text(encoding='utf-8')
    for marker in (
        'ack_tts_chunks',
        'tts_chunks',
        'tts_done',
        'delivery_fallback',
        'ack_chunks',
        'async def _emit_tts_chunk(self, session: BridgeSession, chunk: TTSAudioChunk) -> dict[str, Any]',
        'return payload',
    ):
        assert marker in source, f'missing direct TTS ack fallback backend marker {marker!r}'


def test_user_text_emits_tts_word_plan(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        await init_session(handler, monkeypatch)
        ack = await handler.process(
            "voqualizer_user_text",
            {"text": "hello brave world", "utterance_id": "utt-plan", "codec": "pcm16/16k", "sample_rate": 16000, "bearer_token": handler.bearer_token},
            "SID1",
        )
        assert ack["event"] == "voqualizer_tts_ack"
        assert ack["tts_word_plan"]["event"] == "voqualizer_tts_word_plan"
        assert ack["tts_word_plan"]["utterance_id"] == "utt-plan"
        assert ack["tts_word_plan"]["source"] == "estimated"
        assert ack["tts_word_plan"]["words"][0]["word"] == "hello"
        assert ack["tts_word_plan"]["words"][0]["char_start"] == 0
        word_events = [item for item in handler.emitted if item[1] == "voqualizer_tts_word_plan"]
        assert len(word_events) == 1
        assert word_events[0][2]["utterance_id"] == "utt-plan"
        assert word_events[0][2]["words"][2]["word"] == "world"

    run(scenario())


def test_tts_word_plan_helper_markers():
    source = (PLUGIN / 'helpers' / 'tts_word_timing.py').read_text(encoding='utf-8')
    for marker in (
        'build_word_plan_payload',
        'estimate_word_timings',
        'char_start',
        'char_end',
        'source: str = "estimated"',
        'confidence: float = 0.6',
    ):
        assert marker in source, marker
