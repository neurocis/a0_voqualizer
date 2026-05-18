import asyncio
from pathlib import Path

import pytest

from helpers.tts.base import TTSError, TTSRequest, TTSUnavailableError
from helpers.tts.piper_local import PiperLocalTTSProvider, PiperTTSProvider


def run(coro):
    return asyncio.run(coro)


class FakePiper:
    def __init__(self):
        self.calls = []
        self.closed = False

    def synthesize_stream_raw(self, text, **kwargs):
        self.calls.append((text, kwargs))
        # Deliberately returns provider-sized pieces; adapter still chunks them.
        return [f"{text}:".encode(), b"abc", b"defgh"]

    def close(self):
        self.closed = True


class FakeFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return FakePiper()


def test_alias_is_available():
    assert PiperTTSProvider is PiperLocalTTSProvider


def test_capabilities_are_json_safe_and_config_driven():
    provider = PiperLocalTTSProvider(
        {
            "name": "piper-local",
            "type": "piper",
            "voice": "en_US-amy-medium",
            "sample_rate": 22050,
            "options": {"language": "en-US", "max_text_chars": 1234},
        },
        synthesizer=FakePiper(),
    )

    caps = provider.capabilities.to_dict()

    assert caps["provider"] == "piper-local"
    assert caps["streaming"] is True
    assert caps["voices"] == ["en_US-amy-medium"]
    assert caps["sample_rates"] == [22050]
    assert caps["languages"] == ["en-US"]
    assert caps["max_text_chars"] == 1234
    assert "pcm16/16k" in caps["output_codecs"]


def test_start_uses_injected_synthesizer_and_stop_closes_it():
    async def scenario():
        synth = FakePiper()
        provider = PiperLocalTTSProvider(synthesizer=synth)
        assert provider.started is False
        await provider.start()
        assert provider.started is True
        await provider.stop()
        assert provider.started is False
        assert synth.closed is True

    run(scenario())


def test_start_uses_injected_factory_and_existing_model_path(tmp_path):
    async def scenario():
        model = tmp_path / "voice.onnx"
        model.write_bytes(b"fake")
        config = tmp_path / "voice.onnx.json"
        config.write_text("{}")
        factory = FakeFactory()
        provider = PiperLocalTTSProvider(
            {"name": "piper", "type": "piper", "model": str(model), "options": {"config_path": str(config), "speaker": 0}},
            synthesizer_factory=factory,
        )
        await provider.start()
        assert provider.started is True
        assert factory.calls
        args, kwargs = factory.calls[0]
        assert args[0] == str(model)
        assert args[1] == str(config)
        assert kwargs == {"speaker": 0}

    run(scenario())


def test_missing_model_path_is_unavailable_with_json_safe_error():
    async def scenario():
        provider = PiperLocalTTSProvider(synthesizer_factory=FakeFactory())
        with pytest.raises(TTSUnavailableError) as excinfo:
            await provider.start()
        return excinfo.value.to_dict()

    payload = run(scenario())

    assert payload["code"] == "TTS_UNAVAILABLE"
    assert payload["recoverable"] is True
    assert payload["details"]["option"] == "model/model_path/voice_path"


def test_missing_model_file_is_unavailable(tmp_path):
    async def scenario():
        provider = PiperLocalTTSProvider({"model": str(tmp_path / "missing.onnx")}, synthesizer_factory=FakeFactory())
        with pytest.raises(TTSUnavailableError) as excinfo:
            await provider.start()
        return excinfo.value.to_dict()

    payload = run(scenario())

    assert payload["code"] == "TTS_UNAVAILABLE"
    assert payload["details"]["model_path"].endswith("missing.onnx")


def test_no_dependency_path_is_unavailable_without_import_requirement():
    async def scenario():
        provider = PiperLocalTTSProvider({"model": "/tmp/definitely-missing-piper-model.onnx"})
        with pytest.raises(TTSUnavailableError) as excinfo:
            await provider.start()
        return excinfo.value.to_dict()

    payload = run(scenario())

    # Depending on environment, this may fail before or after dependency lookup;
    # either way it must be the JSON-safe unavailable path, not ImportError/etc.
    assert payload["code"] == "TTS_UNAVAILABLE"
    assert payload["recoverable"] is True


def test_stream_success_with_fake_piper_chunks_and_metadata():
    async def scenario():
        synth = FakePiper()
        provider = PiperLocalTTSProvider({"name": "piper", "voice": "amy"}, synthesizer=synth, chunk_size=4)
        request = TTSRequest(text="hello", utterance_id="utt-piper", voice="amy", codec="pcm16/24k", sample_rate=24000, speed=2.0)
        chunks = [chunk async for chunk in provider.stream(request)]
        return synth, chunks

    synth, chunks = run(scenario())

    assert synth.calls[0][0] == "hello"
    assert synth.calls[0][1]["voice"] == "amy"
    assert synth.calls[0][1]["length_scale"] == 0.5
    assert [chunk.seq for chunk in chunks] == list(range(len(chunks)))
    assert chunks[-1].is_final is True
    assert b"".join(chunk.data for chunk in chunks) == b"hello:abcdefgh"
    assert all(chunk.utterance_id == "utt-piper" for chunk in chunks)
    assert all(chunk.codec == "pcm16/24k" for chunk in chunks)
    assert all(chunk.sample_rate == 24000 for chunk in chunks)
    assert all(chunk.metadata["backend"] == "piper" for chunk in chunks)
    assert chunks[0].event_payload()["event"] == "voqualizer_tts_chunk"


def test_synthesize_collector_uses_stream():
    async def scenario():
        provider = PiperLocalTTSProvider(synthesizer=FakePiper(), chunk_size=64)
        return await provider.synthesize(TTSRequest(text="collect", utterance_id="utt-collect"))

    chunks = run(scenario())

    assert len(chunks) == 3
    assert [chunk.seq for chunk in chunks] == [0, 1, 2]
    assert chunks[-1].is_final is True
    assert b"".join(chunk.data for chunk in chunks) == b"collect:abcdefgh"


def test_runner_seam_supports_bytes_iterables_and_auto_start():
    def runner(request, provider):
        assert provider.started is True
        return [b"one", memoryview(b"two")]

    async def scenario():
        provider = PiperLocalTTSProvider(runner=runner, chunk_size=3)
        return [chunk async for chunk in provider.stream(TTSRequest(text="x", utterance_id="utt-runner"))]

    chunks = run(scenario())

    assert [chunk.data for chunk in chunks] == [b"one", b"two"]
    assert chunks[-1].is_final is True


def test_unsupported_codec_raises_json_safe_tts_error():
    async def scenario():
        provider = PiperLocalTTSProvider(synthesizer=FakePiper())
        with pytest.raises(TTSError) as excinfo:
            [chunk async for chunk in provider.stream(TTSRequest(text="hello", codec="mp3"))]
        return excinfo.value.to_dict()

    payload = run(scenario())

    assert payload["code"] == "TTS_UNSUPPORTED_CODEC"
    assert payload["details"]["codec"] == "mp3"


def test_request_validation_remains_base_contract():
    with pytest.raises(ValueError):
        TTSRequest(text="")
    with pytest.raises(ValueError):
        TTSRequest(text="hello", sample_rate=-1)
