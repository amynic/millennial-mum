"""Shared family-memory service.

Family memory is cross-cutting: every specialist should see the same profile,
and any agent may capture new details. Rather than duplicating memory tools
into each specialist, this service exposes:

* ``profile_context()`` — a text block injected into each agent's context so
  answers are consistent (e.g. child's name/age/allergies).
* ``memory_tools()`` — the Agent Framework memory tools (save_*/get_family_profile),
  attached to the orchestrator so captured details persist once per turn.

The store is still the monolith's ``family_profile.json`` (see tools/memory.py).
Migrating to a Foundry-managed store / Cosmos for true multi-session, multi-user
persistence is noted as future work in the plan.
"""

from __future__ import annotations

from agents.tool_adapter import build_domain_tools
from tools.memory import get_profile_context


def profile_context() -> str:
    """Text block describing what we already know about the family."""
    return get_profile_context()


def memory_tools():
    """Agent Framework tools for reading/writing family memory."""
    return build_domain_tools("memory")
