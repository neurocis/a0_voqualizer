"""W43 setup documentation tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs' / 'wyoming-setup.md'


def test_setup_doc_exists_and_covers_cli_status_browser_external_paths():
    src = DOC.read_text()
    for marker in (
        'tools/wyoming_init_config.py',
        '--ctxid REAL_AGENT_ZERO_CTXID',
        'tools/wyoming_smoke.py',
        '/api/plugins/a0_voqualizer/wyoming_status',
        'window.voqualizerWyomingDebug',
        'window.voqualizerWyomingDomDebug',
        'wyoming_init',
        'wyoming_event',
        'External Wyoming clients',
        'describe',
        'info',
    ):
        assert marker in src, marker


def test_setup_doc_states_one_to_one_ctxid_and_legacy_reference_policy():
    src = DOC.read_text()
    assert 'maps 1:1 to exactly one Agent Zero ctxID' in src
    assert 'Legacy `api/ws_voqualizer.py`' in src
    assert 'reference only' in src


def test_setup_doc_avoids_retired_custom_ws_event_names():
    src = DOC.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_user_text', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'ack_fallback'):
        assert forbidden not in src, forbidden
