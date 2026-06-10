"""Regression tests for live Wyoming runtime startup blockers."""
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTERFACES = ROOT / 'helpers' / 'wyoming_interfaces.py'
SERVER = ROOT / 'helpers' / 'wyoming_server.py'
LIVE_PROVIDERS = ROOT / 'helpers' / 'wyoming_live_providers.py'


def test_wyoming_interfaces_imports_path_for_file_loader():
    src = INTERFACES.read_text()
    assert 'from pathlib import Path' in src
    assert 'def load_interfaces_from_file(path: str | Path)' in src
    assert 'Path(path).read_text()' in src


def test_interface_runtime_supports_composed_pipeline_handler():
    src = SERVER.read_text()
    assert 'self.pipeline_handler: Handler | None = None' in src
    assert 'def set_pipeline(self, handler: Handler) -> None:' in src
    assert 'if self.pipeline_handler:' in src
    assert 'return await self.pipeline_handler(session, incoming)' in src


def test_pipeline_manager_passes_interfaces_to_constructor_and_assigns_runtime_map():
    src = SERVER.read_text()
    fn = src[src.index('def build_wyoming_pipeline_manager'):]
    body = fn.split('\n\nasync def', 1)[0]
    assert 'WyomingInterfaceManager(interfaces)' in body
    assert 'manager.runtimes[interface.id] = build_wyoming_pipeline_runtime(interface)' in body
    assert 'WyomingInterfaceManager()' not in body
    assert '.add_runtime(' not in body


def test_live_provider_binding_uses_current_pipeline_constructor_names():
    src = LIVE_PROVIDERS.read_text()
    assert 'WyomingVoqualizerPipeline(' in src
    assert 'asr=build_a0_asr_adapter(asr_factory)' in src
    assert 'prompt=build_a0_prompt_adapter(submitter)' in src
    assert 'tts=build_a0_tts_adapter(tts_factory)' in src
    assert 'asr_adapter=' not in src
    assert 'prompt_adapter=' not in src
    assert 'tts_adapter=' not in src


def test_load_runtime_from_config_constructs_without_startup_type_errors(tmp_path):
    cfg = tmp_path / 'wyoming_interfaces.json'
    cfg.write_text(json.dumps({
        'interfaces': [{
            'id': 'web',
            'name': 'Web',
            'ctxid': 'ctx-test',
            'enabled': True,
            'bind_host': '127.0.0.1',
            'bind_port': 19071,
        }]
    }))
    runtime_mod = importlib.import_module('helpers.wyoming_runtime')
    runtime = runtime_mod.load_wyoming_runtime(cfg)
    assert runtime.enabled_interfaces[0].id == 'web'
    assert runtime.manager.runtimes.get('web') is not None
    joined = '; '.join(runtime.errors)
    for bad in ('missing 1 required positional argument', 'add_runtime', 'set_pipeline', 'asr_adapter'):
        assert bad not in joined
