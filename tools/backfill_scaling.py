#!/usr/bin/env python3
"""Backfill cpu.scaling_efficiency into results measured before the jnum fix.

Every published result has scaling_efficiency: null, because bc prints ".977"
and lib.sh's jnum rejected leading-dot decimals (see the fix in bench/lib.sh).
The inputs were recorded correctly the whole time, so the value is recoverable
without re-running the benchmark -- which matters, because some of those VMs
no longer exist.

This is a one-shot migration, not part of the run path. New results compute
the field in 02-cpu.sh.

Usage:
    python3 tools/backfill_scaling.py results/ [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def backfill(result: dict) -> bool:
    """Fill cpu.scaling_efficiency from single/multi thread eps and vcpu.

    Returns True if the document was changed. Declines (returns False) when a
    value already exists or an input is missing -- never overwrites a real
    measurement, never invents one.
    """
    cpu = result.get("cpu")
    if not isinstance(cpu, dict) or cpu.get("scaling_efficiency") is not None:
        return False

    st = cpu.get("single_thread_eps")
    mt = cpu.get("multi_thread_eps")
    vcpu = result.get("host", {}).get("vcpu")

    if not all(isinstance(v, (int, float)) for v in (st, mt, vcpu)):
        return False
    if st <= 0 or vcpu <= 0:
        return False

    # Same expression as 02-cpu.sh: mt / (st * cores).
    #
    # TRUNCATE, do not round. bc's `scale=3` truncates, so 02-cpu.sh emits
    # .977 for inputs that round() would turn into 0.978. A backfilled value
    # must be bit-identical to what a re-run would produce, or the same machine
    # measured twice disagrees with itself for no physical reason.
    #
    # math.floor is safe here specifically because scaling efficiency cannot be
    # negative (it is a ratio of two positive throughputs); floor and bc's
    # toward-zero truncation only diverge below zero.
    cpu["scaling_efficiency"] = math.floor(mt / (st * vcpu) * 1000) / 1000
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    paths = sorted(args.target.rglob("*.json")) if args.target.is_dir() else [args.target]
    changed = 0
    for path in paths:
        doc = json.loads(path.read_text())
        if not backfill(doc):
            continue
        changed += 1
        val = doc["cpu"]["scaling_efficiency"]
        print(f"{path}: scaling_efficiency = {val}")
        if not args.dry_run:
            path.write_text(json.dumps(doc, indent=2) + "\n")

    verb = "would change" if args.dry_run else "changed"
    print(f"{verb} {changed} of {len(paths)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
