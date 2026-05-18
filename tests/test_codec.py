from __future__ import annotations

import math

import numpy as np
import pytest

from helpers.codec import (
    CodecError,
    CodecSpec,
    alaw_to_pcm16,
    convert_codec_to_pcm16,
    convert_pcm16_to_codec,
    mulaw_to_pcm16,
    ndarray_to_pcm16,
    parse_codec,
    pcm16_to_alaw,
    pcm16_to_mulaw,
    pcm16_to_ndarray,
    pcm16_to_opus,
    opus_to_pcm16,
    resample_pcm16,
)


def sine_pcm16(rate: int = 16000, seconds: float = 0.25, freq: float = 440.0, amp: float = 0.35) -> bytes:
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    samples = np.sin(2 * math.pi * freq * t) * amp * 32767.0
    return ndarray_to_pcm16(samples)


def rms(pcm16: bytes) -> float:
    samples = pcm16_to_ndarray(pcm16).astype(np.float64)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def correlation(a: bytes, b: bytes) -> float:
    aa = pcm16_to_ndarray(a).astype(np.float64)
    bb = pcm16_to_ndarray(b).astype(np.float64)
    n = min(aa.size, bb.size)
    aa, bb = aa[:n], bb[:n]
    aa -= aa.mean()
    bb -= bb.mean()
    denom = np.linalg.norm(aa) * np.linalg.norm(bb)
    return float(np.dot(aa, bb) / denom) if denom else 0.0


@pytest.mark.parametrize(
    "token,expected",
    [
        ("pcm16/8k", CodecSpec("pcm16", 8000)),
        ("pcm16/16k", CodecSpec("pcm16", 16000)),
        ("mulaw/8k", CodecSpec("mulaw", 8000)),
        ("alaw/8k", CodecSpec("alaw", 8000)),
        ("opus", CodecSpec("opus", None)),
    ],
)
def test_parse_codec(token: str, expected: CodecSpec):
    assert parse_codec(token) == expected


@pytest.mark.parametrize("bad", ["", "pcm16/16", "pcm16/k", 123])
def test_parse_codec_rejects_invalid(bad):
    with pytest.raises(CodecError):
        parse_codec(bad)


@pytest.mark.parametrize("src,dst", [(8000, 16000), (16000, 8000), (16000, 24000), (24000, 16000), (8000, 24000), (24000, 8000)])
def test_resample_pcm16_lengths(src: int, dst: int):
    pcm = sine_pcm16(src, seconds=0.10)
    out = resample_pcm16(pcm, src, dst)
    assert len(out) == int(dst * 0.10) * 2
    assert rms(out) > 1000


def test_resample_same_rate_returns_same_bytes():
    pcm = sine_pcm16(16000)
    assert resample_pcm16(pcm, 16000, 16000) == pcm


@pytest.mark.parametrize("src,dst", [(44100, 16000), (16000, 44100)])
def test_resample_rejects_unsupported_rates(src: int, dst: int):
    with pytest.raises(CodecError):
        resample_pcm16(b"\0\0" * 10, src, dst)


def test_mulaw_roundtrip_has_expected_size_and_signal():
    pcm16 = sine_pcm16(8000, seconds=0.25)
    enc = pcm16_to_mulaw(pcm16)
    dec = mulaw_to_pcm16(enc)
    assert len(enc) == len(pcm16) // 2
    assert len(dec) == len(pcm16)
    assert rms(dec) > 1000
    assert correlation(pcm16, dec) > 0.95


def test_alaw_roundtrip_has_expected_size_and_signal():
    pcm16 = sine_pcm16(8000, seconds=0.25)
    enc = pcm16_to_alaw(pcm16)
    dec = alaw_to_pcm16(enc)
    assert len(enc) == len(pcm16) // 2
    assert len(dec) == len(pcm16)
    assert rms(dec) > 1000
    assert correlation(pcm16, dec) > 0.95


def test_opus_roundtrip_with_ffmpeg_preserves_duration_and_signal():
    pcm16 = sine_pcm16(16000, seconds=0.50)
    opus = pcm16_to_opus(pcm16, 16000)
    decoded = opus_to_pcm16(opus, 16000)
    assert opus.startswith(b"OggS")
    # Opus/ffmpeg may add a tiny amount of priming/trailing padding, but should
    # stay close to the original duration.
    assert abs(len(decoded) - len(pcm16)) <= 16000 * 2 * 0.08
    assert rms(decoded) > 1000
    assert correlation(pcm16, decoded) > 0.85


@pytest.mark.parametrize("codec", ["pcm16/8k", "pcm16/16k", "pcm16/24k", "mulaw/8k", "alaw/8k", "opus"])
def test_convert_helpers_roundtrip_to_pcm16(codec: str):
    src = sine_pcm16(16000, seconds=0.20)
    encoded = convert_pcm16_to_codec(src, codec, src_rate=16000)
    decoded = convert_codec_to_pcm16(encoded, codec, dst_rate=16000)
    assert len(decoded) > 0
    assert rms(decoded) > 1000
    assert abs((len(decoded) // 2) - (len(src) // 2)) <= 16000 * 0.08


def test_pcm16_alignment_validation():
    with pytest.raises(CodecError, match="even"):
        pcm16_to_mulaw(b"\x00")


def test_empty_payloads_are_supported():
    assert pcm16_to_mulaw(b"") == b""
    assert mulaw_to_pcm16(b"") == b""
    assert pcm16_to_alaw(b"") == b""
    assert alaw_to_pcm16(b"") == b""
    assert resample_pcm16(b"", 8000, 16000) == b""
    assert pcm16_to_opus(b"", 16000) == b""
    assert opus_to_pcm16(b"", 16000) == b""
