"""Triage orchestrator — router-as-tools.

The orchestrator is an ``agent_framework.Agent`` whose tools are the four
specialists (each callable as ``ask_<domain>``) plus the shared memory tools.
It decides which specialist(s) to consult, can call several for multi-domain
turns, and composes ONE final reply in the Katherine Ryan voice — switching to
a calm, serious register whenever Health is involved (enforced by the prompt).

For evaluation we also capture which specialists were routed to and which
underlying tools fired, via a per-run trace the router wrappers append to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from agent_framework import Agent, FunctionTool

from agents.clients import client_for
from agents.config import AGENT_DESCRIPTIONS, AGENT_NAMES, ORCHESTRATOR_PROMPT
from agents.memory_service import memory_tools
from agents.specialists import SPECIALIST_DOMAINS, build_specialists


class SpecialistRequest(BaseModel):
    request: str = Field(description="The parent's request, scoped to this specialist's domain.")


@dataclass
class RunTrace:
    """Routing + tool trace for one orchestrator turn (used by evals)."""

    agents_used: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)


def _agent_text(response) -> str:
    return getattr(response, "text", None) or str(response)


class Orchestrator:
    """Router-as-tools triage agent over the four specialists."""

    def __init__(self):
        self.specialists = build_specialists()
        self._trace = RunTrace()
        self.agent = Agent(
            client=client_for("triage"),
            name=AGENT_NAMES["triage"],
            description="Triage orchestrator that routes to specialists and composes the reply.",
            instructions=ORCHESTRATOR_PROMPT,
            tools=self._router_tools() + memory_tools(),
        )

    def _router_tools(self) -> list[FunctionTool]:
        tools = []
        for domain in SPECIALIST_DOMAINS:
            tools.append(self._make_router_tool(domain))
        return tools

    def _make_router_tool(self, domain: str) -> FunctionTool:
        specialist = self.specialists[domain]

        async def ask(request: str) -> str:
            self._trace.agents_used.append(domain)
            response = await specialist.run(request)
            # Best-effort tool-name capture from the specialist response.
            for name in _extract_tool_names(response):
                self._trace.tool_calls.append(name)
            return _agent_text(response)

        ask.__name__ = f"ask_{domain}"
        return FunctionTool(
            name=f"ask_{domain}",
            description=f"Consult the {AGENT_NAMES[domain]} specialist. {AGENT_DESCRIPTIONS[domain]}",
            func=lambda request: ask(request),
            input_model=SpecialistRequest,
        )

    async def run(self, query: str):
        """Run one turn; returns the composed AgentResponse."""
        self._trace = RunTrace()
        return await self.agent.run(query)

    async def run_traced(self, query: str) -> dict:
        """Run one turn and return text + routing/tool trace (for evals)."""
        response = await self.run(query)
        return {
            "response": _agent_text(response),
            "agents_used": list(dict.fromkeys(self._trace.agents_used)),
            "tool_calls": list(dict.fromkeys(self._trace.tool_calls)),
        }


def _extract_tool_names(response) -> list[str]:
    """Pull tool/function names out of an AgentResponse, best-effort."""
    names: list[str] = []
    for attr in ("messages", "contents", "content"):
        items = getattr(response, attr, None)
        if not items:
            continue
        seq = items if isinstance(items, (list, tuple)) else [items]
        for item in seq:
            name = getattr(item, "name", None)
            if name:
                names.append(name)
    return names


_ORCHESTRATOR: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Lazily build a process-wide orchestrator (requires Foundry credentials)."""
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        _ORCHESTRATOR = Orchestrator()
    return _ORCHESTRATOR
