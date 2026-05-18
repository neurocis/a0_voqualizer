"""a0_voqualizer install hooks.

Installs runtime dependencies (faster-whisper, piper-tts, numpy, soundfile,
webrtcvad, aiohttp, samplerate, jsonschema) into the framework runtime when
the plugin is enabled. Skips re-installation on subsequent loads.

Writes a `.dependency_status.json` next to this file so runtime code can
report degraded modes if any optional component is missing.

This module is intentionally conservative — it only uses stdlib at module
import time so it won't break plugin discovery if optional helpers haven't
been materialized yet (mirrors `a0_crosschatapi` and `a0_transcribbler`).
"""

import json
import os
import subprocess
import sys
from datetime import datetime


PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(PLUGIN_DIR, ".dependency_status.json")

# (module_name, pip_spec) — what we check and install.
# pip_spec mirrors requirements.txt; keep them in sync.
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


def _log(level: str, msg: str) -> None:
    """Simple logging without framework dependencies."""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] [{level}] [a0_voqualizer] {msg}")


def _write_status(status: dict) -> None:
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        _log("WARN", f"Could not write status file: {e}")


def _module_importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False
    except Exception as e:
        # Module exists but raised at import time; treat as present so we
        # don't repeatedly reinstall it.
        _log("WARN", f"Module {name} import raised non-ImportError: {e}")
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
    except Exception as e:
        _log("ERROR", f"pip install exception: {e}")
        return False


def _ensure_config_json() -> None:
    """Materialize config.json from default_config.yaml on first activation.

    Helpers/registry.py will own the canonical loader later (A1.2); this is a
    minimal first-run bootstrap so the plugin appears wired-up.
    """
    config_path = os.path.join(PLUGIN_DIR, "config.json")
    default_yaml = os.path.join(PLUGIN_DIR, "default_config.yaml")
    if os.path.exists(config_path):
        return
    try:
        import yaml  # PyYAML ships with the framework runtime
        with open(default_yaml, "r") as f:
            data = yaml.safe_load(f) or {}
        with open(config_path, "w") as f:
            json.dump(data, f, indent=2)
        _log("INFO", f"Materialized config.json from default_config.yaml")
    except Exception as e:
        _log("WARN", f"Could not materialize config.json: {e}")


def install() -> None:
    """Install entry point invoked by the framework when the plugin activates."""
    _log("INFO", "install() called")

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
        # Re-check after install
        present = [m for m, _ in _REQUIREMENTS if _module_importable(m)]

    _ensure_config_json()

    status = {
        "plugin": "a0_voqualizer",
        "checked_at": datetime.now().isoformat(),
        "requirements": [m for m, _ in _REQUIREMENTS],
        "present": present,
        "missing": [m for m, _ in _REQUIREMENTS if m not in present],
        "install_attempted": bool(missing),
        "install_ok": install_ok,
    }
    _write_status(status)
    _log("INFO", f"Status: present={len(present)}/{len(_REQUIREMENTS)} install_ok={install_ok}")


def uninstall() -> None:
    """Uninstall entry point invoked by the framework when the plugin deactivates.

    We deliberately do **not** uninstall pip packages — they may be shared with
    other plugins. We only remove our own status file.
    """
    _log("INFO", "uninstall() called")
    try:
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)
    except Exception as e:
        _log("WARN", f"Could not remove status file: {e}")
