"""The reporting doctrine, tested directly rather than through rendered markdown.

These rules are not formatting preferences. There are two distinct sources of
variance and they support different conclusions:

  time variance (same host_id, different hours)  -> noisy neighbours
  host variance (different host_id, same product) -> the fleet is not uniform

A mean over both says "roughly 3ms" and answers neither. Worst case is the
honest summary, because the tail is what users experience.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from aggregate import (  # noqa: E402
    MIN_RUNS_FOR_SPREAD, by_host, by_product, load_all, spread, worst_grade,
)
from conftest import CORPUS_DIR  # noqa: E402


def _run(host, hour, fsync, grade="A"):
    return {
        "run": {"host_id": host, "local_hour": hour, "tool_version": "0.2.0"},
        "provider": {"name": "p", "region": "r", "product": "x"},
        "disk": {"wal_fsync": {"p999_us": fsync}},
        "grades": {"profiles": {"postgres_oltp": {"grade": grade}},
                   "categories": {"disk": {"grade": grade}}},
    }


def test_spread_reports_median_and_worst_never_a_mean():
    # 1ms, 2ms, 60ms. A mean would say 21ms -- a machine that does not exist.
    # The median says 2ms (typical) and the worst says 60ms (what users feel).
    runs = [_run("h", 3, 1000), _run("h", 12, 2000), _run("h", 18, 60000)]
    med, worst = spread(runs, "disk.wal_fsync.p999_us")
    assert "2" in med and "60" in worst
    assert "21" not in med and "21" not in worst, "a mean leaked into the report"


def test_no_spread_below_three_runs():
    # Two points is a line segment, not a distribution.
    runs = [_run("h", 3, 1000), _run("h", 18, 60000)]
    med, worst = spread(runs, "disk.wal_fsync.p999_us")
    assert med == "-", "computed a median from fewer than 3 runs"
    assert "60" in worst, "worst must still be reported from any number of runs"


def test_min_runs_for_spread_is_three():
    assert MIN_RUNS_FOR_SPREAD == 3


def test_worst_grade_across_runs_wins():
    # A machine that passes at 03:00 and fails at 18:00 is a machine that fails.
    runs = [_run("h", 3, 1000, "A"), _run("h", 18, 60000, "F")]
    assert worst_grade(runs, "postgres_oltp") == "F"


def test_worst_grade_treats_unknown_as_unknown_not_as_good():
    runs = [_run("h", 3, 1000, "A"), _run("h", 18, 1000, "?")]
    assert worst_grade(runs, "postgres_oltp") == "?"


def test_by_host_separates_time_variance_from_host_variance():
    runs = [_run("h1", 3, 1000), _run("h1", 18, 60000), _run("h2", 10, 2000)]
    hosts = by_host(runs)
    assert len(hosts) == 2
    assert len(hosts["h1"]) == 2, "same machine, different hours = time variance"


def test_by_product_groups_provider_region_product():
    runs = [_run("h1", 3, 1000), _run("h2", 10, 2000)]
    key = ("p", "r", "x")
    assert list(by_product(runs)) == [key]
    assert len(by_product(runs)[key]) == 2


def test_load_all_reads_the_real_corpus():
    # The real corpus lives in tests/fixtures/corpus/ (calibration evidence,
    # not synthetic data) -- results/ itself is clean until the next
    # submission. See conftest.py's CORPUS_DIR docstring.
    runs = load_all(CORPUS_DIR)
    assert len(runs) >= 10
    assert all("grades" in r for r in runs), "a v1 result leaked into the corpus"
