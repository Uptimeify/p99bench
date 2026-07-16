"""A freshly measured result must satisfy the repo's own validator.

This seam has broken three times. Phase 1 shipped a schema with
additionalProperties:false that rejected every field its own stages had just
started emitting. Phase 2 deleted tools/verdict.py and left run-all.sh calling
it, so a fresh run produced no grades at all. Then run-all.sh kept hardcoding
schema_version "1.0" after the schema moved to const "2.0".

Every one of those was invisible to `validate.py results/`, because results/
only ever held files produced by the PREVIOUS tool version. The bug only fires
for the next person who actually runs the suite -- who gets an hour of
benchmarking and a rejected PR.

So: pin the contract between what bench/ emits and what schema/ demands.
"""
import json
import re
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from validate import SCHEMA  # noqa: E402


def test_run_all_emits_the_schema_version_the_schema_demands():
    demanded = SCHEMA["properties"]["schema_version"]["const"]
    src = (ROOT / "bench" / "run-all.sh").read_text()
    m = re.search(r'schema_version:\s*"([\d.]+)"', src)
    assert m, "run-all.sh no longer emits a schema_version -- find where it moved"
    assert m.group(1) == demanded, (
        f"run-all.sh emits schema_version {m.group(1)} but the schema demands "
        f"{demanded}; every fresh run would be rejected by validate.py"
    )


def test_run_all_populates_every_schema_required_top_level_key():
    # `grades` is required, and run-all.sh only gets it by invoking grade.py
    # after the merge. If that call is ever dropped or renamed again, a fresh
    # run silently produces an invalid file.
    src = (ROOT / "bench" / "run-all.sh").read_text()
    assert "grade.py" in src, "run-all.sh no longer invokes grade.py -- fresh runs would carry no grades block"


def test_validate_standard_durations_match_the_bench_defaults():
    """tools/ must agree with bench/ on what the standard run IS.

    If validate.py expects 1800s and 01b-steady.sh defaults to something else,
    every fresh run is rejected by our own validator -- the same drift that
    shipped run-all.sh emitting schema_version 1.0 against a const 2.0 schema.
    """
    import validate as V

    disk = (ROOT / "bench" / "01b-steady.sh").read_text()
    m = re.search(r'DURATION="\$\{P99_STEADY_DURATION:-(\d+)\}"', disk)
    assert m, "01b-steady.sh no longer sets a default steady duration"
    assert int(m.group(1)) == V.STANDARD_DISK_STEADY_S, (
        f"01b-steady.sh defaults to {m.group(1)}s but validate.py demands "
        f"{V.STANDARD_DISK_STEADY_S}s; every fresh run would be rejected"
    )

    cpu = (ROOT / "bench" / "02b-cpu-steady.sh").read_text()
    m = re.search(r'MINUTES="\$\{P99_CPU_STEADY_MIN:-(\d+)\}"', cpu)
    assert m, "02b-cpu-steady.sh no longer sets a default minute count"
    assert int(m.group(1)) * 60 == V.STANDARD_CPU_STEADY_S, (
        f"02b-cpu-steady.sh defaults to {m.group(1)} min but validate.py demands "
        f"{V.STANDARD_CPU_STEADY_S}s"
    )


def test_short_steady_run_is_rejected():
    """A 10-minute disk steady test must not publish beside 30-minute runs.

    An AWS gp2 volume bursts for ~33 min, so a 10-min run reports "no
    throttling" about a disk that throttles 10x an hour later. Publishing that
    next to real 30-min results is silent incomparability -- the same failure
    network-targets.yaml's list_version exists to prevent.
    """
    import copy
    import json
    import validate as V

    corpus = sorted((ROOT / "tests" / "fixtures" / "corpus").rglob("*.json"))
    doc = json.loads(corpus[0].read_text())
    assert V.check_policy(corpus[0], doc, results_dir=ROOT / "tests" / "fixtures" / "corpus") == []

    short = copy.deepcopy(doc)
    short["disk"]["steady_state"]["duration_s"] = 600
    errs = V.check_policy(short and corpus[0], short,
                          results_dir=ROOT / "tests" / "fixtures" / "corpus")
    assert any("duration_s is 600s" in e for e in errs), errs
