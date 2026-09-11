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

    async def run(self, messages):
        """Run one turn; returns the composed AgentResponse.

        ``messages`` may be a single query string (used by the eval harness) or
        a list of ChatMessage covering the full conversation so far (used by the
        hosted server for multi-turn context). Agent Framework accepts both.
        """
        self._trace = RunTrace()
        return await self.agent.run(messages)

    def _capture_orchestrator_tools(self, response) -> None:
        """Record the orchestrator's own tool calls (memory) into the trace.

        Memory tools are attached directly to the orchestrator (not a routed
        specialist), so their invocations only appear on the orchestrator's own
        response — never via a router wrapper. We scan the final response for
        function-call contents: ``ask_<domain>`` calls are specialist routing
        (already tracked by the router wrapper, so skipped here); every other
        tool name is an orchestrator-level memory tool, which we record and
        attribute to the ``memory`` domain.
        """
        for name in _extract_tool_names(response):
            if name.startswith("ask_"):
                continue
            self._trace.tool_calls.append(name)
            self._trace.agents_used.append("memory")

    async def run_traced(self, messages) -> dict:
        """Run one turn and return text + routing/tool trace (for evals).

        Accepts a query string or a full ChatMessage list (see :meth:`run`).
        """
        response = await self.run(messages)
        self._capture_orchestrator_tools(response)
        return {
            "response": _agent_text(response),
            "agents_used": list(dict.fromkeys(self._trace.agents_used)),
            "tool_calls": list(dict.fromkeys(self._trace.tool_calls)),
        }

    async def stream_traced(self, messages):
        """Yield the composed reply as text deltas for one turn.

        Uses Agent Framework's streaming run so the hosted server can forward
        tokens to the client as they're generated. Router tool calls fire during
        iteration (updating the trace); the visible final reply streams as text
        deltas. After the stream is exhausted, routing is available on
        ``self.last_agents_used()`` for logging.
        """
        self._trace = RunTrace()
        stream = self.agent.run(messages, stream=True)
        async for update in stream:
            text = getattr(update, "text", None)
            if text:
                yield text
        try:
            final = await stream.get_final_response()
            self._capture_orchestrator_tools(final)
        except Exception:  # pragma: no cover - trace is best-effort
            pass

    def last_agents_used(self) -> list[str]:
        """Distinct specialists routed to on the most recent run."""
        return list(dict.fromkeys(self._trace.agents_used))


def _extract_tool_names(response) -> list[str]:
    """Pull tool/function names out of an AgentResponse, best-effort.

    Agent Framework returns ``AgentResponse.messages -> Message.contents ->
    Content`` where a tool invocation is a ``Content`` whose ``type`` is
    ``"function_call"`` (or ``"function_result"``) and whose ``name`` is the
    tool name. We descend through messages into their content items rather than
    reading ``.name`` off the messages themselves (messages have no ``name``).
    """
    names: list[str] = []
    messages = getattr(response, "messages", None)
    if not messages:
        return names
    seq = messages if isinstance(messages, (list, tuple)) else [messages]
    for message in seq:
        contents = getattr(message, "contents", None) or []
        for content in contents:
            ctype = getattr(content, "type", None)
            name = getattr(content, "name", None)
            if name and ctype in ("function_call", "function_result"):
                names.append(name)
    return names


_ORCHESTRATOR: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Lazily build a process-wide orchestrator (requires Foundry credentials)."""
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        _ORCHESTRATOR = Orchestrator()
    return _ORCHESTRATOR
