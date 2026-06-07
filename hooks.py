"""Lifecycle hooks for the a0_voqualizer Wyoming rewrite.

Breaking rewrite target: canonical Wyoming TCP interfaces, each mapped 1:1 to a
fixed Agent Zero ctxID. The old custom Voqualizer websocket protocol is not a
compatibility target.

This module also preserves the original conservative dependency bootstrap so the
Wyoming runtime can still use ASR/TTS provider packages when real adapters are
wired.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Robust import for plugin-local helpers.
#
# Why this dance:
# 1. hooks.py is loaded by helpers/modules.import_module via
#    spec_from_file_location, so it has NO package context. Relative imports
#    ("from .helpers...") raise ImportError immediately.
# 2. The framework already owns the top-level package name "helpers"
#    (/a0/helpers), so a plain "from helpers.wyoming_runtime import ..."
#    resolves against the framework package and raises ModuleNotFoundError.
# 3. Loading wyoming_runtime.py directly via spec_from_file_location works,
#    BUT wyoming_runtime.py itself uses relative imports like
#    "from .wyoming_interfaces import ...". Without a real parent package
#    those relative imports also raise ImportError.
#
# Fix: register the plugin's helpers/ directory as a proper Python package
# under a unique name ("a0_voqualizer_helpers") with submodule_search_locations
# set. Then importlib.import_module("a0_voqualizer_helpers.wyoming_runtime")
# uses the normal import machinery and all internal "from .wyoming_xxx import"
# statements resolve correctly. This does not shadow the framework "helpers"
# package, so call_plugin_hook -> get_plugin_config / save_plugin_config
# (the standard A0 Settings modal Save path) keeps working.
import importlib as _importlib
import importlib.util as _importlib_util
import sys as _sys

_PLUGIN_DIR_PATH = Path(__file__).resolve().parent
_PLUGIN_HELPERS_DIR = _PLUGIN_DIR_PATH / "helpers"
_PLUGIN_HELPERS_PKG = "a0_voqualizer_helpers"

if _PLUGIN_HELPERS_PKG not in _sys.modules:
    _pkg_init = _PLUGIN_HELPERS_DIR / "__init__.py"
    _pkg_spec = _importlib_util.spec_from_file_location(
        _PLUGIN_HELPERS_PKG,
        str(_pkg_init),
        submodule_search_locations=[str(_PLUGIN_HELPERS_DIR)],
    )
    if _pkg_spec is None or _pkg_spec.loader is None:  # pragma: no cover
        raise ImportError(
            f"Could not register plugin helpers package at {_PLUGIN_HELPERS_DIR}"
        )
    _pkg = _importlib_util.module_from_spec(_pkg_spec)
    _sys.modules[_PLUGIN_HELPERS_PKG] = _pkg
    _pkg_spec.loader.exec_module(_pkg)

_wyoming_runtime_module = _importlib.import_module(
    f"{_PLUGIN_HELPERS_PKG}.wyoming_runtime"
)
DEFAULT_INTERFACE_CONFIG = _wyoming_runtime_module.DEFAULT_INTERFACE_CONFIG
WyomingVoqualizerRuntime = _wyoming_runtime_module.WyomingVoqualizerRuntime
load_wyoming_runtime = _wyoming_runtime_module.load_wyoming_runtime

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(PLUGIN_DIR, ".dependency_status.json")

_REQUIREMENTS = [
    ("numpy", "numpy>=1.24.0"),
    ("soundfile", "soundfile>=0.12.1"),
    ("webrtcvad", "webrtcvad>=2.0.10"),
    ("faster_whisper", "faster-whisper>=1.0.0"),
    ("aiohttp", "aiohttp>=3.9.0"),
    ("piper", "piper-tts>=1.2.0"),
    ("samplerate", "samplerate>=0.2.1"),
    ("jsonschema", "jsonschema>=4.0.0"),
]

_wyoming_runtime: WyomingVoqualizerRuntime | None = None
_wyoming_task: asyncio.Task | None = None
_wyoming_lock = asyncio.Lock()


def _log(level: str, msg: str) -> None:
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] [{level}] [a0_voqualizer] {msg}")


def _write_status(status: dict) -> None:
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as exc:
        _log("WARN", f"Could not write status file: {exc}")


def _module_importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False
    except Exception as exc:
        _log("WARN", f"Module {name} import raised non-ImportError: {exc}")
        return True


def _pip_install(specs: list[str]) -> bool:
    if not specs:
        return True
    _log("INFO", f"Installing: {', '.join(specs)}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *specs],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            _log("INFO", "Install succeeded")
            return True
        _log("ERROR", f"Install failed (rc={result.returncode}): {result.stderr.strip()[:500]}")
        return False
    except subprocess.TimeoutExpired:
        _log("ERROR", "pip install timed out")
        return False
    except Exception as exc:
        _log("ERROR", f"pip install exception: {exc}")
        return False


def _ensure_config_json() -> None:
    config_path = os.path.join(PLUGIN_DIR, "config.json")
    default_yaml = os.path.join(PLUGIN_DIR, "default_config.yaml")
    if os.path.exists(config_path):
        return
    try:
        import yaml
        with open(default_yaml, "r") as f:
            data = yaml.safe_load(f) or {}
        with open(config_path, "w") as f:
            json.dump(data, f, indent=2)
        _log("INFO", "Materialized config.json from default_config.yaml")
    except Exception as exc:
        _log("WARN", f"Could not materialize config.json: {exc}")


def ensure_dependency_bootstrap() -> dict[str, Any]:
    """Install/check runtime dependencies without importing framework helpers."""
    _log("INFO", "ensure_dependency_bootstrap() called")
    missing: list[str] = []
    present: list[str] = []
    for module, spec in _REQUIREMENTS:
        if _module_importable(module):
            present.append(module)
        else:
            missing.append(spec)

    install_ok = True
    if missing:
        install_ok = _pip_install(missing)
        present = [module for module, _ in _REQUIREMENTS if _module_importable(module)]

    _ensure_config_json()
    wyoming_config_path().parent.mkdir(parents=True, exist_ok=True)

    status = {
        "plugin": "a0_voqualizer",
        "mode": "wyoming_rewrite",
        "checked_at": datetime.now().isoformat(),
        "requirements": [module for module, _ in _REQUIREMENTS],
        "present": present,
        "missing": [module for module, _ in _REQUIREMENTS if module not in present],
        "install_attempted": bool(missing),
        "install_ok": install_ok,
        "wyoming_config_path": str(wyoming_config_path()),
    }
    _write_status(status)
    _log("INFO", f"Status: present={len(present)}/{len(_REQUIREMENTS)} install_ok={install_ok}")
    return status


def wyoming_config_path() -> Path:
    return DEFAULT_INTERFACE_CONFIG


def get_wyoming_runtime() -> WyomingVoqualizerRuntime | None:
    return _wyoming_runtime


def validate_wyoming_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path is not None else wyoming_config_path()
    if not path.exists():
        return {"ok": False, "exists": False, "config_path": str(path), "errors": ["config file does not exist"]}
    try:
        runtime = load_wyoming_runtime(path)
        errors = list(runtime.errors)
        return {
            "ok": not errors,
            "exists": True,
            "config_path": str(path),
            "configured_interfaces": len(runtime.interfaces),
            "enabled_interfaces": len(runtime.enabled_interfaces),
            "interface_ids": [iface.id for iface in runtime.enabled_interfaces],
            "errors": errors,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic helper
        return {"ok": False, "exists": True, "config_path": str(path), "errors": [str(exc)]}


def wyoming_runtime_status() -> dict[str, Any]:
    runtime = get_wyoming_runtime()
    dependency_status: dict[str, Any] = {}
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                dependency_status = json.load(f)
    except Exception as exc:
        dependency_status = {"error": str(exc)}
    if runtime is None:
        return {
            "configured": False,
            "running": False,
            "config_path": str(wyoming_config_path()),
            "dependency_status": dependency_status,
            "validation": validate_wyoming_config(),
            "message": "Wyoming runtime has not been started",
        }
    status = runtime.status_dict()
    status["configured"] = True
    status["config_path"] = str(wyoming_config_path())
    status["dependency_status"] = dependency_status
    status["validation"] = validate_wyoming_config()
    return status


async def start_wyoming_runtime(config_path: str | Path | None = None) -> WyomingVoqualizerRuntime | None:
    """Start the configured Wyoming TCP runtime if config exists."""
    global _wyoming_runtime, _wyoming_task
    async with _wyoming_lock:
        path = Path(config_path) if config_path is not None else wyoming_config_path()
        if not path.exists():
            return None
        validation = validate_wyoming_config(path)
        if not validation.get("ok"):
            return None
        if _wyoming_runtime is not None and _wyoming_runtime.running:
            return _wyoming_runtime
        runtime = load_wyoming_runtime(path)
        await runtime.start()
        _wyoming_runtime = runtime
        _wyoming_task = asyncio.current_task()
        return runtime


async def stop_wyoming_runtime() -> None:
    global _wyoming_runtime, _wyoming_task
    async with _wyoming_lock:
        runtime = _wyoming_runtime
        _wyoming_runtime = None
        _wyoming_task = None
        if runtime is not None:
            await runtime.stop()


async def install() -> None:
    """Install hook: dependency bootstrap plus Wyoming config directory setup."""
    ensure_dependency_bootstrap()


async def startup() -> None:
    """Start Wyoming runtime when config/wyoming_interfaces.json exists."""
    await start_wyoming_runtime()


async def shutdown() -> None:
    """Stop Wyoming TCP servers cleanly."""
    await stop_wyoming_runtime()


async def uninstall() -> None:
    """Stop Wyoming TCP servers before plugin removal and remove status file."""
    await stop_wyoming_runtime()
    try:
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)
    except Exception as exc:
        _log("WARN", f"Could not remove status file: {exc}")
