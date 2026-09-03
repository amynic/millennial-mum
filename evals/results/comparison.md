# Millennial Mum — evaluation results (decomposed system)

Live results from the 50-case dataset (`evals/dataset.jsonl`) run against the
**decomposed** system on Microsoft Foundry (per-domain role-named deployments).
Custom deterministic evaluators only — routing, tool-call, and health-safety.
(Azure AI LLM-judges not run; see "Not yet measured".)

Run: `python -m evals.run_eval --target decomposed --retries 3 --out evals/results/decomposed.json`
Region eastus · 50/50 cases · **0 errors**.

## Headline (decomposed, corrected)

| Metric | Score | Notes |
|---|---|---|
| routing_accuracy (partial credit) | **0.788** | 34/50 strict exact-match pass |
| tool_call_f1 | **0.748** | |
| tool_call_recall | **0.863** | |
| tool_call_pass (strict) | **41/50** | |
| nhs_source_only | **1.000** | health answers cite NHS only |
| no_diagnosis | **1.000** | no medical diagnosis given |
| nhs_pass_rate (health cases) | **1.000** | 10/10 |
| no_diagnosis_pass_rate (health cases) | **1.000** | 10/10 |

**Safety is the non-negotiable, and it is perfect: all 10 health cases cite NHS
sources only and none give a diagnosis.**

### By difficulty

| Tier | routing_accuracy | tool_call_f1 |
|---|---|---|
| easy (20) | 0.925 | 0.908 |
| medium (20) | 0.675 | 0.622 |
| hard (10) | 0.742 | 0.680 |

Latency: mean ~53s/case (local run artifact — sequential specialist calls and an
`az` credential shell-out per call). A hosted deployment with managed identity and
connection reuse removes most of this; not representative of production.

## Measurement correction (why these numbers moved)

An earlier decomposed run scored routing 0.737 / tool_recall 0.680. Root cause was
a **trace bug, not agent behaviour**: family-memory tools are attached directly to
the orchestrator (a cross-cutting service, not a routed specialist), so their
invocations were never captured — every memory case scored as a total miss.

Fix (`agents/orchestrator.py`, `run_traced`): scan the orchestrator's own response
for function-call contents; `ask_<domain>` calls are specialist routing (already
tracked), every other tool is an orchestrator memory tool, recorded and attributed
to the `memory` domain.

| Metric | before fix | after fix |
|---|---|---|
| routing_accuracy | 0.737 | 0.788 |
| tool_call_f1 | 0.620 | 0.748 |
| tool_call_recall | 0.680 | 0.863 |

(Snapshot of the pre-fix run kept at `decomposed_v1_pretracefix.json`.)

## Remaining gaps (real signal, two kinds)

**1. Benign memory over-attribution (metric/label design).**
Cases: e07, e08, e09, m03, h02, h08, h09. The orchestrator correctly reads the
family profile before answering (e.g. reads the schedule before planning), so
`actual_agents` includes `memory`, but the dataset labels those cases without it,
so exact-match routing is penalised for *correct* behaviour. Memory is a
cross-cutting service — the honest options are (a) treat `memory` as "don't care"
in the routing metric, or (b) add `memory` to `expected_agent` on context-dependent
cases. Recommend (a).

**2. Genuine under-routing (system weakness to tune).**
- `admin_budget` not consulted: m07 ("overspending on takeaways?"), m16 ("draft a
  thank-you note" — `draft_email` lives on admin_budget). Triage answered inline.
- `planner` not consulted: m11, m14, m20, h07 — calendar/time-reasoning questions
  the triage model handled itself or mis-filed as memory.
Fix path: sharpen the `ask_admin_budget` / `ask_planner` tool descriptions and the
triage prompt so budget-drafting and calendar-reasoning intents route reliably.
This is a prompt/description change, then re-run — no architecture change needed.

## Not yet measured

- **Monolith baseline (the "before").** `--target baseline` runs the original
  Copilot-SDK monolith and needs a `GITHUB_TOKEN`. Provide one and run:
  `python -m evals.run_eval --target baseline --retries 3 --out evals/results/baseline.json`
  then diff against `decomposed.json` for the true before/after.
- **Azure AI LLM-judges** (intent resolution, task adherence, groundedness, etc.):
  add `--azure-judges` once a judge-model deployment + config is wired.
- **Per-domain model bake-off** (swap candidate models per role and compare).
