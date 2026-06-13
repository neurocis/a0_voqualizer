"""Regression: Agent Zero DeferredTask-like prompt results must be awaited."""
import asyncio
import importlib


class DeferredTask:
    def __init__(self, value):
        self.value = value
    async def result(self):
        return self.value


class ProxyRaisesText:
    def __getattr__(self, name):
        if name == 'text':
            raise AttributeError("'str' object has no attribute 'text'")
        raise AttributeError(name)
    def __str__(self):
        return 'fallback-proxy'


def test_maybe_await_handles_deferred_task_by_async_result():
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')
    out = asyncio.run(mod._maybe_await(DeferredTask('assistant final')))
    assert out == 'assistant final'


def test_extract_response_text_ignores_attribute_errors_from_proxies():
    mod = importlib.import_module('helpers.wyoming_a0_prompt_submitter')
    assert mod._extract_response_text(ProxyRaisesText()) == 'fallback-proxy'


def test_source_mentions_deferredtask_regression():
    import pathlib
    src = pathlib.Path('helpers/wyoming_a0_prompt_submitter.py').read_text()
    assert 'DeferredTask' in src
    assert 'result_method' in src
