"""Tests for shared Wyoming smoke diagnostics (W32)."""
import asyncio
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'helpers' / 'wyoming_smoke_diagnostics.py'
API = ROOT / 'api' / 'wyoming_status.py'
FIXTURE = ROOT / 'config' / 'wyoming_interfaces.smoke.example.json'


def test_smoke_diagnostics_helper_exists_and_avoids_legacy_protocol():
    src = HELPER.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_user_text', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'ack_fallback'):
        assert forbidden not in src, forbidden
    for required in ('smoke_report', 'interface_report', 'tcp_describe', 'read_event_from_stream', 'write_event_to_stream'):
        assert required in src, required


def test_smoke_report_loads_fixture_without_tcp():
    mod = importlib.import_module('helpers.wyoming_smoke_diagnostics')
    report = asyncio.run(mod.smoke_report(FIXTURE))
    assert report['ok'] is True
    assert report['configured_interfaces'] == 1
    assert report['interfaces'][0]['id'] == 'hero-smoke'
    assert report['interfaces'][0]['ctxid'] == 'REPLACE_WITH_REAL_CTXID'
    assert report['live_providers']['mode'] == 'live_providers'


def test_wyoming_status_endpoint_exposes_smoke_action():
    src = API.read_text()
    assert 'action == "smoke"' in src
    assert 'smoke_report' in src
    assert 'tcp_describe' in src
    assert 'interface_id' in src
