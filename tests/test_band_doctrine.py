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
from grade import grade_metric, metric_value  # noqa: E402

THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())
CORPUS = [json.loads(p.read_text()) for p in sorted((ROOT / "results").rglob("*.json"))]


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
    expect_at_least_two = [
        "disk.wal_fsync.p999_us", "disk.wal_fsync.iops",
        "disk.rand_read_8k.p99_us", "disk.rand_read_8k.iops",
        "disk.rand_write_8k.iops", "disk.seq_write.bw_mbs",
        "cpu.single_thread_eps",
    ]
    for path in expect_at_least_two:
        measured = {g for g in _grades_for(path, THRESHOLDS["metrics"][path]) if g != "?"}
        assert len(measured) >= 2, (
            f"{path} collapsed to {sorted(measured)} -- it used to tell machines "
            f"apart (spec 6.6). A band edit broke the discriminating power."
        )
