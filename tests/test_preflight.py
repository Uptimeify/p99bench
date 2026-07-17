"""Tests for preflight_tools() in bench/lib.sh.

Added after three separate ~60-minute runs were lost to a missing package:
run-all.sh preflighted only load average, and the stages that just `warn`
and skip a metric (instead of dying loud) skip metrics that grading requires
in MORE profiles than the tools that die loud. See lib.sh for the full story.

Bash-level tests only, no root and no real apt: `preflight_tools` never shells
out to apt-get on the tested paths (only the interactive-accept path does
that, and these tests never reach it -- stdin is always /dev/null here).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "bench" / "lib.sh"
# Resolved once via the *real* PATH, then invoked by absolute path below --
# the tests below set PATH to a restricted/stubbed directory for the
# `command -v` checks inside preflight_tools, which would otherwise also hide
# bash itself from a bare "bash" lookup.
BASH = shutil.which("bash") or "/bin/bash"

REQUIRED_TOOLS = [
    "fio", "sysbench", "jq", "bc", "cyclictest", "mpstat",
    "openssl", "numactl", "dmidecode", "curl", "ping", "lscpu",
]


def _run(path: str) -> subprocess.CompletedProcess:
    """Source lib.sh and call preflight_tools with the given PATH and stdin
    explicitly from /dev/null -- i.e. never a TTY, exactly how CI, nohup, and
    a scripted run see it. That is also what makes this testable at all
    without root or real apt: the interactive install path is unreachable."""
    return subprocess.run(
        [BASH, "-c", "source ./lib.sh && preflight_tools"],
        cwd=str(ROOT / "bench"),
        env={"PATH": path},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def _make_stubs(tmp_path: Path, tools: list[str]) -> str:
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    for tool in tools:
        stub = stub_dir / tool
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    return str(stub_dir)


def test_every_tool_has_a_package_and_a_cost_string():
    """Every row is tool|package|cost -- three non-empty fields. An empty
    cost field would make the whole point of the table (what missing this
    tool does to grading) silently disappear."""
    src = LIB.read_text()
    m = re.search(r"local -a rows=\(\n(.*?)\n  \)\n", src, re.S)
    assert m, "could not find the rows= table in lib.sh -- did preflight_tools move or get renamed?"
    lines = [ln.strip().strip('"') for ln in m.group(1).splitlines() if ln.strip()]
    assert len(lines) == 13, f"expected 13 tools (12 blocking + stress-ng), found {len(lines)}"

    for line in lines:
        parts = line.split("|")
        assert len(parts) == 3, f"row is not tool|package|cost: {line!r}"
        tool, pkg, cost = parts
        assert tool and pkg and cost, f"empty field in row: {line!r}"

    tools = [ln.split("|")[0] for ln in lines]
    assert tools.count("stress-ng") == 1
    for t in REQUIRED_TOOLS:
        assert t in tools, f"{t} is missing from the preflight table"

    # The metrics that fail *soft* today must name the profile count in their
    # cost string -- that number is what makes "should I bother" concrete,
    # per the task that added this table.
    stall = next(ln for ln in lines if ln.startswith("cyclictest|"))
    assert "5 profiles" in stall
    steal = next(ln for ln in lines if ln.startswith("mpstat|"))
    assert "4 profiles" in steal
    tls = next(ln for ln in lines if ln.startswith("openssl|"))
    assert "worker_probe" in tls
    stress = next(ln for ln in lines if ln.startswith("stress-ng|"))
    assert "OPTIONAL" in stress


def test_non_tty_missing_tools_exits_nonzero_and_prints_apt_line():
    """A stripped PATH (no fio/sysbench/etc.) plus non-TTY stdin must NOT
    fall through to the interactive `read` prompt -- that would hang a
    scripted 60-minute run forever. It must exit non-zero and print the
    apt-get line so a human or wrapper script can act without babysitting it.
    """
    proc = _run("/usr/bin:/bin:/usr/local/bin")
    assert proc.returncode != 0, "missing required tools must fail preflight, not just warn"
    assert "apt-get install -y" in proc.stderr
    assert "fio" in proc.stderr  # at least one real blocking package named
    assert "install missing packages now" not in proc.stderr, (
        "must not reach the interactive prompt when stdin is not a TTY"
    )


def test_stress_ng_alone_missing_does_not_block(tmp_path):
    """stress-ng is optional (02-cpu.sh falls back to sysbench for CPU load
    generation), so its absence must be reported but must not fail preflight
    or block a run."""
    stub_path = _make_stubs(tmp_path, REQUIRED_TOOLS)
    proc = _run(stub_path)
    assert proc.returncode == 0, f"stress-ng alone missing must not block: {proc.stderr}"
    assert "stress-ng" in proc.stderr
    assert "optional" in proc.stderr


def test_nothing_missing_is_silent_and_succeeds(tmp_path):
    """When every tool, including stress-ng, is present, preflight_tools
    must not print anything and must succeed -- the common case (a host that
    already has the packages) must not nag on every run."""
    stub_path = _make_stubs(tmp_path, REQUIRED_TOOLS + ["stress-ng"])
    proc = _run(stub_path)
    assert proc.returncode == 0
    assert proc.stderr == ""
