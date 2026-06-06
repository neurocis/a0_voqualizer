from pathlib import Path
import ast

PLUGIN = Path(__file__).resolve().parents[1]
API = PLUGIN / 'api' / 'wyoming_ws.py'


def test_wyoming_ws_endpoint_exposes_bridge_session_helper_and_handler():
    source = API.read_text()
    tree = ast.parse(source)
    names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    assert 'run_wyoming_ws_bridge_session' in names
    assert 'WyomingWs' in names
    assert 'class WyomingWs(ApiHandler)' in source
    assert 'WyomingWsBridge' in source


def test_wyoming_ws_endpoint_supports_status_list_describe_actions():
    source = API.read_text()
    assert '"status"' in source
    assert '"list"' in source
    assert '"describe"' in source
    assert 'supported_actions' in source
    assert 'missing_interface_id' in source
    assert 'unknown_interface' in source


def test_wyoming_ws_endpoint_returns_not_started_when_runtime_absent():
    source = API.read_text()
    assert 'Wyoming runtime has not been started' in source


def test_wyoming_ws_endpoint_source_avoids_old_custom_websocket_protocol():
    source = API.read_text()
    for forbidden in ('voqualizer_init', 'voqualizer_audio_chunk', 'voqualizer_tts_chunk', 'voqualizer_user_text', 'ack_fallback'):
        assert forbidden not in source
    assert 'Wyoming' in source


def test_wyoming_ws_endpoint_supports_interface_discovery_payload():
    source = API.read_text()
    assert 'def _interface_payloads' in source
    assert 'default_interface_id' in source
    assert 'action in {"list", "interfaces"}' in source
    assert 'capabilities' in source
