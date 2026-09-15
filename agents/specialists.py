"""Build the four domain specialist agents (Agent Framework).

Each specialist is an ``agent_framework.Agent`` with:
* its own Foundry chat client (per-domain model), built lazily,
* a focused system prompt (from ``agents.config``) plus the shared family
  profile context, and
* only its domain's tools.

The Health specialist is deliberately isolated with the hardened NHS-only prompt.
"""

from __future__ import annotations

from agent_framework import Agent

from agents.clients import client_for
from agents.config import (
    AGENT_DESCRIPTIONS,
    AGENT_NAMES,
    SPECIALIST_PROMPTS,
)
from agents.memory_service import profile_context
from agents.tool_adapter import build_domain_tools

SPECIALIST_DOMAINS = ["kitchen", "planner", "admin_budget", "health"]


def specialist_instructions(domain: str) -> str:
    """Base prompt for a domain plus the shared family-profile context.

    Public because the direct-stream fast path (``agents.fast_path``) builds a
    second, voice-owning variant of the same specialist and must start from the
    identical instructions.
    """
    base = SPECIALIST_PROMPTS[domain]
    ctx = profile_context()
    return base + ("\n\n" + ctx if ctx else "")


def build_specialist(domain: str) -> Agent:
    """Construct a single specialist agent (requires Foundry credentials)."""
    return Agent(
        client=client_for(domain),
        name=AGENT_NAMES[domain],
        description=AGENT_DESCRIPTIONS[domain],
        instructions=specialist_instructions(domain),
        tools=build_domain_tools(domain),
    )


def build_specialists() -> dict[str, Agent]:
    """Construct all four specialists."""
    return {domain: build_specialist(domain) for domain in SPECIALIST_DOMAINS}
