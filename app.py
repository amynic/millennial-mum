"""Millennial Mum 🍼💼 — The ultimate copilot for working parents.

Built with the GitHub Copilot Python SDK.
Run: python app.py
"""

import asyncio
import sys
import os

# Fix Windows console encoding for emojis
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

from copilot import CopilotClient
from copilot.session import PermissionHandler
from copilot.session_events import (
    AssistantMessageData,
    AssistantMessageDeltaData,
    AssistantReasoningData,
    ToolExecutionStartData,
    SessionIdleData,
)

from agent_config import SYSTEM_PROMPT, AGENT_NAME, AGENT_VERSION
from tools import ALL_TOOLS
from tools.memory import get_profile_context

# Terminal colours
BLUE = "\033[34m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_banner():
    print(f"""
{MAGENTA}{'='*60}
  🍼💼  {BOLD}MILLENNIAL MUM{RESET}{MAGENTA} v{AGENT_VERSION}
  The ultimate copilot for working parents
{'='*60}{RESET}

  Try asking me:
  • "What can I make for dinner with chicken, rice and broccoli? My kid is 3"
  • "Help me plan this week's schedule - nursery Mon/Wed/Fri, I work Tue/Thu"
  • "My toddler is bored and it's raining, I have 30 minutes"
  • "Draft an email to school - Lily is off sick today"
  • "I spent £45 at Tesco and £8 at the pharmacy today"

  Type 'quit' or Ctrl+C to exit.
""")


async def main():
    print_banner()

    client = CopilotClient()
    await client.start()

    # Inject saved family profile into the system prompt
    profile_context = get_profile_context()
    system_prompt = SYSTEM_PROMPT + profile_context

    session = await client.create_session(
        on_permission_request=PermissionHandler.approve_all,
        tools=ALL_TOOLS,
        system_message={"content": system_prompt},
        streaming=True,
    )

    def on_event(event):
        match event.data:
            case AssistantMessageDeltaData() as data:
                delta = data.delta_content or ""
                print(delta, end="", flush=True)
            case ToolExecutionStartData() as data:
                print(f"\n{BLUE}  🔧 Using: {data.tool_name}{RESET}")
            case AssistantReasoningData() as data:
                if data.content:
                    print(f"{YELLOW}  💭 {data.content[:80]}...{RESET}")

    session.on(on_event)

    print(f"{GREEN}Ready! What can I help with today?{RESET}\n")

    try:
        while True:
            user_input = input(f"{BOLD}You:{RESET} ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "bye"):
                print(f"\n{MAGENTA}👋 Take care! You're doing great. 💪{RESET}\n")
                break

            print(f"\n{GREEN}{AGENT_NAME}:{RESET} ", end="", flush=True)
            reply = await session.send_and_wait(user_input)

            # send_and_wait returns the final message; streaming already printed deltas
            # Just add a newline for clean formatting
            print("\n")

    except KeyboardInterrupt:
        print(f"\n\n{MAGENTA}👋 Bye! Remember: you're doing amazing. 🌟{RESET}\n")
    finally:
        await session.disconnect()
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
