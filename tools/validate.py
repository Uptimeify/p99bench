#!/usr/bin/env python3
"""Validate result files against the schema and the contribution rules.

Two layers:
  1. JSON Schema  -- structural correctness.
  2. Policy rules -- things a schema cannot express, like "the grades in the
     file must match what the bands actually compute". This is what stops
     a submitter, including us, from hand-editing a grade.

Usage:
    python3 tools/validate.py results/
    python3 tools/validate.py results/hetzner/foo.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import jsonschema
    import yaml
except ImportError:
    sys.exit("deps required: pip install jsonschema pyyaml")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grade import compute  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
SCHEMA = json.loads((ROOT / "schema" / "result.schema.json").read_text())
THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())

FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}-[a-z0-9-]+\.json$")


def check_policy(path: Path, data: dict) -> list[str]:
    errs: list[str] = []

    # Layout is results/<provider>/<region>/<file>.json. Provider and region are
    # separate levels on purpose: it keeps "how is OVH Zurich?" and "how is OVH
    # overall?" both answerable. Folding the region into the provider slug
    # (ovh-zrh) would destroy the second question.
    provider = data.get("provider", {}).get("name")
    region = data.get("provider", {}).get("region")
    # resolve() both sides: the CLI may hand us a relative path while
    # RESULTS_DIR is absolute, and relative_to does not normalise.
    try:
        rel = path.resolve().relative_to(RESULTS_DIR.resolve())
    except ValueError:
        rel = None

    if rel is None or len(rel.parts) != 3:
        errs.append(
            f"path must be results/<provider>/<region>/<file>.json, got '{path}'"
        )
    else:
        dir_provider, dir_region, _ = rel.parts
        if provider and dir_provider != provider:
            errs.append(
                f"file is in results/{dir_provider}/ but provider.name is '{provider}'"
            )
        if region and dir_region != region:
            errs.append(
                f"file is in results/{dir_provider}/{dir_region}/ but provider.region "
                f"is '{region}'"
            )

    if not FILENAME_RE.match(path.name):
        errs.append(
            f"filename '{path.name}' must look like 2026-07-15T1655-b2-7.json"
        )

    # The whole point of the project: a run without the sustained test cannot
    # distinguish a fast disk from a burst credit balance.
    steady = data.get("disk", {}).get("steady_state")
    if not steady or steady.get("degradation_pct") is None:
        errs.append(
            "disk.steady_state.degradation_pct missing -- run without --skip-steady. "
            "A 60s run cannot see burst throttling."
        )

    # fsync is the headline metric. Without it there is no grade worth having.
    if data.get("disk", {}).get("wal_fsync", {}).get("p999_us") is None:
        errs.append("disk.wal_fsync.p999_us missing -- this is the primary metric")

    if not data.get("run", {}).get("submitter"):
        errs.append("run.submitter missing -- results are not accepted anonymously")

    # Grades must be reproducible from the published bands. This is the trust
    # property, not a lint: a project publishing provider comparisons has an
    # obvious temptation to nudge them, and the only real defence is making a
    # nudge fail the build in public.
    if data.get("grades"):
        stored = data["grades"]
        if stored.get("bands_version") != THRESHOLDS["bands_version"]:
            errs.append(
                f"grades.bands_version is '{stored.get('bands_version')}' but "
                f"schema/thresholds.yaml is '{THRESHOLDS['bands_version']}'. "
                f"Re-run tools/grade.py --in-place."
            )
        expected = compute(data, THRESHOLDS)
        if stored != expected:
            for name, exp in expected["profiles"].items():
                got = stored.get("profiles", {}).get(name, {}).get("grade")
                if got != exp["grade"]:
                    errs.append(
                        f"grades.profiles.{name} is '{got}' but the bands compute "
                        f"'{exp['grade']}'. Do not hand-edit grades; run "
                        f"tools/grade.py --in-place."
                    )
            for name, exp in expected["categories"].items():
                got = stored.get("categories", {}).get(name, {}).get("grade")
                if got != exp["grade"]:
                    errs.append(
                        f"grades.categories.{name} is '{got}' but the bands "
                        f"compute '{exp['grade']}'."
                    )
            if stored.get("storage_class") != expected["storage_class"]:
                errs.append(
                    f"grades.storage_class is '{stored.get('storage_class')}' but "
                    f"the measured fsync latency computes "
                    f"'{expected['storage_class']}'."
                )

    return errs


def validate_file(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e}"]

    errs = []
    validator = jsonschema.Draft202012Validator(SCHEMA)
    for e in sorted(validator.iter_errors(data), key=lambda x: list(x.path)):
        loc = ".".join(str(p) for p in e.path) or "<root>"
        errs.append(f"schema: {loc}: {e.message}")

    if not errs:
        errs.extend(check_policy(path, data))
    return errs


def check_coverage(files: list[Path]) -> list[str]:
    """Cross-file warnings. These do not fail CI.

    A single run is publishable -- it is still a real measurement -- but it
    cannot distinguish hardware from a neighbour, so render.py excludes it from
    spread calculations. Saying so out loud beats silently dropping it.
    """
    warnings: list[str] = []
    by_host: dict[str, list[dict]] = {}
    for f in files:
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        hid = d.get("run", {}).get("host_id")
        if hid:
            by_host.setdefault(hid, []).append(d)

    for hid, runs in sorted(by_host.items()):
        hours = {r["run"].get("local_hour") for r in runs}
        if len(runs) < 3:
            p = runs[0]["provider"]
            warnings.append(
                f"host {hid[:6]} ({p['name']}/{p['region']}/{p['product']}) has "
                f"{len(runs)} run(s); 3+ at different hours is the bar for a spread"
            )
        elif len(hours) < 2:
            warnings.append(
                f"host {hid[:6]} has {len(runs)} runs but all at hour "
                f"{hours.pop():02d}h; time-of-day variance is invisible"
            )
    return warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path, nargs="?", default=RESULTS_DIR)
    args = ap.parse_args()

    files = (
        sorted(args.target.rglob("*.json"))
        if args.target.is_dir()
        else [args.target]
    )
    if not files:
        print("no result files found (that is fine for an empty repo)")
        return 0

    failed = 0
    for f in files:
        errs = validate_file(f)
        rel = f.relative_to(ROOT) if ROOT in f.parents else f
        if errs:
            failed += 1
            print(f"FAIL {rel}")
            for e in errs:
                print(f"     {e}")
        else:
            print(f"ok   {rel}")

    print(f"\n{len(files) - failed}/{len(files)} valid")

    for w in check_coverage(files):
        print(f"warn {w}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())