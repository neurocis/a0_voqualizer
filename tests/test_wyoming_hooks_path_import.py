"""Regression: hooks.validate_wyoming_config must not fail with missing Path import."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / 'hooks.py'


def test_hooks_imports_path_for_config_validation_annotations_and_runtime():
    src = HOOKS.read_text()
    assert 'from pathlib import Path' in src
    assert 'def validate_wyoming_config' in src
    assert 'Path(config_path)' in src or 'Path(' in src


def test_hooks_path_import_precedes_path_usage():
    src = HOOKS.read_text()
    assert src.index('from pathlib import Path') < src.index('def validate_wyoming_config')
