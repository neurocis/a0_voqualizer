"""Tests for W20 live provider binding."""
import asyncio
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'helpers' / 'wyoming_live_providers.py'


def test_live_providers_module_exists():
    assert LIVE.exists()


def test_live_providers_uses_only_wyoming_protocol():
    src = LIVE.read_text()
    for forbidden in (
        'voqualizer_init',
        'voqualizer_user_text',
        'voqualizer_audio_chunk',
        'voqualizer_tts_chunk',
        'ack_fallback',
    ):
        assert forbidden not in src, forbidden
    for required in (
        'build_live_asr_factory',
        'build_live_tts_factory',
        'bind_live_providers_to_runtime',
        'live_provider_status',
        'WyomingVoqualizerPipeline',
        'WyomingInterfaceRuntime',
    ):
        assert required in src, required


def test_factories_return_callables_with_mock_fallback():
    lp = importlib.import_module('helpers.wyoming_live_providers')
    asr = lp.build_live_asr_factory({})
    tts = lp.build_live_tts_factory({})
    assert callable(asr)
    assert callable(tts)
    # Both factories should always return *something* (mock fallback) without raising.
    asr_provider = asr()
    tts_provider = tts()
    assert asr_provider is not None
    assert tts_provider is not None


def test_bind_live_providers_to_runtime_registers_handlers():
    lp = importlib.import_module('helpers.wyoming_live_providers')
    wi = importlib.import_module('helpers.wyoming_interfaces')
    interface = wi.load_interfaces([{'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'bind_port': 10701}])[0]
    runtime = lp.bind_live_providers_to_runtime(interface, cfg={})
    # Pipeline.install_into should register handlers for the Wyoming event types
    for ev in ('audio-start', 'audio-chunk', 'audio-stop', 'transcript', 'voqualizer-text-prompt', 'synthesize'):
        assert ev in runtime.handlers, ev
    assert runtime.interface.ctxid == 'ctx-hero'


def test_live_provider_status_reports_modes():
    lp = importlib.import_module('helpers.wyoming_live_providers')
    status = lp.live_provider_status({})
    assert status['mode'] == 'live_providers'
    assert 'asr' in status and 'tts' in status


def test_default_prompt_submitter_echoes_with_ctxid():
    lp = importlib.import_module('helpers.wyoming_live_providers')
    result = asyncio.run(lp._default_prompt_submitter('hi', {'ctxid': 'ctx-hero'}))
    assert 'ctx-hero' in result
    assert 'hi' in result
