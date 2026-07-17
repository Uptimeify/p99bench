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


def reduce_network(result: dict, path: str, op: str):
    """Worst value across network targets for a `network.<field>` metric.

    Worst, not mean. Every host measures the SAME fixed targets so distance is a
    constant, which is what makes the numbers comparable at all -- and one bad
    path is a bad path. Averaging would bury the 10% loss outlier the corpus
    already contains (ovh/zrh -> hetzner-ash) under three clean paths.

    `op` says which direction is worse: for `lte` metrics (higher = worse,
    e.g. loss_pct, dns_ms) worst is max(); for `gte` metrics (lower = worse,
    e.g. a hypothetical banded mbps) worst is min(). This must be derived from
    the metric definition, not assumed -- every network metric graded today
    happens to be `lte`, but a `gte` one reduced by max() would silently
    return the BEST path, the exact inversion of a worst-wins engine.
    """
    net = result.get("network") or {}
    if not net.get("reachable"):
        return None
    field = path.split(".", 1)[1]
    values = []
    for target in net.get("targets") or []:
        # Do NOT skip on target.get("reachable"). Per-target `reachable` is an
        # HTTP-status flag set by bench/06-network.sh from `http_code == 200`
        # -- it is not a "nothing was measured for this target" flag. Every
        # field is independently nullable there: a target that fails the HTTP
        # check can still carry a real dns_ms (from curl's time_namelookup)
        # and rtt/loss (from ping). Skipping the target on this flag would
        # throw those away and, because max() over a subset is never greater
        # than max() over the full set, could only ever IMPROVE the
        # worst-wins grade -- a compensatory hole this engine must not have.
        # The isinstance() filter below already handles real non-measurement
        # (total curl failure emits every field as null).
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
    return max(values) if op == "lte" else min(values)


def metric_value(result: dict, path: str, metric_def: dict):
    if path.startswith("network."):
        return reduce_network(result, path, metric_def["op"])
    return dig(result, path)


def grade_metric(value, metric_def: dict) -> str:
    """Band lookup for one metric. Returns A-F, or ? when unmeasured."""
    # bool must be excluded explicitly: isinstance(True, int) is True in
    # Python, so without this a bool would fall through to the numeric branch
    # and grade as if it were 0 or 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "?"
    bands, op = metric_def["bands"], metric_def["op"]
    for g in ("A", "B", "C", "D"):
        bound = bands[g]
        if (op == "lte" and value <= bound) or (op == "gte" and value >= bound):
            return g
    return "F"


def rollup(
    graded: dict[str, str], required: dict[str, bool]
) -> tuple[str, str | None, bool, list[str]]:
    """Worst-wins rollup. Returns (grade, binding_metric, incomplete, missing).

    Precedence (spec 4.2):
      - grade = the worst band among MEASURED metrics. F falls out of this
        naturally (it is simply the worst band on the scale) -- there is no
        separate "F beats ?" special case anymore.
      - `?` is reserved for the case where NOTHING in this category/profile
        was measured. There is no lower bound to report, so there is nothing
        to say.
      - `incomplete` is True whenever any `required: true` rule went
        unmeasured. The grade is then a FLOOR, not a final answer: it is "at
        least this bad", because grading is non-compensatory and a missing
        metric can only drag the grade down, never lift it.
      - `missing` names the required metrics that are still unmeasured, so a
        reader knows what could still lower the grade.

    Why the worst MEASURED band, not `?`, when a required rule is missing: a
    host with a 400 eps single_thread_eps (D) and an unmeasured stall_p999_us
    is measurably D right now -- reporting `?` over that discards a fact we
    hold. Grading is non-compensatory, so the missing metric cannot rescue
    the D; it can only make it worse. This also stops `?` being a hiding
    place: under the old rule, skipping a stage could turn a measured D into
    a `?`, which reads BETTER than D -- a strictly better-looking cell
    obtained by running LESS of the suite. Reporting the floor instead removes
    that incentive rather than creating a new one.
    """
    missing = sorted(m for m, g in graded.items() if g == "?" and required.get(m))
    incomplete = bool(missing)

    scored = {m: g for m, g in graded.items() if g in RANK}
    if not scored:
        return ("?", None, incomplete, missing)
    binding = max(scored, key=lambda m: RANK[scored[m]])
    return (scored[binding], binding, incomplete, missing)


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


def _version_gte(version, minimum: str) -> bool:
    """True if a dotted version string (e.g. "0.10.0") is >= minimum.

    A plain string compare ("0.10.0" >= "0.2.0") is False, because it is
    lexicographic ("1" < "2"), not numeric -- a tool newer than the minimum
    would be wrongly told it needs a re-run. Each dotted component is
    compared as an int instead. A missing or malformed version is treated as
    NOT meeting the minimum: safer to ask an unknown-age tool to re-run than
    to silently assume it is new enough.
    """
    if not version or not isinstance(version, str):
        return False
    try:
        parts = tuple(int(p) for p in version.split("."))
        min_parts = tuple(int(p) for p in minimum.split("."))
    except ValueError:
        return False
    return parts >= min_parts


def _grade_rules(result, thresholds, rule_specs):
    graded, required, detail = {}, {}, {}
    for rule in rule_specs:
        path = rule["metric"]
        mdef = thresholds["metrics"][path]
        value = metric_value(result, path, mdef)
        g = grade_metric(value, mdef)
        graded[path] = g
        # .get(), not rule["required"]: rollup() already treats a missing
        # entry in `required` as falsy via required.get(m), so this must be
        # equally defensive rather than KeyError on a rule that omits the
        # key (schema/bands.schema.json mandates it, but nothing at runtime
        # enforces that before a rule reaches here).
        required[path] = rule.get("required", False)
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
        grade, bound, incomplete, missing = rollup(graded, required)
        out["categories"][cat] = {
            "grade": grade,
            "bound_by": bound,
            "incomplete": incomplete,
            "missing": missing,
            "metrics": detail,
        }

    # Profiles are opinions that consume category metrics.
    for name, profile in thresholds["profiles"].items():
        graded, required, _ = _grade_rules(result, thresholds, profile["rules"])
        grade, bound, incomplete, missing = rollup(graded, required)
        entry = {
            "grade": grade,
            "bound_by": bound,
            "incomplete": incomplete,
            "missing": missing,
        }

        if grade == "?":
            # Say WHY it is unknown. A result measured before 0.2.0 simply does
            # not carry the newer metrics; that is a re-run, not a defect.
            # grade == "?" only fires when NOTHING was measured, so `bound` is
            # always None here -- name the first missing required metric
            # instead (there is always at least one: an empty `missing` with
            # nothing scored would mean the profile has zero rules).
            ref = missing[0] if missing else bound
            entry["reason"] = (
                f"{ref} not measured (required)"
                if _version_gte(tool_version, "0.2.0")
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
