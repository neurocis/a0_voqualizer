import asyncio
import os

import pytest

from helpers.tts.base import TTSError, TTSRequest, TTSUnavailableError
from helpers.tts.openai_compatible import (
    DEFAULT_COMPAT_TTS_BASE_URL,
    DEFAULT_COMPAT_TTS_MODEL,
    LocalAITTSProvider,
    OpenAICompatibleTTSProvider,
    normalize_speech_endpoint,
)


def run(coro):
    return asyncio.run(coro)


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
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


def test_normalize_speech_endpoint():
    assert normalize_speech_endpoint() == "http://127.0.0.1:8080/v1/audio/speech"
    assert normalize_speech_endpoint(base_url="http://localai:8080") == "http://localai:8080/v1/audio/speech"
    assert normalize_speech_endpoint(endpoint="http://x/v1/audio/speech/") == "http://x/v1/audio/speech"


def test_localai_defaults_and_alias():
    provider = OpenAICompatibleTTSProvider()

    assert LocalAITTSProvider is OpenAICompatibleTTSProvider
    assert provider.base_url == DEFAULT_COMPAT_TTS_BASE_URL
    assert provider.endpoint == "http://127.0.0.1:8080/v1/audio/speech"
    assert provider.model_name == DEFAULT_COMPAT_TTS_MODEL
    assert provider.require_api_key is False
    caps = provider.capabilities.to_dict()
    assert caps["provider"] == "localai-tts"
    assert caps["streaming"] is True
    assert caps["voices"] == ["alloy"]
    assert "pcm16/24k" in caps["output_codecs"]


def test_endpoint_and_config_parsing_with_nested_options():
    provider = OpenAICompatibleTTSProvider(
        {
            "name": "gateway",
            "type": "localai",
            "base_url": "http://gateway:9000/root",
            "model": "piper-local",
            "voice": "amy",
            "options": {
                "timeout": 7,
                "response_format": "mp3",
                "require_api_key": True,
                "api_key_env": "LOCALAI_TOKEN",
                "voices": ["amy", "bob"],
                "language": "en-US",
                "compatible_extra": {"backend": "piper"},
            },
        },
        api_key="token",
        session_factory=lambda: FakeSession(FakeResponse(body=b"abc")),
    )

    assert provider.endpoint == "http://gateway:9000/root/v1/audio/speech"
    assert provider.model_name == "piper-local"
    assert provider.voice_name == "amy"
    assert provider.timeout == 7.0
    assert provider.require_api_key is True
    assert provider.api_key_env == "LOCALAI_TOKEN"
    assert provider.capabilities.to_dict()["voices"] == ["amy", "bob"]
    payload = provider._build_payload(TTSRequest(text="hi"))
    assert payload["response_format"] == "mp3"
    assert payload["backend"] == "piper"


def test_optional_auth_allows_local_endpoint_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def scenario():
        session = FakeSession(FakeResponse(body=b"local-audio"))
        provider = OpenAICompatibleTTSProvider(session_factory=lambda: session, chunk_size=64)
        chunks = await provider.synthesize(TTSRequest(text="local", utterance_id="utt-local"))
        return session, chunks

    session, chunks = run(scenario())

    assert session.posts
    assert "Authorization" not in session.posts[0][1]["headers"]
    assert chunks[0].data == b"local-audio"
    assert chunks[0].is_final is True
    assert chunks[0].utterance_id == "utt-local"


def test_explicit_auth_header_when_api_key_supplied():
    async def scenario():
        session = FakeSession(FakeResponse(body=b"authed"))
        provider = OpenAICompatibleTTSProvider(api_key="token-123", session_factory=lambda: session)
        chunks = await provider.synthesize(TTSRequest(text="hello", codec="mp3"))
        return session, chunks

    session, chunks = run(scenario())

    assert session.posts[0][1]["headers"]["Authorization"] == "Bearer token-123"
    assert session.posts[0][1]["json"]["response_format"] == "mp3"
    assert chunks[0].codec == "mp3"


def test_require_api_key_missing_is_unavailable(monkeypatch):
    monkeypatch.delenv("LOCALAI_TOKEN", raising=False)

    async def scenario():
        session = FakeSession(FakeResponse(body=b"unused"))
        provider = OpenAICompatibleTTSProvider(
            {"options": {"require_api_key": True, "api_key_env": "LOCALAI_TOKEN"}},
            session_factory=lambda: session,
        )
        with pytest.raises(TTSUnavailableError) as excinfo:
            await provider.synthesize(TTSRequest(text="hello"))
        return excinfo.value.to_dict(), session

    payload, session = run(scenario())

    assert payload["code"] == "TTS_UNAVAILABLE"
    assert payload["details"]["api_key_env"] == "LOCALAI_TOKEN"
    assert payload["details"]["require_api_key"] is True
    assert session.posts == []


def test_successful_fake_http_payload_and_chunk_metadata():
    async def scenario():
        session = FakeSession(FakeResponse(chunks=[b"abcd", b"efgh", b"ij"]))
        provider = OpenAICompatibleTTSProvider(
            {"name": "local", "base_url": "http://local:8080", "model": "piper", "voice": "amy"},
            session_factory=lambda: session,
            chunk_size=4,
        )
        request = TTSRequest(text="say this", utterance_id="utt-compat", voice="amy", codec="pcm16/24k", sample_rate=24000, speed=1.1)
        chunks = await provider.synthesize(request)
        return session, chunks

    session, chunks = run(scenario())

    assert session.posts[0][0] == "http://local:8080/v1/audio/speech"
    assert session.posts[0][1]["json"] == {
        "model": "piper",
        "voice": "amy",
        "input": "say this",
        "response_format": "pcm",
        "speed": 1.1,
    }
    assert b"".join(chunk.data for chunk in chunks) == b"abcdefghij"
    assert [chunk.seq for chunk in chunks] == [0, 1, 2]
    assert chunks[-1].is_final is True
    assert all(chunk.utterance_id == "utt-compat" for chunk in chunks)
    assert all(chunk.codec == "pcm16/24k" for chunk in chunks)
    assert chunks[0].metadata["provider"] == "local"
    assert chunks[0].event_payload()["event"] == "voqualizer_tts_chunk"


def test_async_context_manager_response_and_lifecycle():
    async def scenario():
        session = FakeSession(FakeResponse(body=b"ctx"), context=True)
        provider = OpenAICompatibleTTSProvider(session_factory=lambda: session, chunk_size=64)
        await provider.start()
        assert provider.started is True
        chunks = await provider.synthesize(TTSRequest(text="ctx"))
        await provider.stop()
        return session, provider, chunks

    session, provider, chunks = run(scenario())

    assert session.last_context.entered is True
    assert session.last_context.exited is True
    assert session.closed is True
    assert provider.started is False
    assert chunks[0].data == b"ctx"


def test_http_error_is_json_safe():
    async def scenario():
        session = FakeSession(FakeResponse(status=500, text_body="server exploded"))
        provider = OpenAICompatibleTTSProvider(session_factory=lambda: session)
        with pytest.raises(TTSError) as excinfo:
            await provider.synthesize(TTSRequest(text="hello"))
        return excinfo.value.to_dict()

    payload = run(scenario())

    assert payload["code"] == "TTS_HTTP_ERROR"
    assert payload["recoverable"] is False
    assert payload["details"]["status"] == 500
    assert "server exploded" in payload["details"]["body"]


def test_transport_error_is_json_safe():
    async def scenario():
        session = FakeSession(raises=RuntimeError("connection refused"))
        provider = OpenAICompatibleTTSProvider(session_factory=lambda: session)
        with pytest.raises(TTSError) as excinfo:
            await provider.synthesize(TTSRequest(text="hello"))
        return excinfo.value.to_dict()

    payload = run(scenario())

    assert payload["code"] == "TTS_TRANSPORT_ERROR"
    assert "connection refused" in payload["details"]["error"]


def test_bad_response_and_unsupported_codec_are_json_safe():
    class EmptyResponse:
        status = 200

    async def bad_response():
        provider = OpenAICompatibleTTSProvider(session_factory=lambda: FakeSession(EmptyResponse()))
        with pytest.raises(TTSError) as excinfo:
            await provider.synthesize(TTSRequest(text="hello"))
        return excinfo.value.to_dict()

    async def bad_codec():
        provider = OpenAICompatibleTTSProvider(session_factory=lambda: FakeSession())
        with pytest.raises(TTSError) as excinfo:
            await provider.synthesize(TTSRequest(text="hello", codec="flac"))
        return excinfo.value.to_dict()

    bad_response_payload = run(bad_response())
    bad_codec_payload = run(bad_codec())

    assert bad_response_payload["code"] == "TTS_BAD_RESPONSE"
    assert bad_codec_payload["code"] == "TTS_UNSUPPORTED_CODEC"
    assert bad_codec_payload["details"]["codec"] == "flac"


def test_request_validation_remains_base_contract():
    with pytest.raises(ValueError):
        TTSRequest(text="")
    with pytest.raises(ValueError):
        TTSRequest(text="hello", sample_rate=0)


def test_no_network_determinism_with_fake_session():
    async def scenario():
        session = FakeSession(FakeResponse(body=b"same-local-bytes"))
        provider = OpenAICompatibleTTSProvider(session_factory=lambda: session, chunk_size=64)
        first = await provider.synthesize(TTSRequest(text="same", utterance_id="a"))
        second = await provider.synthesize(TTSRequest(text="same", utterance_id="b"))
        return session, first, second

    session, first, second = run(scenario())

    assert len(session.posts) == 2
    assert [chunk.data for chunk in first] == [chunk.data for chunk in second] == [b"same-local-bytes"]
    assert first[0].utterance_id == "a"
    assert second[0].utterance_id == "b"
