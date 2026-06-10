"""Regression: Wyoming runtime config validation must not fail with missing Path imports."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / 'hooks.py'
INTERFACES = ROOT / 'helpers' / 'wyoming_interfaces.py'


def test_hooks_imports_path_for_config_validation_runtime():
    src = HOOKS.read_text()
    assert 'from pathlib import Path' in src
    assert 'def validate_wyoming_config' in src
    assert 'Path(config_path)' in src or 'Path(' in src
    assert src.index('from pathlib import Path') < src.index('def validate_wyoming_config')


def test_wyoming_interfaces_imports_path_for_load_interfaces_from_file():
    src = INTERFACES.read_text()
    assert 'from pathlib import Path' in src
    assert 'def load_interfaces_from_file(path: str | Path)' in src
    assert 'Path(path).read_text()' in src
    assert src.index('from pathlib import Path') < src.index('def load_interfaces_from_file')
