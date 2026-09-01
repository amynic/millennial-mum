"""Millennial Mum evaluation harness.

Runs the 50-case dataset (``evals/dataset.jsonl``) against a chosen target
(baseline monolith or decomposed Foundry system), applies both the custom
deterministic evaluators and — when Azure credentials are present — the
Azure AI Evaluation SDK's LLM-judge evaluators, aggregates the scores by
difficulty and evaluator, and writes a results JSON.

Usage
-----
Offline self-test (no Azure needed, verifies the harness + evaluators)::

    python -m evals.run_eval --self-test

Baseline run against the current monolith::

    python -m evals.run_eval --target baseline --out evals/results/baseline.json

Decomposed run against the Foundry system::

    python -m evals.run_eval --target decomposed --out evals/results/decomposed.json

Add ``--azure-judges`` to additionally invoke the Azure AI Evaluation SDK
evaluators (Intent Resolution, Tool Call Accuracy, Task Adherence,
Groundedness, Relevance, Coherence, Fluency, Content Safety). That path
requires ``azure-ai-evaluation`` and a model-config / project connection.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.jsonl"

# Allow "python evals/run_eval.py" as well as "-m evals.run_eval".
sys.path.insert(0, str(HERE.parent))

from evals.evaluators import ALL_CUSTOM_EVALUATORS  # noqa: E402
from evals.targets import TARGETS  # noqa: E402


def load_dataset(path: Path = DATASET) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _is_health(case: dict) -> bool:
    agents = case.get("expected_agent") or []
    if isinstance(agents, str):
        agents = [agents]
    domains = case.get("domains") or []
    return "health" in agents or "health" in domains or "emergency" in domains


def evaluate_case(case: dict, run: dict) -> dict:
    """Apply every custom evaluator to a single (case, run) pair."""
    is_health = _is_health(case)
    scores: dict = {}

    scores.update(
        ALL_CUSTOM_EVALUATORS["routing"](
            expected_agents=case.get("expected_agent"),
            actual_agents=run.get("agents_used"),
        )
    )
    scores.update(
        ALL_CUSTOM_EVALUATORS["tool_calls"](
            expected_tools=case.get("expected_tools"),
            actual_tools=run.get("tool_calls"),
        )
    )
    scores.update(
        ALL_CUSTOM_EVALUATORS["nhs_source"](
            response=run.get("response"), is_health=is_health
        )
    )
    scores.update(
        ALL_CUSTOM_EVALUATORS["no_diagnosis"](
            response=run.get("response"), is_health=is_health
        )
    )
    return scores


# Numeric score fields we aggregate across the run.
_METRIC_FIELDS = [
    "routing_accuracy",
    "tool_call_f1",
    "tool_call_recall",
    "nhs_source_only",
    "no_diagnosis",
]


def aggregate(records: list[dict]) -> dict:
    by_difficulty: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_difficulty[r["difficulty"]].append(r["scores"])

    def _mean(values: list[float]) -> float:
        return round(statistics.mean(values), 4) if values else 0.0

    def _summarise(score_dicts: list[dict]) -> dict:
        out = {}
        for field in _METRIC_FIELDS:
            vals = [s[field] for s in score_dicts if field in s]
            out[field] = _mean(vals)
        # Health-only pass rates over applicable cases.
        nhs_applicable = [s for s in score_dicts if s.get("applicable") and "nhs_pass" in s]
        diag_applicable = [s for s in score_dicts if s.get("applicable") and "no_diagnosis_pass" in s]
        out["nhs_pass_rate"] = _mean([1.0 if s["nhs_pass"] else 0.0 for s in nhs_applicable])
        out["no_diagnosis_pass_rate"] = _mean(
            [1.0 if s["no_diagnosis_pass"] else 0.0 for s in diag_applicable]
        )
        return out

    overall = _summarise([r["scores"] for r in records])
    per_difficulty = {d: _summarise(v) for d, v in by_difficulty.items()}
    return {"overall": overall, "by_difficulty": per_difficulty}


def _build_model_config():
    """Build an Azure OpenAI judge config from env (API key or AAD)."""
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_JUDGE_DEPLOYMENT") or os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    if not (endpoint and deployment):
        raise RuntimeError(
            "Azure judges need AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_JUDGE_DEPLOYMENT "
            "(or AZURE_OPENAI_DEPLOYMENT) set."
        )
    config = {
        "azure_endpoint": endpoint,
        "azure_deployment": deployment,
        "api_version": api_version,
    }
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    if api_key:
        config["api_key"] = api_key  # else the SDK falls back to AAD / DefaultAzureCredential
    return config


def run_azure_judges(records: list[dict], cases_by_id: dict[str, dict]) -> dict:
    """Score every record with the Azure AI Evaluation SDK LLM judges.

    Runs the query/response/context judges that don't require full agent
    message threads, plus Content Safety when an Azure AI project is set.
    Scores are attached back onto each record under ``azure`` and averaged
    into the returned summary. Raises if the SDK isn't installed or the
    judge model isn't configured — this path is meant to actually run, not
    silently no-op.
    """
    from azure.ai.evaluation import (
        IntentResolutionEvaluator,
        TaskAdherenceEvaluator,
        GroundednessEvaluator,
        RelevanceEvaluator,
        CoherenceEvaluator,
        FluencyEvaluator,
    )

    model_config = _build_model_config()

    judges = {
        "intent_resolution": IntentResolutionEvaluator(model_config=model_config),
        "task_adherence": TaskAdherenceEvaluator(model_config=model_config),
        "groundedness": GroundednessEvaluator(model_config=model_config),
        "relevance": RelevanceEvaluator(model_config=model_config),
        "coherence": CoherenceEvaluator(model_config=model_config),
        "fluency": FluencyEvaluator(model_config=model_config),
    }

    # Content Safety is a service-based (not LLM-config) evaluator; enable when a project is set.
    project = os.getenv("AZURE_AI_PROJECT")
    if project:
        try:
            from azure.ai.evaluation import ContentSafetyEvaluator
            from azure.identity import DefaultAzureCredential

            judges["content_safety"] = ContentSafetyEvaluator(
                azure_ai_project=project, credential=DefaultAzureCredential()
            )
        except Exception as exc:  # pragma: no cover
            print(f"[azure-judges] content safety unavailable: {exc}")

    def _score(name, evaluator, case, rec) -> dict | None:
        query, response = case["query"], rec.get("response", "")
        try:
            if name == "groundedness":
                return evaluator(query=query, response=response, context=case.get("ground_truth", ""))
            if name == "fluency":
                return evaluator(response=response)
            if name == "content_safety":
                return evaluator(query=query, response=response)
            return evaluator(query=query, response=response)
        except Exception as exc:  # pragma: no cover - per-case robustness
            print(f"[azure-judges] {name} failed on {case['id']}: {exc}")
            return None

    numeric: dict[str, list[float]] = defaultdict(list)
    for rec in records:
        case = cases_by_id[rec["id"]]
        rec_scores: dict = {}
        for name, evaluator in judges.items():
            result = _score(name, evaluator, case, rec)
            if not result:
                continue
            rec_scores[name] = result
            for k, v in result.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    numeric[f"{name}.{k}"].append(float(v))
        rec["azure"] = rec_scores

    summary = {k: round(statistics.mean(v), 4) for k, v in numeric.items() if v}
    return summary


def run(target_name: str, out_path: Path | None, azure_judges: bool) -> dict:
    cases = load_dataset()
    target_cls = TARGETS[target_name]
    target = target_cls()

    records = []
    for case in cases:
        run_record = target(case)
        scores = evaluate_case(case, run_record)
        records.append(
            {
                "id": case["id"],
                "difficulty": case["difficulty"],
                "query": case["query"],
                "response": run_record.get("response", ""),
                "agents_used": run_record.get("agents_used", []),
                "tool_calls": run_record.get("tool_calls", []),
                "latency_ms": run_record.get("latency_ms", 0.0),
                "scores": scores,
            }
        )

    summary = aggregate(records)
    if azure_judges:
        cases_by_id = {c["id"]: c for c in cases}
        try:
            summary["azure_judges"] = run_azure_judges(records, cases_by_id)
        except ImportError as exc:
            raise SystemExit(
                "--azure-judges requested but azure-ai-evaluation is not installed. "
                "Install it with:  pip install azure-ai-evaluation azure-identity\n"
                f"(import error: {exc})"
            )

    result = {
        "target": target_name,
        "n_cases": len(records),
        "summary": summary,
        "records": records,
    }

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Wrote {out_path}")
    return result


def self_test() -> int:
    """Run the stub target and assert the harness scores it near-perfectly."""
    result = run("stub", None, azure_judges=False)
    overall = result["summary"]["overall"]
    print(json.dumps(result["summary"], indent=2))

    failures = []
    if overall["routing_accuracy"] < 0.99:
        failures.append(f"routing_accuracy={overall['routing_accuracy']}")
    if overall["tool_call_recall"] < 0.99:
        failures.append(f"tool_call_recall={overall['tool_call_recall']}")
    if overall["nhs_pass_rate"] < 0.99:
        failures.append(f"nhs_pass_rate={overall['nhs_pass_rate']}")
    if overall["no_diagnosis_pass_rate"] < 0.99:
        failures.append(f"no_diagnosis_pass_rate={overall['no_diagnosis_pass_rate']}")

    if failures:
        print("SELF-TEST FAILED:", ", ".join(failures))
        return 1
    print(f"SELF-TEST PASSED over {result['n_cases']} cases.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Millennial Mum eval harness")
    parser.add_argument("--target", choices=list(TARGETS), default="baseline")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--azure-judges", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    run(args.target, args.out, args.azure_judges)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
