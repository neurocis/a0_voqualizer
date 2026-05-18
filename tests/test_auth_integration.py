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
from usr.plugins.a0_voqualizer.helpers.auth import (
    AUTH_ERROR_CODE,
    SESSION_TOKEN_METADATA_KEY,
    ensure_session_bearer_token,
    extract_bearer_token,
    verify_session_bearer_token,
)
from usr.plugins.a0_voqualizer.helpers.frame import encode_frame
from usr.plugins.a0_voqualizer.helpers.registry import BridgeRegistry
from usr.plugins.a0_voqualizer.helpers.session import BridgeSession

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
        "tts": {"default": "mock-tts", "providers": [{"name": "mock-tts", "type": "mock", "voice": "mock", "chunk_size": 4}]},
        "protocol": {
            "input_codecs": ["pcm16/16k"],
            "output_codecs": ["pcm16/16k"],
            "default_input_codec": "pcm16/16k",
            "default_output_codec": "pcm16/16k",
            "heartbeat_interval_seconds": 15,
            "session_resume_window_seconds": 30,
        },
        "behavior": {"barge_in": True},
        "limits": {"audio_queue_max_frames": 4, "max_concurrent_sessions": 4, "max_session_seconds": 300},
    }


async def init(handler, monkeypatch, *, session_id="auth-1"):
    config = cfg()
    BridgeRegistry.reset_instance()
    monkeypatch.setattr(ws_mod, "_safe_load_config", lambda: config)
    BridgeRegistry.from_config(config, replace=True)
    ready = await handler.process("voqualizer_init", {"session_id": session_id}, "SID1")
    assert ready["event"] == "voqualizer_ready"
    assert ready["session_id"] == session_id
    assert ready["bearer_token"]
    return ready


def error_code(result):
    return result.as_result(handler_id="h", fallback_correlation_id=None)["error"]["code"]


def test_ws_handler_explicitly_reuses_a0_auth_csrf_gate():
    # A0 base WsHandler exposes requires_auth/requires_csrf as classmethods,
    # so we override with a classmethod too — a plain bool attribute would
    # raise TypeError inside _check_security and silently fail handler
    # activation, producing NO_HANDLERS at dispatch.
    assert callable(WsVoqualizer.requires_auth)
    assert WsVoqualizer.requires_auth() is True
    assert WsVoqualizer.requires_csrf() is True


def test_session_token_helpers_are_stable_and_support_bearer_shapes():
    session = BridgeSession("s1")
    token = ensure_session_bearer_token(session)
    assert ensure_session_bearer_token(session) == token
    assert session.metadata[SESSION_TOKEN_METADATA_KEY] == token
    assert extract_bearer_token({"authorization": f"Bearer {token}"}) == token
    assert extract_bearer_token({"auth": {"bearer_token": token}}) == token
    assert verify_session_bearer_token(session, {"session_token": token}) is True
    assert verify_session_bearer_token(session, {"bearer_token": "wrong"}) is False


def test_ready_issues_per_session_bearer_token_and_resume_reuses_it(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        ready1 = await init(handler, monkeypatch)
        session = BridgeRegistry.instance().get("auth-1")
        assert session.metadata[SESSION_TOKEN_METADATA_KEY] == ready1["bearer_token"]

        await handler.process("voqualizer_control", {"action": "resume", "bearer_token": ready1["bearer_token"]}, "SID1")
        ready2 = await handler.process("voqualizer_init", {"session_id": "auth-1"}, "SID1")
        assert ready2["resumed"] is True
        assert ready2["bearer_token"] == ready1["bearer_token"]

    run(scenario())


def test_missing_or_invalid_token_rejected_for_session_bound_operations(monkeypatch):
    async def scenario():
        handler = CapturingWs()
        ready = await init(handler, monkeypatch)
        token = ready["bearer_token"]

        control_missing = await handler.process("voqualizer_control", {"action": "mute"}, "SID1")
        assert error_code(control_missing) == AUTH_ERROR_CODE

        tts_bad = await handler.process("voqualizer_user_text", {"text": "hi", "bearer_token": "bad"}, "SID1")
        assert error_code(tts_bad) == AUTH_ERROR_CODE

        frame = encode_frame(1, 20, b"")
        audio_bad = await handler.process("voqualizer_audio_chunk", {"frame": frame, "authorization": "Bearer bad"}, "SID1")
        assert error_code(audio_bad) == AUTH_ERROR_CODE

        ok = await handler.process("voqualizer_control", {"action": "mute", "authorization": f"Bearer {token}"}, "SID1")
        assert ok["event"] == "voqualizer_control_ack"

    run(scenario())


def test_context_session_mismatch_cannot_be_authorized_with_other_session_token(monkeypatch):
    async def scenario():
        first = CapturingWs()
        second = CapturingWs()
        ready1 = await init(first, monkeypatch, session_id="auth-a")
        ready2 = await second.process("voqualizer_init", {"session_id": "auth-b", "context_id": "ctx-b"}, "SID2")
        assert ready2["event"] == "voqualizer_ready"
        assert ready1["bearer_token"] != ready2["bearer_token"]

        bad = await second.process("voqualizer_control", {"action": "mute", "bearer_token": ready1["bearer_token"]}, "SID2")
        assert error_code(bad) == AUTH_ERROR_CODE
        ok = await second.process("voqualizer_control", {"action": "mute", "bearer_token": ready2["bearer_token"]}, "SID2")
        assert ok["session_id"] == "auth-b"

    run(scenario())
