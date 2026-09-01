"""Live target runners for the eval harness.

These execute a real turn against either the current monolith or the
decomposed system and return ``{response, tool_calls, agents_used}``. Both
require credentials (GITHUB_TOKEN for the monolith; Foundry/Azure identity for
the decomposed system), so imports are lazy and this module is only touched by
``BaselineMonolithTarget`` / ``DecomposedTarget`` at run time.
"""

from __future__ import annotations

import asyncio


def run_decomposed(query: str) -> dict:
    """Run one turn against the decomposed orchestrator (needs Foundry)."""
    from agents.orchestrator import get_orchestrator

    async def _go():
        return await get_orchestrator().run_traced(query)

    return asyncio.run(_go())


def run_monolith(query: str, capture_tools: bool = True) -> dict:
    """Run one turn against the current Copilot-SDK monolith (needs GITHUB_TOKEN)."""
    import os

    from copilot import CopilotClient
    from copilot.session import PermissionHandler

    from agent_config import SYSTEM_PROMPT
    from tools import ALL_TOOLS
    from tools.memory import get_profile_context

    async def _go():
        client = CopilotClient(github_token=os.environ.get("GITHUB_TOKEN"))
        await client.start()
        try:
            session = await client.create_session(
                on_permission_request=PermissionHandler.approve_all,
                tools=ALL_TOOLS,
                system_message={"content": SYSTEM_PROMPT + get_profile_context()},
                streaming=False,
            )
            reply = await session.send_and_wait(query)
            data = getattr(reply, "data", None)
            text = ""
            if data is not None:
                text = getattr(data, "content", None) or ""
            return {"response": text, "tool_calls": [], "agents_used": ["monolith"]}
        finally:
            await client.stop()

    return asyncio.run(_go())
