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
