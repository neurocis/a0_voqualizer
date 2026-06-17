"""Regression: Wyoming-origin completed submits clear A0 progress_active."""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeLog:
    def __init__(self):
        self.progress = "Running"
        self.progress_active = True
        self.calls = []

    def set_progress(self, progress, no=0, active=True):
        self.progress = progress
        self.progress_active = active
        self.calls.append((progress, active))


class FakeContext:
    def __init__(self):
        self.log = FakeLog()


def test_settle_context_progress_clears_log_progress_active():
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')
    ctx = FakeContext()
    mod._settle_context_progress(ctx, reason='test')
    assert ctx.log.progress_active is False
    assert ctx.log.progress == 'Waiting for input'
    assert ctx.log.calls[-1] == ('Waiting for input', False)


def test_settle_context_progress_is_best_effort_for_missing_log():
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')
    mod._settle_context_progress(object(), reason='missing_log')


if __name__ == '__main__':
    test_settle_context_progress_clears_log_progress_active()
    test_settle_context_progress_is_best_effort_for_missing_log()
    print('OK')
