#!/usr/bin/env python3
"""One-shot migration of published results from schema 1.0 to 2.0.

What changes: the DERIVED block only. `verdict` (one word per profile) becomes
`grades` (A-F per category and per profile), and schema_version goes to "2.0".

What does not change: any measured number. Results are immutable measurements;
grades are derived and always current (spec 9.1). This tool must never touch
run/provider/host/disk/cpu/ram/network/app, and tests/test_migrate.py enforces
that.

Why migrate rather than support both shapes: two schemas means two code paths in
validate.py and render.py forever, and a stale verdict could hide in the seam.
The measurements survive untouched; only the derivation is regenerated, which is
exactly what CI does on every threshold change anyway.

Usage:
    python3 tools/migrate_v1_v2.py results/ [--dry-run]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grade import compute  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS_PATH = ROOT / "schema" / "thresholds.yaml"

# Every section holding measured data. The migration must leave all of these
# byte-identical; only the derived block is regenerated.
MEASURED_SECTIONS = (
    "run", "provider", "host", "disk", "cpu", "ram", "network", "app",
)


def migrate(result: dict, thresholds: dict) -> bool:
    """v1 -> v2 in place. Returns True if the document changed."""
    if result.get("schema_version") == "2.0" and "verdict" not in result:
        return False

    result.pop("verdict", None)
    result["schema_version"] = "2.0"
    result["grades"] = compute(result, thresholds)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    thresholds = yaml.safe_load(THRESHOLDS_PATH.read_text())
    paths = sorted(args.target.rglob("*.json")) if args.target.is_dir() else [args.target]

    changed = 0
    for path in paths:
        doc = json.loads(path.read_text())
        if not migrate(doc, thresholds):
            continue
        changed += 1
        profiles = doc["grades"]["profiles"]
        summary = " ".join(f"{n}={p['grade']}" for n, p in sorted(profiles.items()))
        print(f"{path}\n  {doc['grades']['storage_class']}  {summary}")
        if not args.dry_run:
            path.write_text(json.dumps(doc, indent=2) + "\n")

    verb = "would migrate" if args.dry_run else "migrated"
    print(f"{verb} {changed} of {len(paths)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
