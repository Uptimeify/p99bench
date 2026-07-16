#!/usr/bin/env python3
"""Compute grades from schema/thresholds.yaml.

A grade is never a human judgement. It is a pure function of the measured
numbers and the published bands. If you disagree with a grade, argue about the
band in THRESHOLDS.md -- do not edit the grade. CI recomputes every grade and
rejects any file whose stored block differs.

This replaces tools/verdict.py. The old name lied once the output stopped being
a single verdict.

Usage:
    python3 tools/grade.py results/hetzner/foo.json --in-place
    python3 tools/grade.py results/hetzner/foo.json           # print only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS_PATH = ROOT / "schema" / "thresholds.yaml"

GRADES = ("A", "B", "C", "D", "F")
# Worst-wins ordering among real grades. "?" is deliberately absent: it is not a
# point on this scale, it is the absence of one, and it is handled by explicit
# precedence in rollup() rather than by comparison.
RANK = {g: i for i, g in enumerate(GRADES)}

# Storage class boundaries, in microseconds per durable write (spec 4.5).
# Derived from measured fsync latency-per-op, never from provider marketing.
# A facet, never a curve: it explains a grade, it never softens one.
STORAGE_CLASS_BOUNDS = [
    (300, "local-nvme"),
    (1500, "net-fast"),
    (10000, "net-slow"),
]
STORAGE_CLASS_WORST = "degraded"


def dig(obj: dict, path: str):
    """Walk a dotted path, returning None if anything along the way is absent."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def reduce_network(result: dict, path: str):
    """Worst value across network targets for a `network.<field>` metric.

    Worst, not mean. Every host measures the SAME fixed targets so distance is a
    constant, which is what makes the numbers comparable at all -- and one bad
    path is a bad path. Averaging would bury the 10% loss outlier the corpus
    already contains (ovh/zrh -> hetzner-ash) under three clean paths.
    """
    net = result.get("network") or {}
    if not net.get("reachable"):
        return None
    field = path.split(".", 1)[1]
    values = []
    for target in net.get("targets") or []:
        if not target.get("reachable", True):
            continue
        if field == "rtt_jitter_ratio":
            p50, p99 = target.get("rtt_p50_ms"), target.get("rtt_p99_ms")
            if isinstance(p50, (int, float)) and isinstance(p99, (int, float)) and p50 > 0:
                values.append(p99 / p50)
            continue
        v = target.get(field)
        if isinstance(v, (int, float)):
            values.append(v)
    if not values:
        return None
    # Every network metric is "lower is worse when higher", so worst == max.
    return max(values)


def metric_value(result: dict, path: str):
    if path.startswith("network."):
        return reduce_network(result, path)
    return dig(result, path)


def grade_metric(value, metric_def: dict) -> str:
    """Band lookup for one metric. Returns A-F, or ? when unmeasured."""
    if value is None or isinstance(value, bool):
        return "?"
    if not isinstance(value, (int, float)):
        return "?"
    bands, op = metric_def["bands"], metric_def["op"]
    for g in ("A", "B", "C", "D"):
        bound = bands[g]
        if (op == "lte" and value <= bound) or (op == "gte" and value >= bound):
            return g
    return "F"


def rollup(graded: dict[str, str], required: dict[str, bool]) -> tuple[str, str | None]:
    """Worst-wins rollup. Returns (grade, binding_metric).

    Precedence (spec 4.2):
      1. any rule at F            -> F
      2. else any required rule ? -> ?
      3. else the worst band present

    F beats ? on purpose. A host with a 459ms fsync p99.9 is F whether or not
    its stall was measured -- grading is non-compensatory, so no unmeasured
    metric could rescue it, and reporting ? would discard a fact we hold. It
    also stops ? being a hiding place: if a missing metric outranked a measured
    failure, skipping a stage would upgrade an F to a ?, a better-looking cell
    obtained by running less of the suite.
    """
    failures = [m for m, g in graded.items() if g == "F"]
    if failures:
        return ("F", failures[0])

    missing_required = [m for m, g in graded.items() if g == "?" and required.get(m)]
    if missing_required:
        return ("?", missing_required[0])

    scored = {m: g for m, g in graded.items() if g in RANK}
    if not scored:
        return ("?", None)
    binding = max(scored, key=lambda m: RANK[scored[m]])
    return (scored[binding], binding)


def storage_class(result: dict) -> str | None:
    """Derive storage class from measured fsync latency-per-op (spec 4.5).

    Derived here rather than emitted by bench/ because it is a DERIVED value,
    not a measurement, and because the published v1 results can never grow a new
    field -- computing it alongside grades covers every result, old and new.
    """
    iops = dig(result, "disk.wal_fsync.iops")
    if not isinstance(iops, (int, float)) or iops <= 0:
        return None
    us_per_op = 1_000_000 / iops
    for bound, name in STORAGE_CLASS_BOUNDS:
        if us_per_op < bound:
            return name
    return STORAGE_CLASS_WORST


def _grade_rules(result, thresholds, rule_specs):
    graded, required, detail = {}, {}, {}
    for rule in rule_specs:
        path = rule["metric"]
        mdef = thresholds["metrics"][path]
        value = metric_value(result, path)
        g = grade_metric(value, mdef)
        graded[path] = g
        required[path] = rule["required"]
        detail[path] = {"value": value, "grade": g}
    return graded, required, detail


def compute(result: dict, thresholds: dict) -> dict:
    """Return the `grades` block: categories, profiles, storage_class."""
    tool_version = (result.get("run") or {}).get("tool_version")

    out = {
        "bands_version": thresholds["bands_version"],
        "storage_class": storage_class(result),
        "categories": {},
        "profiles": {},
    }

    # Categories describe the machine: every metric in the category, all
    # treated as required, because a category grade with a hole in it is not a
    # description of anything.
    for cat, paths in thresholds["categories"].items():
        specs = [{"metric": p, "required": True} for p in paths]
        graded, required, detail = _grade_rules(result, thresholds, specs)
        grade, bound = rollup(graded, required)
        out["categories"][cat] = {
            "grade": grade,
            "bound_by": bound,
            "metrics": detail,
        }

    # Profiles are opinions that consume category metrics.
    for name, profile in thresholds["profiles"].items():
        graded, required, _ = _grade_rules(result, thresholds, profile["rules"])
        grade, bound = rollup(graded, required)
        entry = {"grade": grade, "bound_by": bound}

        if grade == "?":
            # Say WHY it is unknown. A result measured before 0.2.0 simply does
            # not carry the newer metrics; that is a re-run, not a defect.
            entry["reason"] = (
                f"{bound} not measured (required)"
                if tool_version and tool_version >= "0.2.0"
                else "needs re-run (tool >= 0.2.0)"
            )
        if profile.get("network_half_unmeasured"):
            # Commit latency in a sync cluster is local fsync + inter-node RTT.
            # We measure the first term only. A grade that silently covers half
            # an equation is worse than no grade (spec 7.3).
            entry["network_half_unmeasured"] = True
        out["profiles"][name] = entry

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result", type=Path)
    ap.add_argument("--in-place", action="store_true", help="write grades back into the file")
    args = ap.parse_args()

    thresholds = yaml.safe_load(THRESHOLDS_PATH.read_text())
    result = json.loads(args.result.read_text())
    grades = compute(result, thresholds)

    if args.in_place:
        result["grades"] = grades
        args.result.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote grades to {args.result}", file=sys.stderr)
    else:
        print(json.dumps(grades, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
