import json
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())
BANDS_SCHEMA = json.loads((ROOT / "schema" / "bands.schema.json").read_text())


def test_thresholds_file_matches_its_own_schema():
    # A typo in a band bound is a silent mis-grade with no stack trace, so the
    # rules file is itself validated.
    jsonschema.Draft202012Validator(BANDS_SCHEMA).validate(THRESHOLDS)


def test_bands_are_monotonic_in_the_direction_of_the_op():
    # For op lte (lower is better) bounds must ascend A<B<C<D; for gte they must
    # descend. A transposed pair silently grades good hosts badly and vice
    # versa, and nothing else would catch it.
    for name, m in THRESHOLDS["metrics"].items():
        vals = [m["bands"][g] for g in ("A", "B", "C", "D")]
        if m["op"] == "lte":
            assert vals == sorted(vals), f"{name}: lte bands must ascend, got {vals}"
        else:
            assert vals == sorted(vals, reverse=True), (
                f"{name}: gte bands must descend, got {vals}"
            )


def test_every_metric_declares_why_and_confidence():
    for name, m in THRESHOLDS["metrics"].items():
        assert m.get("why"), f"{name} has no reasoning"
        assert m.get("confidence") in ("high", "medium", "low"), name


def test_every_category_metric_exists():
    for cat, metrics in THRESHOLDS["categories"].items():
        for path in metrics:
            assert path in THRESHOLDS["metrics"], f"{cat} references unknown {path}"
