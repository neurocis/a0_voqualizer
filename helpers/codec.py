"""Audio codec helpers for the Voqualizer protocol.

A2.2 covers the protocol-level codecs used before ASR/TTS adapters land:

* PCM16 little-endian mono at 8/16/24 kHz
* G.711 μ-law (``mulaw/8k``)
* G.711 A-law (``alaw/8k``)
* Opus via ``ffmpeg`` shell-out
* Sample-rate conversion among 8 kHz, 16 kHz, and 24 kHz

The module intentionally keeps the public API byte-oriented because websocket
frames and provider adapters exchange raw payload bytes. PCM samples are always
signed 16-bit little-endian mono unless a function explicitly says otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import shutil
import subprocess
from typing import Final, Literal

import numpy as np

try:  # preferred dependency from requirements.txt, may not be installed yet
    import samplerate as _samplerate  # type: ignore
except Exception:  # pragma: no cover - exercised when dependency absent
    _samplerate = None

try:  # present in the framework venv and useful fallback
    from scipy import signal as _scipy_signal  # type: ignore
except Exception:  # pragma: no cover - only if scipy removed
    _scipy_signal = None

SUPPORTED_SAMPLE_RATES: Final[set[int]] = {8000, 16000, 24000}
PCM16_MAX: Final[float] = 32768.0


class CodecError(ValueError):
    """Raised for invalid codec inputs or failed transcodes."""


@dataclass(frozen=True, slots=True)
class CodecSpec:
    """Parsed protocol codec string.

    Examples:
        ``pcm16/16k`` -> ``CodecSpec(name='pcm16', sample_rate=16000)``
        ``mulaw/8k`` -> ``CodecSpec(name='mulaw', sample_rate=8000)``
        ``opus`` -> ``CodecSpec(name='opus', sample_rate=None)``
    """

    name: str
    sample_rate: int | None = None


def parse_codec(codec: str) -> CodecSpec:
    """Parse a protocol codec token such as ``pcm16/16k`` or ``opus``."""

    if not isinstance(codec, str) or not codec:
        raise CodecError("codec must be a non-empty string")
    if "/" not in codec:
        return CodecSpec(codec, None)
    name, rate_s = codec.split("/", 1)
    if not name or not rate_s.endswith("k"):
        raise CodecError(f"invalid codec string {codec!r}")
    try:
        rate = int(rate_s[:-1]) * 1000
    except Exception as exc:
        raise CodecError(f"invalid codec sample rate in {codec!r}") from exc
    return CodecSpec(name, rate)


def ensure_pcm16_bytes(pcm16: bytes | bytearray | memoryview) -> bytes:
    """Return a bytes copy and validate 16-bit sample alignment."""

    try:
        raw = bytes(pcm16)
    except Exception as exc:  # pragma: no cover - exact exception varies
        raise CodecError("pcm16 must be bytes-like") from exc
    if len(raw) % 2:
        raise CodecError("pcm16 byte length must be even")
    return raw


def pcm16_to_ndarray(pcm16: bytes | bytearray | memoryview) -> np.ndarray:
    """Decode little-endian PCM16 bytes as an int16 numpy array copy."""

    raw = ensure_pcm16_bytes(pcm16)
    return np.frombuffer(raw, dtype="<i2").astype(np.int16, copy=True)


def ndarray_to_pcm16(samples: np.ndarray) -> bytes:
    """Encode numeric samples as clipped little-endian PCM16 bytes."""

    arr = np.asarray(samples)
    if arr.dtype.kind in {"f", "c"}:
        arr = np.nan_to_num(arr.real if arr.dtype.kind == "c" else arr)
        arr = np.clip(arr, -32768, 32767)
    else:
        arr = np.clip(arr, -32768, 32767)
    return arr.astype("<i2", copy=False).tobytes()


def _pcm16_to_float(pcm16: bytes | bytearray | memoryview) -> np.ndarray:
    return pcm16_to_ndarray(pcm16).astype(np.float32) / PCM16_MAX


def _float_to_pcm16(samples: np.ndarray) -> bytes:
    arr = np.asarray(samples, dtype=np.float32)
    arr = np.nan_to_num(arr)
    arr = np.clip(arr, -1.0, 0.9999695)
    return (arr * PCM16_MAX).astype("<i2").tobytes()


def resample_pcm16(
    pcm16: bytes | bytearray | memoryview,
    src_rate: int,
    dst_rate: int,
    *,
    converter: str = "sinc_best",
) -> bytes:
    """Resample mono PCM16 bytes between supported protocol rates.

    Uses ``samplerate`` when available, otherwise falls back to
    ``scipy.signal.resample_poly``. The fallback is deterministic and avoids
    adding an activation-time dependency to unit tests.
    """

    if src_rate not in SUPPORTED_SAMPLE_RATES:
        raise CodecError(f"unsupported source sample rate {src_rate}")
    if dst_rate not in SUPPORTED_SAMPLE_RATES:
        raise CodecError(f"unsupported destination sample rate {dst_rate}")
    raw = ensure_pcm16_bytes(pcm16)
    if src_rate == dst_rate:
        return raw
    samples = _pcm16_to_float(raw)
    if samples.size == 0:
        return b""

    ratio = dst_rate / src_rate
    if _samplerate is not None:  # pragma: no cover - dependency absent in CI here
        out = _samplerate.resample(samples, ratio, converter)
        return _float_to_pcm16(out)

    if _scipy_signal is None:
        raise CodecError("resampling requires samplerate or scipy")

    frac = Fraction(dst_rate, src_rate).limit_denominator()
    out = _scipy_signal.resample_poly(samples, frac.numerator, frac.denominator)
    return _float_to_pcm16(out)


# ---------------------------------------------------------------------------
# G.711 μ-law / A-law
# ---------------------------------------------------------------------------


def pcm16_to_mulaw(pcm16: bytes | bytearray | memoryview) -> bytes:
    """Encode PCM16 to G.711 μ-law bytes.

    Implements the standard ITU-T G.711 μ-law companding table algorithm in
    numpy. No deprecated ``audioop`` dependency is used.
    """

    pcm = pcm16_to_ndarray(pcm16).astype(np.int32)
    sign = (pcm < 0).astype(np.int32) * 0x80
    mag = np.abs(pcm)
    mag = np.minimum(mag + 132, 32635)

    seg = np.zeros_like(mag)
    thresholds = np.array([0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF], dtype=np.int32)
    for i, threshold in enumerate(thresholds):
        seg = np.where((seg == 0) & (mag <= threshold), i, seg)
    # Values above first threshold with segment 0 are valid segment 0; loop is
    # fine because mag is clipped to the last threshold.
    quant = (mag >> (seg + 3)) & 0x0F
    code = ~(sign | (seg << 4) | quant) & 0xFF
    return code.astype(np.uint8).tobytes()


def mulaw_to_pcm16(mulaw: bytes | bytearray | memoryview) -> bytes:
    """Decode G.711 μ-law bytes to PCM16."""

    try:
        raw = bytes(mulaw)
    except Exception as exc:  # pragma: no cover
        raise CodecError("mulaw must be bytes-like") from exc
    u = np.frombuffer(raw, dtype=np.uint8).astype(np.int32)
    u = (~u) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = ((mantissa << 3) + 132) << exponent
    sample = sample - 132
    sample = np.where(sign != 0, -sample, sample)
    return ndarray_to_pcm16(sample)


def pcm16_to_alaw(pcm16: bytes | bytearray | memoryview) -> bytes:
    """Encode PCM16 to G.711 A-law bytes."""

    pcm = pcm16_to_ndarray(pcm16).astype(np.int32)
    sign_mask = np.where(pcm >= 0, 0xD5, 0x55).astype(np.int32)
    mag = np.abs(pcm)
    mag = np.minimum(mag, 32635)

    compressed = np.empty_like(mag)
    small = mag < 256
    compressed[small] = mag[small] >> 4

    large_mag = mag[~small]
    if large_mag.size:
        # floor(log2(mag)) - 7 gives A-law segment for mag >= 256.
        seg = np.floor(np.log2(large_mag)).astype(np.int32) - 7
        seg = np.clip(seg, 1, 7)
        mant = (large_mag >> (seg + 3)) & 0x0F
        compressed[~small] = (seg << 4) | mant

    code = compressed ^ sign_mask
    return code.astype(np.uint8).tobytes()


def alaw_to_pcm16(alaw: bytes | bytearray | memoryview) -> bytes:
    """Decode G.711 A-law bytes to PCM16."""

    try:
        raw = bytes(alaw)
    except Exception as exc:  # pragma: no cover
        raise CodecError("alaw must be bytes-like") from exc
    a = np.frombuffer(raw, dtype=np.uint8).astype(np.int32) ^ 0x55
    sign = a & 0x80
    exponent = (a >> 4) & 0x07
    mantissa = a & 0x0F
    sample = np.where(
        exponent == 0,
        (mantissa << 4) + 8,
        ((mantissa << 4) + 0x108) << (exponent - 1),
    )
    sample = np.where(sign != 0, sample, -sample)
    return ndarray_to_pcm16(sample)


# ---------------------------------------------------------------------------
# ffmpeg-backed codecs
# ---------------------------------------------------------------------------


def _require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise CodecError("ffmpeg is required for opus/mp3 transcoding")
    return exe


def _run_ffmpeg(args: list[str], input_bytes: bytes) -> bytes:
    exe = _require_ffmpeg()
    proc = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise CodecError(f"ffmpeg failed ({proc.returncode}): {err}")
    return proc.stdout


def pcm16_to_opus(
    pcm16: bytes | bytearray | memoryview,
    sample_rate: int = 16000,
    *,
    channels: int = 1,
    bitrate: str = "32k",
) -> bytes:
    """Encode PCM16 to an Ogg Opus payload using ffmpeg/libopus."""

    if sample_rate not in SUPPORTED_SAMPLE_RATES:
        raise CodecError(f"unsupported opus source sample rate {sample_rate}")
    if channels != 1:
        raise CodecError("only mono audio is supported in v1")
    raw = ensure_pcm16_bytes(pcm16)
    if not raw:
        return b""
    return _run_ffmpeg(
        [
            "-f", "s16le",
            "-ar", str(sample_rate),
            "-ac", str(channels),
            "-i", "pipe:0",
            "-c:a", "libopus",
            "-application", "voip",
            "-b:a", bitrate,
            "-vbr", "on",
            "-f", "opus",
            "pipe:1",
        ],
        raw,
    )


def opus_to_pcm16(
    opus: bytes | bytearray | memoryview,
    target_rate: int = 16000,
    *,
    channels: int = 1,
) -> bytes:
    """Decode an Ogg Opus payload to PCM16 using ffmpeg."""

    if target_rate not in SUPPORTED_SAMPLE_RATES:
        raise CodecError(f"unsupported opus target sample rate {target_rate}")
    if channels != 1:
        raise CodecError("only mono audio is supported in v1")
    try:
        raw = bytes(opus)
    except Exception as exc:  # pragma: no cover
        raise CodecError("opus must be bytes-like") from exc
    if not raw:
        return b""
    return _run_ffmpeg(
        [
            "-i", "pipe:0",
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ar", str(target_rate),
            "-ac", str(channels),
            "pipe:1",
        ],
        raw,
    )


def convert_pcm16_to_codec(
    pcm16: bytes | bytearray | memoryview,
    codec: str,
    *,
    src_rate: int = 16000,
) -> bytes:
    """Encode PCM16 bytes into one of the protocol codecs."""

    spec = parse_codec(codec)
    if spec.name == "pcm16":
        if spec.sample_rate is None:
            raise CodecError("pcm16 codec requires a sample rate")
        return resample_pcm16(pcm16, src_rate, spec.sample_rate)
    if spec.name == "mulaw" and spec.sample_rate == 8000:
        return pcm16_to_mulaw(resample_pcm16(pcm16, src_rate, 8000))
    if spec.name == "alaw" and spec.sample_rate == 8000:
        return pcm16_to_alaw(resample_pcm16(pcm16, src_rate, 8000))
    if spec.name == "opus":
        return pcm16_to_opus(pcm16, src_rate)
    raise CodecError(f"unsupported output codec {codec!r}")


def convert_codec_to_pcm16(
    data: bytes | bytearray | memoryview,
    codec: str,
    *,
    dst_rate: int = 16000,
) -> bytes:
    """Decode a protocol codec payload into PCM16 at ``dst_rate``."""

    spec = parse_codec(codec)
    if spec.name == "pcm16":
        if spec.sample_rate is None:
            raise CodecError("pcm16 codec requires a sample rate")
        return resample_pcm16(data, spec.sample_rate, dst_rate)
    if spec.name == "mulaw" and spec.sample_rate == 8000:
        return resample_pcm16(mulaw_to_pcm16(data), 8000, dst_rate)
    if spec.name == "alaw" and spec.sample_rate == 8000:
        return resample_pcm16(alaw_to_pcm16(data), 8000, dst_rate)
    if spec.name == "opus":
        return opus_to_pcm16(data, dst_rate)
    raise CodecError(f"unsupported input codec {codec!r}")
