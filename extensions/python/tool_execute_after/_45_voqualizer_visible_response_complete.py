"""Notify Wyoming submitters when the visible response tool completes.

Agent Zero may continue running monologue_end/process_chain_end hooks after the
response tool is visible. Voqualizer's browser lifecycle should finalize at the
visible response boundary, not after all post-response maintenance work.
"""
from __future__ import annotations

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class VisibleResponseComplete(Extension):
    async def execute(self, **kwargs):
        if str(kwargs.get("tool_name") or "") != "response":
            return
        try:
            from usr.plugins.a0_voqualizer.helpers.wyoming_a0_prompt_submitter import notify_visible_response_completion
            count = notify_visible_response_completion(
                self.agent,
                response=kwargs.get("response"),
                tool_name=str(kwargs.get("tool_name") or ""),
            )
            if count:
                PrintStyle(font_color="#8bd5ff", padding=False).print(
                    f"voqualizer: finalized {count} Wyoming submitter(s) at visible response completion"
                )
        except Exception as exc:  # noqa: BLE001
            PrintStyle.error(f"voqualizer visible response completion hook failed: {exc}")
