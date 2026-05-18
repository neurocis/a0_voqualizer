from __future__ import annotations

import sys
from pathlib import Path

import pytest

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

from usr.plugins.a0_voqualizer.api.ws_voqualizer import _extract_audio_frame_payload
from usr.plugins.a0_voqualizer.helpers.frame import FrameError, encode_frame

sys.path[:] = _ORIG_SYS_PATH
for _name in list(sys.modules):
    if _name == "helpers" or _name.startswith("helpers."):
        sys.modules.pop(_name, None)


def frame() -> bytes:
    return encode_frame(3, 40, b"\x01\x02\x03\x04")


@pytest.mark.parametrize(
    "payload",
    [
        lambda f: f,
        lambda f: {"frame": f},
        lambda f: {"frame": bytearray(f)},
        lambda f: {"frame": memoryview(f)},
        lambda f: {"frame": list(f)},
        lambda f: {"frame": {"type": "Buffer", "data": list(f)}},
        lambda f: {"frame": {"data": list(f)}},
        lambda f: {"frame": {str(i): b for i, b in enumerate(f)}},
        lambda f: {"payload": {"type": "Buffer", "data": list(f)}, "final": True},
        lambda f: {"audio": {"buffer": {"data": list(f)}}},
        lambda f: {"type": "Buffer", "data": list(f)},
        lambda f: {str(i): b for i, b in enumerate(f)},
    ],
)
def test_extract_audio_frame_payload_accepts_browser_socketio_shapes(payload):
    raw, is_final = _extract_audio_frame_payload(payload(frame()))
    assert raw == frame()
    if isinstance(payload(frame()), dict) and payload(frame()).get("final") is True:
        assert is_final is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"frame": "not binary"},
        {"frame": [1, 2, "x"]},
        {"frame": {"type": "Buffer", "data": [999]}},
        {"frame": [1, 2, 3]},
        b"\x00\x01\x02",
    ],
)
def test_extract_audio_frame_payload_rejects_malformed_shapes(payload):
    with pytest.raises(FrameError):
        _extract_audio_frame_payload(payload)
