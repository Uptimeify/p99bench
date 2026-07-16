import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import validate as V  # noqa: E402
from conftest import CORPUS_DIR  # noqa: E402

SAMPLE = CORPUS_DIR / "hetzner" / "hel-1" / "2026-07-16T1012-cpx32.json"


def test_hand_edited_grade_is_rejected(tmp_path):
    # The trust property. A project publishing provider comparisons has an
    # obvious temptation to nudge them; the only real defence is making a nudge
    # fail the build.
    doc = json.loads(SAMPLE.read_text())
    doc["grades"]["profiles"]["postgres_oltp"]["grade"] = "A"
    p = tmp_path / "x.json"
    p.write_text(json.dumps(doc))
    errs = V.check_policy(SAMPLE, doc, results_dir=CORPUS_DIR)
    assert any("do not hand-edit" in e.lower() or "computes" in e for e in errs)


def test_stale_bands_version_is_rejected(tmp_path):
    doc = json.loads(SAMPLE.read_text())
    doc["grades"]["bands_version"] = "1.9"
    errs = V.check_policy(SAMPLE, doc, results_dir=CORPUS_DIR)
    assert any("bands_version" in e for e in errs)


def test_unmodified_published_results_pass():
    doc = json.loads(SAMPLE.read_text())
    assert V.check_policy(SAMPLE, doc, results_dir=CORPUS_DIR) == []


def test_missing_grades_block_is_rejected():
    # Deleting the grades block used to sail through check_policy silently
    # (`if data.get("grades")` is falsy on absence), and render.py's
    # worst_grade ranks "?" better than "F" -- so deleting the block turned a
    # published F into a better-looking "?". A missing block must be an error.
    doc = json.loads(SAMPLE.read_text())
    del doc["grades"]
    errs = V.check_policy(SAMPLE, doc, results_dir=CORPUS_DIR)
    assert any("grades" in e.lower() for e in errs), errs


def test_null_grades_block_is_rejected():
    # Same hole, different shape: grades present but null. The schema's type
    # allows null, so this must be caught at the policy layer explicitly.
    doc = json.loads(SAMPLE.read_text())
    doc["grades"] = None
    errs = V.check_policy(SAMPLE, doc, results_dir=CORPUS_DIR)
    assert any("grades" in e.lower() for e in errs), errs


def test_tampered_bound_by_is_rejected():
    # The `if stored != expected:` block only appended errors for profile
    # grades, category grades, and storage_class -- a mismatch confined to
    # bound_by (rendered into RESULTS.md's "Why runs failed") fell through
    # both loops and check_policy returned []. A catch-all must fire.
    doc = json.loads(SAMPLE.read_text())
    doc["grades"]["profiles"]["postgres_oltp"]["bound_by"] = "not.a.real.metric"
    errs = V.check_policy(SAMPLE, doc, results_dir=CORPUS_DIR)
    assert errs, "tampered bound_by produced no error -- the hole is still open"


def test_tampered_metric_value_is_rejected():
    # categories.*.metrics.*.value is a published number, hand-editable past
    # validate.py the same way: neither loop compares the metrics detail.
    doc = json.loads(SAMPLE.read_text())
    cat = doc["grades"]["categories"]["disk"]
    metric = next(iter(cat["metrics"]))
    cat["metrics"][metric]["value"] = -1
    errs = V.check_policy(SAMPLE, doc, results_dir=CORPUS_DIR)
    assert errs, "tampered metrics[].value produced no error -- the hole is still open"
