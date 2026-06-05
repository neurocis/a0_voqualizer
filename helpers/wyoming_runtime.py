"""Wyoming Voqualizer runtime bootstrap helpers.

This module loads configured 1:1 Wyoming interfaces, builds composed pipeline
runtimes for each enabled interface, and manages the TCP server lifecycle.
It remains provider-agnostic until the real A0 ASR/context/TTS adapters are wired.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
from pathlib import Path
from typing import Any

from .wyoming_interfaces import WyomingInterface, load_interfaces_from_file, load_interfaces
from .wyoming_live_providers import bind_live_providers_to_runtime
from .wyoming_server import (
    WyomingInterfaceManager,
    WyomingTcpInterfaceManager,
    build_wyoming_pipeline_manager,
)


DEFAULT_INTERFACE_CONFIG = Path(__file__).resolve().parents[1] / "config" / "wyoming_interfaces.json"


@dataclass(slots=True)
class WyomingRuntimeStatus:
    configured_interfaces: int = 0
    enabled_interfaces: int = 0
    running: bool = False
    bind_ports: list[int] = field(default_factory=list)
    interface_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class WyomingVoqualizerRuntime:
    """Owns configured interface manager and TCP lifecycle for Wyoming rewrite."""

    def __init__(self, interfaces: list[WyomingInterface]) -> None:
        self.interfaces = interfaces
        self.enabled_interfaces = [interface for interface in interfaces if interface.enabled]
        self.manager: WyomingInterfaceManager = build_wyoming_pipeline_manager(self.enabled_interfaces)
        self.tcp_manager = WyomingTcpInterfaceManager(self.manager)
        # W20/W21: replace scaffold runtimes with live-bound ASR/prompt/TTS runtimes.
        for iface in self.enabled_interfaces:
            try:
                live_runtime = bind_live_providers_to_runtime(iface)
            except Exception as exc:  # noqa: BLE001 - keep runtime construction resilient
                self.errors.append(f"live provider binding failed for {iface.id}: {exc}")
                continue
            self.manager.runtimes[iface.id] = live_runtime
        self.running = False
        self._lock = asyncio.Lock()
        self.errors: list[str] = []

    async def start(self) -> None:
        async with self._lock:
            if self.running:
                return
            try:
                await self.tcp_manager.start()
                self.running = True
            except Exception as exc:
                self.errors.append(str(exc))
                raise

    async def stop(self) -> None:
        async with self._lock:
            if not self.running:
                return
            try:
                await self.tcp_manager.stop()
            finally:
                self.running = False

    def status(self) -> WyomingRuntimeStatus:
        return WyomingRuntimeStatus(
            configured_interfaces=len(self.interfaces),
            enabled_interfaces=len(self.enabled_interfaces),
            running=self.running,
            bind_ports=[interface.bind_port for interface in self.enabled_interfaces],
            interface_ids=[interface.id for interface in self.enabled_interfaces],
            errors=list(self.errors),
        )

    def status_dict(self) -> dict[str, Any]:
        status = self.status()
        return {
            "configured_interfaces": status.configured_interfaces,
            "enabled_interfaces": status.enabled_interfaces,
            "running": status.running,
            "bind_ports": list(status.bind_ports),
            "interface_ids": list(status.interface_ids),
            "errors": list(status.errors),
            "manager": self.manager.list_info(),
        }


def load_wyoming_runtime(config_path: str | Path = DEFAULT_INTERFACE_CONFIG) -> WyomingVoqualizerRuntime:
    """Load a runtime from a Wyoming interface config file."""
    interfaces = load_interfaces_from_file(config_path)
    return WyomingVoqualizerRuntime(interfaces)


def build_wyoming_runtime_from_records(records: list[dict[str, Any]]) -> WyomingVoqualizerRuntime:
    """Build runtime from raw config records for tests/admin preview."""
    return WyomingVoqualizerRuntime(load_interfaces(records))


async def run_wyoming_runtime_forever(runtime: WyomingVoqualizerRuntime) -> None:
    """Start runtime and block until cancelled, stopping cleanly on exit."""
    await runtime.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runtime.stop()
