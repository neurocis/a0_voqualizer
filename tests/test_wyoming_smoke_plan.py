"""W22 smoke/interop checklist tests for the Wyoming rewrite."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / 'config' / 'wyoming_interfaces.smoke.example.json'
DOC = ROOT / 'docs' / 'wyoming-voqualizer-migration.md'


def test_smoke_interface_example_exists_and_documents_ctxid_replacement():
    src = SMOKE.read_text()
    assert 'hero-smoke' in src
    assert 'REPLACE_WITH_REAL_CTXID' in src
    assert '10701' in src


def test_w22_doc_contains_interop_checklist():
    src = DOC.read_text()
    assert '### W22 Interop/smoke validation checklist' in src
    for marker in (
        'TCP describe/info',
        'Socket.IO wyoming_init',
        'text prompt',
        'audio-start/audio-chunk/audio-stop',
        'Home Assistant',
        'wyoming.net',
    ):
        assert marker in src, marker


def test_runtime_initializes_errors_before_live_binding():
    src = (ROOT / 'helpers' / 'wyoming_runtime.py').read_text()
    errors_pos = src.index('self.errors: list[str] = []')
    bind_pos = src.index('bind_live_providers_to_runtime(iface)')
    assert errors_pos < bind_pos
