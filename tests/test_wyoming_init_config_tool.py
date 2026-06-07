"""W42 CLI config initializer tests."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'wyoming_init_config.py'


def run_tool(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=str(ROOT), text=True, capture_output=True, timeout=10)


def test_cli_tool_exists_and_avoids_legacy_protocol():
    src = TOOL.read_text()
    assert 'init_wyoming_config' in src
    assert '--ctxid' in src
    assert '--overwrite' in src
    for forbidden in ('voqualizer_init', 'voqualizer_user_text', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'ack_fallback'):
        assert forbidden not in src, forbidden


def test_cli_writes_valid_config(tmp_path):
    config = tmp_path / 'wyoming_interfaces.json'
    proc = run_tool('--ctxid', 'ctx-real', '--interface', 'hero smoke', '--config', str(config))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    report = json.loads(proc.stdout)
    assert report['ok'] is True
    assert report['interface_id'] == 'hero-smoke'
    data = json.loads(config.read_text())
    assert data['interfaces'][0]['ctxid'] == 'ctx-real'
    assert data['interfaces'][0]['id'] == 'hero-smoke'


def test_cli_rejects_placeholder_ctxid(tmp_path):
    config = tmp_path / 'wyoming_interfaces.json'
    proc = run_tool('--ctxid', 'REPLACE_WITH_REAL_CTXID', '--config', str(config))
    assert proc.returncode == 2
    report = json.loads(proc.stdout)
    assert report['ok'] is False
    assert 'placeholder ctxid' in report['error']
    assert not config.exists()


def test_cli_refuses_existing_config_without_overwrite(tmp_path):
    config = tmp_path / 'wyoming_interfaces.json'
    first = run_tool('--ctxid', 'ctx-one', '--config', str(config))
    assert first.returncode == 0
    second = run_tool('--ctxid', 'ctx-two', '--config', str(config))
    assert second.returncode == 2
    assert json.loads(second.stdout)['error'] == 'config_exists'
    third = run_tool('--ctxid', 'ctx-two', '--config', str(config), '--overwrite')
    assert third.returncode == 0
    assert json.loads(config.read_text())['interfaces'][0]['ctxid'] == 'ctx-two'
