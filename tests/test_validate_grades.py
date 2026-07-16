import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import validate as V  # noqa: E402

SAMPLE = ROOT / "results" / "hetzner" / "hel-1" / "2026-07-16T1012-cpx32.json"


def test_hand_edited_grade_is_rejected(tmp_path):
    # The trust property. A project publishing provider comparisons has an
    # obvious temptation to nudge them; the only real defence is making a nudge
    # fail the build.
    doc = json.loads(SAMPLE.read_text())
    doc["grades"]["profiles"]["postgres_oltp"]["grade"] = "A"
    p = tmp_path / "x.json"
    p.write_text(json.dumps(doc))
    errs = V.check_policy(SAMPLE, doc)
    assert any("do not hand-edit" in e.lower() or "computes" in e for e in errs)


def test_stale_bands_version_is_rejected(tmp_path):
    doc = json.loads(SAMPLE.read_text())
    doc["grades"]["bands_version"] = "1.9"
    errs = V.check_policy(SAMPLE, doc)
    assert any("bands_version" in e for e in errs)


def test_unmodified_published_results_pass():
    doc = json.loads(SAMPLE.read_text())
    assert V.check_policy(SAMPLE, doc) == []
