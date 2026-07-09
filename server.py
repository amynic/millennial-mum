"""Millennial Mum — Foundry Hosted Agent Server.

Wraps the Copilot SDK agent with the Foundry responses hosting adapter.
Run: python server.py
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv(override=False)

from azure.ai.agentserver.responses import ResponsesAgentServerHost
from starlette.middleware.cors import CORSMiddleware

from copilot import CopilotClient
from copilot.session import PermissionHandler

from agent_config import SYSTEM_PROMPT, AGENT_NAME
from tools import ALL_TOOLS
from tools.memory import get_profile_context

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(AGENT_NAME)

app = ResponsesAgentServerHost()

# CORS: restrict to known origins in production
ALLOWED_ORIGINS = [
    "https://blue-field-0a7fff90f.7.azurestaticapps.net",
    "http://localhost:4280",
    "http://127.0.0.1:4280",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.response_handler
async def handle_response(request, context, cancellation_signal):
    """Process incoming messages via the Copilot SDK agent."""
    from azure.ai.agentserver.responses import TextResponse

    # Extract the latest user message from the request input
    user_message = ""
    input_items = request.input if hasattr(request, "input") else []

    # Log raw input for debugging
    logger.info(f"Raw input type: {type(input_items).__name__}")
    logger.info(f"Raw input repr: {repr(input_items)[:300]}")

    # Handle string input directly (Foundry sends plain string)
    if isinstance(input_items, str):
        user_message = input_items
    else:
        # Handle structured input (list of message items)
        for item in reversed(list(input_items)):
            logger.info(f"  Item type={type(item).__name__}, attrs={[a for a in dir(item) if not a.startswith('_')][:10]}")
            if hasattr(item, "role") and item.role == "user":
                if hasattr(item, "content"):
                    content = item.content
                    if isinstance(content, str):
                        user_message = content
                    elif isinstance(content, list):
                        for block in content:
                            if hasattr(block, "text"):
                                user_message = block.text
                                break
                            elif hasattr(block, "input_text"):
                                user_message = block.input_text
                                break
                break
            # Also try dict-style access
            elif isinstance(item, dict):
                if item.get("role") == "user":
                    content = item.get("content", "")
                    if isinstance(content, str):
                        user_message = content
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict):
                                user_message = block.get("text", "") or block.get("input_text", "")
                                if user_message:
                                    break
                    break

    if not user_message:
        # Last resort: stringify the whole input
        user_message = str(input_items) if input_items else "Hello"
        logger.warning(f"Failed to extract message, using fallback: {user_message[:100]}")

    logger.info(f"Extracted message: {user_message[:80]}...")

    async def get_reply():
        try:
            github_token = os.environ.get("GITHUB_TOKEN")
            client = CopilotClient(github_token=github_token)
            await client.start()
            try:
                profile_context = get_profile_context()
                system_prompt = SYSTEM_PROMPT + profile_context

                session = await client.create_session(
                    on_permission_request=PermissionHandler.approve_all,
                    tools=ALL_TOOLS,
                    system_message={"content": system_prompt},
                    streaming=False,
                )

                reply = await session.send_and_wait(user_message)
                if reply and hasattr(reply, 'data'):
                    data = reply.data
                    text = getattr(data, 'content', None) or getattr(data, 'encrypted_content', None) or str(data.to_dict())
                else:
                    text = "Sorry, I couldn't generate a response."
            finally:
                await client.stop()
        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            text = "⚠️ Something went wrong on my end. Please try again in a moment."

        logger.info(f"Response generated ({len(text)} chars)")
        return text

    return TextResponse(context, request, text=get_reply)


def main():
    port = int(os.environ.get("PORT", "8088"))
    logger.info(f"Starting {AGENT_NAME} server on port {port}")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
