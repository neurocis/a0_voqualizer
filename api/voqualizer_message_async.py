"""Standalone Voqualizer typed-prompt submit proxy.

The standalone page uses this plugin-scoped endpoint instead of the core
``/api/message_async`` route so failures are reported through the normal plugin
API path and the page stays isolated from core route/caching quirks.
"""

from __future__ import annotations

import os
import sys
from typing import Any

_A0_ROOT = "/a0"
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_a0_root_first() -> None:
    """Prefer framework imports over plugin-local helper packages."""
    if _A0_ROOT in sys.path:
        sys.path.remove(_A0_ROOT)
    sys.path.insert(0, _A0_ROOT)


_ensure_a0_root_first()

from helpers.api import ApiHandler, Request, Response  # noqa: E402


class VoqualizerMessageAsync(ApiHandler):
    """Proxy typed standalone prompts to the core MessageAsync handler."""

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    async def process(self, input: dict[str, Any], request: Request) -> dict | Response:
        _ensure_a0_root_first()
        from api.message_async import MessageAsync

        payload = {
            "text": str(input.get("text") or ""),
            "context": str(input.get("context") or ""),
            "message_id": str(input.get("message_id") or ""),
        }
        core = MessageAsync(self.app, self.thread_lock)
        return await core.process(payload, request)
