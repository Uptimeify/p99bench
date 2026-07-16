import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from backfill_scaling import backfill  # noqa: E402


def test_backfill_computes_from_existing_inputs():
    # Real values from results/hetzner/hel-1/2026-07-16T1012-cpx32.json.
    doc = {
        "cpu": {"single_thread_eps": 1661.19, "multi_thread_eps": 6496.39,
                "scaling_efficiency": None},
        "host": {"vcpu": 4},
    }
    assert backfill(doc) is True
    assert doc["cpu"]["scaling_efficiency"] == 0.977


def test_backfill_truncates_like_bc_does_not_round():
    # 6496.39 / (1661.19 * 4) = 0.977671...
    # bc `scale=3` truncates  -> .977   (what 02-cpu.sh emits)
    # python round(x, 3)      -> 0.978  (wrong: same machine, two answers)
    # A backfilled value must equal what a re-run would produce.
    doc = {
        "cpu": {"single_thread_eps": 1661.19, "multi_thread_eps": 6496.39,
                "scaling_efficiency": None},
        "host": {"vcpu": 4},
    }
    backfill(doc)
    assert doc["cpu"]["scaling_efficiency"] != 0.978


def test_backfill_leaves_existing_value_alone():
    doc = {
        "cpu": {"single_thread_eps": 1661.19, "multi_thread_eps": 6496.39,
                "scaling_efficiency": 0.5},
        "host": {"vcpu": 4},
    }
    assert backfill(doc) is False
    assert doc["cpu"]["scaling_efficiency"] == 0.5


def test_backfill_declines_when_inputs_missing():
    doc = {"cpu": {"single_thread_eps": None, "multi_thread_eps": 6496.39,
                   "scaling_efficiency": None},
           "host": {"vcpu": 4}}
    assert backfill(doc) is False
    assert doc["cpu"]["scaling_efficiency"] is None


def test_backfill_declines_on_zero_vcpu():
    # Guard against ZeroDivisionError on a malformed inventory fragment.
    doc = {"cpu": {"single_thread_eps": 1661.19, "multi_thread_eps": 6496.39,
                   "scaling_efficiency": None},
           "host": {"vcpu": 0}}
    assert backfill(doc) is False
