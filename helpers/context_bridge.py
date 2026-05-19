"""A0 chat/context bridge for a0_voqualizer (M5 / A5.1).

This module is intentionally small and framework-adapter focused: it maps a
Voqualizer voice ``session_id`` to exactly one Agent Zero ``AgentContext`` and
injects final ASR transcript text through ``context.communicate(UserMessage)``.

The implementation avoids importing the full A0 runtime at module import time so
normal plugin unit tests remain deterministic and can stub the runtime objects.
Live runtime classes are imported lazily only when a default bridge is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
import uuid

try:  # pragma: no cover - TYPE_CHECKING imports only
    from typing import TypeAlias
except ImportError:  # pragma: no cover
    TypeAlias = Any  # type: ignore


class ContextBridgeError(Exception):
    """Base error for JSON-safe context bridge failures."""

    code = "CONTEXT_BRIDGE_ERROR"
    recoverable = True

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
            "details": dict(self.details),
        }


class ContextBridgeUnavailableError(ContextBridgeError):
    """Raised when the A0 AgentContext/UserMessage runtime is unavailable."""

    code = "CONTEXT_BRIDGE_UNAVAILABLE"


class ContextBridgeInputError(ContextBridgeError):
    """Raised for invalid session ids, context ids, or transcript text."""

    code = "CONTEXT_BRIDGE_BAD_REQUEST"


class _AgentContextLike(Protocol):
    id: str
    config: Any

    def communicate(self, msg: Any, broadcast_level: int = 1) -> Any: ...


ContextGetter = Callable[[str], _AgentContextLike | None]
ContextFactory = Callable[..., _AgentContextLike]
UserMessageFactory = Callable[..., Any]
ConfigFactory = Callable[[], Any]


@dataclass(slots=True)
class BridgeContextBinding:
    """A stable mapping from a voice bridge session to an A0 chat context."""

    session_id: str
    context_id: str
    created: bool = False
    reused: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "context_id": self.context_id,
            "created": self.created,
            "reused": self.reused,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class TranscriptInjectionResult:
    """Result of injecting a transcript into AgentContext.communicate."""

    session_id: str
    context_id: str
    message_id: str
    text: str
    task: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "context_id": self.context_id,
            "message_id": self.message_id,
            "text": self.text,
            "task": self.task,
        }


def _clean_non_empty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextBridgeInputError(f"{name} must be a non-empty string", details={name: value})
    return value.strip()


def _asr_provider_label(metadata: dict[str, Any] | None) -> str:
    if not isinstance(metadata, dict):
        return "unknown"
    for key in ("asr_provider_display_name", "provider_display_name", "asr_provider", "provider"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _visible_asr_prompt(text: str, metadata: dict[str, Any] | None) -> str:
    """Return the visible prompt text for ASR-originated injections.

    A0's normal ``AgentContext.communicate(UserMessage)`` path is what writes
    submitted user prompts into the visible chat log.  Prefixing exactly here
    guarantees the log entry is created only when the final ASR transcript is
    actually injected into the target context, never for partial transcripts or
    suppressed candidates.
    """

    if not isinstance(metadata, dict):
        return text
    source = str(metadata.get("source") or "")
    is_asr = source == "voqualizer_asr_final" or bool(metadata.get("asr_prompt"))
    if not is_asr:
        return text
    if text.lstrip().startswith("{ASR:"):
        return text
    return f"{{ASR: {_asr_provider_label(metadata)}}} {text}"


def _load_runtime() -> tuple[type, type]:
    """Load Agent Zero runtime classes lazily.

    Keeping this import out of module import time lets pytest run without fully
    bootstrapping Agent Zero or starting network/model-dependent services.
    """

    try:
        from agent import AgentContext, UserMessage  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised via injection tests
        raise ContextBridgeUnavailableError(
            "A0 AgentContext/UserMessage runtime is unavailable",
            details={"error": str(exc)},
        ) from exc
    return AgentContext, UserMessage


def _default_context_getter(context_id: str) -> _AgentContextLike | None:
    AgentContext, _ = _load_runtime()
    return AgentContext.get(context_id)


def _default_context_factory(**kwargs: Any) -> _AgentContextLike:
    AgentContext, _ = _load_runtime()
    return AgentContext(**kwargs)


def _default_user_message_factory(**kwargs: Any) -> Any:
    _, UserMessage = _load_runtime()
    return UserMessage(**kwargs)


def _default_config_factory() -> Any:
    AgentContext, _ = _load_runtime()
    current = AgentContext.current()
    if current is not None:
        return current.config
    first = AgentContext.first()
    if first is not None:
        return first.config
    raise ContextBridgeUnavailableError(
        "No active A0 AgentContext is available to provide a config for a new Voqualizer context"
    )


class ContextBridge:
    """Map Voqualizer sessions to AgentContext instances and inject transcripts.

    A single ``ContextBridge`` instance owns one binding table. The WS handler can
    keep it globally or per-process; tests may instantiate isolated bridges. For
    each ``session_id`` the first resolved/created context is reused on all later
    injections, even if later calls omit ``context_id``.
    """

    def __init__(
        self,
        *,
        context_getter: ContextGetter | None = None,
        context_factory: ContextFactory | None = None,
        user_message_factory: UserMessageFactory | None = None,
        config_factory: ConfigFactory | None = None,
        name_prefix: str = "Voqualizer",
    ) -> None:
        self._bindings: dict[str, BridgeContextBinding] = {}
        self._context_getter = context_getter or _default_context_getter
        self._context_factory = context_factory or _default_context_factory
        self._user_message_factory = user_message_factory or _default_user_message_factory
        self._config_factory = config_factory or _default_config_factory
        self.name_prefix = name_prefix

    def get_binding(self, session_id: str) -> BridgeContextBinding | None:
        session_id = _clean_non_empty(session_id, "session_id")
        return self._bindings.get(session_id)

    def bindings_for_context(self, context_id: str) -> list[BridgeContextBinding]:
        """Return all session bindings currently mapped to ``context_id``.

        A5.2 uses this reverse lookup from the Agent Zero response-stream
        extension, where the framework gives us ``agent.context.id`` rather than
        a Voqualizer ``session_id``. Returning a copy keeps callers from
        mutating the bridge's binding table while iterating.
        """

        context_id = _clean_non_empty(context_id, "context_id")
        return [
            binding
            for binding in self._bindings.values()
            if binding.context_id == context_id
        ]

    def bind_session(
        self,
        session_id: str,
        *,
        context_id: str | None = None,
        create: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> BridgeContextBinding:
        """Resolve or create the AgentContext for a Voqualizer session.

        If a binding already exists for ``session_id`` it is returned unchanged.
        If ``context_id`` is supplied it must resolve via ``AgentContext.get``;
        otherwise a new context is created when ``create`` is true.
        """

        session_id = _clean_non_empty(session_id, "session_id")
        existing = self._bindings.get(session_id)
        if existing is not None:
            return existing

        metadata = dict(metadata or {})
        requested_context_id = context_id.strip() if isinstance(context_id, str) else ""

        if requested_context_id:
            context = self._context_getter(requested_context_id)
            if context is None:
                raise ContextBridgeInputError(
                    f"context_id {requested_context_id!r} was not found",
                    details={"session_id": session_id, "context_id": requested_context_id},
                )
            binding = BridgeContextBinding(
                session_id=session_id,
                context_id=str(context.id),
                created=False,
                reused=True,
                metadata=metadata,
            )
            self._bindings[session_id] = binding
            return binding

        if not create:
            raise ContextBridgeInputError(
                "session is not bound to an AgentContext",
                details={"session_id": session_id},
            )

        context = self._create_context(session_id=session_id, metadata=metadata)
        binding = BridgeContextBinding(
            session_id=session_id,
            context_id=str(context.id),
            created=True,
            reused=False,
            metadata=metadata,
        )
        self._bindings[session_id] = binding
        return binding

    def _create_context(self, *, session_id: str, metadata: dict[str, Any]) -> _AgentContextLike:
        config = self._config_factory()
        name = metadata.get("name") or f"{self.name_prefix} {session_id[:8]}"
        data = {
            "voqualizer_session_id": session_id,
            **{k: v for k, v in metadata.items() if k != "name"},
        }
        try:
            return self._context_factory(config=config, name=name, data=data)
        except TypeError:
            # Test doubles and future framework constructors may accept fewer
            # keyword arguments. Preserve A5.1 semantics by retrying minimally.
            return self._context_factory(config=config)

    def inject_transcript(
        self,
        session_id: str,
        text: str,
        *,
        context_id: str | None = None,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        broadcast_level: int = 1,
    ) -> TranscriptInjectionResult:
        """Send a final ASR transcript to the mapped AgentContext.

        The transcript enters A0 through the normal ``communicate(UserMessage)``
        path. This method returns immediately with the framework task object; it
        does not await LLM completion, keeping tests and WS handlers deterministic.
        """

        session_id = _clean_non_empty(session_id, "session_id")
        text = _clean_non_empty(text, "text")
        message_id = str(message_id or uuid.uuid4().hex)
        binding = self.bind_session(
            session_id,
            context_id=context_id,
            create=True,
            metadata=metadata,
        )
        context = self._context_getter(binding.context_id)
        if context is None:
            raise ContextBridgeUnavailableError(
                "bound AgentContext is no longer available",
                details={"session_id": session_id, "context_id": binding.context_id},
            )

        visible_text = _visible_asr_prompt(text, metadata)
        msg = self._user_message_factory(message=visible_text, id=message_id)
        task = context.communicate(msg, broadcast_level=broadcast_level)
        return TranscriptInjectionResult(
            session_id=session_id,
            context_id=binding.context_id,
            message_id=message_id,
            text=visible_text,
            task=task,
        )

    def unbind_session(self, session_id: str) -> BridgeContextBinding | None:
        session_id = _clean_non_empty(session_id, "session_id")
        return self._bindings.pop(session_id, None)


_default_bridge: ContextBridge | None = None


def get_default_context_bridge() -> ContextBridge:
    """Return the process-wide context bridge used by future WS integration."""

    global _default_bridge
    if _default_bridge is None:
        _default_bridge = ContextBridge()
    return _default_bridge


__all__ = [
    "BridgeContextBinding",
    "ContextBridge",
    "ContextBridgeError",
    "ContextBridgeInputError",
    "ContextBridgeUnavailableError",
    "TranscriptInjectionResult",
    "get_default_context_bridge",
]
