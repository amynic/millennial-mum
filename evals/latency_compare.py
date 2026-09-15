"""Compare two latency benchmark runs and judge them against the targets.

``latency_bench`` answers "how slow is it?". This answers the question that
actually decides whether the fast path ships: "did it get faster, and is it fast
enough?" Feed it the two reports from the A/B in DEPLOY.md::

    python -m evals.latency_compare \
        evals/results/latency_orchestrated.json \
        evals/results/latency_fastpath.json

The verdict uses the acceptance criteria from issue #10 — warm p50
time-to-first-token under 5s, warm p95 under 10s — applied to the *after* run.
A run can improve a lot and still miss the target, so the two judgements are
reported separately rather than collapsed into one pass/fail.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TARGET_WARM_P50_MS = 5_000.0
TARGET_WARM_P95_MS = 10_000.0

_METRICS = ("first_token_ms", "total_ms")
_PERCENTILES = ("p50", "p95", "p99")


def load_report(path: Path) -> dict:
    """Read a benchmark report, accepting either a full report or a bare summary."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"No such benchmark report: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"{path} does not contain a benchmark report object")
    summary = data.get("summary", data)
    if not isinstance(summary, dict) or "warm" not in summary:
        raise SystemExit(f"{path} has no 'warm' summary - was it produced by latency_bench?")
    return data


def delta(before: float | None, after: float | None) -> dict | None:
    """Absolute and relative change, or None when either side is missing."""
    if before is None or after is None:
        return None
    change = after - before
    return {
        "before": round(before, 1),
        "after": round(after, 1),
        "delta_ms": round(change, 1),
        # A zero baseline can't be expressed as a percentage; report None rather
        # than dividing by zero or silently printing 0%.
        "delta_pct": round(change / before * 100, 1) if before else None,
    }


def _bucket_delta(before: dict | None, after: dict | None) -> dict:
    out: dict = {}
    for metric in _METRICS:
        before_stats = (before or {}).get(metric) or {}
        after_stats = (after or {}).get(metric) or {}
        changes = {
            pct: delta(before_stats.get(pct), after_stats.get(pct)) for pct in _PERCENTILES
        }
        out[metric] = {pct: value for pct, value in changes.items() if value is not None}
    return out


def compare(before: dict, after: dict) -> dict:
    """Per-bucket deltas plus the target verdict for the 'after' run."""
    before_summary = before.get("summary", before)
    after_summary = after.get("summary", after)

    kinds = sorted(
        set(before_summary.get("warm_by_kind", {})) | set(after_summary.get("warm_by_kind", {}))
    )
    return {
        "cold": _bucket_delta(before_summary.get("cold"), after_summary.get("cold")),
        "warm": _bucket_delta(before_summary.get("warm"), after_summary.get("warm")),
        "warm_by_kind": {
            kind: _bucket_delta(
                before_summary.get("warm_by_kind", {}).get(kind),
                after_summary.get("warm_by_kind", {}).get(kind),
            )
            for kind in kinds
        },
        "targets": verdict(after_summary),
    }


def verdict(summary: dict) -> dict:
    """Judge a run's warm TTFT against the issue #10 acceptance criteria."""
    warm_ttft = (summary.get("warm") or {}).get("first_token_ms") or {}
    p50, p95 = warm_ttft.get("p50"), warm_ttft.get("p95")
    checks = {
        "warm_p50_first_token_ms": {
            "value": p50,
            "target": TARGET_WARM_P50_MS,
            "met": None if p50 is None else p50 < TARGET_WARM_P50_MS,
        },
        "warm_p95_first_token_ms": {
            "value": p95,
            "target": TARGET_WARM_P95_MS,
            "met": None if p95 is None else p95 < TARGET_WARM_P95_MS,
        },
    }
    outcomes = [check["met"] for check in checks.values()]
    checks["all_met"] = None if None in outcomes else all(outcomes)
    return checks


def _fmt_ms(value: float | None) -> str:
    return "-" if value is None else f"{value / 1000:.2f}s"


def _fmt_delta(change: dict | None) -> str:
    if change is None:
        return "-"
    pct = "" if change["delta_pct"] is None else f" ({change['delta_pct']:+.1f}%)"
    # Faster is the goal, so lead with the sign of the change, not its magnitude.
    return f"{_fmt_ms(change['before'])} -> {_fmt_ms(change['after'])}{pct}"


def _render_bucket(title: str, bucket: dict, lines: list[str]) -> None:
    rows = [
        (metric, pct, bucket.get(metric, {}).get(pct))
        for metric in _METRICS
        for pct in _PERCENTILES
        if bucket.get(metric, {}).get(pct) is not None
    ]
    if not rows:
        return
    lines.append("")
    lines.append(title)
    lines.append(f"  {'metric':<18} {'pct':<5} {'before -> after':<34}")
    for metric, pct, change in rows:
        lines.append(f"  {metric:<18} {pct:<5} {_fmt_delta(change):<34}")


def render(result: dict, before_path: Path, after_path: Path) -> str:
    lines = [
        "Latency comparison",
        f"  before : {before_path}",
        f"  after  : {after_path}",
    ]
    _render_bucket("Warm (steady state)", result["warm"], lines)
    _render_bucket("Cold", result["cold"], lines)

    for kind, bucket in result["warm_by_kind"].items():
        _render_bucket(f"Warm - {kind}", bucket, lines)

    targets = result["targets"]
    lines.append("")
    lines.append("Targets (warm time-to-first-token, issue #10)")
    for name in ("warm_p50_first_token_ms", "warm_p95_first_token_ms"):
        check = targets[name]
        met = check["met"]
        status = "unknown" if met is None else ("PASS" if met else "FAIL")
        lines.append(
            f"  {name:<26} {_fmt_ms(check['value']):>8}  target < {_fmt_ms(check['target'])}  {status}"
        )
    overall = targets["all_met"]
    lines.append(
        "  overall                    "
        + ("unknown - no warm samples" if overall is None else ("PASS" if overall else "FAIL"))
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two latency_bench reports and judge them against the targets."
    )
    parser.add_argument("before", type=Path, help="Baseline report (e.g. fast path off).")
    parser.add_argument("after", type=Path, help="Comparison report (e.g. fast path on).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    parser.add_argument("--out", type=Path, help="Write the comparison JSON here.")
    parser.add_argument(
        "--fail-on-target-miss",
        action="store_true",
        help="Exit non-zero if the 'after' run misses the warm TTFT targets.",
    )
    args = parser.parse_args(argv)

    before, after = load_report(args.before), load_report(args.after)
    result = compare(before, after)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Wrote {args.out}")

    print(json.dumps(result, indent=2) if args.json else render(result, args.before, args.after))

    if args.fail_on_target_miss and result["targets"]["all_met"] is not True:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
