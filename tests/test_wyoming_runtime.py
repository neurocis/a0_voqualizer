from pathlib import Path
import asyncio
import importlib
import json

PLUGIN = Path(__file__).resolve().parents[1]
RUNTIME = PLUGIN / 'helpers' / 'wyoming_runtime.py'
EXAMPLE = PLUGIN / 'config' / 'wyoming_interfaces.example.json'


def test_runtime_builds_pipeline_manager_for_enabled_interfaces_only():
    rt = importlib.import_module('helpers.wyoming_runtime')
    runtime = rt.build_wyoming_runtime_from_records([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'enabled': True, 'bind_port': 10701},
        {'id': 'off', 'name': 'Off', 'ctxid': 'ctx-off', 'enabled': False, 'bind_port': 10702},
    ])
    status = runtime.status_dict()
    assert status['configured_interfaces'] == 2
    assert status['enabled_interfaces'] == 1
    assert status['interface_ids'] == ['hero']
    assert status['bind_ports'] == [10701]
    assert status['manager'][0]['ctxid'] == 'ctx-hero'


def test_runtime_start_stop_lifecycle_uses_tcp_manager(monkeypatch=None):
    rt = importlib.import_module('helpers.wyoming_runtime')
    runtime = rt.build_wyoming_runtime_from_records([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'enabled': True, 'bind_host': '127.0.0.1', 'bind_port': 0},
    ])

    async def run():
        await runtime.start()
        assert runtime.status().running is True
        await runtime.stop()
        assert runtime.status().running is False

    asyncio.run(run())


def test_runtime_loads_from_config_file(tmp_path):
    rt = importlib.import_module('helpers.wyoming_runtime')
    config = tmp_path / 'interfaces.json'
    config.write_text(json.dumps([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-hero', 'enabled': True, 'bind_port': 10701},
    ]))
    runtime = rt.load_wyoming_runtime(config)
    assert runtime.status_dict()['interface_ids'] == ['hero']


def test_example_config_documents_one_interface_per_ctxid():
    data = json.loads(EXAMPLE.read_text())
    assert isinstance(data, list)
    assert data[0]['id'] == 'hero'
    assert data[0]['ctxid'] == 'REPLACE_WITH_HERO_CTXID'
    assert data[0]['bind_port'] == 10701
    assert data[0]['capabilities']['authoritative_tts'] is True


def test_runtime_source_avoids_old_custom_websocket_protocol():
    source = RUNTIME.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text', 'ack_fallback'):
        assert forbidden not in source
    assert 'WyomingVoqualizerRuntime' in source
    assert 'load_wyoming_runtime' in source
    assert 'build_wyoming_runtime_from_records' in source
    assert 'run_wyoming_runtime_forever' in source
