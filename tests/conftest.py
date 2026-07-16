"""Shared fixtures. Two test tiers:

  * bash-helper tests   -- shell out to lib.sh, run anywhere including macOS
  * container tests     -- need fio/sysbench/cyclictest, so need Linux

Container tests are marked @pytest.mark.docker and skip cleanly when Docker
is absent, so a contributor without Docker still gets a useful test run.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


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
