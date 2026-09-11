"""Millennial Mum — Foundry Hosted Agent Server (decomposed / Agent Framework).

Drop-in replacement for ``server.py`` that serves the **decomposed** system:
the triage orchestrator (router-as-tools) built on Microsoft Agent Framework,
with each specialist on its own Foundry catalog model.

Differences from ``server.py``:
  * No GitHub Copilot SDK. Auth is Azure identity (DefaultAzureCredential) to a
    Foundry project via ``FOUNDRY_PROJECT_ENDPOINT`` + per-agent model env vars.
  * Emits OpenTelemetry traces to Application Insights (see agents/observability).
  * Returns the orchestrator's composed reply and logs which specialists routed.

Run: python server_af.py
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv(override=False)

from azure.ai.agentserver.responses import ResponsesAgentServerHost, TextResponse
from agent_framework import Message
from starlette.middleware.cors import CORSMiddleware

from agents.observability import setup_observability
from agents.orchestrator import get_orchestrator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("millennial-mum-af")

setup_observability()

app = ResponsesAgentServerHost()

# CORS: restrict to known origins in production.
ALLOWED_ORIGINS = [
    "https://thankful-desert-05e2c3e0f.6.azurestaticapps.net",
    "http://localhost:4280",
    "http://127.0.0.1:4280",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


def _extract_user_message(input_items) -> str:
    """Pull the latest user text out of the Foundry responses request input."""
    if isinstance(input_items, str):
        return input_items

    for item in reversed(list(input_items or [])):
        role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else None)
        if role != "user":
            continue
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if hasattr(block, "text"):
                    return block.text
                if hasattr(block, "input_text"):
                    return block.input_text
                if isinstance(block, dict):
                    txt = block.get("text", "") or block.get("input_text", "")
                    if txt:
                        return txt
    return str(input_items) if input_items else "Hello"


# Map Responses item roles -> agent_framework ChatMessage roles.
_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "system": "system",
    "developer": "system",
}


def _part_text(part) -> str:
    text = getattr(part, "text", None)
    if text is None and isinstance(part, dict):
        text = part.get("text") or part.get("input_text")
    return text or ""


def _item_to_message(item) -> Message | None:
    """Convert one Responses input item into a ChatMessage (or None to skip)."""
    role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else None)
    mapped = _ROLE_MAP.get(role)
    if mapped is None:
        return None  # skip function calls / tool outputs / references

    content = getattr(item, "content", None)
    if content is None and isinstance(item, dict):
        content = item.get("content")

    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    else:
        for part in content or []:
            ptype = getattr(part, "type", None) or (part.get("type") if isinstance(part, dict) else None)
            if ptype in ("input_text", "output_text", "text") or ptype is None:
                txt = _part_text(part)
                if txt:
                    parts.append(txt)

    text = "\n".join(parts).strip()
    if not text:
        return None
    return Message(role=mapped, contents=text)


async def _build_conversation(request, context) -> list[Message]:
    """Rebuild the full multi-turn transcript from the request input items.

    The client (PWA) carries the running transcript and sends it as the
    Responses ``input`` array (user turns as ``input_text``, prior assistant
    turns as ``output_text``). We map every message item to a ChatMessage so the
    orchestrator sees the whole conversation and can answer in context — earlier
    we passed only the latest user string, which made the agent forget context.
    """
    messages: list[Message] = []
    try:
        items = await context.get_input_items()
        for item in items:
            msg = _item_to_message(item)
            if msg is not None:
                messages.append(msg)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to expand input items, falling back to latest turn: %s", e)

    if not messages:
        input_items = request.input if hasattr(request, "input") else []
        messages = [Message(role="user", contents=_extract_user_message(input_items))]
    return messages


@app.response_handler
async def handle_response(request, context, cancellation_signal):
    """Process an incoming message through the decomposed orchestrator."""
    messages = await _build_conversation(request, context)
    last_user = next(
        (m.text for m in reversed(messages) if m.role == "user" and getattr(m, "text", None)),
        "",
    )
    logger.info("Turn: %d msgs, latest user: %s", len(messages), last_user[:80])

    async def get_reply():
        try:
            result = await get_orchestrator().run_traced(messages)
            routed = ", ".join(result.get("agents_used", [])) or "(none)"
            logger.info("Routed to: %s", routed)
            return result["response"]
        except Exception as e:  # pragma: no cover - depends on live Foundry
            logger.error("Agent error: %s", e, exc_info=True)
            return "⚠️ Something went wrong on my end. Please try again in a moment."

    return TextResponse(context, request, text=get_reply)


def main():
    port = int(os.environ.get("PORT", "8088"))
    logger.info("Starting millennial-mum (decomposed) server on port %s", port)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()

