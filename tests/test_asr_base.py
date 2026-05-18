import asyncio

import pytest

from helpers.asr.base import (
    ASRCapabilities,
    ASRError,
    ASRProvider,
    ASRProviderSpec,
    AudioChunk,
    MockASRProvider,
    TranscriptKind,
    TranscriptResult,
    iter_audio_chunks,
)


def pcm16_silence(samples=160):
    return b"\x00\x00" * samples


def test_provider_spec_from_config_preserves_known_and_option_fields():
    spec = ASRProviderSpec.from_config(
        {
            "name": "whisper-local",
            "type": "whisper",
            "model": "large-v3",
            "language": "auto",
            "streaming": True,
            "vad": True,
        }
    )
    assert spec.name == "whisper-local"
    assert spec.type == "whisper"
    assert spec.model == "large-v3"
    assert spec.streaming is True
    assert spec.options == {"vad": True}
    assert spec.to_dict()["vad"] is True


@pytest.mark.parametrize("bad", [None, {}, {"name": "x"}, {"type": "mock"}, {"name": "", "type": "mock"}])
def test_provider_spec_rejects_bad_config(bad):
    with pytest.raises(ASRError) as exc:
        ASRProviderSpec.from_config(bad)  # type: ignore[arg-type]
    assert exc.value.code == "BAD_ASR_CONFIG"
    assert exc.value.recoverable is False


def test_capabilities_are_json_safe():
    caps = ASRCapabilities(
        provider="mock",
        streaming=True,
        languages=("auto", "en"),
        input_sample_rates=(8000, 16000),
        input_codecs=("pcm16/8k", "pcm16/16k"),
        partials=True,
    )
    assert caps.to_dict() == {
        "provider": "mock",
        "streaming": True,
        "languages": ["auto", "en"],
        "input_sample_rates": [8000, 16000],
        "input_codecs": ["pcm16/8k", "pcm16/16k"],
        "partials": True,
        "finals": True,
        "word_timestamps": False,
        "confidence": True,
    }


def test_audio_chunk_normalizes_bytes_like_inputs():
    source = bytearray(pcm16_silence(4))
    chunk = AudioChunk(source, sample_rate=16000, seq=7, ts_ms=20)
    source[:] = b"\xff" * len(source)
    assert chunk.pcm16 == pcm16_silence(4)
    assert chunk.seq == 7
    assert chunk.ts_ms == 20


@pytest.mark.parametrize("payload", [b"\x00", object()])
def test_audio_chunk_rejects_invalid_payload(payload):
    with pytest.raises((TypeError, ValueError)):
        AudioChunk(payload)  # type: ignore[arg-type]


def test_transcript_result_maps_to_partial_protocol_event():
    result = TranscriptResult(
        text="hel",
        kind=TranscriptKind.PARTIAL,
        confidence=0.4,
        t_start=0.0,
        t_end=0.2,
        provider="mock",
        metadata={"seq": 1},
    )
    assert result.is_partial
    assert result.to_protocol_event() == {
        "event": "voqualizer_asr_partial",
        "text": "hel",
        "conf": 0.4,
        "t_start": 0.0,
        "t_end": 0.2,
        "provider": "mock",
        "metadata": {"seq": 1},
    }


def test_transcript_result_maps_to_final_protocol_event_with_language():
    result = TranscriptResult(text="hello", kind="final", confidence=0.9, language="en")
    assert result.is_final
    event = result.to_protocol_event()
    assert event["event"] == "voqualizer_asr_final"
    assert event["text"] == "hello"
    assert event["conf"] == 0.9
    assert event["lang"] == "en"


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_transcript_result_rejects_invalid_confidence(confidence):
    with pytest.raises(ValueError):
        TranscriptResult(text="x", confidence=confidence)


def test_asr_error_to_dict_is_json_safe():
    err = ASRError("boom", code="NOPE", recoverable=False, details={"x": 1})
    assert err.to_dict() == {
        "code": "NOPE",
        "message": "boom",
        "recoverable": False,
        "details": {"x": 1},
    }


def test_base_provider_is_abstract():
    with pytest.raises(TypeError):
        ASRProvider({"name": "x", "type": "mock"})  # type: ignore[abstract]


def test_iter_audio_chunks_accepts_sync_iterables():
    async def run():
        chunks = [AudioChunk(pcm16_silence(2), seq=1), AudioChunk(pcm16_silence(2), seq=2)]
        got = []
        async for chunk in iter_audio_chunks(chunks):
            got.append(chunk.seq)
        assert got == [1, 2]

    asyncio.run(run())


def test_iter_audio_chunks_accepts_async_iterables():
    async def run():
        async def gen():
            yield AudioChunk(pcm16_silence(2), seq=3)
            yield AudioChunk(pcm16_silence(2), seq=4)

        got = []
        async for chunk in iter_audio_chunks(gen()):
            got.append(chunk.seq)
        assert got == [3, 4]

    asyncio.run(run())


def test_iter_audio_chunks_rejects_wrong_types():
    async def run():
        with pytest.raises(TypeError):
            async for _ in iter_audio_chunks([object()]):
                pass

    asyncio.run(run())


def test_mock_provider_transcribe_returns_final_result():
    async def run():
        provider = MockASRProvider(final_text="hello world")
        await provider.start()
        result = await provider.transcribe(AudioChunk(pcm16_silence(16000), sample_rate=16000), language="en")
        await provider.close()

        assert provider.started is True
        assert provider.closed is True
        assert result.to_protocol_event()["event"] == "voqualizer_asr_final"
        assert result.text == "hello world"
        assert result.language == "en"
        assert result.t_end == 1.0
        assert result.provider == "mock-asr"

    asyncio.run(run())


def test_mock_provider_stream_emits_partials_then_final_for_sync_chunks():
    async def run():
        provider = MockASRProvider(final_text="done")
        chunks = [
            AudioChunk(pcm16_silence(160), seq=1, ts_ms=10),
            AudioChunk(pcm16_silence(160), seq=2, ts_ms=20),
        ]
        events = [item async for item in provider.stream(chunks, language="en", metadata={"utterance": "u1"})]

        assert [e.kind for e in events] == [TranscriptKind.PARTIAL, TranscriptKind.PARTIAL, TranscriptKind.FINAL]
        assert [e.to_protocol_event()["event"] for e in events] == [
            "voqualizer_asr_partial",
            "voqualizer_asr_partial",
            "voqualizer_asr_final",
        ]
        assert events[0].text == "partial 1"
        assert events[1].metadata == {"seq": 2}
        assert events[2].text == "done"
        assert events[2].metadata == {"utterance": "u1"}
        assert provider.feed_count == 2

    asyncio.run(run())


def test_mock_provider_stream_accepts_async_chunks():
    async def run():
        async def gen():
            yield AudioChunk(pcm16_silence(4), seq=9)

        provider = MockASRProvider()
        events = [item async for item in provider.stream(gen())]
        assert len(events) == 2
        assert events[0].is_partial
        assert events[1].is_final

    asyncio.run(run())


def test_mock_provider_capabilities_advertise_streaming_and_protocol_codecs():
    caps = MockASRProvider().capabilities().to_dict()
    assert caps["provider"] == "mock-asr"
    assert caps["streaming"] is True
    assert caps["partials"] is True
    assert "pcm16/16k" in caps["input_codecs"]
    assert 16000 in caps["input_sample_rates"]
