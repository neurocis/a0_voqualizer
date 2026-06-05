from pathlib import Path
import ast

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / 'hooks.py'
API = PLUGIN / 'api' / 'wyoming_status.py'


def test_hooks_expose_wyoming_lifecycle_functions():
    source = HOOKS.read_text()
    tree = ast.parse(source)
    names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in (
        'wyoming_config_path',
        'get_wyoming_runtime',
        'wyoming_runtime_status',
        'ensure_dependency_bootstrap',
        'start_wyoming_runtime',
        'stop_wyoming_runtime',
        'install',
        'startup',
        'shutdown',
        'uninstall',
    ):
        assert name in names
    assert 'DEFAULT_INTERFACE_CONFIG' in source
    assert 'load_wyoming_runtime' in source
    assert '_REQUIREMENTS' in source
    assert 'STATUS_FILE' in source


def test_hooks_do_not_start_without_config_and_report_status():
    source = HOOKS.read_text()
    assert 'if not path.exists()' in source
    assert 'return None' in source
    assert 'Wyoming runtime has not been started' in source
    assert 'config/wyoming_interfaces.json' not in source  # use helper constant, not hard-coded routes


def test_wyoming_status_endpoint_supports_status_start_stop():
    source = API.read_text()
    assert 'class WyomingStatus(ApiHandler)' in source
    assert 'wyoming_runtime_status' in source
    assert 'start_wyoming_runtime' in source
    assert 'stop_wyoming_runtime' in source
    assert 'supported_actions' in source
    assert 'bootstrap' in source
    assert 'status' in source
    assert 'start' in source
    assert 'stop' in source


def test_lifecycle_sources_avoid_old_custom_websocket_protocol():
    combined = HOOKS.read_text() + '\n' + API.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text', 'ack_fallback'):
        assert forbidden not in combined
    assert 'Wyoming' in combined


def test_plugin_yaml_describes_wyoming_breaking_rewrite():
    plugin = PLUGIN / 'plugin.yaml'
    source = plugin.read_text()
    assert 'Wyoming-compatible TCP interfaces' in source
    assert 'mapped 1:1 to a fixed Agent' in source
    assert 'custom Voqualizer WebSocket' in source
    assert 'version: 0.2.0' in source
