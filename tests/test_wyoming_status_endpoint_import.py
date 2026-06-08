"""Regression: api/wyoming_status.py must import the framework ApiHandler from
helpers.api (not the non-existent `python.helpers.api`).

The wrong import caused every request to /api/plugins/a0_voqualizer/wyoming_status
to return HTTP 500, which broke the DOM-only ASR/TTS toggle: the legacy and new
DOM extensions could not probe `action=dom_integration`, fell through their
defensive try/catch, and rendered as if the toggle were on.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / 'api' / 'wyoming_status.py'


def test_wyoming_status_uses_real_framework_api_import():
    src = HANDLER.read_text()
    assert 'from helpers.api import ApiHandler' in src
    assert 'from python.helpers.api' not in src
