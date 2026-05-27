"""Standalone Voqualizer typed-prompt submit endpoint.

This plugin-scoped endpoint intentionally avoids importing ``api.message_async``
from the live plugin loader context, because that route can be shadowed by the
runtime's dynamic module cache.  It implements the same JSON prompt flow as the
core async message handler:

- read ``text``, ``context``, and ``message_id``;
- resolve/use the target AgentContext;
- run ``user_message_ui`` extensions;
- log the user message;
- start ``context.communicate(...)`` without awaiting the final response;
- return ``Message received.`` with the context id.
"""

from __future__ import annotations

import os
import sys
from typing import Any

_A0_ROOT = "/a0"
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_a0_root_first() -> None:
    """Prefer framework imports over plugin-local helper packages."""
    sys.path[:] = [p for p in sys.path if p not in ("", _PLUGIN_DIR)]
    if _A0_ROOT in sys.path:
        sys.path.remove(_A0_ROOT)
    sys.path.insert(0, _A0_ROOT)


_ensure_a0_root_first()

from agent import UserMessage  # noqa: E402
from helpers import extension, message_queue as mq  # noqa: E402
from helpers.api import ApiHandler, Request, Response  # noqa: E402


class VoqualizerMessageAsync(ApiHandler):
    """Submit standalone typed prompts using the core async message semantics."""

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    async def process(self, input: dict[str, Any], request: Request) -> dict | Response:
        text = str(input.get("text") or "")
        ctxid = str(input.get("context") or "")
        message_id = str(input.get("message_id") or "")
        attachment_paths: list[str] = []

        context = self.use_context(ctxid)

        data: dict[str, Any] = {
            "message": text,
            "attachment_paths": attachment_paths,
        }
        await extension.call_extensions_async(
            "user_message_ui",
            agent=context.get_agent(),
            data=data,
        )

        message = str(data.get("message") or "")
        attachment_paths = list(data.get("attachment_paths") or [])

        mq.log_user_message(context, message, attachment_paths, message_id or None)
        context.communicate(
            UserMessage(
                message=message,
                attachments=attachment_paths,
                id=message_id or "",
            )
        )

        return {
            "message": "Message received.",
            "context": context.id,
        }
