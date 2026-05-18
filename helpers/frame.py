"""Binary audio frame header helpers for the Voqualizer WebSocket protocol.

A2.1 defines the compact binary frame envelope used by
``voqualizer_audio_chunk`` and future ``voqualizer_tts_chunk`` events.

The PLAN.md v1 protocol specifies a *4-byte* binary header containing
``{seq, ts_ms}``. To keep the declared 4-byte wire size while preserving two
integer fields, the v1 header is encoded as two unsigned 16-bit integers in
network byte order::

    0               15 16              31
    +----------------+----------------+
    | seq:uint16     | ts_ms:uint16    |
    +----------------+----------------+
    | payload bytes ...

``seq`` is the audio-frame sequence number. ``ts_ms`` is a millisecond
presentation timestamp modulo 65536. Jitter/reorder code can treat both as
wrapping counters in later artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Final

HEADER_FORMAT: Final[str] = "!HH"
HEADER_SIZE: Final[int] = struct.calcsize(HEADER_FORMAT)
MAX_U16: Final[int] = 0xFFFF


class FrameError(ValueError):
    """Raised when a binary audio frame/header is malformed."""


@dataclass(frozen=True, slots=True)
class FrameHeader:
    """Parsed v1 audio-frame header.

    Attributes:
        seq: Unsigned 16-bit frame sequence number.
        ts_ms: Unsigned 16-bit millisecond timestamp, modulo 65536.
    """

    seq: int
    ts_ms: int

    def __post_init__(self) -> None:
        _validate_u16("seq", self.seq)
        _validate_u16("ts_ms", self.ts_ms)

    def to_bytes(self) -> bytes:
        """Return this header encoded as the 4-byte wire representation."""

        return pack_header(self.seq, self.ts_ms)


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """A binary protocol frame split into header metadata and payload."""

    header: FrameHeader
    payload: bytes

    @property
    def seq(self) -> int:
        return self.header.seq

    @property
    def ts_ms(self) -> int:
        return self.header.ts_ms

    def to_bytes(self) -> bytes:
        """Return the full binary wire frame."""

        return self.header.to_bytes() + self.payload


def _validate_u16(name: str, value: int) -> None:
    if not isinstance(value, int):
        raise FrameError(f"{name} must be an integer")
    if value < 0 or value > MAX_U16:
        raise FrameError(f"{name} must be between 0 and {MAX_U16}")


def _bytes_like(value: bytes | bytearray | memoryview, *, name: str) -> bytes:
    try:
        return bytes(value)
    except Exception as exc:  # pragma: no cover - exact TypeError varies
        raise FrameError(f"{name} must be bytes-like") from exc


def pack_header(seq: int, ts_ms: int) -> bytes:
    """Pack ``seq`` and ``ts_ms`` into the 4-byte v1 frame header."""

    _validate_u16("seq", seq)
    _validate_u16("ts_ms", ts_ms)
    return struct.pack(HEADER_FORMAT, seq, ts_ms)


def unpack_header(data: bytes | bytearray | memoryview) -> FrameHeader:
    """Parse a 4-byte v1 header.

    Args:
        data: Exactly four bytes containing ``seq`` and ``ts_ms``.

    Raises:
        FrameError: if the input is not exactly ``HEADER_SIZE`` bytes.
    """

    raw = _bytes_like(data, name="header")
    if len(raw) != HEADER_SIZE:
        raise FrameError(f"header must be exactly {HEADER_SIZE} bytes")
    seq, ts_ms = struct.unpack(HEADER_FORMAT, raw)
    return FrameHeader(seq=seq, ts_ms=ts_ms)


def encode_frame(seq: int, ts_ms: int, payload: bytes | bytearray | memoryview = b"") -> bytes:
    """Encode a full binary audio frame from metadata and payload."""

    return pack_header(seq, ts_ms) + _bytes_like(payload, name="payload")


def decode_frame(frame: bytes | bytearray | memoryview) -> AudioFrame:
    """Decode a full binary audio frame into header and payload.

    Empty payloads are valid; frames shorter than the 4-byte header are not.
    """

    raw = _bytes_like(frame, name="frame")
    if len(raw) < HEADER_SIZE:
        raise FrameError(f"frame must be at least {HEADER_SIZE} bytes")
    return AudioFrame(header=unpack_header(raw[:HEADER_SIZE]), payload=raw[HEADER_SIZE:])


def split_frame(frame: bytes | bytearray | memoryview) -> tuple[int, int, bytes]:
    """Convenience wrapper returning ``(seq, ts_ms, payload)``."""

    parsed = decode_frame(frame)
    return parsed.seq, parsed.ts_ms, parsed.payload
