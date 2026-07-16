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


import sys

sys.path.insert(0, str(ROOT / "tools"))
from grade import (  # noqa: E402
    compute, grade_metric, reduce_network, rollup,
)

LTE = {"op": "lte", "bands": {"A": 1000, "B": 3000, "C": 10000, "D": 50000}}
GTE = {"op": "gte", "bands": {"A": 1400, "B": 1000, "C": 700, "D": 400}}


def test_grade_metric_lte_boundaries_are_inclusive():
    assert grade_metric(1000, LTE) == "A"
    assert grade_metric(1001, LTE) == "B"
    assert grade_metric(50000, LTE) == "D"
    assert grade_metric(50001, LTE) == "F"


def test_grade_metric_gte_boundaries_are_inclusive():
    assert grade_metric(1400, GTE) == "A"
    assert grade_metric(1399, GTE) == "B"
    assert grade_metric(400, GTE) == "D"
    assert grade_metric(399, GTE) == "F"


def test_grade_metric_missing_is_question_mark():
    assert grade_metric(None, LTE) == "?"


def test_real_corpus_values_grade_as_spec_66_says():
    # Spec 6.6 pins the grade matrix this corpus must produce. These are real
    # published numbers; if a band edit changes them, that is the system
    # working -- but it must be a deliberate edit, not a drift.
    assert grade_metric(1875.97, LTE) == "B"    # hetzner/hel-1 best fsync p99.9
    assert grade_metric(8355.84, LTE) == "C"    # ovh/prg
    assert grade_metric(117964.8, LTE) == "F"   # ovh/zrh
    assert grade_metric(459276.29, LTE) == "F"  # windcloud
    assert grade_metric(356.25, GTE) == "F"     # ovh/waw single_thread_eps
    assert grade_metric(1661.19, GTE) == "A"    # hetzner/hel-1


def test_rollup_worst_wins():
    g = {"a": "A", "b": "C", "c": "B"}
    assert rollup(g, {"a": True, "b": True, "c": True}) == ("C", "b")


def test_rollup_f_beats_question_mark():
    # Spec 4.2 precedence. A host with a 459ms fsync is F whether or not its
    # stall was measured -- grading is non-compensatory, so no unmeasured
    # metric could rescue it. And ? must not be a hiding place: if a missing
    # metric outranked a measured failure, skipping a stage would upgrade an F
    # to a ?, a better-looking cell obtained by running LESS of the suite.
    g = {"fsync": "F", "stall": "?"}
    grade, bound = rollup(g, {"fsync": True, "stall": True})
    assert grade == "F"
    assert bound == "fsync"


def test_rollup_question_mark_when_required_missing_and_no_failure():
    g = {"fsync": "B", "stall": "?"}
    assert rollup(g, {"fsync": True, "stall": True})[0] == "?"


def test_rollup_skips_missing_optional_rule():
    g = {"fsync": "B", "advisory": "?"}
    assert rollup(g, {"fsync": True, "advisory": False}) == ("B", "fsync")


def test_rollup_names_the_binding_constraint():
    g = {"fsync": "D", "reads": "B"}
    assert rollup(g, {"fsync": True, "reads": True}) == ("D", "fsync")


def test_reduce_network_takes_the_worst_target():
    # One bad path is a bad path. Averaging loss across targets would hide the
    # exact 10% outlier the corpus contains (ovh/zrh -> hetzner-ash).
    result = {"network": {"reachable": True, "targets": [
        {"id": "a", "loss_pct": 0.0},
        {"id": "b", "loss_pct": 10.0},
    ]}}
    assert reduce_network(result, "network.loss_pct") == 10.0


def test_reduce_network_none_when_unreachable():
    assert reduce_network({"network": {"reachable": False}}, "network.loss_pct") is None
