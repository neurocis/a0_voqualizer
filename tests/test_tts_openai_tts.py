import asyncio
import os

import pytest

from helpers.tts.base import TTSError, TTSRequest, TTSUnavailableError
from helpers.tts.openai_tts import DEFAULT_OPENAI_TTS_ENDPOINT, DEFAULT_OPENAI_TTS_MODEL, OpenAITTSProvider


def run(coro):
    return asyncio.run(coro)


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks
        self.sizes = []

    async def iter_chunked(self, size):
        self.sizes.append(size)
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield chunk


class FakeResponse:
    def __init__(self, *, status=200, body=b"audio", text_body="", chunks=None):
        self.status = status
        self._body = body
        self._text_body = text_body
        self.content = FakeContent(chunks) if chunks is not None else None

    async def read(self):
        return self._body

    async def text(self):
        return self._text_body


class FakeRequestContext:
    def __init__(self, response):
        self.response = response
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


class FakeSession:
    def __init__(self, response=None, *, context=False, raises=None):
        self.response = response or FakeResponse(body=b"fake-audio")
        self.context = context
        self.raises = raises
        self.posts = []
        self.closed = False
        self.last_context = None

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.raises:
            raise self.raises
        if self.context:
            self.last_context = FakeRequestContext(self.response)
            return self.last_context
        return self.response

    async def close(self):
        self.closed = True


def test_capabilities_and_defaults_are_json_safe():
    provider = OpenAITTSProvider()
    caps = provider.capabilities.to_dict()

    assert provider.endpoint == DEFAULT_OPENAI_TTS_ENDPOINT
    assert provider.model_name == DEFAULT_OPENAI_TTS_MODEL
    assert provider.voice_name == "alloy"
    assert caps["provider"] == "openai-tts"
    assert caps["streaming"] is True
    assert "mp3" in caps["output_codecs"]
    assert "pcm16/24k" in caps["output_codecs"]
    assert "alloy" in caps["voices"]
    assert caps["supports_cancellation"] is True


def test_config_parsing_endpoint_model_voice_timeout_and_format():
    provider = OpenAITTSProvider(
        {
            "name": "hosted",
            "type": "openai",
            "endpoint": "https://example.test/v1/audio/speech",
            "model": "tts-model",
            "voice": "nova",
            "sample_rate": 24000,
            "options": {"timeout": 12.5, "response_format": "mp3", "max_text_chars": 99},
        },
        api_key="sk-test",
        session_factory=lambda: FakeSession(FakeResponse(body=b"abc")),
    )

    assert provider.endpoint == "https://example.test/v1/audio/speech"
    assert provider.model_name == "tts-model"
    assert provider.voice_name == "nova"
    assert provider.timeout == 12.5
    assert provider.capabilities.to_dict()["max_text_chars"] == 99
    assert provider._build_payload(TTSRequest(text="hi"))["response_format"] == "mp3"


def test_missing_api_key_is_unavailable_without_network(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def scenario():
        session = FakeSession(FakeResponse(body=b"should-not-be-used"))
        provider = OpenAITTSProvider(session_factory=lambda: session)
        with pytest.raises(TTSUnavailableError) as excinfo:
            [chunk async for chunk in provider.stream(TTSRequest(text="hello"))]
        return excinfo.value.to_dict(), session

    payload, session = run(scenario())

    assert payload["code"] == "TTS_UNAVAILABLE"
    assert payload["details"]["api_key_env"] == "OPENAI_API_KEY"
    assert session.posts == []


def test_env_api_key_is_supported_and_session_lifecycle(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")

    async def scenario():
        session = FakeSession(FakeResponse(body=b"abcdef"))
        provider = OpenAITTSProvider(session_factory=lambda: session, chunk_size=3)
        await provider.start()
        assert provider.started is True
        chunks = await provider.synthesize(TTSRequest(text="hello", utterance_id="utt-env", voice="echo", codec="mp3"))
        await provider.stop()
        return provider, session, chunks

    provider, session, chunks = run(scenario())

    assert session.closed is True
    assert provider.started is False
    assert session.posts[0][1]["headers"]["Authorization"] == "Bearer sk-env"
    assert [chunk.data for chunk in chunks] == [b"abc", b"def"]
    assert chunks[-1].is_final is True


def test_successful_fake_http_synthesis_payload_and_chunks():
    async def scenario():
        session = FakeSession(FakeResponse(chunks=[b"abcd", b"ef", b"ghij"]))
        provider = OpenAITTSProvider(
            {"name": "openai", "model": "gpt-test", "voice": "shimmer"},
            api_key="sk-test",
            session_factory=lambda: session,
            chunk_size=4,
        )
        request = TTSRequest(
            text="say this",
            utterance_id="utt-openai",
            voice="nova",
            codec="pcm16/24k",
            sample_rate=24000,
            speed=1.25,
            metadata={"instructions": "speak brightly"},
        )
        chunks = await provider.synthesize(request)
        return provider, session, chunks

    provider, session, chunks = run(scenario())

    assert session.posts[0][0] == DEFAULT_OPENAI_TTS_ENDPOINT
    sent = session.posts[0][1]
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["json"] == {
        "model": "gpt-test",
        "voice": "nova",
        "input": "say this",
        "response_format": "pcm",
        "speed": 1.25,
        "instructions": "speak brightly",
    }
    assert provider.last_request["json"]["input"] == "say this"
    assert b"".join(chunk.data for chunk in chunks) == b"abcdefghij"
    assert [chunk.seq for chunk in chunks] == [0, 1, 2]
    assert chunks[-1].is_final is True
    assert all(chunk.utterance_id == "utt-openai" for chunk in chunks)
    assert all(chunk.codec == "pcm16/24k" for chunk in chunks)
    assert all(chunk.sample_rate == 24000 for chunk in chunks)
    assert chunks[0].event_payload()["event"] == "voqualizer_tts_chunk"
    assert chunks[0].metadata["model"] == "gpt-test"


def test_async_context_manager_response_supported():
    async def scenario():
        session = FakeSession(FakeResponse(body=b"ctx-audio"), context=True)
        provider = OpenAITTSProvider(api_key="sk-test", session_factory=lambda: session, chunk_size=64)
        chunks = await provider.synthesize(TTSRequest(text="ctx"))
        return session, chunks

    session, chunks = run(scenario())

    assert session.last_context.entered is True
    assert session.last_context.exited is True
    assert chunks[0].data == b"ctx-audio"
    assert chunks[0].is_final is True


def test_api_http_error_is_json_safe():
    async def scenario():
        session = FakeSession(FakeResponse(status=429, text_body='{"error":"rate"}'))
        provider = OpenAITTSProvider(api_key="sk-test", session_factory=lambda: session)
        with pytest.raises(TTSError) as excinfo:
            await provider.synthesize(TTSRequest(text="hello"))
        return excinfo.value.to_dict()

    payload = run(scenario())

    assert payload["code"] == "TTS_HTTP_ERROR"
    assert payload["recoverable"] is True
    assert payload["details"]["status"] == 429
    assert "rate" in payload["details"]["body"]


def test_transport_failure_is_json_safe():
    async def scenario():
        session = FakeSession(raises=RuntimeError("network down"))
        provider = OpenAITTSProvider(api_key="sk-test", session_factory=lambda: session)
        with pytest.raises(TTSError) as excinfo:
            await provider.synthesize(TTSRequest(text="hello"))
        return excinfo.value.to_dict()

    payload = run(scenario())

    assert payload["code"] == "TTS_TRANSPORT_ERROR"
    assert payload["recoverable"] is True
    assert "network down" in payload["details"]["error"]


def test_bad_response_without_audio_is_json_safe():
    class EmptyResponse:
        status = 200

    async def scenario():
        session = FakeSession(EmptyResponse())
        provider = OpenAITTSProvider(api_key="sk-test", session_factory=lambda: session)
        with pytest.raises(TTSError) as excinfo:
            await provider.synthesize(TTSRequest(text="hello"))
        return excinfo.value.to_dict()

    payload = run(scenario())

    assert payload["code"] == "TTS_BAD_RESPONSE"


def test_unsupported_codec_raises_json_safe_error():
    async def scenario():
        provider = OpenAITTSProvider(api_key="sk-test", session_factory=lambda: FakeSession())
        with pytest.raises(TTSError) as excinfo:
            await provider.synthesize(TTSRequest(text="hello", codec="flac"))
        return excinfo.value.to_dict()

    payload = run(scenario())

    assert payload["code"] == "TTS_UNSUPPORTED_CODEC"
    assert payload["details"]["codec"] == "flac"


def test_request_validation_remains_base_contract():
    with pytest.raises(ValueError):
        TTSRequest(text="")
    with pytest.raises(ValueError):
        TTSRequest(text="hello", speed=0)


def test_no_network_determinism_with_fake_session():
    async def scenario():
        session = FakeSession(FakeResponse(body=b"same-bytes"))
        provider = OpenAITTSProvider(api_key="sk-test", session_factory=lambda: session, chunk_size=64)
        first = await provider.synthesize(TTSRequest(text="same", utterance_id="a"))
        second = await provider.synthesize(TTSRequest(text="same", utterance_id="b"))
        return session, first, second

    session, first, second = run(scenario())

    assert len(session.posts) == 2
    assert [c.data for c in first] == [c.data for c in second] == [b"same-bytes"]
    assert first[0].utterance_id == "a"
    assert second[0].utterance_id == "b"


def test_explicit_wav_format_surfaces_wav_codec():
    from helpers.tts.openai_tts import OpenAITTSProvider
    from helpers.tts.base import TTSRequest
    provider = OpenAITTSProvider({
        "name": "x",
        "type": "openai",
        "api_key_env": "X",
        "format": "wav",
    }, api_key="k", session_factory=lambda: None)
    request = TTSRequest(text="hello", codec="pcm16/16k")
    assert provider._codec_for_response_format("wav", request) == "wav"


def test_explicit_pcm_format_uses_sample_rate_codec():
    from helpers.tts.openai_tts import OpenAITTSProvider
    from helpers.tts.base import TTSRequest
    provider = OpenAITTSProvider({"name": "x", "type": "openai", "api_key_env": "X", "format": "pcm"}, api_key="k", session_factory=lambda: None)
    assert provider._codec_for_response_format("pcm", TTSRequest(text="hello", codec="pcm16/16k", sample_rate=24000)) == "pcm16/24k"
    assert provider._codec_for_response_format("pcm", TTSRequest(text="hello", codec="pcm16/16k", sample_rate=16000)) == "pcm16/16k"
