"""Local entrypoint for the decomposed system.

Run a single turn or an interactive chat against the triage orchestrator.
Requires a provisioned Foundry project and Azure identity:

    az login
    setx FOUNDRY_PROJECT_ENDPOINT "https://<your-project>.services.ai.azure.com/..."
    python -m agents.app "What can I make with pasta and peas? My son is 3."

Omit the query for an interactive loop.
"""

from __future__ import annotations

import asyncio
import sys

from agents.observability import setup_observability
from agents.orchestrator import get_orchestrator


async def run_turn(query: str) -> str:
    result = await get_orchestrator().run_traced(query)
    routed = ", ".join(result["agents_used"]) or "(none)"
    print(f"[routed to: {routed}]")
    return result["response"]


async def _interactive() -> None:
    print("Millennial Mum (decomposed). Ctrl-C to exit.\n")
    orchestrator = get_orchestrator()
    while True:
        try:
            query = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not query:
            continue
        result = await orchestrator.run_traced(query)
        print(f"\nmum> {result['response']}\n")


def main() -> None:
    setup_observability()
    if len(sys.argv) > 1:
        print(asyncio.run(run_turn(" ".join(sys.argv[1:]))))
    else:
        asyncio.run(_interactive())


if __name__ == "__main__":
    main()
