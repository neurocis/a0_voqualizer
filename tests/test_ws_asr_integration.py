from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

# Import the WS handler the same way the running framework does: from /a0.
# The plugin has a regular `helpers` package while framework `/a0/helpers` is a
# namespace package, so plugin-root/current-dir entries must be removed or they
# shadow `helpers.ws` during pytest collection from the plugin directory.  After
# importing the handler, restore pytest's path/module state so the rest of the
# plugin tests can continue using plugin-local `helpers.*` imports.
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

from usr.plugins.a0_voqualizer.api.ws_voqualizer import WsVoqualizer
from usr.plugins.a0_voqualizer.helpers.codec import convert_pcm16_to_codec
from usr.plugins.a0_voqualizer.helpers.frame import encode_frame
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry

# Undo the framework-helper import preference for any later-collected tests that
# use the historical plugin-root import style (`from helpers.codec import ...`).
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


async def init_mock_session(handler: CapturingWs, monkeypatch, *, codec="pcm16/16k"):
    BridgeRegistry.reset_instance()
    cfg = {
        "asr": {
            "default": "mock-asr",
            "providers": [
                {
                    "name": "mock-asr",
                    "type": "mock",
                    "streaming": True,
                    "language": "en",
                    "final_text": "hello from mock",
                }
            ],
        },
        "tts": {
            "default": "piper-local",
            "providers": [{"name": "piper-local", "type": "piper"}],
        },
        "protocol": {
            "input_codecs": ["pcm16/8k", "pcm16/16k", "mulaw/8k", "alaw/8k"],
            "output_codecs": ["pcm16/16k"],
            "default_input_codec": codec,
            "default_output_codec": "pcm16/16k",
            "heartbeat_interval_seconds": 15,
            "session_resume_window_seconds": 30,
        },
        "behavior": {"barge_in": True},
        "limits": {"audio_queue_max_frames": 4, "max_concurrent_sessions": 4, "max_session_seconds": 300},
    }
    monkeypatch.setattr("usr.plugins.a0_voqualizer.api.ws_voqualizer._safe_load_config", lambda: cfg)
    BridgeRegistry.from_config(cfg, replace=True)
    ready = await handler.process(
        "voqualizer_init",
        {"session_id": "asr-1", "asr": {"provider": "mock-asr", "codec": codec}},
        "SID1",
    )
    assert ready["event"] == "voqualizer_ready"
    assert isinstance(ready["bearer_token"], str) and ready["bearer_token"]
    handler.bearer_token = ready["bearer_token"]
    return cfg


def test_audio_chunk_emits_partial_and_final_transcript_events(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        await init_mock_session(handler, monkeypatch)
        pcm16 = (b"\x01\x00" * 160)
        frame = encode_frame(7, 1234, pcm16)

        ack = await handler.process("voqualizer_audio_chunk", {"frame": frame, "bearer_token": handler.bearer_token}, "SID1")

        assert ack["event"] == "voqualizer_audio_ack"
        assert ack["seq"] == 7
        assert ack["emitted"] == 2
        assert ack["backpressure"]["audio_frames_enqueued"] == 1
        events = [event for _sid, event, _data in handler.emitted]
        assert events == ["voqualizer_asr_partial", "voqualizer_asr_final"]
        partial = handler.emitted[0][2]
        final = handler.emitted[1][2]
        assert partial["session_id"] == "asr-1"
        assert partial["text"] == "partial 1"
        assert final["text"] == "hello from mock"
        assert final["lang"] == "en"
        assert final["provider"] == "mock-asr"

    run(scenario())


def test_audio_chunk_accepts_dict_payload_and_mulaw_codec(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        await init_mock_session(handler, monkeypatch, codec="mulaw/8k")
        pcm16 = b"\x02\x00" * 160
        mulaw = convert_pcm16_to_codec(pcm16, "mulaw/8k", src_rate=16000)
        frame = encode_frame(1, 20, mulaw)

        ack = await handler.process("voqualizer_audio_chunk", {"frame": frame, "bearer_token": handler.bearer_token}, "SID1")

        assert ack["event"] == "voqualizer_audio_ack"
        assert ack["emitted"] == 2
        assert [event for _sid, event, _data in handler.emitted] == [
            "voqualizer_asr_partial",
            "voqualizer_asr_final",
        ]

    run(scenario())


def test_audio_chunk_requires_session(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        result = await handler.process("voqualizer_audio_chunk", b"bad", "SID1")
        assert result.as_result(handler_id="h", fallback_correlation_id=None)["error"]["code"] == "NO_SESSION"

    run(scenario())


def test_bad_audio_chunk_returns_recoverable_error(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        await init_mock_session(handler, monkeypatch)
        result = await handler.process("voqualizer_audio_chunk", {"frame": b"", "bearer_token": handler.bearer_token}, "SID1")
        err = result.as_result(handler_id="h", fallback_correlation_id=None)["error"]
        assert err["code"] == "BAD_AUDIO_CHUNK"

    run(scenario())


def test_audio_chunk_accepts_socketio_list_payload_shape(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        await init_mock_session(handler, monkeypatch)
        frame = encode_frame(9, 160, b"\x00\x00" * 160)

        ack = await handler.process(
            "voqualizer_audio_chunk",
            {"frame": list(frame), "bearer_token": handler.bearer_token},
            "SID1",
        )

        assert ack["event"] == "voqualizer_audio_ack"
        assert ack["seq"] == 9
        assert ack["emitted"] == 2
        assert [event for _sid, event, _data in handler.emitted] == [
            "voqualizer_asr_partial",
            "voqualizer_asr_final",
        ]

    run(scenario())


def test_audio_chunk_accepts_socketio_buffer_payload_shape(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        await init_mock_session(handler, monkeypatch)
        frame = encode_frame(10, 180, b"\x00\x00" * 160)

        ack = await handler.process(
            "voqualizer_audio_chunk",
            {"frame": {"type": "Buffer", "data": list(frame)}, "bearer_token": handler.bearer_token},
            "SID1",
        )

        assert ack["event"] == "voqualizer_audio_ack"
        assert ack["seq"] == 10
        assert ack["emitted"] == 2

    run(scenario())




def test_batch_asr_utterance_buffer_emits_partial_then_final(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        await init_mock_session(handler, monkeypatch)
        session = handler._registry().get("asr-1")
        session.metadata["asr_partial_interval_ms"] = 1000.0
        session.metadata["asr_final_silence_ms"] = 800.0
        session.metadata["asr_min_speech_ms"] = 500.0

        class BatchProvider:
            name = "batch-asr"
            calls = []

            def capabilities(self):
                from usr.plugins.a0_voqualizer.helpers.asr import ASRCapabilities
                return ASRCapabilities(provider="batch-asr", streaming=False, partials=False, finals=True)

            async def transcribe(self, audio, *, language=None, sample_rate=16000, metadata=None):
                from usr.plugins.a0_voqualizer.helpers.asr import TranscriptResult, TranscriptKind
                meta = dict(metadata or {})
                self.calls.append((list(audio), meta))
                text = "hello partial" if meta.get("utterance_event") == "partial" else "hello final"
                return TranscriptResult(
                    text=text,
                    kind=TranscriptKind.FINAL,
                    language=language or "en",
                    provider="batch-asr",
                    metadata=meta,
                )

        provider = BatchProvider()
        session.metadata["asr_provider_instance"] = provider
        token = handler.bearer_token
        speech20ms = b"\x10\x27" * 320
        silence20ms = b"\x00\x00" * 320

        # 49 speech frames (980ms) are below the 1000ms partial interval.
        for idx in range(49):
            ack = await handler.process(
                "voqualizer_audio_chunk",
                {"frame": encode_frame(idx, idx * 20, speech20ms), "bearer_token": token},
                "SID1",
            )
            assert ack["event"] == "voqualizer_audio_ack"
            assert ack["emitted"] == 0

        # 50th speech frame reaches 1000ms and emits one partial.
        ack = await handler.process(
            "voqualizer_audio_chunk",
            {"frame": encode_frame(49, 980, speech20ms), "bearer_token": token},
            "SID1",
        )
        assert ack["emitted"] == 1
        assert [event for _sid, event, _data in handler.emitted] == ["voqualizer_asr_partial"]
        assert handler.emitted[0][2]["text"] == "hello partial"

        # 40 silence frames = 800ms trailing silence, which finalizes utterance.
        for idx in range(50, 89):
            ack = await handler.process(
                "voqualizer_audio_chunk",
                {"frame": encode_frame(idx, idx * 20, silence20ms), "bearer_token": token},
                "SID1",
            )
            assert ack["emitted"] == 0
        ack = await handler.process(
            "voqualizer_audio_chunk",
            {"frame": encode_frame(89, 1780, silence20ms), "bearer_token": token},
            "SID1",
        )
        assert ack["emitted"] == 1
        assert [event for _sid, event, _data in handler.emitted] == [
            "voqualizer_asr_partial",
            "voqualizer_asr_final",
        ]
        assert handler.emitted[1][2]["text"] == "hello final"
        assert provider.calls[0][1]["utterance_event"] == "partial"
        assert provider.calls[1][1]["utterance_event"] == "final"
        assert provider.calls[1][1]["trailing_silence_ms"] == 800.0

    run(scenario())
