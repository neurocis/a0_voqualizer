"""a0_voqualizer runtime registry and config loader.

This module currently exposes the **config loader** for A1.2. The full
`BridgeRegistry` + `BridgeSession` machinery for A1.3 will be added by Voq_Core
on top of this scaffold.

The loader:

1. Reads `default_config.yaml` (canonical defaults shipped with the plugin).
2. Reads `config.json` (runtime overlay; written by the admin REST endpoint).
3. Deep-merges overlay onto defaults (overlay wins; lists are *replaced*, not
   merged element-wise — provider order in `config.json` is authoritative).
4. Validates the merged result against `CONFIG_SCHEMA`.
5. Performs cross-field semantic checks (default provider must exist; default
   codec must be in the supported list; etc.).
6. Returns the merged config as a plain dict.

Validation errors raise `ConfigError`. All errors include the JSON pointer of
the offending field whenever possible.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

from .config_schema import CONFIG_SCHEMA


PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(PLUGIN_DIR, "default_config.yaml")
RUNTIME_CONFIG_PATH = os.path.join(PLUGIN_DIR, "config.json")


class ConfigError(ValueError):
    """Raised when the merged config is invalid.

    `path` is a slash-delimited JSON pointer to the failing field (best-effort).
    """

    def __init__(self, message: str, path: str = "") -> None:
        super().__init__(message)
        self.path = path

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base} (at {self.path})" if self.path else base


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge ``overlay`` onto ``base``.

    - Dicts merge key-by-key.
    - Lists from overlay *replace* the base list entirely (no element merging).
    - Scalars from overlay win.
    """
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_yaml(path: str) -> dict:
    import yaml  # PyYAML ships with the framework runtime
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} did not parse to a dict (got {type(data).__name__})")
    return data


def _load_json(path: str) -> dict:
    with open(path, "r") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"{path} did not parse to a dict (got {type(data).__name__})")
    return data


def _validate_schema(config: dict) -> None:
    try:
        import jsonschema
    except ImportError as e:
        raise ConfigError(f"jsonschema not installed: {e}")

    try:
        jsonschema.validate(instance=config, schema=CONFIG_SCHEMA)
    except jsonschema.ValidationError as e:
        ptr = "/".join(str(p) for p in e.absolute_path)
        raise ConfigError(e.message, path=ptr) from e


def _semantic_check(config: dict) -> None:
    """Cross-field invariants that JSON Schema can't express."""
    asr = config["asr"]
    asr_names = [p["name"] for p in asr["providers"]]
    if asr["default"] not in asr_names:
        raise ConfigError(
            f"asr.default {asr['default']!r} not in providers {asr_names!r}",
            path="asr/default",
        )

    tts = config["tts"]
    tts_names = [p["name"] for p in tts["providers"]]
    if tts["default"] not in tts_names:
        raise ConfigError(
            f"tts.default {tts['default']!r} not in providers {tts_names!r}",
            path="tts/default",
        )

    proto = config["protocol"]
    if proto["default_input_codec"] not in proto["input_codecs"]:
        raise ConfigError(
            f"protocol.default_input_codec {proto['default_input_codec']!r} "
            f"not in input_codecs {proto['input_codecs']!r}",
            path="protocol/default_input_codec",
        )
    if proto["default_output_codec"] not in proto["output_codecs"]:
        raise ConfigError(
            f"protocol.default_output_codec {proto['default_output_codec']!r} "
            f"not in output_codecs {proto['output_codecs']!r}",
            path="protocol/default_output_codec",
        )

    # Provider names must be unique within each side
    for side, names in (("asr", asr_names), ("tts", tts_names)):
        if len(set(names)) != len(names):
            raise ConfigError(
                f"{side}.providers contains duplicate name(s): {names}",
                path=f"{side}/providers",
            )


def load_config(
    default_path: str | None = None,
    runtime_path: str | None = None,
    *,
    apply_overlay: bool = True,
) -> dict[str, Any]:
    """Load, merge, and validate the plugin config.

    Args:
        default_path: override for default_config.yaml location.
        runtime_path: override for config.json overlay location.
        apply_overlay: if False, ignore runtime_path (defaults-only).

    Returns:
        The merged, validated config as a plain dict.

    Raises:
        ConfigError on any schema or semantic violation.
        FileNotFoundError if default_config.yaml is missing.
    """
    dpath = default_path or DEFAULT_CONFIG_PATH
    rpath = runtime_path or RUNTIME_CONFIG_PATH

    if not os.path.exists(dpath):
        raise FileNotFoundError(f"default config not found: {dpath}")
    base = _load_yaml(dpath)

    if apply_overlay and os.path.exists(rpath):
        try:
            overlay = _load_json(rpath)
        except (json.JSONDecodeError, ConfigError) as e:
            # Overlay corruption: degrade to defaults rather than crash plugin load.
            # The admin REST endpoint will surface this in /providers responses.
            raise ConfigError(f"failed to read runtime overlay {rpath}: {e}") from e
        merged = _deep_merge(base, overlay)
    else:
        merged = base

    _validate_schema(merged)
    _semantic_check(merged)
    return merged


def validate_config(config: dict) -> None:
    """Validate a config dict (already merged) against schema + semantics.

    Raises ConfigError on failure. Use this from the admin REST endpoint before
    persisting overlay updates.
    """
    _validate_schema(config)
    _semantic_check(config)


def save_overlay(overlay: dict, runtime_path: str | None = None) -> None:
    """Persist `overlay` to config.json after validating the merged result.

    The overlay is merged onto defaults purely for validation; the saved file
    contains only the overlay layer.
    """
    rpath = runtime_path or RUNTIME_CONFIG_PATH
    base = _load_yaml(DEFAULT_CONFIG_PATH)
    merged = _deep_merge(base, overlay)
    _validate_schema(merged)
    _semantic_check(merged)
    tmp = rpath + ".tmp"
    with open(tmp, "w") as f:
        json.dump(overlay, f, indent=2)
    os.replace(tmp, rpath)


# ---------------------------------------------------------------------------
# A1.3 — BridgeRegistry (multi-instance session tracking)
# ---------------------------------------------------------------------------

import asyncio
import time
from typing import Callable, Iterator, Optional

from .session import (
    BridgeSession,
    STATE_CLOSED,
)


ClockFn = Callable[[], float]
"""Callable returning monotonic seconds. Injected for deterministic tests."""


class RegistryFull(RuntimeError):
    """Raised when :meth:`BridgeRegistry.create_or_resume` would exceed
    ``limits.max_concurrent_sessions``.
    """

    def __init__(self, current: int, limit: int) -> None:
        self.current = current
        self.limit = limit
        super().__init__(
            f"BridgeRegistry full: {current} active sessions, limit {limit}"
        )


class BridgeRegistry:
    """Process-wide singleton tracking live :class:`BridgeSession` instances.

    Responsibilities
    ----------------

    * Track sessions keyed by ``session_id``.
    * Enforce ``limits.max_concurrent_sessions`` from the merged config.
    * Resume sessions by id within ``protocol.session_resume_window_seconds``
      after they were last touched; spawn a fresh session beyond that window
      (the old one is evicted first).
    * GC sessions older than ``limits.max_session_seconds`` of idle time.
    * Provide thread-safe iteration / count for metrics surfaces.

    The registry stores plain dict lookups under an :class:`asyncio.Lock` for
    mutating ops; lock-free fast reads are exposed via :meth:`get` and
    :meth:`count` (the read snapshot is point-in-time and may briefly observe
    a session that's being removed elsewhere — fine for metrics, not fine for
    decisions, which must take the lock).
    """

    _instance: "BridgeRegistry | None" = None

    def __init__(
        self,
        *,
        max_concurrent_sessions: int = 32,
        session_resume_window_seconds: float = 30.0,
        max_session_seconds: float = 1800.0,
        audio_queue_max_frames: int = 256,
        clock: ClockFn | None = None,
    ) -> None:
        if max_concurrent_sessions < 1:
            raise ValueError("max_concurrent_sessions must be >= 1")
        if session_resume_window_seconds < 0:
            raise ValueError("session_resume_window_seconds must be >= 0")
        if max_session_seconds < 1:
            raise ValueError("max_session_seconds must be >= 1")
        if audio_queue_max_frames < 1:
            raise ValueError("audio_queue_max_frames must be >= 1")

        self.max_concurrent_sessions = int(max_concurrent_sessions)
        self.session_resume_window_seconds = float(session_resume_window_seconds)
        self.max_session_seconds = float(max_session_seconds)
        self.audio_queue_max_frames = int(audio_queue_max_frames)
        self._clock: ClockFn = clock or time.monotonic
        self._sessions: dict[str, BridgeSession] = {}
        # Tombstone window: session_id -> (last_activity, snapshot) so we can
        # resume within the resume window after the session itself was removed
        # (e.g. WS disconnect cleanup) without losing identity.
        self._tombstones: dict[str, tuple[float, BridgeSession]] = {}
        self._lock = asyncio.Lock()

    # ---- Singleton helpers ------------------------------------------------

    @classmethod
    def instance(cls) -> "BridgeRegistry":
        """Return the process-wide singleton, creating it with config defaults
        if it doesn't yet exist.

        Callers that need to bind concrete config values should call
        :meth:`configure` first (typically from the plugin install hook).
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def configure(
        cls,
        *,
        max_concurrent_sessions: int,
        session_resume_window_seconds: float,
        max_session_seconds: float,
        audio_queue_max_frames: int,
        clock: ClockFn | None = None,
    ) -> "BridgeRegistry":
        """Create or replace the singleton with the supplied parameters.

        Existing sessions on a prior instance are *not* migrated; this is
        intended for startup wiring or test isolation.
        """
        cls._instance = cls(
            max_concurrent_sessions=max_concurrent_sessions,
            session_resume_window_seconds=session_resume_window_seconds,
            max_session_seconds=max_session_seconds,
            audio_queue_max_frames=audio_queue_max_frames,
            clock=clock,
        )
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Drop the singleton reference (test isolation)."""
        cls._instance = None

    @classmethod
    def from_config(
        cls,
        config: dict,
        *,
        clock: ClockFn | None = None,
        replace: bool = True,
    ) -> "BridgeRegistry":
        """Build a registry from a merged plugin config dict.

        When ``replace`` is True (default) the new instance becomes the
        singleton; otherwise a detached registry is returned (useful for
        tests).
        """
        limits = config.get("limits", {})
        protocol = config.get("protocol", {})
        kwargs = dict(
            max_concurrent_sessions=int(limits.get("max_concurrent_sessions", 32)),
            session_resume_window_seconds=float(
                protocol.get("session_resume_window_seconds", 30)
            ),
            max_session_seconds=float(limits.get("max_session_seconds", 1800)),
            audio_queue_max_frames=int(limits.get("audio_queue_max_frames", 256)),
            clock=clock,
        )
        if replace:
            return cls.configure(**kwargs)
        return cls(**kwargs)

    # ---- Read-side helpers (lock-free fast path) --------------------------

    def get(self, session_id: str) -> Optional[BridgeSession]:
        """Return the live session or ``None``. Lock-free."""
        return self._sessions.get(session_id)

    def count(self) -> int:
        """Number of live (non-closed) sessions. Lock-free."""
        return len(self._sessions)

    def iter_active(self) -> Iterator[BridgeSession]:
        """Iterate over live sessions. Snapshot the values so callers can
        mutate the registry during iteration safely.
        """
        return iter(list(self._sessions.values()))

    # ---- Mutating ops (locked) -------------------------------------------

    async def create_or_resume(
        self,
        session_id: str,
        *,
        context_id: str = "",
        asr_provider: str = "",
        tts_provider: str = "",
        input_codec: str = "",
        output_codec: str = "",
        language: str = "auto",
        barge_in: bool = True,
    ) -> tuple[BridgeSession, bool]:
        """Return ``(session, resumed)``.

        Resume semantics:

        * If a live session with ``session_id`` exists → return it as-is
          (``resumed=True``). The caller may overwrite metadata if desired.
        * Else if a tombstoned session with ``session_id`` exists and was last
          active within ``session_resume_window_seconds`` → re-promote it
          (``resumed=True``). All asyncio primitives are recreated lazily
          (the old queue/event may belong to a dead loop after a reconnect).
        * Else if the registry is at ``max_concurrent_sessions`` → raise
          :class:`RegistryFull`.
        * Else → construct a fresh session (``resumed=False``).
        """
        if not session_id:
            raise ValueError("session_id must be non-empty")
        async with self._lock:
            now = self._clock()
            self._expire_tombstones(now)

            live = self._sessions.get(session_id)
            if live is not None and live.state != STATE_CLOSED:
                live.touch(now)
                return live, True

            tomb_entry = self._tombstones.pop(session_id, None)
            if tomb_entry is not None:
                last_seen, snapshot = tomb_entry
                if (now - last_seen) <= self.session_resume_window_seconds:
                    # Re-promote: rebuild a fresh BridgeSession but keep the id
                    # and any prior context/codec/language so the client can
                    # reconnect transparently.
                    resumed = BridgeSession(
                        session_id=session_id,
                        context_id=context_id or snapshot.context_id,
                        asr_provider=asr_provider or snapshot.asr_provider,
                        tts_provider=tts_provider or snapshot.tts_provider,
                        input_codec=input_codec or snapshot.input_codec,
                        output_codec=output_codec or snapshot.output_codec,
                        language=language if language != "auto" else snapshot.language,
                        barge_in=barge_in,
                        audio_queue_max_frames=self.audio_queue_max_frames,
                    )
                    resumed.created_at = snapshot.created_at  # preserve origin
                    resumed.touch(now)
                    # ready state on resume — handshake already completed once.
                    resumed.transition_to("ready")
                    self._sessions[session_id] = resumed
                    return resumed, True
                # else: tombstone expired between expire pass and pop — drop it.

            if len(self._sessions) >= self.max_concurrent_sessions:
                raise RegistryFull(len(self._sessions), self.max_concurrent_sessions)

            fresh = BridgeSession(
                session_id=session_id,
                context_id=context_id,
                asr_provider=asr_provider,
                tts_provider=tts_provider,
                input_codec=input_codec,
                output_codec=output_codec,
                language=language,
                barge_in=barge_in,
                audio_queue_max_frames=self.audio_queue_max_frames,
            )
            fresh.touch(now)
            self._sessions[session_id] = fresh
            return fresh, False

    async def remove(self, session_id: str, *, tombstone: bool = True) -> bool:
        """Remove ``session_id`` from the live map and close it.

        When ``tombstone`` is True (default) the snapshot is retained for
        ``session_resume_window_seconds`` so a quick reconnect can resume.

        Returns True if a live session was removed.
        """
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return False
            session.close()
            if tombstone and self.session_resume_window_seconds > 0:
                self._tombstones[session_id] = (self._clock(), session)
            return True

    async def gc_idle(
        self,
        now: float | None = None,
        ttl: float | None = None,
    ) -> list[str]:
        """Evict sessions idle longer than ``ttl`` (default ``max_session_seconds``).

        Returns the list of removed session_ids. Tombstones are also pruned
        against ``session_resume_window_seconds`` as a free side-effect.
        """
        async with self._lock:
            nowv = self._clock() if now is None else float(now)
            ttlv = self.max_session_seconds if ttl is None else float(ttl)
            evicted: list[str] = []
            for sid, session in list(self._sessions.items()):
                if (nowv - session.last_activity_at) > ttlv:
                    session.close()
                    self._sessions.pop(sid, None)
                    # Eviction does NOT tombstone — the session aged out, the
                    # client clearly isn't coming back.
                    evicted.append(sid)
            self._expire_tombstones(nowv)
            return evicted

    # ---- Internal helpers -------------------------------------------------

    def _expire_tombstones(self, now: float) -> None:
        if self.session_resume_window_seconds <= 0:
            self._tombstones.clear()
            return
        expired = [
            sid for sid, (ts, _) in self._tombstones.items()
            if (now - ts) > self.session_resume_window_seconds
        ]
        for sid in expired:
            self._tombstones.pop(sid, None)


__all__ = [
    "ConfigError",
    "DEFAULT_CONFIG_PATH",
    "RUNTIME_CONFIG_PATH",
    "load_config",
    "validate_config",
    "save_overlay",
    # A1.3 BridgeRegistry
    "BridgeRegistry",
    "RegistryFull",
    "ClockFn",
]
