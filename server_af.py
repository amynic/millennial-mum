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

from agents.latency import REQUEST_ID_HEADER, TurnTimer, extract_request_id
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
    allow_headers=["Content-Type", "Authorization", REQUEST_ID_HEADER],
)


def _incoming_request_id(context) -> str | None:
    """Recover the proxy's correlation id from the Responses request context.

    The agent server extracts request headers prefixed ``x-client-`` into
    ``ResponseContext.client_headers``, which is how our id crosses the Foundry
    platform boundary. If it isn't there — an older proxy, a direct API call, or
    a platform that dropped the header — we return ``None`` and the timer mints a
    fresh id so the hosted-agent leg is still measurable on its own.
    """
    for attr in ("client_headers", "headers"):
        headers = getattr(context, attr, None)
        found = extract_request_id(headers)
        if found:
            return found
    return None


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
    return Message(role=mapped, contents=[text])


async def _build_conversation(request, context) -> list[Message]:
    """Rebuild the full multi-turn transcript from the request input items.

    The client (PWA) carries the running transcript and sends it as the
    Responses ``input`` array. We map every user and assistant message item to a
    ChatMessage so the orchestrator sees the whole conversation and can answer
    in context — earlier we passed only the latest user string, which made the
    agent forget context.
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
        messages = [Message(role="user", contents=[_extract_user_message(input_items)])]
    return messages


@app.response_handler
async def handle_response(request, context, cancellation_signal):
    """Process an incoming message through the decomposed orchestrator.

    Streams the composed reply token-by-token: when the client sends
    ``stream: true`` the SDK relays each text delta as an SSE
    ``response.output_text.delta`` event, so the PWA renders the answer as it's
    generated instead of waiting for the full reply.

    The turn is measured end-to-end (:class:`agents.latency.TurnTimer`) under the
    correlation id the proxy forwarded, so per-hop durations, time to first
    token, and the chosen route all land in Application Insights against the same
    request id the proxy and the PWA logged.
    """
    request_id = _incoming_request_id(context)
    timer = TurnTimer("hosted_agent", request_id)

    with timer.stage("build_conversation"):
        messages = await _build_conversation(request, context)
    last_user = next(
        (m.text for m in reversed(messages) if m.role == "user" and getattr(m, "text", None)),
        "",
    )
    timer.annotate(turns=len(messages))
    logger.info(
        "Turn [%s]: %d msgs, latest user: %s", timer.request_id, len(messages), last_user[:80]
    )

    with timer.stage("get_orchestrator"):
        orchestrator = get_orchestrator()

    async def token_stream():
        try:
            async for chunk in orchestrator.stream_traced(messages, timer=timer):
                yield chunk
            routed = ", ".join(orchestrator.last_agents_used()) or "(none)"
            summary = timer.finish(
                route_mode=orchestrator.last_route_mode(),
                agent_count=len(orchestrator.last_agents_used()),
            )
            logger.info(
                "Routed to: %s (%s, ttft=%sms, total=%sms)",
                routed,
                orchestrator.last_route_mode(),
                summary.get("first_token_ms"),
                summary.get("total_ms"),
            )
        except Exception as e:  # pragma: no cover - depends on live Foundry
            logger.error("Agent error [%s]: %s", timer.request_id, e, exc_info=True)
            timer.finish(failed=True)
            yield "⚠️ Something went wrong on my end. Please try again in a moment."

    return TextResponse(context, request, text=token_stream())


def main():
    port = int(os.environ.get("PORT", "8088"))
    logger.info("Starting millennial-mum (decomposed) server on port %s", port)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
