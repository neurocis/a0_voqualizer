"""Minimal Wyoming protocol helpers for the Voqualizer rewrite.

This module is intentionally small for W1 scaffolding. It models Wyoming events
as JSON header lines with optional binary payload bytes, which matches the common
Wyoming framing shape used by Wyoming-compatible services.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Mapping


class WyomingProtocolError(ValueError):
    """Raised when a Wyoming frame/event cannot be parsed or encoded."""


@dataclass(slots=True)
class WyomingEvent:
    """A Wyoming event with a type/name, JSON data, and optional payload bytes."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    payload: bytes = b""

    def header(self) -> dict[str, Any]:
        header: dict[str, Any] = {"type": self.type}
        if self.data:
            header["data"] = self.data
        if self.payload:
            header["payload_length"] = len(self.payload)
        return header


def encode_event(event: WyomingEvent) -> bytes:
    """Encode a Wyoming event as a JSON header line plus optional payload."""
    if not event.type or not isinstance(event.type, str):
        raise WyomingProtocolError("event type must be a non-empty string")
    header = event.header()
    try:
        encoded = json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n"
    except Exception as exc:  # pragma: no cover - defensive
        raise WyomingProtocolError(f"failed to encode Wyoming event header: {exc}") from exc
    return encoded + (event.payload or b"")


def decode_header_line(line: bytes | str) -> tuple[str, dict[str, Any], int]:
    """Decode a Wyoming JSON header line.

    Returns `(event_type, data, payload_length)`.
    """
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WyomingProtocolError("Wyoming header is not valid UTF-8") from exc
    try:
        header = json.loads(line.strip())
    except json.JSONDecodeError as exc:
        raise WyomingProtocolError(f"Wyoming header is not valid JSON: {exc.msg}") from exc
    if not isinstance(header, Mapping):
        raise WyomingProtocolError("Wyoming header must be a JSON object")
    event_type = header.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise WyomingProtocolError("Wyoming header missing non-empty type")
    data = header.get("data") or {}
    if not isinstance(data, dict):
        raise WyomingProtocolError("Wyoming header data must be an object")
    payload_length = header.get("payload_length", 0)
    if not isinstance(payload_length, int) or payload_length < 0:
        raise WyomingProtocolError("Wyoming payload_length must be a non-negative integer")
    return event_type, data, payload_length


def decode_event(header_line: bytes | str, payload: bytes = b"") -> WyomingEvent:
    """Decode a complete Wyoming event from header line and payload bytes."""
    event_type, data, payload_length = decode_header_line(header_line)
    payload = payload or b""
    if payload_length != len(payload):
        raise WyomingProtocolError(
            f"Wyoming payload length mismatch: header={payload_length} actual={len(payload)}"
        )
    return WyomingEvent(type=event_type, data=data, payload=payload)


def event(type_: str, **data: Any) -> WyomingEvent:
    """Convenience constructor for JSON-only Wyoming events."""
    return WyomingEvent(type=type_, data=dict(data))


async def read_event_from_stream(reader, *, max_header_bytes: int = 65536) -> WyomingEvent | None:
    """Read one Wyoming event from an asyncio StreamReader-like object.

    Returns None on clean EOF before a header is read. Raises WyomingProtocolError
    for malformed headers, oversized headers, or truncated payloads.
    """
    try:
        header_line = await reader.readline()
    except Exception as exc:  # pragma: no cover - stream defensive
        raise WyomingProtocolError(f"failed to read Wyoming header: {exc}") from exc
    if not header_line:
        return None
    if len(header_line) > max_header_bytes:
        raise WyomingProtocolError("Wyoming header exceeds maximum size")
    event_type, data, payload_length = decode_header_line(header_line)
    payload = b""
    if payload_length:
        try:
            payload = await reader.readexactly(payload_length)
        except Exception as exc:
            raise WyomingProtocolError(
                f"failed to read Wyoming payload length={payload_length}: {exc}"
            ) from exc
    return WyomingEvent(type=event_type, data=data, payload=payload)


async def write_event_to_stream(writer, outgoing: WyomingEvent) -> None:
    """Write one Wyoming event to an asyncio StreamWriter-like object."""
    writer.write(encode_event(outgoing))
    drain = getattr(writer, "drain", None)
    if drain is not None:
        await drain()
