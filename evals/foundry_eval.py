"""Run the Millennial Mum evaluation **as a Microsoft Foundry Evaluation**.

Unlike ``run_eval.py`` (which scores locally and writes a JSON), this script
uploads the run to the Foundry project so it appears in the portal's
**Evaluations** tab with a shareable studio URL, run history, and per-metric
charts. It reuses:

  * the same 50-case dataset (``evals/dataset.jsonl``), and
  * the same four custom deterministic evaluators (routing, tool-calls,
    NHS-source-only, no-diagnosis) from ``evals/evaluators.py``.

It does **not** re-run inference: it consumes the responses a prior harness run
already produced against the Foundry-hosted models (``evals/results/*.json``),
re-scores them with the custom evaluators, and registers the scored run in
Foundry via ``azure.ai.evaluation.evaluate(..., azure_ai_project=...)``.

Usage
-----
    # ensure you're logged in (az login) and the endpoint is set
    $env:FOUNDRY_PROJECT_ENDPOINT = "https://<acct>.services.ai.azure.com/api/projects/<project>"
    python -m evals.foundry_eval --run evals/results/decomposed.json --name "decomposed-baseline"

The project endpoint may be passed with ``--project`` instead of the env var.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from evals.evaluators import (  # noqa: E402
    NhsSourceOnlyEvaluator,
    NoDiagnosisEvaluator,
    RoutingAccuracyEvaluator,
    ToolCallAccuracyEvaluator,
)

DATASET = HERE / "dataset.jsonl"

_ROUTING = RoutingAccuracyEvaluator()
_TOOLS = ToolCallAccuracyEvaluator()
_NHS = NhsSourceOnlyEvaluator()
_NODIAG = NoDiagnosisEvaluator()


# ---------------------------------------------------------------------------
# Module-level evaluator wrappers (must be importable/picklable for the SDK).
# Each returns ONLY flat scalar fields so Foundry can aggregate + chart them.
# ---------------------------------------------------------------------------
def routing_accuracy(*, expected_agents, actual_agents):
    r = _ROUTING(expected_agents=expected_agents, actual_agents=actual_agents)
    return {"routing_accuracy": r["routing_accuracy"], "routing_pass": float(r["routing_pass"])}


def tool_call_accuracy(*, expected_tools, actual_tools):
    r = _TOOLS(expected_tools=expected_tools, actual_tools=actual_tools)
    return {
        "tool_call_f1": r["tool_call_f1"],
        "tool_call_precision": r["tool_call_precision"],
        "tool_call_recall": r["tool_call_recall"],
        "tool_call_pass": float(r["tool_call_pass"]),
    }


def nhs_source_only(*, response, is_health):
    r = _NHS(response=response, is_health=is_health)
    return {"nhs_source_only": r["nhs_source_only"], "nhs_pass": float(r["nhs_pass"])}


def no_diagnosis(*, response, is_health):
    r = _NODIAG(response=response, is_health=is_health)
    return {"no_diagnosis": r["no_diagnosis"], "no_diagnosis_pass": float(r["no_diagnosis_pass"])}


def _is_health(case: dict) -> bool:
    agents = case.get("expected_agent") or []
    if isinstance(agents, str):
        agents = [agents]
    domains = case.get("domains") or []
    return "health" in agents or "health" in domains or "emergency" in domains


def load_dataset(path: Path = DATASET) -> dict[str, dict]:
    cases = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            case = json.loads(line)
            cases[case["id"]] = case
    return cases


def build_jsonl(run_path: Path, out_path: Path) -> int:
    """Merge dataset ground-truth with a run's responses into an eval JSONL."""
    cases = load_dataset()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    records = run.get("records", [])
    if not records:
        raise SystemExit(f"No records found in {run_path}")

    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        for rec in records:
            case = cases.get(rec["id"])
            if not case:
                continue
            expected_agent = case.get("expected_agent") or []
            if isinstance(expected_agent, str):
                expected_agent = [expected_agent]
            row = {
                "id": rec["id"],
                "difficulty": rec.get("difficulty", case.get("difficulty")),
                "query": case["query"],
                "response": rec.get("response", ""),
                "is_health": _is_health(case),
                "expected_agents": expected_agent,
                "actual_agents": rec.get("agents_used", []),
                "expected_tools": case.get("expected_tools", []),
                "actual_tools": rec.get("tool_calls", []),
            }
            f.write(json.dumps(row) + "\n")
            n += 1
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a Foundry Evaluation for Millennial Mum")
    parser.add_argument("--run", type=Path, default=HERE / "results" / "decomposed.json",
                        help="Harness results JSON whose responses will be scored + uploaded.")
    parser.add_argument("--name", default="millennial-mum-decomposed",
                        help="Evaluation run name shown in the Foundry portal.")
    parser.add_argument("--project", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
                        help="Foundry project endpoint (or set FOUNDRY_PROJECT_ENDPOINT).")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "_foundry_eval_out.json")
    args = parser.parse_args()

    if not args.project:
        raise SystemExit(
            "No Foundry project endpoint. Pass --project or set FOUNDRY_PROJECT_ENDPOINT, e.g.\n"
            "  https://millennial-mum-foundry.services.ai.azure.com/api/projects/millennial-mum"
        )

    from azure.ai.evaluation import evaluate

    tmp = Path(tempfile.gettempdir()) / f"mm_foundry_eval_{args.run.stem}.jsonl"
    n = build_jsonl(args.run, tmp)
    print(f"Prepared {n} scored cases from {args.run} -> {tmp}", file=sys.stderr)

    evaluators = {
        "routing": routing_accuracy,
        "tool_calls": tool_call_accuracy,
        "nhs_source": nhs_source_only,
        "no_diagnosis": no_diagnosis,
    }
    evaluator_config = {
        "routing": {"column_mapping": {
            "expected_agents": "${data.expected_agents}",
            "actual_agents": "${data.actual_agents}",
        }},
        "tool_calls": {"column_mapping": {
            "expected_tools": "${data.expected_tools}",
            "actual_tools": "${data.actual_tools}",
        }},
        "nhs_source": {"column_mapping": {
            "response": "${data.response}",
            "is_health": "${data.is_health}",
        }},
        "no_diagnosis": {"column_mapping": {
            "response": "${data.response}",
            "is_health": "${data.is_health}",
        }},
    }

    print(f"Uploading evaluation '{args.name}' to Foundry project:\n  {args.project}", file=sys.stderr)
    result = evaluate(
        data=str(tmp),
        evaluators=evaluators,
        evaluator_config=evaluator_config,
        evaluation_name=args.name,
        azure_ai_project=args.project,
        output_path=str(args.out),
        tags={"system": "millennial-mum", "variant": "decomposed", "dataset": "50-case-v1"},
    )

    studio_url = result.get("studio_url") if isinstance(result, dict) else None
    metrics = result.get("metrics") if isinstance(result, dict) else None
    print("\n=== Foundry evaluation registered ===")
    if metrics:
        print(json.dumps(metrics, indent=2))
    if studio_url:
        print(f"\nView in Foundry portal:\n{studio_url}")
    else:
        print("\n(Run uploaded; open the Foundry portal > Evaluations to view it.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
