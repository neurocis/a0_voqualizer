import asyncio

import pytest

from helpers.tts.base import (
    AudioChunk,
    MockTTSProvider,
    TTSCapabilities,
    TTSError,
    TTSProvider,
    TTSProviderSpec,
    TTSRequest,
    TTSUnavailableError,
    iter_text_chunks,
)


def run(coro):
    return asyncio.run(coro)


def test_provider_spec_from_config_flattens_options_and_extras():
    spec = TTSProviderSpec.from_config(
        {
            "name": "localai-tts",
            "type": "openai-compatible",
            "endpoint": "http://127.0.0.1:8080/v1/audio/speech",
            "voice": "amy",
            "streaming": True,
            "options": {"temperature": 0.1},
            "custom": "kept",
        }
    )

    assert spec.name == "localai-tts"
    assert spec.type == "openai-compatible"
    assert spec.voice == "amy"
    assert spec.options == {"temperature": 0.1, "custom": "kept"}
    assert spec.to_dict()["endpoint"].endswith("/audio/speech")


def test_capabilities_are_json_safe():
    caps = TTSCapabilities(provider="mock", streaming=True)

    assert caps.to_dict() == {
        "provider": "mock",
        "streaming": True,
        "output_codecs": ["pcm16/16k"],
        "sample_rates": [16000],
        "voices": ["mock"],
        "languages": ["en"],
        "max_text_chars": 4000,
        "supports_cancellation": True,
    }


def test_tts_request_validation_and_dict():
    request = TTSRequest(text="hello", voice="alloy", codec="pcm16/24k", sample_rate=24000, speed=1.2)

    assert request.to_dict()["text"] == "hello"
    assert request.voice == "alloy"

    with pytest.raises(ValueError):
        TTSRequest(text="")
    with pytest.raises(ValueError):
        TTSRequest(text="hello", sample_rate=0)
    with pytest.raises(ValueError):
        TTSRequest(text="hello", speed=0)


def test_audio_chunk_normalization_and_event_payload():
    chunk = AudioChunk(data=bytearray(b"abc"), seq=2, utterance_id="utt", is_final=True)

    assert chunk.data == b"abc"
    assert chunk.event_payload()["event"] == "voqualizer_tts_chunk"
    assert chunk.event_payload()["seq"] == 2
    assert chunk.event_payload()["utterance_id"] == "utt"
    assert chunk.event_payload()["is_final"] is True

    with pytest.raises(ValueError):
        AudioChunk(data=b"x", seq=-1, utterance_id="utt")
    with pytest.raises(TypeError):
        AudioChunk(data="not bytes", seq=0, utterance_id="utt")


def test_error_dict_shapes():
    err = TTSError("bad", code="BAD_TTS", recoverable=False, details={"x": 1})
    unavailable = TTSUnavailableError("missing model")

    assert err.to_dict() == {"code": "BAD_TTS", "message": "bad", "recoverable": False, "details": {"x": 1}}
    assert unavailable.to_dict()["code"] == "TTS_UNAVAILABLE"
    assert unavailable.to_dict()["recoverable"] is True


def test_abstract_provider_enforced():
    with pytest.raises(TypeError):
        TTSProvider()


def test_iter_text_chunks_accepts_string_sync_and_async():
    async def agen():
        yield "a"
        yield ""
        yield "b"

    assert run(iter_text_chunks("hello").__anext__()) == "hello"
    assert run(_collect(iter_text_chunks(["a", "", "b"]))) == ["a", "b"]
    assert run(_collect(iter_text_chunks(agen()))) == ["a", "b"]


async def _collect(iterator):
    return [item async for item in iterator]


def test_mock_provider_capabilities():
    provider = MockTTSProvider({"name": "mock1", "voice": "voice1"})
    caps = provider.capabilities.to_dict()

    assert caps["provider"] == "mock1"
    assert caps["streaming"] is True
    assert "pcm16/16k" in caps["output_codecs"]
    assert "voice1" in caps["voices"]


def test_mock_provider_synthesize_is_deterministic():
    async def scenario():
        provider = MockTTSProvider(chunk_size=5)
        request = TTSRequest(text="Hello deterministic world", utterance_id="utt-1", voice="mock")
        first = await provider.synthesize(request)
        second = await provider.synthesize(request)
        return first, second

    first, second = run(scenario())

    assert [chunk.data for chunk in first] == [chunk.data for chunk in second]
    assert [chunk.seq for chunk in first] == list(range(len(first)))
    assert first[-1].is_final is True
    assert all(chunk.utterance_id == "utt-1" for chunk in first)


def test_mock_provider_text_changes_audio():
    async def scenario():
        provider = MockTTSProvider(chunk_size=64)
        a = await provider.synthesize(TTSRequest(text="alpha", utterance_id="a"))
        b = await provider.synthesize(TTSRequest(text="bravo", utterance_id="b"))
        return b"".join(chunk.data for chunk in a), b"".join(chunk.data for chunk in b)

    a_audio, b_audio = run(scenario())

    assert a_audio != b_audio


def test_mock_provider_streaming_chunks():
    async def scenario():
        provider = MockTTSProvider(chunk_size=4)
        request = TTSRequest(text="stream me", utterance_id="utt-stream", codec="pcm16/24k", sample_rate=24000)
        return [chunk async for chunk in provider.stream(request)]

    chunks = run(scenario())

    assert len(chunks) >= 2
    assert [chunk.seq for chunk in chunks] == list(range(len(chunks)))
    assert chunks[-1].is_final is True
    assert all(chunk.codec == "pcm16/24k" for chunk in chunks)
    assert all(chunk.sample_rate == 24000 for chunk in chunks)
    assert all(chunk.event_payload()["event"] == "voqualizer_tts_chunk" for chunk in chunks)


def test_mock_provider_lifecycle():
    async def scenario():
        provider = MockTTSProvider()
        assert provider.started is False
        await provider.start()
        assert provider.started is True
        await provider.stop()
        assert provider.started is False

    run(scenario())
