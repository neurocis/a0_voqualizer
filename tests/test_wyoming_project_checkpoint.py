"""W50 project checkpoint tests."""
from pathlib import Path

PROJECT = Path('/a0/usr/projects/a0-voqualizer')
STATUS = PROJECT / 'STATUS.md'
PLAN = PROJECT / 'PLAN.md'
MIGRATION = Path(__file__).resolve().parents[1] / 'docs' / 'wyoming-voqualizer-migration.md'


def test_project_status_checkpoint_mentions_readiness_and_legacy_policy():
    src = STATUS.read_text()
    for marker in (
        'Wyoming-first architecture',
        'maps 1:1 to exactly one fixed Agent Zero `ctxid`',
        'action":"readiness"',
        'window.voqualizerWyomingReadiness',
        'window.voqualizerWyomingDomReadiness',
        'Legacy files are intentionally retained',
    ):
        assert marker in src, marker


def test_project_plan_lists_live_validation_next_milestones():
    src = PLAN.read_text()
    for marker in (
        'W51 — Live framework smoke execution',
        'W52 — Browser standalone live smoke',
        'W53 — DOM main UI extension live smoke',
        'W54 — External Wyoming client interop',
        'W55 — Legacy feature parity gap review',
    ):
        assert marker in src, marker


def test_migration_doc_has_w50_checkpoint_note():
    src = MIGRATION.read_text()
    assert '### W50 checkpoint status update' in src
    assert 'W51-W55 candidate milestones' in src
