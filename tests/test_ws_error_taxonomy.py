from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

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
from usr.plugins.a0_voqualizer.helpers.frame import encode_frame
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry

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


def cfg():
    return {
        "asr": {"default": "mock-asr", "providers": [{"name": "mock-asr", "type": "mock", "streaming": True, "language": "en"}]},
        "tts": {"default": "mock-tts", "providers": [{"name": "mock-tts", "type": "mock", "chunk_size": 4}]},
        "protocol": {"input_codecs": ["pcm16/16k"], "output_codecs": ["pcm16/16k"], "default_input_codec": "pcm16/16k", "default_output_codec": "pcm16/16k", "heartbeat_interval_seconds": 15, "session_resume_window_seconds": 30},
        "behavior": {"barge_in": True},
        "limits": {"audio_queue_max_frames": 4, "max_concurrent_sessions": 4, "max_session_seconds": 300},
    }


async def init(handler, monkeypatch):
    BridgeRegistry.reset_instance()
    conf = cfg()
    monkeypatch.setattr(ws_mod, "_safe_load_config", lambda: conf)
    BridgeRegistry.from_config(conf, replace=True)
    ready = await handler.process("voqualizer_init", {"session_id": "err-1", "asr": {"provider": "mock-asr"}, "tts": {"provider": "mock-tts"}}, "SID")
    assert ready["event"] == "voqualizer_ready"
    return ready["bearer_token"]


def test_unknown_event_uses_stable_error_code_and_logs(monkeypatch):
    logs = []
    monkeypatch.setattr(ws_mod, "log_voqualizer_error", lambda *args, **kwargs: logs.append((args, kwargs)))

    async def scenario():
        handler = CapturingWs()
        result = await handler.process("voqualizer_future_event", {}, "SID")
        err = result.as_result(handler_id="h", fallback_correlation_id=None)["error"]
        assert err["code"] == "UNKNOWN_EVENT"
        assert logs and logs[0][0][0] == "UNKNOWN_EVENT"

    run(scenario())


def test_auth_required_uses_stable_code_and_logs(monkeypatch):
    logs = []
    monkeypatch.setattr(ws_mod, "log_voqualizer_error", lambda *args, **kwargs: logs.append((args, kwargs)))

    async def scenario():
        handler = CapturingWs()
        await init(handler, monkeypatch)
        frame = encode_frame(1, 20, b"\0\0" * 320)
        result = await handler.process("voqualizer_audio_chunk", {"frame": frame, "bearer_token": "wrong"}, "SID")
        err = result.as_result(handler_id="h", fallback_correlation_id=None)["error"]
        assert err["code"] == "AUTH_REQUIRED"
        assert logs and logs[-1][0][0] == "AUTH_REQUIRED"
        assert logs[-1][1]["operation"] == "voqualizer_audio_chunk"

    run(scenario())


def test_bad_audio_chunk_uses_stable_code_and_logs(monkeypatch):
    logs = []
    monkeypatch.setattr(ws_mod, "log_voqualizer_error", lambda *args, **kwargs: logs.append((args, kwargs)))

    async def scenario():
        handler = CapturingWs()
        token = await init(handler, monkeypatch)
        result = await handler.process("voqualizer_audio_chunk", {"frame": b"", "bearer_token": token}, "SID")
        err = result.as_result(handler_id="h", fallback_correlation_id=None)["error"]
        assert err["code"] == "BAD_AUDIO_CHUNK"
        assert logs and logs[-1][0][0] == "BAD_AUDIO_CHUNK"

    run(scenario())
