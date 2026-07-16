"""--check is the CI contract: generated files are never hand-edited."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _render(*args):
    return subprocess.run(
        [sys.executable, "tools/render.py", *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )


def test_check_passes_on_a_clean_tree():
    assert _render("--check").returncode == 0, _render("--check").stdout


def test_check_fails_and_names_the_file_when_an_artifact_is_stale(tmp_path):
    target = ROOT / "RESULTS.md"
    original = target.read_text()
    try:
        target.write_text(original + "\nhand-edited\n")
        proc = _render("--check")
        assert proc.returncode != 0, "--check passed on a hand-edited artifact"
        assert "RESULTS.md" in proc.stdout + proc.stderr, "did not name the stale file"
    finally:
        target.write_text(original)
