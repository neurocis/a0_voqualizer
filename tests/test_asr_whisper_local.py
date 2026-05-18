import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from helpers.asr.base import ASRError, ASRUnavailableError, AudioChunk, TranscriptKind
from helpers.asr.whisper_local import FasterWhisperASRProvider, WhisperLocalASRProvider


def sine_pcm16(rate=16000, seconds=0.1):
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    x = 0.2 * np.sin(2 * np.pi * 440 * t)
    return (x * 32767).astype('<i2').tobytes()


class FakeSegment:
    def __init__(self, text, start, end, avg_logprob=-0.1):
        self.text = text
        self.start = start
        self.end = end
        self.avg_logprob = avg_logprob


class FakeWhisperModel:
    def __init__(self, name='fake-model', **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.calls = []

    def transcribe(self, samples, **options):
        self.calls.append((samples.copy(), dict(options)))
        assert samples.dtype == np.float32
        assert samples.ndim == 1
        assert np.max(np.abs(samples)) <= 1.0 if samples.size else True
        return [FakeSegment(' hello ', 0.0, 0.4), {'text': 'world', 'start': 0.4, 'end': 0.8, 'no_speech_prob': 0.05}], SimpleNamespace(language='en')


def test_alias_is_available():
    assert WhisperLocalASRProvider is FasterWhisperASRProvider


def test_capabilities_advertise_local_whisper_contract():
    provider = FasterWhisperASRProvider(model=FakeWhisperModel())
    caps = provider.capabilities().to_dict()
    assert caps['provider'] == 'whisper-local'
    assert caps['streaming'] is True
    assert caps['partials'] is True
    assert caps['finals'] is True
    assert 'pcm16/16k' in caps['input_codecs']
    assert 16000 in caps['input_sample_rates']
    assert 'en' in caps['languages']


def test_start_uses_injected_model_factory_and_options():
    made = []

    def factory(model_name, **kwargs):
        made.append((model_name, kwargs))
        return FakeWhisperModel(model_name, **kwargs)

    async def run():
        provider = FasterWhisperASRProvider(
            {'name': 'local', 'type': 'whisper', 'model': 'tiny', 'streaming': True, 'device': 'cpu', 'compute_type': 'int8'},
            model_factory=factory,
        )
        await provider.start()
        assert provider.is_started is True
        assert made == [('tiny', {'device': 'cpu', 'compute_type': 'int8'})]
        await provider.close()
        assert provider.is_started is False

    asyncio.run(run())


def test_start_raises_unavailable_when_dependency_missing_and_no_factory(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'faster_whisper':
            raise ImportError('no faster_whisper')
        return real_import(name, *args, **kwargs)

    async def run():
        monkeypatch.setattr(builtins, '__import__', fake_import)
        provider = FasterWhisperASRProvider()
        with pytest.raises(ASRUnavailableError) as exc:
            await provider.start()
        assert exc.value.code == 'ASR_UNAVAILABLE'
        assert exc.value.details['dependency'] == 'faster_whisper'

    asyncio.run(run())


def test_transcribe_with_injected_model_returns_final_protocol_result():
    async def run():
        model = FakeWhisperModel()
        provider = FasterWhisperASRProvider(model=model)
        result = await provider.transcribe(AudioChunk(sine_pcm16(), sample_rate=16000), language='en', metadata={'utt': 'u1'})
        event = result.to_protocol_event()
        assert event['event'] == 'voqualizer_asr_final'
        assert event['text'] == 'hello world'
        assert event['lang'] == 'en'
        assert event['provider'] == 'whisper-local'
        assert event['metadata']['utt'] == 'u1'
        assert event['metadata']['model'] == 'large-v3'
        assert event['metadata']['segments'] == 2
        assert result.t_start == 0.0
        assert result.t_end == 0.8
        assert result.confidence is not None and 0.0 <= result.confidence <= 1.0
        assert model.calls[0][1]['language'] == 'en'
        assert model.calls[0][1]['vad_filter'] is True

    asyncio.run(run())


def test_transcribe_resamples_audio_chunks_to_16k():
    async def run():
        model = FakeWhisperModel()
        provider = FasterWhisperASRProvider(model=model)
        chunk = AudioChunk(sine_pcm16(rate=8000, seconds=0.1), sample_rate=8000)
        result = await provider.transcribe(chunk)
        samples = model.calls[0][0]
        assert len(samples) == 1600  # 0.1s at 16 kHz
        assert result.text == 'hello world'

    asyncio.run(run())


def test_transcribe_rejects_bad_audio_sequence():
    async def run():
        provider = FasterWhisperASRProvider(model=FakeWhisperModel())
        with pytest.raises(ASRError) as exc:
            await provider.transcribe([object()])  # type: ignore[list-item]
        assert exc.value.code == 'BAD_ASR_AUDIO'

    asyncio.run(run())


def test_stream_emits_partials_then_final_and_stops_on_final_chunk():
    async def run():
        provider = FasterWhisperASRProvider(model=FakeWhisperModel())
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
        assert events[2].text == 'hello world'
        assert events[2].metadata['stream'] is True

    asyncio.run(run())


def test_auto_language_passes_none_to_faster_whisper():
    async def run():
        model = FakeWhisperModel()
        provider = FasterWhisperASRProvider({'name': 'local', 'type': 'whisper', 'language': 'auto'}, model=model)
        await provider.transcribe(sine_pcm16())
        assert model.calls[0][1]['language'] is None

    asyncio.run(run())
