"""Regression: client-supplied generation_id must be preserved in replies."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers.wyoming_prompt import WyomingPromptAdapter  # noqa: E402
from helpers.wyoming_protocol import WyomingEvent  # noqa: E402
from helpers.wyoming_interfaces import WyomingInterface  # noqa: E402
from helpers.wyoming_server import WyomingSession  # noqa: E402


def test_client_supplied_generation_id_propagates_to_response_events():
    iface = WyomingInterface(id="web", name="web", ctxid="ctx-1")
    session = WyomingSession(interface=iface)

    async def echo_provider(text, metadata):
        return f"echo:{text}"

    adapter = WyomingPromptAdapter(provider=echo_provider)
    incoming = WyomingEvent(
        type="voqualizer-text-prompt",
        data={"text": "hello", "generation_id": "client-gen-123"},
        payload=b"",
    )
    replies = asyncio.run(adapter.handle_event(session, incoming))
    gens = {str(r.data.get("generation_id") or "") for r in replies if r.data.get("generation_id")}
    assert gens == {"client-gen-123"}, f"unexpected generation ids: {gens}"
    assert session.active_generation_id == "client-gen-123"


def test_missing_client_generation_id_falls_back_to_session_new_generation():
    iface = WyomingInterface(id="web", name="web", ctxid="ctx-1")
    session = WyomingSession(interface=iface)

    async def echo_provider(text, metadata):
        return f"echo:{text}"

    adapter = WyomingPromptAdapter(provider=echo_provider)
    incoming = WyomingEvent(type="voqualizer-text-prompt", data={"text": "hello"}, payload=b"")
    replies = asyncio.run(adapter.handle_event(session, incoming))
    gens = {str(r.data.get("generation_id") or "") for r in replies if r.data.get("generation_id")}
    assert len(gens) == 1
    assert session.active_generation_id in gens


if __name__ == "__main__":
    test_client_supplied_generation_id_propagates_to_response_events()
    test_missing_client_generation_id_falls_back_to_session_new_generation()
    print('OK')
