"""W44 README Wyoming rewrite status tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / 'README.md'


def test_readme_documents_wyoming_rewrite_setup_paths():
    src = README.read_text()
    for marker in (
        '## Wyoming rewrite status',
        'Wyoming-compatible TCP',
        'mapped 1:1 to exactly one fixed Agent Zero ctxID',
        'docs/wyoming-setup.md',
        'tools/wyoming_init_config.py',
        'tools/wyoming_smoke.py',
        '/api/plugins/a0_voqualizer/wyoming_status',
        'plugins/a0_voqualizer/ws_wyoming',
        'webui/voqualizer-wyoming.html',
        'voqualizer-wyoming-buttons.html',
    ):
        assert marker in src, marker


def test_readme_preserves_legacy_reference_policy():
    src = README.read_text()
    assert 'legacy custom `voqualizer_*` WebSocket API' in src
    assert 'remain in-tree for reference' in src
    assert 'not the target compatibility protocol' in src
