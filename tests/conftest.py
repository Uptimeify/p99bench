"""Shared fixtures. Two test tiers:

  * bash-helper tests   -- shell out to lib.sh, run anywhere including macOS
  * container tests     -- need fio/sysbench/cyclictest, so need Linux

Container tests are marked @pytest.mark.docker and skip cleanly when Docker
is absent, so a contributor without Docker still gets a useful test run.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

# The 10 published measurements this project has ever taken, moved here from
# results/ (2026-07-16) so results/ can start clean for the tool 0.2.0
# re-run. These are NOT synthetic fixtures -- they are real hardware
# measurements, kept as calibration evidence and as the proof behind spec
# 2.4 (storage-class regimes), 2.5 (the ovh waw-vs-zrh headline: same
# product, same price, opposite failures), and 4.4 (the broken-vs-quiet band
# doctrine), plus the trust property (validate.py rejects a hand-edited
# grade). If a test disagrees with this data, the code or the band is wrong,
# not the data -- never hand-edit a file here to make a test pass.
#
# One home for the corpus path so it is never glob-duplicated across test
# files: import CORPUS_DIR (for path-building / rglob) or load_corpus() (for
# the parsed dicts, via aggregate.load_all) from here.
CORPUS_DIR = ROOT / "tests" / "fixtures" / "corpus"


def load_corpus() -> list[dict]:
    """Every real result, parsed. Thin wrapper over aggregate.load_all so
    there is exactly one function that knows where the corpus lives."""
    import aggregate

    return aggregate.load_all(CORPUS_DIR)


@pytest.fixture
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def run_bash(tmp_path):
    """Run a bash snippet with lib.sh sourced. P99_WORK is redirected to a
    tmp dir so tests never touch /tmp/p99bench or each other."""

    def _run(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
        full_env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "P99_WORK": str(tmp_path / "work"),
            "P99_TARGET": str(tmp_path / "target"),
        }
        if env:
            full_env.update(env)
        return subprocess.run(
            ["bash", "-c", f"cd {ROOT}/bench && source ./lib.sh && {script}"],
            capture_output=True,
            text=True,
            env=full_env,
        )

    return _run


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: needs Docker (Linux bench tools)")


def pytest_collection_modifyitems(config, items):
    if shutil.which("docker"):
        return
    skip = pytest.mark.skip(reason="docker not available")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)
