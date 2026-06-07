"""W45 live checklist CLI tests."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'wyoming_live_checklist.py'
FIXTURE = ROOT / 'config' / 'wyoming_interfaces.smoke.example.json'


def run_tool(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=str(ROOT), text=True, capture_output=True, timeout=10)


def test_live_checklist_tool_exists_and_avoids_legacy_protocol():
    src = TOOL.read_text()
    for marker in ('run_checklist', '--tcp-describe', 'real_ctxid_configured', 'tcp_describe_info'):
        assert marker in src, marker
    for forbidden in ('voqualizer_init', 'voqualizer_user_text', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'ack_fallback'):
        assert forbidden not in src, forbidden


def test_live_checklist_reports_placeholder_ctxid_as_failed_step():
    proc = run_tool('--config', str(FIXTURE))
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    steps = {step['name']: step for step in data['steps']}
    assert steps['config_load']['ok'] is True
    assert steps['enabled_interface_present']['ok'] is True
    assert steps['real_ctxid_configured']['ok'] is False
    assert steps['tcp_describe_info']['skipped'] is True


def test_live_checklist_passes_non_tcp_steps_for_real_ctxid(tmp_path):
    config = tmp_path / 'wyoming_interfaces.json'
    config.write_text(json.dumps({'interfaces': [{
        'id': 'hero',
        'name': 'Hero',
        'ctxid': 'ctx-real',
        'enabled': True,
        'bind_host': '127.0.0.1',
        'bind_port': 10701,
    }]}))
    proc = run_tool('--config', str(config), '--interface', 'hero')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    steps = {step['name']: step for step in data['steps']}
    assert steps['real_ctxid_configured']['ok'] is True
    assert steps['tcp_describe_info']['ok'] is None
    assert data['next_actions']
