"""W37 runtime config validation and placeholder ctxID guard tests."""
import asyncio
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'helpers' / 'wyoming_runtime.py'
HOOKS = ROOT / 'hooks.py'
API = ROOT / 'api' / 'wyoming_status.py'


def test_runtime_exposes_validation_guard_markers():
    src = RUNTIME.read_text()
    assert 'PLACEHOLDER_CTXID_PREFIXES' in src
    assert 'validate_runtime_interfaces' in src
    assert 'placeholder ctxid' in src
    assert 'Wyoming runtime config validation failed' in src


def test_placeholder_ctxid_blocks_runtime_start():
    rt = importlib.import_module('helpers.wyoming_runtime')
    runtime = rt.build_wyoming_runtime_from_records([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'REPLACE_WITH_REAL_CTXID', 'enabled': True, 'bind_host': '127.0.0.1', 'bind_port': 0},
    ])
    assert any('placeholder ctxid' in err for err in runtime.errors)

    async def run():
        try:
            await runtime.start()
        except RuntimeError as exc:
            assert 'validation failed' in str(exc)
        else:
            raise AssertionError('placeholder ctxid should block startup')

    asyncio.run(run())


def test_valid_ctxid_can_start_on_ephemeral_port():
    rt = importlib.import_module('helpers.wyoming_runtime')
    runtime = rt.build_wyoming_runtime_from_records([
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'ctx-real', 'enabled': True, 'bind_host': '127.0.0.1', 'bind_port': 0},
    ])
    assert runtime.errors == []

    async def run():
        await runtime.start()
        assert runtime.running is True
        await runtime.stop()

    asyncio.run(run())


def test_hooks_expose_validate_wyoming_config(tmp_path):
    hooks = importlib.import_module('hooks')
    config = tmp_path / 'wyoming_interfaces.json'
    config.write_text(json.dumps({'interfaces': [
        {'id': 'hero', 'name': 'Hero', 'ctxid': 'REPLACE_WITH_REAL_CTXID', 'enabled': True, 'bind_port': 10701},
    ]}))
    report = hooks.validate_wyoming_config(config)
    assert report['ok'] is False
    assert report['exists'] is True
    assert 'placeholder ctxid' in ' '.join(report['errors'])


def test_status_endpoint_supports_validate_action():
    src = API.read_text()
    assert 'action == "validate"' in src
    assert 'validate_wyoming_config' in src
    assert '"validate"' in src


def test_validation_sources_avoid_retired_custom_websocket_protocol():
    combined = RUNTIME.read_text() + '\n' + HOOKS.read_text() + '\n' + API.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_user_text', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'ack_fallback'):
        assert forbidden not in combined, forbidden
