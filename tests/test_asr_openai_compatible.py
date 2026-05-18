import asyncio
import wave
from io import BytesIO

import numpy as np
import pytest

from helpers.asr.base import ASRUnavailableError, AudioChunk, TranscriptKind
from helpers.asr.openai_compatible import (
    DEFAULT_COMPAT_BASE_URL,
    LocalAIASRProvider,
    OpenAICompatibleASRProvider,
    normalize_transcriptions_endpoint,
)


def sine_pcm16(rate=16000, seconds=0.1):
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    x = 0.2 * np.sin(2 * np.pi * 440 * t)
    return (x * 32767).astype('<i2').tobytes()


class FakeResponse:
    status = 200

    async def json(self):
        return {'text': 'local transcript', 'language': 'en', 'duration': 0.33}

    async def text(self):
        return ''


class FakeRequestContext:
    def __init__(self, response=None):
        self.response = response or FakeResponse()

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self):
        self.posts = []
        self.closed = False

    def post(self, url, *, data=None, headers=None):
        self.posts.append({'url': url, 'data': data, 'headers': dict(headers or {})})
        return FakeRequestContext()

    async def close(self):
        self.closed = True


def test_endpoint_normalization_from_base_url_and_override():
    assert normalize_transcriptions_endpoint(base_url='http://localhost:8080') == 'http://localhost:8080/v1/audio/transcriptions'
    assert normalize_transcriptions_endpoint(base_url='http://localhost:8080/') == 'http://localhost:8080/v1/audio/transcriptions'
    assert normalize_transcriptions_endpoint('http://x/custom') == 'http://x/custom'
    assert normalize_transcriptions_endpoint(base_url=None) == f'{DEFAULT_COMPAT_BASE_URL}/v1/audio/transcriptions'


def test_alias_is_available():
    assert LocalAIASRProvider is OpenAICompatibleASRProvider


def test_capabilities_use_configured_provider_name():
    provider = OpenAICompatibleASRProvider({'name': 'localai', 'type': 'localai', 'base_url': 'http://localai:8080'})
    caps = provider.capabilities().to_dict()
    assert caps['provider'] == 'localai'
    assert caps['streaming'] is False
    assert caps['partials'] is False
    assert caps['finals'] is True
    assert 'pcm16/16k' in caps['input_codecs']


def test_transcribe_default_localai_does_not_require_api_key_or_auth_header(monkeypatch):
    async def run():
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        session = FakeSession()
        provider = OpenAICompatibleASRProvider(session_factory=lambda: session)
        result = await provider.transcribe(AudioChunk(sine_pcm16(), sample_rate=16000), language='en', metadata={'utt': 'u1'})
        event = result.to_protocol_event()
        assert event['event'] == 'voqualizer_asr_final'
        assert event['text'] == 'local transcript'
        assert event['provider'] == 'localai'
        assert event['metadata']['utt'] == 'u1'
        assert session.posts[0]['url'] == f'{DEFAULT_COMPAT_BASE_URL}/v1/audio/transcriptions'
        assert 'Authorization' not in session.posts[0]['headers']
        assert provider.last_request['headers'] == {}

    asyncio.run(run())


def test_transcribe_with_api_key_sends_bearer_header_and_custom_endpoint():
    async def run():
        session = FakeSession()
        provider = OpenAICompatibleASRProvider(
            {'name': 'compat', 'type': 'openai-compatible', 'endpoint': 'http://gw/v1/audio/transcriptions', 'model': 'whisper-large'},
            api_key='local-secret',
            session_factory=lambda: session,
        )
        result = await provider.transcribe(sine_pcm16(), language='en')
        assert result.provider == 'compat'
        assert session.posts[0]['url'] == 'http://gw/v1/audio/transcriptions'
        assert session.posts[0]['headers']['Authorization'] == 'Bearer local-secret'
        fields = provider.last_request['fields']
        assert fields['model'] == 'whisper-large'
        with wave.open(BytesIO(fields['file']['bytes']), 'rb') as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1

    asyncio.run(run())


def test_require_api_key_preserves_graceful_unavailable(monkeypatch):
    async def run():
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        provider = OpenAICompatibleASRProvider({'name': 'secure-local', 'type': 'localai', 'require_api_key': True}, session_factory=FakeSession)
        with pytest.raises(ASRUnavailableError) as exc:
            await provider.transcribe(sine_pcm16())
        assert exc.value.code == 'ASR_UNAVAILABLE'
        assert exc.value.details['provider'] == 'secure-local'

    asyncio.run(run())


def test_base_url_from_options_and_env_name_are_preserved():
    provider = OpenAICompatibleASRProvider({
        'name': 'compat',
        'type': 'localai',
        'model': 'custom-asr',
        'options': {'base_url': 'http://box:1234', 'api_key_env': 'LOCALAI_API_KEY'},
        'api_key_env': 'LOCALAI_API_KEY',
    })
    assert provider.endpoint == 'http://box:1234/v1/audio/transcriptions'
    assert provider.base_url == 'http://box:1234'
    assert provider.model_name == 'custom-asr'
    assert provider.api_key_env == 'LOCALAI_API_KEY'


def test_stream_emits_partials_then_final_for_localai():
    async def run():
        provider = OpenAICompatibleASRProvider(api_key=None, session_factory=FakeSession)
        chunks = [
            AudioChunk(sine_pcm16(seconds=0.02), seq=1, ts_ms=20),
            AudioChunk(sine_pcm16(seconds=0.02), seq=2, ts_ms=40, is_final=True),
            AudioChunk(sine_pcm16(seconds=0.02), seq=3, ts_ms=60),
        ]
        events = [evt async for evt in provider.stream(chunks, language='en', metadata={'stream': True})]
        assert [evt.kind for evt in events] == [TranscriptKind.PARTIAL, TranscriptKind.PARTIAL, TranscriptKind.FINAL]
        assert events[0].to_protocol_event()['event'] == 'voqualizer_asr_partial'
        assert events[2].text == 'local transcript'
        assert events[2].metadata['stream'] is True

    asyncio.run(run())
