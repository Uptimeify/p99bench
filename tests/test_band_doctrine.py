"""Spec 4.4, made executable.

This project shipped three thresholds no VM could ever pass -- fsync IOPS
>= 15000 against a best-measured 1588, rand-read p99 <= 1ms against 2408us,
intrinsic latency <= 200us against 1642us. Every one of 10 runs failed all
three, so three of four profiles could never return anything but `fail` and the
verdict column carried no information. Nobody noticed until someone analysed the
corpus.

A doctrine that lives only in prose gets violated again. So:

  broken threshold -- unreachable by construction, on every machine, in any
                      plausible corpus. Dead forever. Must never ship.
  quiet metric     -- reachable in both directions; this corpus merely happens
                      to be clean. Keep it: it is insurance that fires on a bad
                      host, and its silence is itself a finding.

The two look identical from inside a single corpus -- one grade, every time. The
difference is intent, so the author must DECLARE it. This test forces that
declaration.
"""
import collections
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from grade import grade_metric, metric_value  # noqa: E402
from conftest import CORPUS_DIR  # noqa: E402

THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())
# The real corpus lives in tests/fixtures/corpus/, not results/ -- see
# conftest.py's CORPUS_DIR docstring for why.
CORPUS = [json.loads(p.read_text()) for p in sorted(CORPUS_DIR.rglob("*.json"))]


def _grades_for(path, mdef):
    return collections.Counter(
        grade_metric(metric_value(doc, path, mdef), mdef) for doc in CORPUS
    )


def test_corpus_is_not_empty():
    # Every assertion below is vacuous without data. Guard explicitly rather
    # than letting the suite go green on an empty results/ directory.
    assert len(CORPUS) >= 10


def test_no_band_set_is_unreachable_by_every_host():
    """No metric may grade F (or A) across the entire corpus by construction."""
    offenders = []
    for path, mdef in THRESHOLDS["metrics"].items():
        if mdef.get("provisional"):
            continue  # no corpus behind it yet -- Task 5 of spec 10 recalibrates
        counts = _grades_for(path, mdef)
        measured = {g: n for g, n in counts.items() if g != "?"}
        if not measured:
            continue
        if set(measured) == {"F"}:
            offenders.append(
                f"{path}: every measured host grades F -- either the band is "
                f"unreachable (the fsync-IOPS-15000 mistake) or the field is broken"
            )
    assert not offenders, "\n".join(offenders)


def test_single_grade_metrics_are_declared_quiet_or_provisional():
    """A metric yielding one grade must say which kind it is."""
    offenders = []
    for path, mdef in THRESHOLDS["metrics"].items():
        if mdef.get("provisional") or mdef.get("quiet"):
            continue
        counts = _grades_for(path, mdef)
        measured = {g: n for g, n in counts.items() if g != "?"}
        if len(measured) == 1:
            grade = next(iter(measured))
            offenders.append(
                f"{path}: produces only '{grade}' across all {len(CORPUS)} runs. "
                f"Declare `quiet: true` (reachable both ways, corpus happens "
                f"clean) or `provisional: true` (no corpus yet) -- or fix the "
                f"bands. A metric that cannot tell two machines apart is not "
                f"earning its place (spec 4.4)."
            )
    assert not offenders, "\n".join(offenders)


def test_metrics_declared_quiet_really_are_quiet():
    """A `quiet: true` that starts discriminating must lose the label.

    Otherwise `quiet` rots into a blanket exemption from the doctrine, which is
    how the original mistake survived.
    """
    offenders = []
    for path, mdef in THRESHOLDS["metrics"].items():
        if not mdef.get("quiet") or mdef.get("provisional"):
            continue
        measured = {g for g in _grades_for(path, mdef) if g != "?"}
        if len(measured) > 1:
            offenders.append(
                f"{path}: declared quiet but produces {sorted(measured)}. "
                f"It discriminates now -- drop the label."
            )
    assert not offenders, "\n".join(offenders)


def test_the_discriminating_metrics_still_discriminate():
    """Spec 6.6 pins that 7 metrics produce 3-4 distinct grades on this corpus.

    This is the redesign's headline claim. If a band edit collapses one of them
    to a single grade, that is a regression in the thing the project exists to
    do, and it must not pass silently.
    """
    # Spec 6.6 named seven. disk.rand_read_8k.p99_us was one of them and is
    # now ungraded: at QD128 it was Little's law restating disk.rand_read_8k.iops
    # (median ratio 1.08 across 13 runs), so its "discriminating power" was the
    # IOPS metric's, counted a second time. Six remain. Its replacement,
    # disk.rand_read_8k_qd1.p99_us, is provisional and joins this list once a
    # corpus exists (spec 11).
    expect_at_least_two = [
        "disk.wal_fsync.p999_us", "disk.wal_fsync.iops",
        "disk.rand_read_8k.iops",
        "disk.rand_write_8k.iops", "disk.seq_write.bw_mbs",
        "cpu.single_thread_eps",
    ]
    for path in expect_at_least_two:
        measured = {g for g in _grades_for(path, THRESHOLDS["metrics"][path]) if g != "?"}
        assert len(measured) >= 2, (
            f"{path} collapsed to {sorted(measured)} -- it used to tell machines "
            f"apart (spec 6.6). A band edit broke the discriminating power."
        )


def test_qd128_random_read_p99_is_not_graded():
    """disk.rand_read_8k.p99_us must not band a queuing delay as a tail.

    It was measured at --iodepth=32 --numjobs=4 (128 outstanding) but banded
    on a QD1 rationale ("a query doing 100 lookups at 5ms p99"), at
    confidence: High. Little's law makes those the same number: across the
    13 runs measured to date, p99_us / (128/IOPS) had a median of 1.08 and
    was within 7% of 1.0 on every OVH host. Grading it alongside
    rand_read_8k.iops counted one measurement twice, and worst-wins always
    took the harsher of the two views -- band A (<=500us) demanded 256,000
    IOPS at QD128 while the iops band called 100,000 an A.

    disk.rand_read_8k_qd1.p99_us replaces it. The field is still emitted and
    still shown; it is informational, like network mbps.
    """
    assert "disk.rand_read_8k.p99_us" not in THRESHOLDS["metrics"], (
        "QD128 rand-read p99 is a queuing delay, not a tail latency -- it must "
        "not carry a band. Grade disk.rand_read_8k_qd1.p99_us instead."
    )
    for cat, metrics in THRESHOLDS["categories"].items():
        assert "disk.rand_read_8k.p99_us" not in metrics, f"still graded in category {cat}"
    for prof, pdef in THRESHOLDS["profiles"].items():
        paths = [r["metric"] for r in pdef["rules"]]
        assert "disk.rand_read_8k.p99_us" not in paths, f"still graded in profile {prof}"


def test_qd1_random_read_p99_is_graded_and_declared_provisional():
    mdef = THRESHOLDS["metrics"].get("disk.rand_read_8k_qd1.p99_us")
    assert mdef, "the QD1 random-read tail must be a graded metric"
    assert mdef["op"] == "lte"
    assert mdef["unit"] == "us"
    # No host has ever measured this, so it cannot claim a corpus. The bands
    # carry over the workload rationale the QD128 metric was written with and
    # could not honour; spec 11 recalibrates them once runs land.
    assert mdef.get("provisional") is True
    assert "disk.rand_read_8k_qd1.p99_us" in THRESHOLDS["categories"]["disk"]
    pg = [r for r in THRESHOLDS["profiles"]["postgres_oltp"]["rules"]
          if r["metric"] == "disk.rand_read_8k_qd1.p99_us"]
    assert pg and pg[0]["required"] is True, \
        "index-lookup latency is not optional for an OLTP database"
