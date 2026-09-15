"""Latency benchmark — repeatable TTFT/total baselines against the live path.

Runs a fixed set of representative turns through the deployed Function proxy
(the same entry point the PWA uses) and reports, per case and overall:

* **time to first token** — the number the parent actually feels;
* **total duration** — start of request to last streamed byte;
* **p50 / p95 / p99**, with **cold and warm separated**, because a cold worker
  and a warm one differ by tens of seconds and averaging them hides both.

Each turn carries a ``request_id`` that the proxy and hosted agent stamp on
their own spans, so any outlier here can be opened in Application Insights and
broken down per hop without guessing which trace it was.

Usage
-----
    # warm baseline, 3 repeats per case
    python -m evals.latency_bench --repeat 3

    # include the first (cold) request in its own bucket, and save a baseline
    python -m evals.latency_bench --repeat 3 --out evals/results/latency_baseline.json

    # compare the two routing paths on the same cases
    MM_FAST_PATH=false python -m evals.latency_bench --out before.json
    MM_FAST_PATH=true  python -m evals.latency_bench --out after.json

``MM_FAST_PATH`` is read by the *hosted agent*, not by this script — set it on
the deployment (or restart the local server with it) between runs.

Only the request/response envelope is recorded. Replies are measured by length,
never stored, so a benchmark artifact can't leak family or health content.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field

import httpx

DEFAULT_ENDPOINT = os.getenv(
    "MM_CHAT_ENDPOINT", "https://millennial-mum-api-flex.azurewebsites.net/api/chat"
)

REQUEST_ID_HEADER = "x-client-mm-request-id"


@dataclass(frozen=True)
class Case:
    """One benchmark turn.

    ``kind`` groups results so a regression in, say, multi-domain turns isn't
    masked by easy single-domain wins.
    """

    name: str
    kind: str
    query: str


#: Coverage mirrors the shapes the issue calls out: easy/medium/hard,
#: single-domain vs multi-domain, memory, tool-calling, and health. Health is
#: included because it must be *measured* even though it deliberately stays on
#: the slower orchestrated path.
CASES: tuple[Case, ...] = (
    Case("greeting", "easy", "hiya"),
    Case("kitchen_simple", "single_domain", "quick dinner idea with pasta and peas for a 3 year old?"),
    Case(
        "kitchen_tool",
        "tool_calling",
        "add nappies and porridge oats to the shopping list",
    ),
    Case("planner_simple", "single_domain", "what's on today?"),
    Case(
        "planner_tool",
        "tool_calling",
        "book swimming on Saturday at 10am and tell me what else is on that day",
    ),
    Case(
        "admin_budget_simple",
        "single_domain",
        "draft a short email to nursery saying Sam will be off Thursday",
    ),
    Case("health_simple", "health", "my 2 year old has a temperature of 38.5, what should I do?"),
    Case(
        "multi_domain",
        "multi_domain",
        "plan tonight's dinner and tell me if I've got time for the school run first",
    ),
    Case(
        "memory",
        "memory",
        "remember that Sam is allergic to peanuts, then suggest a snack",
    ),
    Case(
        "hard_reasoning",
        "hard",
        "I'm overwhelmed: work deadline Friday, nursery closed Wednesday, and we're out of food. Help me triage the week.",
    ),
)


@dataclass
class Measurement:
    """One executed turn."""

    case: str
    kind: str
    request_id: str
    cold: bool
    status: int | None = None
    headers_ms: float | None = None
    first_token_ms: float | None = None
    total_ms: float | None = None
    chars: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status == 200 and self.chars > 0


@dataclass
class Report:
    endpoint: str
    started_at: str
    repeat: int
    measurements: list[Measurement] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "started_at": self.started_at,
            "repeat": self.repeat,
            "summary": summarise(self.measurements),
            "measurements": [asdict(m) for m in self.measurements],
        }


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile.

    Deliberately not interpolated: benchmark runs are small (tens of samples),
    and an interpolated p95 over 12 points invents a number that was never
    measured.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(pct / 100 * len(ordered))))
    return round(ordered[rank - 1], 1)


def _stats(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        "mean": round(statistics.fmean(values), 1),
    }


def _bucket(measurements: list[Measurement]) -> dict:
    usable = [m for m in measurements if m.ok]
    return {
        "requests": len(measurements),
        "succeeded": len(usable),
        "first_token_ms": _stats([m.first_token_ms for m in usable if m.first_token_ms is not None]),
        "total_ms": _stats([m.total_ms for m in usable if m.total_ms is not None]),
    }


def summarise(measurements: list[Measurement]) -> dict:
    """Cold/warm split, then a per-kind warm breakdown."""
    warm = [m for m in measurements if not m.cold]
    kinds = sorted({m.kind for m in warm})
    return {
        "cold": _bucket([m for m in measurements if m.cold]),
        "warm": _bucket(warm),
        "warm_by_kind": {kind: _bucket([m for m in warm if m.kind == kind]) for kind in kinds},
    }


async def run_case(client: httpx.AsyncClient, endpoint: str, case: Case, cold: bool) -> Measurement:
    """Execute one turn, timing first token and total from the client's side."""
    request_id = uuid.uuid4().hex
    measurement = Measurement(case=case.name, kind=case.kind, request_id=request_id, cold=cold)
    payload = {"messages": [{"role": "user", "content": case.query}], "request_id": request_id}
    started = time.perf_counter()

    def elapsed_ms() -> float:
        return (time.perf_counter() - started) * 1000

    try:
        async with client.stream(
            "POST",
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json", REQUEST_ID_HEADER: request_id},
        ) as response:
            measurement.headers_ms = round(elapsed_ms(), 1)
            measurement.status = response.status_code
            if response.status_code != 200:
                await response.aread()
                measurement.error = f"HTTP {response.status_code}"
                return measurement
            async for chunk in response.aiter_text():
                if not chunk:
                    continue
                if measurement.first_token_ms is None:
                    measurement.first_token_ms = round(elapsed_ms(), 1)
                measurement.chars += len(chunk)
    except Exception as exc:
        measurement.error = f"{type(exc).__name__}: {exc}"
    finally:
        measurement.total_ms = round(elapsed_ms(), 1)
    return measurement


async def run_benchmark(
    endpoint: str, cases: tuple[Case, ...], repeat: int, include_cold: bool, pause: float
) -> Report:
    """Run every case ``repeat`` times, tagging only the very first as cold."""
    report = Report(
        endpoint=endpoint,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        repeat=repeat,
    )
    timeout = httpx.Timeout(240.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        first = True
        for _ in range(repeat):
            for case in cases:
                cold = first
                first = False
                if cold and not include_cold:
                    # Still issue the request (it warms the worker) but discard it.
                    await run_case(client, endpoint, case, cold=True)
                    continue
                measurement = await run_case(client, endpoint, case, cold=cold)
                report.measurements.append(measurement)
                _print_measurement(measurement)
                if pause:
                    time.sleep(pause)
    return report


def _print_measurement(m: Measurement) -> None:
    status = "ok " if m.ok else "ERR"
    ttft = f"{m.first_token_ms:>8.0f}" if m.first_token_ms is not None else "       -"
    total = f"{m.total_ms:>8.0f}" if m.total_ms is not None else "       -"
    tag = " cold" if m.cold else ""
    detail = f"  {m.error}" if m.error else ""
    print(f"  {status} {m.case:<22} ttft={ttft}ms total={total}ms{tag}{detail}")


def _print_summary(summary: dict) -> None:
    for bucket in ("cold", "warm"):
        stats = summary[bucket]
        if not stats["requests"]:
            continue
        ttft = stats["first_token_ms"]
        total = stats["total_ms"]
        print(f"\n{bucket.upper()}  ({stats['succeeded']}/{stats['requests']} ok)")
        if ttft:
            print(f"  first token  p50={ttft['p50']}ms  p95={ttft['p95']}ms  p99={ttft['p99']}ms")
        if total:
            print(f"  total        p50={total['p50']}ms  p95={total['p95']}ms  p99={total['p99']}ms")

    by_kind = summary.get("warm_by_kind") or {}
    if by_kind:
        print("\nWARM by kind (first token p50 / total p50)")
        for kind, stats in by_kind.items():
            ttft = stats["first_token_ms"]
            total = stats["total_ms"]
            ttft_p50 = ttft["p50"] if ttft else "-"
            total_p50 = total["p50"] if total else "-"
            print(f"  {kind:<16} {ttft_p50}ms / {total_p50}ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Chat proxy URL.")
    parser.add_argument("--repeat", type=int, default=3, help="Runs per case (default 3).")
    parser.add_argument("--case", action="append", help="Run only these case names.")
    parser.add_argument(
        "--include-cold",
        action="store_true",
        help="Record the first request as a cold sample instead of discarding it.",
    )
    parser.add_argument(
        "--pause", type=float, default=0.0, help="Seconds to wait between turns (default 0)."
    )
    parser.add_argument("--out", help="Write the full report JSON here.")
    args = parser.parse_args()

    cases = CASES
    if args.case:
        wanted = set(args.case)
        cases = tuple(c for c in CASES if c.name in wanted)
        missing = wanted - {c.name for c in cases}
        if missing:
            parser.error(f"unknown case(s): {', '.join(sorted(missing))}")

    print(f"Benchmarking {args.endpoint}\n{len(cases)} case(s) x {args.repeat}\n")
    report = asyncio.run(
        run_benchmark(args.endpoint, cases, args.repeat, args.include_cold, args.pause)
    )
    summary = summarise(report.measurements)
    _print_summary(summary)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2)
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
