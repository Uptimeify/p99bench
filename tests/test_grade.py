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
    _grade_rules, _version_gte, compute, grade_metric, reduce_network, rollup,
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


def test_grade_metric_bool_is_question_mark_not_a_grade():
    # isinstance(True, int) is True in Python, so without an explicit bool
    # guard grade_metric(True, LTE) would fall through to the numeric branch
    # and compare True (== 1) against the bands, silently returning "A". A
    # bool is not a measurement.
    assert grade_metric(True, LTE) == "?"
    assert grade_metric(False, LTE) == "?"


def test_real_corpus_values_grade_as_spec_66_says():
    # Spec 6.6 pins the grade matrix this corpus must produce. These are real
    # published numbers; if a band edit changes them, that is the system
    # working -- but it must be a deliberate edit, not a drift. Graded against
    # THRESHOLDS["metrics"] (the real, live bands from schema/thresholds.yaml),
    # not local fixtures -- a local LTE/GTE dict here would be an unmarked
    # duplicate of the real bands, free to diverge silently, and no edit to
    # thresholds.yaml could ever trip it.
    fsync = THRESHOLDS["metrics"]["disk.wal_fsync.p999_us"]
    eps = THRESHOLDS["metrics"]["cpu.single_thread_eps"]
    assert grade_metric(1875.97, fsync) == "B"    # hetzner/hel-1 best fsync p99.9
    assert grade_metric(8355.84, fsync) == "C"    # ovh/prg
    assert grade_metric(117964.8, fsync) == "F"   # ovh/zrh
    assert grade_metric(459276.29, fsync) == "F"  # windcloud
    assert grade_metric(356.25, eps) == "F"       # ovh/waw single_thread_eps
    assert grade_metric(1661.19, eps) == "A"      # hetzner/hel-1


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
    # loss_pct is lte-shaped (higher = worse), so worst == max.
    result = {"network": {"reachable": True, "targets": [
        {"id": "a", "loss_pct": 0.0},
        {"id": "b", "loss_pct": 10.0},
    ]}}
    assert reduce_network(result, "network.loss_pct", "lte") == 10.0


def test_reduce_network_none_when_unreachable():
    assert reduce_network({"network": {"reachable": False}}, "network.loss_pct", "lte") is None


def test_reduce_network_counts_target_with_http_reachable_false_but_valid_dns():
    # Per-target `reachable` is an HTTP-status flag (http_code == 200 in
    # bench/06-network.sh), not a "nothing was measured here" flag. Every
    # field is independently nullable, and a target that fails the HTTP check
    # can still carry a real dns_ms/rtt/loss_pct from curl/ping. This is the
    # real shape of results/*/*/*.json's "ovh-gra" target: reachable=false,
    # mbps=null, dns_ms populated. Dropping the whole target on `reachable`
    # silently discards valid measurements and can only IMPROVE (never
    # worsen) the worst-wins reduction -- the exact compensatory hole spec
    # 4.2 exists to prevent.
    result = {"network": {"reachable": True, "targets": [
        {"id": "ovh-gra", "reachable": False, "mbps": None, "dns_ms": 58.7,
         "rtt_p50_ms": 28.9, "rtt_p99_ms": 29.1, "loss_pct": 0},
        {"id": "hetzner-ash", "reachable": True, "mbps": 120.0, "dns_ms": 5.0,
         "rtt_p50_ms": 10.0, "rtt_p99_ms": 10.5, "loss_pct": 0},
    ]}}
    assert reduce_network(result, "network.dns_ms", "lte") == 58.7


def test_reduce_network_gte_metric_reduces_by_min():
    # network.mbps is not graded today, but it is gte-shaped (higher = better)
    # per-target. If it is ever banded, "worst" must be the SMALLEST value,
    # not the largest -- max() would silently return the best path, the exact
    # inversion of a worst-wins engine. reduce_network must derive worst from
    # the metric's op rather than assume "lower is worse" universally.
    result = {"network": {"reachable": True, "targets": [
        {"id": "a", "mbps": 500.0},
        {"id": "b", "mbps": 50.0},
    ]}}
    assert reduce_network(result, "network.mbps", "gte") == 50.0


def test_reduce_network_rtt_jitter_ratio_is_p99_over_p50_not_the_inverse():
    # rtt_jitter_ratio is defined (schema/thresholds.yaml) as p99/p50: a
    # sluggish, spiky path has a big ratio. Getting the division backwards
    # (p50/p99) would silently turn "worse" into a number below 1 and make a
    # bad path look calm -- a mutation-tested-and-missed bug in this exact
    # engine (see IMPORTANT 4). Pin the direction with an asymmetric pair
    # where the two orderings cannot be confused for each other.
    result = {"network": {"reachable": True, "targets": [
        {"id": "a", "rtt_p50_ms": 10.0, "rtt_p99_ms": 40.0},
    ]}}
    assert reduce_network(result, "network.rtt_jitter_ratio", "lte") == 4.0


def test_reduce_network_rtt_jitter_ratio_guards_against_zero_p50():
    # p99/p50 with p50 == 0 is a ZeroDivisionError waiting to happen; a target
    # that somehow reports zero-latency p50 (or a malformed measurement) must
    # be excluded from the reduction rather than crash the whole grading run.
    result = {"network": {"reachable": True, "targets": [
        {"id": "a", "rtt_p50_ms": 0.0, "rtt_p99_ms": 5.0},
        {"id": "b", "rtt_p50_ms": 10.0, "rtt_p99_ms": 11.0},
    ]}}
    assert reduce_network(result, "network.rtt_jitter_ratio", "lte") == 1.1


def test_reduce_network_rtt_jitter_ratio_none_when_only_zero_p50_target():
    result = {"network": {"reachable": True, "targets": [
        {"id": "a", "rtt_p50_ms": 0.0, "rtt_p99_ms": 5.0},
    ]}}
    assert reduce_network(result, "network.rtt_jitter_ratio", "lte") is None


# --- compute() at the category level ----------------------------------------
#
# `compute` was imported by this file and never called (IMPORTANT 4): the
# engine's actual entry point had zero coverage, and every fix above was only
# proven at the unit level. `profiles: {}` stays empty until Task 4, so the
# `entry["reason"]` string branch in compute() is genuinely unreachable on
# real data right now -- there is no profile to grade "?" and hand a reason
# to. That branch is intentionally NOT tested here; it belongs to Task 4,
# once profiles exist to exercise it. What real v1 data DOES exercise through
# compute() today is the category rollup: worst-wins across a full metric
# list, the "?" precedence when a required metric is unmeasured, and F
# beating ? per spec 4.2. These fixtures are trimmed real fragments (only the
# fields the graded metrics touch) taken verbatim from the named result file.

# results/hetzner/hel-1/2026-07-15T2119-cpx32.json, trimmed to the fields the
# disk/cpu/ram/network categories read. tool_version 0.1.0 predates
# cpu.stall_p999_us, cpu.steady_state, cpu.tls_verify_s and ram.bw_read_mbs,
# so those come back "?" -- a real, still-unpatched v1 gap, not a fixture bug.
HEL1_2119 = {
    "run": {"tool_version": "0.1.0"},
    "disk": {
        "wal_fsync": {"iops": 1191.23, "p999_us": 2867.2},
        "rand_read_8k": {"iops": 80835.69, "p99_us": 2408.45},
        "rand_write_8k": {"iops": 66242.01},
        "seq_write": {"bw_mbs": 5336.15},
        "seq_read": {"bw_mbs": 6938.62},
        "steady_state": {"degradation_pct": 2.02},
    },
    "cpu": {
        "single_thread_eps": 1650.6,
        "scaling_efficiency": 0.983,
        "steal_pct_under_load": 0.0,
    },
    "ram": {"rnd_read_mbs": 15671.5},  # not bw_read_mbs -- the real v1 gap
    "network": {
        "reachable": True,
        "targets": [
            {"id": "hetzner-fsn1", "reachable": True, "dns_ms": 1.283,
             "rtt_p50_ms": 25.2, "rtt_p99_ms": 26.1, "loss_pct": 0},
            {"id": "hetzner-hel1", "reachable": True, "dns_ms": None,
             "rtt_p50_ms": 0.449, "rtt_p99_ms": 0.708, "loss_pct": 0},
            # HTTP-unreachable target that still carries a real dns_ms/rtt --
            # the exact CRITICAL 1 shape. Must still count.
            {"id": "ovh-gra", "reachable": False, "mbps": 0, "dns_ms": 58.701,
             "rtt_p50_ms": 28.9, "rtt_p99_ms": 29.1, "loss_pct": 0},
            {"id": "hetzner-ash", "reachable": True, "dns_ms": 25.018,
             "rtt_p50_ms": 103, "rtt_p99_ms": 105, "loss_pct": 0},
        ],
    },
}


def test_compute_category_worst_wins_on_real_v1_data():
    grades = compute(HEL1_2119, THRESHOLDS)
    disk = grades["categories"]["disk"]
    assert disk["grade"] == "C"
    assert disk["bound_by"] == "disk.rand_read_8k.p99_us"

    # network.dns_ms is no longer graded (demoted to informational -- it is
    # measured as a single uncached lookup and cannot be honestly banded
    # yet, see its `why:` in thresholds.yaml), so it is absent from this
    # category entirely and the remaining metric, rtt_jitter_ratio, binds:
    # hetzner-hel1's ratio (0.708/0.449 = 1.577) crosses the C bound (1.5).
    network = grades["categories"]["network"]
    assert network["grade"] == "C"
    assert network["bound_by"] == "network.rtt_jitter_ratio"
    assert "network.dns_ms" not in network["metrics"]
    assert network["metrics"]["network.rtt_jitter_ratio"]["value"] == 1.576837416481069


def test_compute_category_is_question_mark_when_a_required_metric_is_unmeasured():
    # cpu.stall_p999_us, cpu.steady_state.degradation_pct and cpu.tls_verify_s
    # do not exist in this real tool_version 0.1.0 result. None of the
    # measured cpu metrics is F, so the category rollup's "missing required"
    # precedence (spec 4.2, step 2) is what fires, through compute() end to
    # end -- not just the rollup() unit in isolation.
    grades = compute(HEL1_2119, THRESHOLDS)
    cpu = grades["categories"]["cpu"]
    assert cpu["grade"] == "?"
    assert cpu["metrics"]["cpu.stall_p999_us"]["grade"] == "?"

    ram = grades["categories"]["ram"]
    assert ram["grade"] == "?"
    assert ram["bound_by"] == "ram.bw_read_mbs"


# results/ovh/waw/2026-07-16T1017-vps-1-lz-2026.json cpu fragment, trimmed.
# single_thread_eps (356.25) is F (spec 6.6); stall_p999_us etc. are still
# unmeasured "?" on this 0.1.0 result, same gap as HEL1_2119 above.
WAW_1017_CPU = {
    "run": {"tool_version": "0.1.0"},
    "cpu": {
        "single_thread_eps": 356.25,
        "scaling_efficiency": 0.957,
        "steal_pct_under_load": 0.1,
    },
}


def test_compute_category_f_beats_question_mark_on_real_v1_data():
    # Spec 4.2: F must win even though this same category also has a required
    # "?" (cpu.stall_p999_us, unmeasured on 0.1.0). Proven here through the
    # full compute() path on a real published F, not just the rollup() unit.
    grades = compute(WAW_1017_CPU, THRESHOLDS)
    cpu = grades["categories"]["cpu"]
    assert cpu["metrics"]["cpu.stall_p999_us"]["grade"] == "?"
    assert cpu["grade"] == "F"
    assert cpu["bound_by"] == "cpu.single_thread_eps"


# --- MINOR 5 --------------------------------------------------------------

def test_grade_rules_defaults_missing_required_key_to_false():
    # schema/bands.schema.json mandates "required" on every rule, but nothing
    # at runtime stops Task 4 from writing a rule that omits it. rollup()
    # already treats a missing entry as falsy via required.get(m);
    # _grade_rules must be equally defensive with rule.get("required") rather
    # than rule["required"], or a malformed rule KeyErrors the whole grading
    # run instead of just being (correctly) treated as optional.
    result = {"cpu": {"single_thread_eps": 1650.6}}
    specs = [{"metric": "cpu.single_thread_eps"}]  # no "required" key
    graded, required, detail = _grade_rules(result, THRESHOLDS, specs)
    assert graded["cpu.single_thread_eps"] == "A"
    assert required["cpu.single_thread_eps"] is False


def test_version_gte_compares_numerically_not_lexicographically():
    # "0.10.0" >= "0.2.0" is False as a STRING compare (lexicographic: "1" <
    # "2"), which would wrongly tell a NEWER tool (0.10.0) it needs a re-run
    # for metrics it already carries. Each dotted part must compare as an
    # int.
    assert _version_gte("0.10.0", "0.2.0") is True
    assert _version_gte("0.2.0", "0.2.0") is True
    assert _version_gte("0.1.0", "0.2.0") is False


def test_version_gte_guards_malformed_or_missing_version():
    # A missing/garbled tool_version must not crash grading, and must not be
    # assumed new enough -- treat it as NOT meeting the minimum so the result
    # is flagged for re-run rather than silently trusted.
    assert _version_gte(None, "0.2.0") is False
    assert _version_gte("", "0.2.0") is False
    assert _version_gte("not-a-version", "0.2.0") is False
