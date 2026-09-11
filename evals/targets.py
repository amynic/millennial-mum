"""Target adapters for the eval harness.

A *target* takes a case query and returns a normalised run record:

    {
        "response": str,          # final natural-language answer
        "tool_calls": [str],      # tool names invoked during the turn
        "agents_used": [str],     # specialist agents involved (["monolith"] for baseline)
        "latency_ms": float,
    }

Three targets are provided:

* ``StubTarget`` — deterministic, offline. Replays each case's ground-truth
  metadata so the harness (and its custom evaluators) can be exercised with
  no Azure dependency. Used by ``run_eval.py --self-test``.
* ``BaselineMonolithTarget`` — the current single-agent system. Runs the
  monolith and records which tools fired. Requires the app's runtime.
* ``DecomposedTarget`` — the Agent Framework triage orchestrator + specialists.
  Requires a provisioned Foundry project. Records per-hop routing + tools.

The two live targets are intentionally thin wrappers: they depend on runtime
packages/credentials that aren't available in every environment, so their
heavy imports are done lazily inside ``__call__``.
"""

from __future__ import annotations

import time
from typing import Protocol


class Target(Protocol):
    name: str

    def __call__(self, case: dict) -> dict: ...


class StubTarget:
    """Offline target: echoes each case's expected metadata.

    This lets us verify the harness plumbing and the custom evaluators
    end-to-end without any model calls. It is *not* a measure of quality —
    it simply proves that a well-behaved system would score ~1.0.
    """

    name = "stub"

    def __call__(self, case: dict) -> dict:
        response = case.get("ground_truth", "") or ""
        # Simulate an NHS-safe health answer so safety evaluators exercise both paths.
        if case.get("safety_critical"):
            response = (
                response
                + " Please contact NHS 111 for advice, or call 999 / go to A&E if "
                "you see any red-flag symptoms. See nhs.uk for guidance. "
                "This is not a diagnosis."
            )
        expected_agent = case.get("expected_agent", [])
        if isinstance(expected_agent, str):
            expected_agent = [expected_agent]
        return {
            "response": response,
            "tool_calls": list(case.get("expected_tools", [])),
            "agents_used": list(expected_agent),
            "latency_ms": 0.0,
        }


class BaselineMonolithTarget:
    """Runs the current single-agent monolith (GitHub Copilot SDK).

    Requires ``GITHUB_TOKEN`` and the app's tool runtime. Tool-call capture
    is best-effort: we wrap the tool registry to record invocations.
    """

    name = "baseline"

    def __init__(self, capture_tools: bool = True):
        self.capture_tools = capture_tools

    def __call__(self, case: dict) -> dict:  # pragma: no cover - requires live runtime
        # Imported lazily so the module loads without the Copilot SDK present.
        from evals._runners import run_monolith

        start = time.perf_counter()
        result = run_monolith(case["query"], capture_tools=self.capture_tools)
        return {
            "response": result["response"],
            "tool_calls": result["tool_calls"],
            "agents_used": ["monolith"],
            "latency_ms": (time.perf_counter() - start) * 1000,
        }


class DecomposedTarget:
    """Runs the decomposed Agent Framework system on Foundry.

    Requires ``FOUNDRY_PROJECT_ENDPOINT`` + Azure identity. Captures the
    orchestrator's routing decisions and each specialist's tool calls.
    """

    name = "decomposed"

    def __call__(self, case: dict) -> dict:  # pragma: no cover - requires Foundry
        from evals._runners import run_decomposed

        start = time.perf_counter()
        result = run_decomposed(case["query"])
        return {
            "response": result["response"],
            "tool_calls": result["tool_calls"],
            "agents_used": result["agents_used"],
            "latency_ms": (time.perf_counter() - start) * 1000,
        }


TARGETS = {
    "stub": StubTarget,
    "baseline": BaselineMonolithTarget,
    "decomposed": DecomposedTarget,
}
