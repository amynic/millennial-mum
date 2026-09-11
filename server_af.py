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


@app.response_handler
async def handle_response(request, context, cancellation_signal):
    """Process an incoming message through the decomposed orchestrator."""
    # Use the SDK's resolver — it correctly expands typed ItemMessage /
    # input_text content from the Responses request. A hand-rolled extractor
    # missed these shapes and fed the orchestrator empty text (triage greeted
    # instead of routing).
    try:
        user_message = (await context.get_input_text()).strip()
    except Exception:  # pragma: no cover - defensive
        user_message = ""
    if not user_message:
        input_items = request.input if hasattr(request, "input") else []
        user_message = _extract_user_message(input_items)
    logger.info("Extracted message: %s", user_message[:80])

    async def get_reply():
        try:
            result = await get_orchestrator().run_traced(user_message)
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
