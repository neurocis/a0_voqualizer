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
PLACEHOLDER_CTXID_PREFIXES = ("REPLACE_WITH_", "PLACEHOLDER", "TODO", "CTXID_HERE")


def validate_runtime_interfaces(interfaces: list[WyomingInterface]) -> list[str]:
    """Return non-fatal runtime config validation warnings/errors.

    The runtime must not accidentally bind live TCP ports when a copied example
    config still contains placeholder ctxIDs. Treat those as startup-blocking
    validation errors, but keep the API status path diagnostic-friendly.
    """
    errors: list[str] = []
    seen_ports: set[tuple[str, int]] = set()
    seen_ids: set[str] = set()
    for iface in interfaces:
        if iface.id in seen_ids:
            errors.append(f"duplicate interface id: {iface.id}")
        seen_ids.add(iface.id)
        if not iface.enabled:
            continue
        ctxid = str(iface.ctxid or "").strip()
        if not ctxid:
            errors.append(f"enabled interface {iface.id} is missing ctxid")
        if any(ctxid.upper().startswith(prefix) for prefix in PLACEHOLDER_CTXID_PREFIXES):
            errors.append(f"enabled interface {iface.id} has placeholder ctxid: {ctxid}")
        port_key = (str(iface.bind_host or "0.0.0.0"), int(iface.bind_port or 0))
        if port_key in seen_ports:
            errors.append(f"duplicate enabled bind endpoint: {port_key[0]}:{port_key[1]}")
        seen_ports.add(port_key)
    return errors



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
        self.running = False
        # Compatibility flag used by the framework Socket.IO handler. Keep it
        # mirrored with `running` so runtime.status(), admin APIs, and WS init
        # all agree on lifecycle state.
        self._started = False
        self._lock = asyncio.Lock()
        self.errors: list[str] = validate_runtime_interfaces(self.interfaces)
        # W20/W21: replace scaffold runtimes with live-bound ASR/prompt/TTS runtimes.
        for iface in self.enabled_interfaces:
            try:
                live_runtime = bind_live_providers_to_runtime(iface)
            except Exception as exc:  # noqa: BLE001 - keep runtime construction resilient
                self.errors.append(f"live provider binding failed for {iface.id}: {exc}")
                continue
            self.manager.runtimes[iface.id] = live_runtime

    async def start(self) -> None:
        async with self._lock:
            if self.running:
                return
            if self.errors:
                raise RuntimeError("Wyoming runtime config validation failed: " + "; ".join(self.errors))
            try:
                await self.tcp_manager.start()
                self.running = True
                self._started = True
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
                self._started = False

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
