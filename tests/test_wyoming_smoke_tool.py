"""Tests for W23 Wyoming smoke diagnostic runner."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'wyoming_smoke.py'
FIXTURE = ROOT / 'config' / 'wyoming_interfaces.smoke.example.json'


def test_smoke_tool_exists_and_avoids_legacy_protocol():
    assert TOOL.exists()
    src = TOOL.read_text()
    for forbidden in (
        'voqualizer_init',
        'voqualizer_user_text',
        'voqualizer_audio_chunk',
        'voqualizer_tts_chunk',
        'ack_fallback',
    ):
        assert forbidden not in src, forbidden
    for required in (
        'load_interfaces',
        'live_provider_status',
        'tcp_describe',
        'read_event_from_stream',
        'write_event_to_stream',
        'describe',
    ):
        assert required in src, required


def test_smoke_tool_reports_fixture_without_tcp_probe():
    result = subprocess.run(
        [sys.executable, str(TOOL), '--config', str(FIXTURE)],
        cwd=str(ROOT),
        check=True,
        text=True,
        capture_output=True,
    )
    data = json.loads(result.stdout)
    assert data['configured_interfaces'] == 1
    assert data['enabled_interfaces'] == 1
    assert data['interfaces'][0]['id'] == 'hero-smoke'
    assert data['interfaces'][0]['ctxid'] == 'REPLACE_WITH_REAL_CTXID'
    assert data['live_providers']['mode'] == 'live_providers'
