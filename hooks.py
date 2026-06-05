"""Lifecycle hooks for the a0_voqualizer Wyoming rewrite.

The Wyoming runtime is intentionally independent from the retired custom
Voqualizer websocket protocol. These hooks provide an opt-in startup scaffold for
1:1 Wyoming interface -> ctxID TCP services.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .helpers.wyoming_runtime import DEFAULT_INTERFACE_CONFIG, WyomingVoqualizerRuntime, load_wyoming_runtime

_wyoming_runtime: WyomingVoqualizerRuntime | None = None
_wyoming_task: asyncio.Task | None = None
_wyoming_lock = asyncio.Lock()


def wyoming_config_path() -> Path:
    return DEFAULT_INTERFACE_CONFIG


def get_wyoming_runtime() -> WyomingVoqualizerRuntime | None:
    return _wyoming_runtime


def wyoming_runtime_status() -> dict[str, Any]:
    runtime = get_wyoming_runtime()
    if runtime is None:
        return {
            "configured": False,
            "running": False,
            "config_path": str(wyoming_config_path()),
            "message": "Wyoming runtime has not been started",
        }
    status = runtime.status_dict()
    status["configured"] = True
    status["config_path"] = str(wyoming_config_path())
    return status


async def start_wyoming_runtime(config_path: str | Path | None = None) -> WyomingVoqualizerRuntime | None:
    """Start the configured Wyoming TCP runtime if config exists.

    Missing config is treated as non-fatal because the migration scaffold should
    not start placeholder ports until the operator creates config/wyoming_interfaces.json.
    """
    global _wyoming_runtime, _wyoming_task
    async with _wyoming_lock:
        path = Path(config_path) if config_path is not None else wyoming_config_path()
        if not path.exists():
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
    """Install hook: no-op except ensuring config directory exists."""
    wyoming_config_path().parent.mkdir(parents=True, exist_ok=True)


async def startup() -> None:
    """Start Wyoming runtime when config/wyoming_interfaces.json exists."""
    await start_wyoming_runtime()


async def shutdown() -> None:
    """Stop Wyoming TCP servers cleanly."""
    await stop_wyoming_runtime()


async def uninstall() -> None:
    """Stop Wyoming TCP servers before plugin removal."""
    await stop_wyoming_runtime()
