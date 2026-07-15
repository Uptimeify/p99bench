#!/usr/bin/env python3
"""Compute verdicts from schema/thresholds.yaml.

The verdict is never a human judgement. It is a pure function of the measured
numbers and the published thresholds. If you disagree with a verdict, argue
about the threshold in THRESHOLDS.md -- do not edit the verdict.

Usage:
    python3 tools/verdict.py results/hetzner/foo.json --in-place
    python3 tools/verdict.py results/hetzner/foo.json           # print only
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
THRESHOLDS = ROOT / "schema" / "thresholds.yaml"

# Worst wins: any fail makes the profile fail.
RANK = {"pass": 0, "marginal": 1, "unknown": 2, "fail": 3}
UNRANK = {v: k for k, v in RANK.items()}


def dig(obj: dict, path: str):
    """Walk a dotted path, returning None if anything along the way is absent."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def evaluate_rule(value, rule: dict) -> tuple[str, str | None]:
    """Return (verdict, reason_if_not_pass) for a single rule."""
    if value is None:
        return ("unknown", f"{rule['path']} not measured")

    op = rule["op"]
    p, m = rule["pass"], rule["marginal"]

    if op == "gte":
        if value >= p:
            return ("pass", None)
        if value >= m:
            return ("marginal", f"{rule['path']} = {value} (marginal, want >= {p})")
        return ("fail", f"{rule['path']} = {value} < {m}")
    if op == "lte":
        if value <= p:
            return ("pass", None)
        if value <= m:
            return ("marginal", f"{rule['path']} = {value} (marginal, want <= {p})")
        return ("fail", f"{rule['path']} = {value} > {m}")

    raise ValueError(f"unknown op: {op}")


def compute(result: dict, thresholds: dict) -> dict:
    verdict = {"reasons": []}

    for profile_name, profile in thresholds["profiles"].items():
        worst = 0
        reasons = []
        has_unknown_required = False

        for rule in profile["rules"]:
            value = dig(result, rule["path"])
            v, reason = evaluate_rule(value, rule)

            if v == "unknown":
                # An unmeasured optional rule is simply skipped. An unmeasured
                # required rule means we cannot honestly claim a verdict.
                if rule.get("required"):
                    has_unknown_required = True
                    reasons.append(f"[{profile_name}] {reason} (required)")
                continue

            if reason:
                reasons.append(f"[{profile_name}] {reason}")
            worst = max(worst, RANK[v])

        if has_unknown_required:
            verdict[profile_name] = "unknown"
        else:
            verdict[profile_name] = UNRANK[worst]
        verdict["reasons"].extend(reasons)

    return verdict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result", type=Path)
    ap.add_argument("--in-place", action="store_true", help="write verdict back into the file")
    args = ap.parse_args()

    thresholds = yaml.safe_load(THRESHOLDS.read_text())
    result = json.loads(args.result.read_text())
    verdict = compute(result, thresholds)

    if args.in_place:
        result["verdict"] = verdict
        args.result.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote verdict to {args.result}", file=sys.stderr)
    else:
        print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
