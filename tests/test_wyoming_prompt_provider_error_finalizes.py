"""Regression: prompt provider errors should not fail ACK after partial response."""
import asyncio
import importlib


async def _collect_with_failing_async_iter():
    prompt = importlib.import_module('helpers.wyoming_prompt')
    server = importlib.import_module('helpers.wyoming_server')
    interfaces = importlib.import_module('helpers.wyoming_interfaces')
    proto = importlib.import_module('helpers.wyoming_protocol')

    async def provider(text, metadata):
        async def gen():
            yield 'partial response'
            raise AttributeError("'str' object has no attribute 'text'")
        return gen()

    iface = interfaces.WyomingInterface(id='web', ctxid='ctx-test', name='Web', enabled=True)
    runtime = server.WyomingInterfaceRuntime(iface)
    session = runtime.create_session()
    adapter = prompt.WyomingPromptAdapter(provider=provider)
    replies = await adapter.handle_text_prompt(session, proto.event('voqualizer-text-prompt', text='hi'))
    return replies


def test_provider_error_after_partial_emits_error_and_final_instead_of_raising():
    replies = asyncio.run(_collect_with_failing_async_iter())
    types = [r.type for r in replies]
    assert 'voqualizer-response-start' in types
    assert 'voqualizer-response-chunk' in types
    assert 'error' in types
    assert 'voqualizer-response-final' in types
    final = [r for r in replies if r.type == 'voqualizer-response-final'][-1]
    assert final.data['text'] == 'partial response'
    assert final.data['ok'] is False
    assert "str' object has no attribute 'text" in final.data['provider_error']


def test_provider_error_without_partial_emits_final_error_text():
    prompt = importlib.import_module('helpers.wyoming_prompt')
    server = importlib.import_module('helpers.wyoming_server')
    interfaces = importlib.import_module('helpers.wyoming_interfaces')
    proto = importlib.import_module('helpers.wyoming_protocol')

    async def provider(text, metadata):
        raise AttributeError("'str' object has no attribute 'text'")

    async def run():
        iface = interfaces.WyomingInterface(id='web', ctxid='ctx-test', name='Web', enabled=True)
        runtime = server.WyomingInterfaceRuntime(iface)
        session = runtime.create_session()
        adapter = prompt.WyomingPromptAdapter(provider=provider)
        return await adapter.handle_text_prompt(session, proto.event('voqualizer-text-prompt', text='hi'))

    replies = asyncio.run(run())
    final = [r for r in replies if r.type == 'voqualizer-response-final'][-1]
    assert final.data['ok'] is False
    assert 'Prompt provider error:' in final.data['text']
