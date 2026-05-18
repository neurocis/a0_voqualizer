from __future__ import annotations

import os

import pytest

from helpers.frame import (
    AudioFrame,
    FrameError,
    FrameHeader,
    HEADER_SIZE,
    MAX_U16,
    decode_frame,
    encode_frame,
    pack_header,
    split_frame,
    unpack_header,
)


def test_header_size_is_four_bytes():
    assert HEADER_SIZE == 4
    assert len(pack_header(1, 2)) == 4


@pytest.mark.parametrize(
    "seq,ts_ms",
    [
        (0, 0),
        (1, 2),
        (255, 1000),
        (256, 16000),
        (MAX_U16, MAX_U16),
    ],
)
def test_header_roundtrip_preserves_fields(seq: int, ts_ms: int):
    raw = pack_header(seq, ts_ms)
    parsed = unpack_header(raw)
    assert parsed == FrameHeader(seq=seq, ts_ms=ts_ms)
    assert parsed.to_bytes() == raw


def test_network_byte_order_is_stable():
    # seq=0x1234, ts_ms=0xABCD -> big-endian / network byte order.
    assert pack_header(0x1234, 0xABCD) == bytes.fromhex("1234abcd")


@pytest.mark.parametrize("payload", [b"", b"abc", bytes(range(256)), os.urandom(4096)])
def test_full_frame_roundtrip_preserves_arbitrary_payload(payload: bytes):
    encoded = encode_frame(42, 1234, payload)
    parsed = decode_frame(encoded)
    assert isinstance(parsed, AudioFrame)
    assert parsed.seq == 42
    assert parsed.ts_ms == 1234
    assert parsed.payload == payload
    assert parsed.to_bytes() == encoded


def test_split_frame_returns_tuple():
    payload = b"pcm bytes"
    encoded = encode_frame(7, 99, payload)
    assert split_frame(encoded) == (7, 99, payload)


@pytest.mark.parametrize("bad", [b"", b"\x00", b"\x00\x01\x00", b"\x00" * 5])
def test_unpack_header_requires_exactly_four_bytes(bad: bytes):
    with pytest.raises(FrameError, match="exactly 4 bytes"):
        unpack_header(bad)


@pytest.mark.parametrize("bad", [b"", b"\x00", b"\x00\x01\x00"])
def test_decode_frame_rejects_short_frames(bad: bytes):
    with pytest.raises(FrameError, match="at least 4 bytes"):
        decode_frame(bad)


@pytest.mark.parametrize("field,value", [("seq", -1), ("seq", MAX_U16 + 1), ("ts_ms", -1), ("ts_ms", MAX_U16 + 1)])
def test_pack_header_rejects_out_of_range(field: str, value: int):
    kwargs = {"seq": 1, "ts_ms": 2}
    kwargs[field] = value
    with pytest.raises(FrameError, match=field):
        pack_header(**kwargs)


@pytest.mark.parametrize("field,value", [("seq", 1.5), ("ts_ms", "2")])
def test_pack_header_rejects_non_int(field: str, value):
    kwargs = {"seq": 1, "ts_ms": 2}
    kwargs[field] = value
    with pytest.raises(FrameError, match=field):
        pack_header(**kwargs)


def test_decode_accepts_bytearray_and_memoryview_without_mutation():
    encoded = bytearray(encode_frame(9, 10, b"payload"))
    parsed = decode_frame(memoryview(encoded))
    assert parsed == AudioFrame(FrameHeader(9, 10), b"payload")
    encoded[-1] = ord("X")
    assert parsed.payload == b"payload"
