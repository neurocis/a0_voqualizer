"""Tests for W21 live binding into the runtime + status surface."""
import importlib
from pathlib import Path


def test_runtime_imports_live_provider_binder():
    src = Path('helpers/wyoming_runtime.py').read_text()
    assert 'bind_live_providers_to_runtime' in src
    assert 'bind_live_providers_to_runtime(iface)' in src


def test_status_endpoint_includes_live_providers_field():
    src = Path('api/wyoming_status.py').read_text()
    assert 'live_provider_status' in src
    assert '"live_providers"' in src
    assert 'def _attach_live_provider_status' in src


def test_runtime_replaces_runtimes_with_live_bound_versions():
    rt_mod = importlib.import_module('helpers.wyoming_runtime')
    wi = importlib.import_module('helpers.wyoming_interfaces')
    interfaces = wi.load_interfaces([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'enabled': True, 'bind_port': 10701}
    ])
    runtime = rt_mod.WyomingVoqualizerRuntime(interfaces)
    runtimes = runtime.manager.runtimes
    assert 'hero' in runtimes
    rt = runtimes['hero']
    for ev in ('audio-start', 'audio-chunk', 'audio-stop', 'voqualizer-text-prompt', 'synthesize'):
        assert ev in rt.handlers, ev
    assert rt.interface.ctxid == 'ctx-hero'
