"""Authentication helpers for a0_voqualizer session-bound WS events.

The framework-level WebSocket handler already enforces A0 authentication and
CSRF for this plugin handler.  This module adds the M5 per-session bearer token:
a short-lived opaque token issued with ``voqualizer_ready`` and required on
subsequent operations that act on the bound voice session.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

from .session import BridgeSession


SESSION_TOKEN_METADATA_KEY = "session_bearer_token"
AUTH_ERROR_CODE = "AUTH_REQUIRED"


def generate_session_bearer_token() -> str:
    """Return an opaque bearer token suitable for a single Voqualizer session."""

    return secrets.token_urlsafe(32)


def ensure_session_bearer_token(session: BridgeSession) -> str:
    """Return the existing per-session token or create one lazily."""

    existing = session.metadata.get(SESSION_TOKEN_METADATA_KEY)
    if isinstance(existing, str) and existing:
        return existing
    token = generate_session_bearer_token()
    session.metadata[SESSION_TOKEN_METADATA_KEY] = token
    return token


def extract_bearer_token(payload: Any) -> str | None:
    """Extract a bearer token from a protocol payload.

    Supported deterministic client shapes:
    - ``{"bearer_token": "..."}``
    - ``{"session_token": "..."}``
    - ``{"authorization": "Bearer ..."}``
    - ``{"auth": {"bearer_token": "..."}}``
    - ``{"auth": {"authorization": "Bearer ..."}}``
    """

    if not isinstance(payload, Mapping):
        return None

    for key in ("bearer_token", "session_token", "token"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value

    authorization = payload.get("authorization") or payload.get("Authorization")
    if isinstance(authorization, str):
        token = _parse_authorization_header(authorization)
        if token:
            return token

    auth = payload.get("auth")
    if isinstance(auth, Mapping):
        for key in ("bearer_token", "session_token", "token"):
            value = auth.get(key)
            if isinstance(value, str) and value:
                return value
        authorization = auth.get("authorization") or auth.get("Authorization")
        if isinstance(authorization, str):
            token = _parse_authorization_header(authorization)
            if token:
                return token

    return None


def verify_session_bearer_token(session: BridgeSession, payload: Any) -> bool:
    """Return true when ``payload`` carries the token issued for ``session``."""

    expected = ensure_session_bearer_token(session)
    supplied = extract_bearer_token(payload)
    return isinstance(supplied, str) and secrets.compare_digest(supplied, expected)


def _parse_authorization_header(value: str) -> str | None:
    parts = value.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
        return parts[1]
    return None
