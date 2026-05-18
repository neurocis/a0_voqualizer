from __future__ import annotations

import os
import random
import string

import pytest

from helpers.codec import (
    CodecError,
    alaw_to_pcm16,
    convert_codec_to_pcm16,
    ensure_pcm16_bytes,
    mulaw_to_pcm16,
    parse_codec,
)
from helpers.frame import FrameError, decode_frame, pack_header


def deterministic_bytes(seed: int, length: int) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randrange(0, 256) for _ in range(length))


@pytest.mark.parametrize("length", list(range(0, 4)))
def test_frame_fuzz_rejects_short_frames_without_crashing(length: int):
    with pytest.raises(FrameError):
        decode_frame(deterministic_bytes(length, length))


@pytest.mark.parametrize("seed", range(20))
def test_frame_fuzz_decodes_or_rejects_random_payloads_deterministically(seed: int):
    payload = deterministic_bytes(seed, random.Random(seed).randrange(4, 80))
    parsed = decode_frame(payload)
    assert 0 <= parsed.seq <= 0xFFFF
    assert 0 <= parsed.ts_ms <= 0xFFFF
    assert parsed.payload == payload[4:]


@pytest.mark.parametrize("seq,ts_ms", [(-1, 0), (0, -1), (0x10000, 0), (0, 0x10000), (1.5, 0), (0, "bad")])
def test_frame_fuzz_rejects_invalid_header_metadata(seq, ts_ms):
    with pytest.raises(FrameError):
        pack_header(seq, ts_ms)


@pytest.mark.parametrize("bad", ["", "/16k", "pcm16/16", "pcm16/k", "pcm16/-16k", "mulaw/7k", "not a codec", object()])
def test_codec_fuzz_parse_rejects_or_marks_invalid_tokens(bad):
    if not isinstance(bad, str) or not bad or bad in {"/16k", "pcm16/16", "pcm16/k"}:
        with pytest.raises(CodecError):
            parse_codec(bad)  # type: ignore[arg-type]
    else:
        # parse_codec is syntactic: semantically unsupported rates/names are rejected by conversion helpers.
        spec = parse_codec(bad)
        assert spec.name


@pytest.mark.parametrize("payload", [b"\x00", b"abc", os.urandom(15)])
def test_codec_fuzz_rejects_odd_length_pcm16(payload: bytes):
    with pytest.raises(CodecError, match="even"):
        ensure_pcm16_bytes(payload)


@pytest.mark.parametrize("codec", ["pcm16", "pcm16/7k", "pcm16/-16k", "mulaw/16k", "alaw/16k", "unknown/16k", "opus/16k"])
def test_codec_fuzz_rejects_unsupported_decode_codecs(codec: str):
    with pytest.raises(CodecError):
        convert_codec_to_pcm16(b"\0\0" * 8, codec, dst_rate=16000)


@pytest.mark.parametrize("seed", range(20))
def test_g711_fuzz_decoders_accept_arbitrary_bytes_without_crashing(seed: int):
    payload = deterministic_bytes(seed + 100, random.Random(seed).randrange(0, 128))
    mulaw = mulaw_to_pcm16(payload)
    alaw = alaw_to_pcm16(payload)
    assert len(mulaw) == len(payload) * 2
    assert len(alaw) == len(payload) * 2


def test_codec_fuzz_random_printable_strings_do_not_crash_parser():
    rng = random.Random(1234)
    alphabet = string.ascii_letters + string.digits + "/_- ."
    for _ in range(50):
        token = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 16)))
        try:
            parse_codec(token)
        except CodecError:
            pass
