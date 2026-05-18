"""Reference Asterisk AudioSocket bridge for a0_voqualizer.

The core conversion/forwarding logic is dependency-light and transport-injected
so it can be reviewed and tested without Asterisk, a live A0 backend, network,
credentials, or model/provider calls.
"""

from __future__ import annotations

import asyncio
import audioop
import json
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

VOQUALIZER_HANDLER = "plugins/a0_voqualizer/ws_voqualizer"
INPUT_CODEC = "pcm16/16k"
OUTPUT_CODEC = "pcm16/16k"
ASTERISK_SAMPLE_RATE = 8000
VOQUALIZER_SAMPLE_RATE = 16000
FRAME_HEADER_BYTES = 4


def resample_pcm16_linear(pcm16: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Dependency-free mono PCM16 little-endian linear resampler."""

    if src_rate == dst_rate or not pcm16:
        return bytes(pcm16)
    sample_count = len(pcm16) // 2
    if sample_count == 0:
        return b""
    samples = struct.unpack("<" + "h" * sample_count, pcm16[: sample_count * 2])
    out_count = max(1, round(sample_count * dst_rate / src_rate))
    scale = src_rate / dst_rate
    out: list[int] = []
    for index in range(out_count):
        pos = index * scale
        left = min(sample_count - 1, int(pos))
        right = min(sample_count - 1, left + 1)
        frac = pos - left
        value = samples[left] + (samples[right] - samples[left]) * frac
        out.append(max(-32768, min(32767, round(value))))
    return struct.pack("<" + "h" * len(out), *out)


def asterisk_slin8_to_voqualizer_pcm16(slin8: bytes) -> bytes:
    """Convert Asterisk signed-linear 8 kHz PCM16 to Voqualizer PCM16 16 kHz."""

    return resample_pcm16_linear(slin8, ASTERISK_SAMPLE_RATE, VOQUALIZER_SAMPLE_RATE)


def voqualizer_pcm16_to_asterisk_slin8(pcm16k: bytes) -> bytes:
    """Convert Voqualizer PCM16 16 kHz to Asterisk signed-linear 8 kHz PCM16."""

    return resample_pcm16_linear(pcm16k, VOQUALIZER_SAMPLE_RATE, ASTERISK_SAMPLE_RATE)


def ulaw8_to_voqualizer_pcm16(ulaw8: bytes) -> bytes:
    """Optional G.711 μ-law 8 kHz ingress helper for non-slin Asterisk paths."""

    return asterisk_slin8_to_voqualizer_pcm16(audioop.ulaw2lin(ulaw8, 2))


def voqualizer_pcm16_to_ulaw8(pcm16k: bytes) -> bytes:
    """Optional G.711 μ-law 8 kHz egress helper for non-slin Asterisk paths."""

    return audioop.lin2ulaw(voqualizer_pcm16_to_asterisk_slin8(pcm16k), 2)


def encode_voqualizer_frame(seq: int, ts_ms: int, pcm16: bytes) -> bytes:
    """Encode A2 frame: uint16 seq + uint16 ts_ms in network byte order."""

    return struct.pack("!HH", seq & 0xFFFF, ts_ms & 0xFFFF) + bytes(pcm16)


class VoqualizerTransport(Protocol):
    async def connect(self, handler: str) -> None: ...
    async def emit_with_ack(self, event: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def on(self, event: str, callback: Callable[[dict[str, Any]], Any]) -> None: ...


class AsteriskAudioSink(Protocol):
    async def write_audio(self, pcm16_8k: bytes) -> None: ...
    async def mark(self, name: str) -> None: ...


@dataclass
class AsteriskVoqualizerBridge:
    """Transport-injected Asterisk audio-fork ↔ Voqualizer bridge."""

    voqualizer: VoqualizerTransport
    asterisk_sink: AsteriskAudioSink
    session_id: str = field(default_factory=lambda: f"asterisk-{uuid.uuid4().hex}")
    bearer_token: str = ""
    seq: int = 0
    started_at: float = field(default_factory=time.monotonic)

    async def connect(self) -> dict[str, Any]:
        await self.voqualizer.connect(VOQUALIZER_HANDLER)
        ready = await self.voqualizer.emit_with_ack(
            "voqualizer_init",
            {
                "session_id": self.session_id,
                "asr": {"codec": INPUT_CODEC},
                "tts": {"codec": OUTPUT_CODEC},
                "barge_in": True,
            },
        )
        token = ready.get("bearer_token")
        if not token:
            raise RuntimeError("voqualizer_ready did not issue bearer_token")
        self.session_id = str(ready.get("session_id") or self.session_id)
        self.bearer_token = str(token)
        self.voqualizer.on("voqualizer_tts_chunk", self.handle_tts_chunk)
        self.voqualizer.on("voqualizer_tts_done", self.handle_tts_done)
        return ready

    async def forward_asterisk_audio(self, slin8: bytes) -> dict[str, Any]:
        self.ensure_bearer_token()
        pcm16k = asterisk_slin8_to_voqualizer_pcm16(slin8)
        ts_ms = int((time.monotonic() - self.started_at) * 1000) & 0xFFFF
        frame = encode_voqualizer_frame(self.seq, ts_ms, pcm16k)
        self.seq = (self.seq + 1) & 0xFFFF
        return await self.voqualizer.emit_with_ack(
            "voqualizer_audio_chunk",
            self.session_payload({"frame": frame}),
        )

    async def end_session(self) -> dict[str, Any]:
        self.ensure_bearer_token()
        return await self.voqualizer.emit_with_ack(
            "voqualizer_control",
            self.session_payload({"action": "end_session"}),
        )

    def handle_tts_chunk(self, payload: dict[str, Any]) -> None:
        audio = payload.get("audio") or payload.get("data") or payload.get("pcm16") or b""
        pcm8k = voqualizer_pcm16_to_asterisk_slin8(bytes(audio))
        asyncio.create_task(self.asterisk_sink.write_audio(pcm8k))

    def handle_tts_done(self, payload: dict[str, Any]) -> None:
        asyncio.create_task(self.asterisk_sink.mark("voqualizer_tts_done"))

    def session_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {**payload, "bearer_token": self.bearer_token}

    def ensure_bearer_token(self) -> None:
        if not self.bearer_token:
            raise RuntimeError("connect before forwarding Asterisk audio")


class JsonLineAudioSocketSink:
    """Tiny sample sink for local harnesses: writes JSON-line audio records."""

    def __init__(self, writer: Any):
        self.writer = writer

    async def write_audio(self, pcm16_8k: bytes) -> None:
        self.writer.write(json.dumps({"event": "audio", "pcm16_8k_hex": pcm16_8k.hex()}).encode() + b"\n")
        await self.writer.drain()

    async def mark(self, name: str) -> None:
        self.writer.write(json.dumps({"event": "mark", "name": name}).encode() + b"\n")
        await self.writer.drain()


__all__ = [
    "VOQUALIZER_HANDLER",
    "INPUT_CODEC",
    "OUTPUT_CODEC",
    "ASTERISK_SAMPLE_RATE",
    "VOQUALIZER_SAMPLE_RATE",
    "FRAME_HEADER_BYTES",
    "resample_pcm16_linear",
    "asterisk_slin8_to_voqualizer_pcm16",
    "voqualizer_pcm16_to_asterisk_slin8",
    "ulaw8_to_voqualizer_pcm16",
    "voqualizer_pcm16_to_ulaw8",
    "encode_voqualizer_frame",
    "AsteriskVoqualizerBridge",
    "JsonLineAudioSocketSink",
]


if __name__ == "__main__":
    print("A0 Voqualizer Asterisk bridge reference module. Wire AudioSocket + Socket.IO transports for production use.")
