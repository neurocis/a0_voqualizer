from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class WordTiming:
    word_index: int
    word: str
    char_start: int
    char_end: int
    start_ms: int
    end_ms: int
    source: str = "estimated"
    confidence: float = 0.6

    def to_dict(self) -> dict[str, Any]:
        return {
            "word_index": self.word_index,
            "word": self.word,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "source": self.source,
            "confidence": self.confidence,
        }


def estimate_audio_duration_ms(*, chunks: int = 0, total_bytes: int = 0, codec: str = "", sample_rate: int = 0) -> int:
    """Estimate synthesized audio duration for a TTS utterance.

    PCM16 duration can be computed exactly from bytes. Encoded formats keep a
    conservative speech-rate fallback because we do not decode MP3/Opus here.
    """

    codec = str(codec or "").lower()
    sample_rate = int(sample_rate or 0)
    if total_bytes > 0 and sample_rate > 0 and codec.startswith("pcm16/"):
        return max(1, round((total_bytes / 2) / sample_rate * 1000))
    # Fallback only from audio metadata is intentionally conservative. The
    # caller should use estimate_word_plan(text=...) when text is available.
    return max(0, int(chunks or 0) * 250)


def _word_weight(word: str) -> float:
    cleaned = word.strip()
    alpha = sum(1 for ch in cleaned if ch.isalnum())
    punctuation_pause = 0.0
    if cleaned.endswith(('.', '!', '?')):
        punctuation_pause += 2.0
    elif cleaned.endswith((',', ';', ':')):
        punctuation_pause += 1.0
    return max(1.0, alpha * 0.85 + punctuation_pause)


def estimate_word_timings(text: str, *, duration_ms: int | None = None, source: str = "estimated", confidence: float = 0.6) -> list[WordTiming]:
    """Return deterministic word timings with char offsets.

    If duration is unknown, use a readable speech-rate estimate. Timings are
    weighted by word length with small punctuation pauses so highlighting feels
    natural enough until provider-native timestamps are available.
    """

    text = str(text or "")
    matches = list(_WORD_RE.finditer(text))
    if not matches:
        return []
    if duration_ms is None or duration_ms <= 0:
        # Approx. 165 words/minute plus punctuation-weighted pauses.
        duration_ms = max(350, int(len(matches) * 365))
    weights = [_word_weight(m.group(0)) for m in matches]
    total_weight = sum(weights) or float(len(matches))
    timings: list[WordTiming] = []
    cursor = 0
    for idx, (match, weight) in enumerate(zip(matches, weights, strict=True)):
        if idx == len(matches) - 1:
            end = int(duration_ms)
        else:
            end = max(cursor + 1, round((sum(weights[: idx + 1]) / total_weight) * duration_ms))
        timings.append(WordTiming(
            word_index=idx,
            word=match.group(0),
            char_start=match.start(),
            char_end=match.end(),
            start_ms=int(cursor),
            end_ms=int(end),
            source=source,
            confidence=float(confidence),
        ))
        cursor = int(end)
    return timings


def build_word_plan_payload(
    *,
    session_id: str,
    utterance_id: str,
    text: str,
    context_id: str = "",
    message_id: str = "",
    stream_id: str = "",
    codec: str = "",
    sample_rate: int = 0,
    duration_ms: int | None = None,
    chunks: int = 0,
    total_bytes: int = 0,
    source: str = "estimated",
    confidence: float = 0.6,
) -> dict[str, Any]:
    if duration_ms is None or duration_ms <= 0:
        audio_duration = estimate_audio_duration_ms(chunks=chunks, total_bytes=total_bytes, codec=codec, sample_rate=sample_rate)
        duration_ms = audio_duration if audio_duration > 0 else None
    words = estimate_word_timings(text, duration_ms=duration_ms, source=source, confidence=confidence)
    final_duration = int(duration_ms or (words[-1].end_ms if words else 0))
    return {
        "event": "voqualizer_tts_word_plan",
        "session_id": session_id,
        "context_id": context_id,
        "message_id": message_id,
        "stream_id": stream_id,
        "utterance_id": utterance_id,
        "text": str(text or ""),
        "codec": codec,
        "sample_rate": int(sample_rate or 0),
        "duration_ms": final_duration,
        "source": source,
        "confidence": confidence,
        "words": [word.to_dict() for word in words],
        "server_time": int(time.time() * 1000),
    }
