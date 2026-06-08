"""W54 live browser/admin capture validation documentation tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / 'docs' / 'wyoming-setup.md'
MIGRATION = ROOT / 'docs' / 'wyoming-voqualizer-migration.md'
PAGE = ROOT / 'webui' / 'voqualizer-wyoming.html'
API = ROOT / 'api' / 'wyoming_status.py'


def test_setup_doc_contains_w54_live_validation_steps():
    src = SETUP.read_text()
    for marker in (
        'W54 live runtime/browser validation',
        'window.voqualizerWyomingCapture()',
        '{"action":"live_admin_capture"',
        'tools/wyoming_live_admin_capture.py',
        'tools/wyoming_live_smoke_capture.py',
        'hard refresh',
        'real ctxID',
    ):
        assert marker in src, marker


def test_w54_page_and_api_capture_markers_remain_present():
    page = PAGE.read_text()
    api = API.read_text()
    for marker in ('id="voq-wyoming-capture"', 'window.voqualizerWyomingCapture', "action: 'live_admin_capture'"):
        assert marker in page, marker
    assert 'action == "live_admin_capture"' in api
    assert 'live_admin_capture(' in api


def test_migration_doc_records_w54_validation_phase():
    src = MIGRATION.read_text()
    assert '### W54 live runtime/browser validation plan' in src
    assert 'capture button' in src
    assert 'real ctxID' in src
