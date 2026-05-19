import asyncio
import wave
from io import BytesIO

import numpy as np
import pytest

from helpers.asr.base import ASRError, ASRUnavailableError, AudioChunk, TranscriptKind
from helpers.asr.openai_whisper import DEFAULT_OPENAI_TRANSCRIPTIONS_ENDPOINT, OpenAIWhisperASRProvider


def sine_pcm16(rate=16000, seconds=0.1):
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    x = 0.2 * np.sin(2 * np.pi * 440 * t)
    return (x * 32767).astype('<i2').tobytes()


class FakeResponse:
    def __init__(self, payload=None, status=200, text=''):
        self._payload = payload if payload is not None else {'text': 'hello from api', 'language': 'en', 'duration': 0.5}
        self.status = status
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class FakeRequestContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.posts = []
        self.closed = False

    def post(self, url, *, data=None, headers=None):
        self.posts.append({'url': url, 'data': data, 'headers': dict(headers or {})})
        return FakeRequestContext(self.response)

    async def close(self):
        self.closed = True


def test_capabilities_advertise_openai_whisper_contract():
    provider = OpenAIWhisperASRProvider(api_key='sk-test', session_factory=FakeSession)
    caps = provider.capabilities().to_dict()
    assert caps['provider'] == 'openai-whisper'
    assert caps['streaming'] is False
    assert caps['partials'] is False
    assert caps['finals'] is True
    assert 'pcm16/16k' in caps['input_codecs']
    assert 16000 in caps['input_sample_rates']


def test_missing_api_key_is_graceful(monkeypatch):
    async def run():
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        provider = OpenAIWhisperASRProvider(session_factory=FakeSession)
        with pytest.raises(ASRUnavailableError) as exc:
            await provider.transcribe(sine_pcm16())
        assert exc.value.code == 'ASR_UNAVAILABLE'
        assert exc.value.details['api_key_env'] == 'OPENAI_API_KEY'

    asyncio.run(run())


def test_start_and_close_own_injected_session():
    session = FakeSession()

    async def run():
        provider = OpenAIWhisperASRProvider(api_key='sk-test', session_factory=lambda: session)
        await provider.start()
        assert provider.is_started is True
        await provider.close()
        assert provider.is_started is False
        assert session.closed is True

    asyncio.run(run())


def test_transcribe_posts_auth_and_chunked_multipart_form_metadata():
    session = FakeSession(FakeResponse({'text': 'mock transcript', 'language': 'en', 'duration': 0.25}))

    async def run():
        provider = OpenAIWhisperASRProvider(
            {'name': 'openai-whisper', 'type': 'openai', 'model': 'whisper-1', 'api_key_env': 'OPENAI_API_KEY'},
            api_key='sk-unit',
            session_factory=lambda: session,
        )
        result = await provider.transcribe(AudioChunk(sine_pcm16(), sample_rate=16000), language='en', metadata={'prompt': 'domain words', 'utt': 'u1'})
        event = result.to_protocol_event()
        assert event['event'] == 'voqualizer_asr_final'
        assert event['text'] == 'mock transcript'
        assert event['lang'] == 'en'
        assert event['provider'] == 'openai-whisper'
        assert event['metadata']['utt'] == 'u1'
        assert event['metadata']['model'] == 'whisper-1'
        assert event['metadata']['endpoint'] == DEFAULT_OPENAI_TRANSCRIPTIONS_ENDPOINT
        assert event['metadata']['duration'] == 0.25
        assert session.posts[0]['url'] == DEFAULT_OPENAI_TRANSCRIPTIONS_ENDPOINT
        assert session.posts[0]['headers']['Authorization'] == 'Bearer sk-unit'
        assert provider.last_request is not None
        fields = provider.last_request['fields']
        assert fields['model'] == 'whisper-1'
        assert fields['language'] == 'en'
        assert fields['prompt'] == 'domain words'
        assert fields['file']['filename'] == 'audio.wav'
        assert fields['file']['content_type'] == 'audio/wav'
        with wave.open(BytesIO(fields['file']['bytes']), 'rb') as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == 16000
            assert wav.getnframes() == 1600

    asyncio.run(run())


def test_transcribe_resamples_8k_audio_to_16k_wav_upload():
    session = FakeSession()

    async def run():
        provider = OpenAIWhisperASRProvider(api_key='sk-unit', session_factory=lambda: session)
        await provider.transcribe(AudioChunk(sine_pcm16(rate=8000, seconds=0.1), sample_rate=8000))
        fields = provider.last_request['fields']
        with wave.open(BytesIO(fields['file']['bytes']), 'rb') as wav:
            assert wav.getframerate() == 16000
            assert wav.getnframes() == 1600

    asyncio.run(run())


def test_auto_language_omits_language_field():
    async def run():
        provider = OpenAIWhisperASRProvider({'name': 'openai', 'type': 'openai', 'language': 'auto'}, api_key='sk-unit', session_factory=FakeSession)
        await provider.transcribe(sine_pcm16())
        assert 'language' not in provider.last_request['fields']

    asyncio.run(run())


def test_http_error_maps_to_asr_error():
    session = FakeSession(FakeResponse(status=401, text='unauthorized'))

    async def run():
        provider = OpenAIWhisperASRProvider(api_key='sk-unit', session_factory=lambda: session)
        with pytest.raises(ASRError) as exc:
            await provider.transcribe(sine_pcm16())
        assert exc.value.code == 'ASR_HTTP_ERROR'
        assert exc.value.recoverable is True
        assert exc.value.details['status'] == 401
        assert 'unauthorized' in exc.value.details['body']

    asyncio.run(run())


def test_bad_audio_sequence_maps_to_bad_audio_error():
    async def run():
        provider = OpenAIWhisperASRProvider(api_key='sk-unit', session_factory=FakeSession)
        with pytest.raises(ASRError) as exc:
            await provider.transcribe([object()])  # type: ignore[list-item]
        assert exc.value.code == 'BAD_ASR_AUDIO'

    asyncio.run(run())


def test_stream_emits_partials_then_final_and_stops_on_final_chunk():
    async def run():
        provider = OpenAIWhisperASRProvider(api_key='sk-unit', session_factory=FakeSession)
        chunks = [
            AudioChunk(sine_pcm16(seconds=0.02), seq=1, ts_ms=20),
            AudioChunk(sine_pcm16(seconds=0.02), seq=2, ts_ms=40, is_final=True),
            AudioChunk(sine_pcm16(seconds=0.02), seq=3, ts_ms=60),
        ]
        events = [evt async for evt in provider.stream(chunks, language='en', metadata={'stream': True})]
        assert [evt.kind for evt in events] == [TranscriptKind.PARTIAL, TranscriptKind.PARTIAL, TranscriptKind.FINAL]
        assert events[0].to_protocol_event()['event'] == 'voqualizer_asr_partial'
        assert events[0].metadata == {'seq': 1, 'buffered_chunks': 1}
        assert events[1].metadata == {'seq': 2, 'buffered_chunks': 2}
        assert events[2].text == 'hello from api'
        assert events[2].metadata['stream'] is True

    asyncio.run(run())


def test_openai_whisper_source_includes_quality_options():
    from pathlib import Path
    src = Path('/a0/usr/plugins/a0_voqualizer/helpers/asr/openai_whisper.py').read_text()
    assert 'asr_options' in src
    assert 'no_speech_threshold' in src
    assert 'temperature' in src
    assert 'suppress_tokens' in src
