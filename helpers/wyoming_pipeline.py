"""Composable Wyoming Voqualizer pipeline scaffold.

This module connects the W4 ASR, W5 prompt/assistant, and W6 TTS adapters into
one interface-scoped pipeline. It is still provider-agnostic, but encodes the
breaking Wyoming rewrite rule: every request is routed through the Wyoming
interface's fixed ctxID and old custom websocket routes are not part of the
runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .wyoming_asr import WyomingAsrAdapter
from .wyoming_prompt import WyomingPromptAdapter
from .wyoming_protocol import WyomingEvent, event
from .wyoming_server import WyomingSession
from .wyoming_tts import WyomingTtsAdapter


@dataclass(slots=True)
class WyomingPipelineDebug:
    event_count: int = 0
    asr_events: int = 0
    prompt_events: int = 0
    tts_events: int = 0
    generated_events: int = 0
    last_event_type: str = ""
    last_generation_id: str = ""
    last_ctxid: str = ""
    dropped_stale_events: int = 0
    unsupported_events: int = 0


@dataclass(slots=True)
class WyomingVoqualizerPipeline:
    """Route Wyoming events through ASR -> prompt -> authoritative TTS."""

    asr: WyomingAsrAdapter = field(default_factory=WyomingAsrAdapter)
    prompt: WyomingPromptAdapter = field(default_factory=WyomingPromptAdapter)
    tts: WyomingTtsAdapter = field(default_factory=WyomingTtsAdapter)
    speak_assistant_finals: bool = True
    debug: WyomingPipelineDebug = field(default_factory=WyomingPipelineDebug)

    async def handle_event(self, session: WyomingSession, incoming: WyomingEvent) -> list[WyomingEvent]:
        self.debug.event_count += 1
        self.debug.last_event_type = incoming.type
        self.debug.last_ctxid = session.ctxid

        if incoming.type == "describe":
            info = session.info_event()
            self.debug.generated_events += 1
            return [info]

        if incoming.type in {"audio-start", "audio-chunk", "audio-stop"}:
            self.debug.asr_events += 1
            asr_replies = await self.asr.handle_event(session, incoming)
            return await self._post_process(session, asr_replies)

        if incoming.type in {"transcript", "voqualizer-text-prompt"}:
            self.debug.prompt_events += 1
            prompt_replies = await self.prompt.handle_event(session, incoming)
            return await self._post_process(session, prompt_replies)

        if incoming.type in {"synthesize", "voqualizer-response-final"}:
            self.debug.tts_events += 1
            tts_replies = await self.tts.handle_event(session, incoming)
            return await self._post_process(session, tts_replies, synthesize_finals=False)

        if incoming.type == "voqualizer-control":
            action = str(incoming.data.get("action") or "").strip().lower()
            if action in {"cancel", "cancel_tts", "barge_in", "stop", "stop_tts"}:
                # Browser clients expose cancelTts() as a Wyoming control event;
                # normalize it to the same interface-scoped cancel path used by
                # native Wyoming cancel/pause-satellite events.
                incoming = event(
                    "cancel",
                    reason=str(incoming.data.get("reason") or action or "client_cancel"),
                    source="voqualizer-control",
                )
            else:
                self.debug.unsupported_events += 1
                return [event("error", code="unsupported_control_action", message=f"Unsupported control action: {action}")]

        if incoming.type in {"cancel", "voqualizer-cancel", "pause-satellite"}:
            # Cancel all three adapters so barge-in clears ASR/prompt/TTS state for
            # this interface/session only.
            replies: list[WyomingEvent] = []
            replies.extend(self.prompt.handle_cancel(session, incoming))
            replies.extend(self.tts.handle_cancel(session, incoming))
            self.debug.last_generation_id = session.active_generation_id or ""
            self.debug.generated_events += len(replies)
            return replies

        self.debug.unsupported_events += 1
        return [event("error", code="unsupported_pipeline_event", message=f"Unsupported Wyoming event: {incoming.type}")]

    async def _post_process(self, session: WyomingSession, replies: Iterable[WyomingEvent], *, synthesize_finals: bool = True) -> list[WyomingEvent]:
        output: list[WyomingEvent] = []
        for reply in replies:
            output.append(reply)
            generation_id = str(reply.data.get("generation_id") or session.active_generation_id or "")
            if generation_id:
                self.debug.last_generation_id = generation_id
            if (
                synthesize_finals
                and self.speak_assistant_finals
                and reply.type == "voqualizer-response-final"
                and str(reply.data.get("generation_id") or "") == str(session.active_generation_id or "")
            ):
                # Authoritative audio path: assistant final text triggers Wyoming
                # audio-start/audio-chunk/audio-stop only through WyomingTtsAdapter.
                tts_replies = await self.tts.handle_response_final(session, reply)
                output.extend(tts_replies)
        self.debug.generated_events += len(output)
        return output

    def snapshot(self) -> dict[str, object]:
        return {
            "event_count": self.debug.event_count,
            "asr_events": self.debug.asr_events,
            "prompt_events": self.debug.prompt_events,
            "tts_events": self.debug.tts_events,
            "generated_events": self.debug.generated_events,
            "last_event_type": self.debug.last_event_type,
            "last_generation_id": self.debug.last_generation_id,
            "last_ctxid": self.debug.last_ctxid,
            "dropped_stale_events": self.debug.dropped_stale_events,
            "unsupported_events": self.debug.unsupported_events,
        }

    def install_into(self, runtime) -> None:
        """Register this pipeline's handle_event for every supported event type.

        The Wyoming interface runtime dispatches by event type, so the pipeline
        registers itself as the handler for ASR, prompt, TTS, and control event
        types it understands. Unsupported events fall through to the runtime's
        default error response.
        """
        supported_event_types = (
            "audio-start",
            "audio-chunk",
            "audio-stop",
            "transcript",
            "voqualizer-text-prompt",
            "synthesize",
            "voqualizer-control",
        )
        for event_type in supported_event_types:
            runtime.on(event_type, self.handle_event)
