"""W78 visible-response finalization regression tests."""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeContext:
    id = "ctx-visible"


class FakeAgent:
    context = FakeContext()


class FakeResponse:
    break_loop = True
    message = "visible final text"


def test_visible_response_notification_wakes_registered_waiter():
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')
    waiter = mod._register_visible_response_waiter('ctx-visible', 'msg-1')
    try:
        count = mod.notify_visible_response_completion(FakeAgent(), response=FakeResponse(), tool_name='response')
        assert count == 1
        assert waiter.event.is_set()
        assert waiter.text == 'visible final text'
    finally:
        mod._unregister_visible_response_waiter(waiter)


def test_non_response_tool_does_not_wake_waiter():
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')
    waiter = mod._register_visible_response_waiter('ctx-visible', 'msg-2')
    try:
        count = mod.notify_visible_response_completion(FakeAgent(), response=FakeResponse(), tool_name='browser')
        assert count == 0
        assert not waiter.event.is_set()
    finally:
        mod._unregister_visible_response_waiter(waiter)


def test_tool_execute_after_extension_present():
    path = ROOT / 'extensions' / 'python' / 'tool_execute_after' / '_45_voqualizer_visible_response_complete.py'
    src = path.read_text()
    assert 'notify_visible_response_completion' in src
    assert 'tool_name' in src
    assert 'response' in src


if __name__ == '__main__':
    test_visible_response_notification_wakes_registered_waiter()
    test_non_response_tool_does_not_wake_waiter()
    test_tool_execute_after_extension_present()
    print('OK')
