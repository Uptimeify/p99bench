# p99bench Phase 1: Metric Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix or replace the four `bench/` metrics that measure the wrong thing, and add the three new metrics the graded-category redesign needs, so that Phase 2's grading engine has trustworthy inputs.

**Architecture:** `bench/*.sh` stages each measure one domain and write a JSON fragment via `emit_json` from `lib.sh`; `run-all.sh` deep-merges fragments into one result. This plan changes stage internals and adds one stage. It does not touch the merge, the grading engine, or the render pipeline — those are Plans 2 and 3.

**Tech Stack:** bash (POSIX-ish), fio, sysbench, cyclictest (`rt-tests`), openssl, jq, bc, mpstat (`sysstat`). Tests: pytest driving bash via subprocess; Docker (debian:13) for stages needing Linux benchmark tools.

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-07-16-p99bench-graded-categories-design.md` and `CONTRIBUTING.md`:

- Shell stays POSIX-ish bash, readable over clever. Comment the *why*, not the *what* — a reader needs to know why `psync` and not `libaio`, not what `psync` is.
- **Changed measurement demands a changed field name** (spec §9.2). Never silently redefine an existing field.
- Old fields are retained in existing files, unbanded, marked legacy. `schema_version` goes to `2.0` (Plan 2 does the schema; this plan only emits).
- `emit_json` dies on empty/invalid/unwritable payloads. `jnum` records `null` with a warning rather than passing garbage. Never widen a parser to "just accept it".
- Every fio invocation uses `--direct=1`.
- A new metric needs three things or it does not land: a field in `schema/result.schema.json`, a rule in `schema/thresholds.yaml` (or an explicit informational-only note), and a row in `THRESHOLDS.md` with reasoning and a confidence level. **This plan emits the fields; Plan 2 adds the rules and the THRESHOLDS.md rows.** Task 8 records the informational-only notes.
- Do not add a required dependency on a running Redis/Postgres.
- CI must stay green: `shellcheck -e SC1091 bench/*.sh`.
- New tool dependency introduced by this plan: `rt-tests` (for `cyclictest`). Must be added to README install line, CONTRIBUTING, and CI.
- **CI output is never a measurement.** Container/CI runs prove scripts emit well-formed fragments; they prove nothing about hardware.

## Notes for the implementer

The bug you are fixing in Task 2 is the reason this plan exists in this order. `bc` prints numbers between -1 and 1 without a leading zero (`.977`), and `jnum`'s regex requires a digit before the decimal point, so those values silently become `null`. Verified:

```
$ echo "scale=3; 6496.39 / (1661.19 * 4)" | bc
.977
```

That is why `cpu.scaling_efficiency` is `null` in all 10 committed results despite both inputs being present. The same bug silently nulls any `dns_ms` under 1 ms.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `tests/conftest.py` | pytest fixtures: repo root, bash runner, docker availability | 1 |
| `tests/test_lib.py` | unit tests for `lib.sh` helpers (`jnum`, `jstr`, `emit_json`) | 1, 2 |
| `tests/test_stages.py` | container smoke tests asserting stage fragment shape | created in 4; extended by 5, 6, 7 |
| `tests/test_backfill.py` | unit tests for the scaling backfill | 3 |
| `tests/Dockerfile` | debian:13 + bench tool deps, for stage tests | 1 |
| `bench/lib.sh` | shared helpers; `jnum` regex fix | 2 |
| `tools/backfill_scaling.py` | one-shot backfill of `scaling_efficiency` into existing results | 3 |
| `bench/05-latency.sh` | replace redis-cli with cyclictest; emit stall percentiles | 4 |
| `bench/03-ram.sh` | LLC-exceeding working set; emit `ram.bw_read_mbs` | 5 |
| `bench/02-cpu.sh` | emit `cpu.tls_handshakes_s` | 6 |
| `bench/02b-cpu-steady.sh` | **new** 15-min CPU sustained stage | 7 |
| `bench/run-all.sh` | wire in new stage + `--skip-cpu-steady` | 7 |
| `.github/workflows/validate.yml` | install `rt-tests`; smoke-assert new fields | 8 |
| `README.md`, `CONTRIBUTING.md`, `bench/lib.sh` version | deps + run time + version bump | 8 |

---

### Task 1: Test harness

There is no test suite in this repo today and no pytest installed. Everything downstream needs this, so it lands first. Two tiers: bash-helper unit tests that run anywhere (macOS included), and container tests for stages needing Linux benchmark tools.

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_lib.py`
- Create: `tests/Dockerfile`
- Create: `requirements-dev.txt`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: pytest fixtures `repo_root` (`pathlib.Path`), `run_bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess`, and marker `@pytest.mark.docker` (auto-skips when Docker is absent). Every later task's tests use these.

- [ ] **Step 1: Write the failing test**

Create `tests/conftest.py`:

```python
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
```

Create `tests/test_lib.py`:

```python
"""Unit tests for bench/lib.sh helpers."""


def test_jnum_accepts_plain_integer(run_bash):
    assert run_bash('jnum "123"').stdout == "123"


def test_jnum_rejects_word_as_null(run_bash):
    # Parsers awk their way through tool output whose format shifts between
    # versions. When a parse misses, the variable holds a word. Recording null
    # loses one metric; passing garbage to jq kills the whole fragment.
    assert run_bash('jnum "bytes"').stdout == "null"


def test_jnum_empty_is_null(run_bash):
    assert run_bash('jnum ""').stdout == "null"
```

Create `requirements-dev.txt`:

```
# Test-only deps. The benchmark scripts in bench/ need none of this --
# see requirements.txt for why that separation matters.
pytest>=8.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_lib.py -v
```

Expected: FAIL — `No module named pytest`.

- [ ] **Step 3: Install and make it pass**

```bash
python3 -m pip install -r requirements-dev.txt --break-system-packages
```

Create `tests/Dockerfile`:

```dockerfile
# Container for stage tests. Proves the scripts run and emit well-formed
# fragments. It is NOT a measurement: numbers from a container on a laptop
# describe nothing. Same reasoning as the CI smoke job.
FROM debian:13

RUN apt-get update && apt-get install -y --no-install-recommends \
      fio sysbench rt-tests redis-tools jq bc sysstat openssl \
      numactl stress-ng dmidecode procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /p99bench
```

Add to `.gitignore`:

```
.pytest_cache/
__pycache__/
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_lib.py -v
```

Expected: 3 passed.

Build the container once (later tasks reuse it):

```bash
docker build -t p99bench-test tests/
```

Expected: `Successfully tagged p99bench-test:latest`.

- [ ] **Step 5: Commit**

```bash
git add tests/ requirements-dev.txt .gitignore
git commit -m "test: bootstrap pytest harness and container rig

No test suite existed. Two tiers: bash-helper tests that run anywhere,
and container tests for stages needing Linux benchmark tools, which skip
cleanly when Docker is absent."
```

---

### Task 2: Fix the `jnum` leading-zero bug

`bc` prints values between -1 and 1 as `.977`. `jnum`'s regex demands a digit before the decimal point, so every such value silently becomes `null`. This nulls `cpu.scaling_efficiency` on every run (it is always 0–1) and would null any `dns_ms` under 1 ms — which under the incoming §6.5 bands is grade A, making the best DNS performance unmeasurable.

Fixing `jnum` fixes all six `bc` call sites at once, present and future. That is why this is a lib fix and not six call-site fixes.

**Files:**
- Modify: `bench/lib.sh:72-82`
- Test: `tests/test_lib.py`

**Interfaces:**
- Consumes: `run_bash` fixture from Task 1.
- Produces: `jnum` accepting leading-dot decimals. No signature change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_lib.py`:

```python
def test_jnum_accepts_leading_dot_decimal(run_bash):
    # bc prints values between -1 and 1 without a leading zero:
    #   $ echo "scale=3; 6496.39 / (1661.19 * 4)" | bc
    #   .977
    # The old regex required a digit before the point, so every scaling
    # efficiency (always 0-1) silently became null. Regression guard.
    assert run_bash('jnum ".977"').stdout == ".977"


def test_jnum_accepts_negative_leading_dot(run_bash):
    assert run_bash('jnum "-.5"').stdout == "-.5"


def test_jnum_accepts_leading_dot_exponent(run_bash):
    assert run_bash('jnum ".5e-3"').stdout == ".5e-3"


def test_jnum_still_rejects_bare_dot(run_bash):
    assert run_bash('jnum "."').stdout == "null"


def test_jnum_still_rejects_malformed_decimal(run_bash):
    assert run_bash('jnum "1.2.3"').stdout == "null"


def test_jnum_bc_scaling_roundtrip(run_bash):
    # End-to-end: the exact computation 02-cpu.sh performs, using real
    # values from results/hetzner/hel-1/2026-07-16T1012-cpx32.json.
    out = run_bash('jnum "$(echo "scale=3; 6496.39 / (1661.19 * 4)" | bc)"')
    assert out.stdout == ".977"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_lib.py -v -k "leading_dot or roundtrip"
```

Expected: 4 FAIL — `assert 'null' == '.977'`. The two `still_rejects` tests pass already.

- [ ] **Step 3: Write minimal implementation**

In `bench/lib.sh`, replace the `jnum` body (lines 72-82):

```bash
jnum() {
  local v="$1"
  if [[ -z "$v" || "$v" == "null" ]]; then
    printf 'null'
  elif [[ "$v" =~ ^-?([0-9]+|[0-9]*[.][0-9]+)([eE][-+]?[0-9]+)?$ ]]; then
    printf '%s' "$v"
  else
    warn "expected a number, got '${v:0:40}' - recording null"
    printf 'null'
  fi
}
```

Also extend the comment block above `jnum` (after the existing paragraph):

```bash
# The leading-dot branch is not cosmetic. bc prints values between -1 and 1
# without a leading zero (".977", not "0.977"), so a regex demanding a digit
# before the point silently nulls every ratio this suite computes -
# scaling_efficiency is always 0-1 and was null in every published result
# because of exactly this. JSON itself rejects ".977", but jq --argjson
# accepts it and normalises, so emitting it is safe.
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_lib.py -v
```

Expected: 9 passed.

Confirm no shellcheck regression:

```bash
docker run --rm -v "$PWD:/p99bench" -w /p99bench koalaman/shellcheck-alpine:stable \
  shellcheck -e SC1091 bench/lib.sh
```

Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add bench/lib.sh tests/test_lib.py
git commit -m "fix: jnum silently nulled every bc value between -1 and 1

bc prints .977, not 0.977. jnum's regex required a digit before the
decimal point, so every ratio the suite computes became null -- which is
why cpu.scaling_efficiency is null in all 10 published results despite
both inputs being present.

Same bug nulls any dns_ms under 1ms, i.e. the best resolvers, which the
incoming network bands grade A. Fixing jnum fixes all six bc call sites."
```

---

### Task 3: Backfill `scaling_efficiency` into existing results

Spec §5.3 / §9.3: backfillable without re-running, because both inputs are already in every file. This keeps 10 real measurements from needing a re-run they cannot get (some of those VMs no longer exist).

**Files:**
- Create: `tools/backfill_scaling.py`
- Test: `tests/test_backfill.py`
- Modify: `results/*/*/*.json` (data, via the tool)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `backfill(result: dict) -> bool` — mutates `result["cpu"]["scaling_efficiency"]` in place, returns `True` if it changed the doc. CLI: `python3 tools/backfill_scaling.py results/ [--dry-run]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backfill.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_backfill.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'backfill_scaling'`.

- [ ] **Step 3: Write minimal implementation**

Create `tools/backfill_scaling.py`:

```python
#!/usr/bin/env python3
"""Backfill cpu.scaling_efficiency into results measured before the jnum fix.

Every published result has scaling_efficiency: null, because bc prints ".977"
and lib.sh's jnum rejected leading-dot decimals (see the fix in bench/lib.sh).
The inputs were recorded correctly the whole time, so the value is recoverable
without re-running the benchmark -- which matters, because some of those VMs
no longer exist.

This is a one-shot migration, not part of the run path. New results compute
the field in 02-cpu.sh.

Usage:
    python3 tools/backfill_scaling.py results/ [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def backfill(result: dict) -> bool:
    """Fill cpu.scaling_efficiency from single/multi thread eps and vcpu.

    Returns True if the document was changed. Declines (returns False) when a
    value already exists or an input is missing -- never overwrites a real
    measurement, never invents one.
    """
    cpu = result.get("cpu")
    if not isinstance(cpu, dict) or cpu.get("scaling_efficiency") is not None:
        return False

    st = cpu.get("single_thread_eps")
    mt = cpu.get("multi_thread_eps")
    vcpu = result.get("host", {}).get("vcpu")

    if not all(isinstance(v, (int, float)) for v in (st, mt, vcpu)):
        return False
    if st <= 0 or vcpu <= 0:
        return False

    # Same expression as 02-cpu.sh: mt / (st * cores).
    #
    # TRUNCATE, do not round. bc's `scale=3` truncates, so 02-cpu.sh emits
    # .977 for inputs that round() would turn into 0.978. A backfilled value
    # must be bit-identical to what a re-run would produce, or the same machine
    # measured twice disagrees with itself for no physical reason.
    #
    # math.floor is safe here specifically because scaling efficiency cannot be
    # negative (it is a ratio of two positive throughputs); floor and bc's
    # toward-zero truncation only diverge below zero.
    cpu["scaling_efficiency"] = math.floor(mt / (st * vcpu) * 1000) / 1000
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    paths = sorted(args.target.rglob("*.json")) if args.target.is_dir() else [args.target]
    changed = 0
    for path in paths:
        doc = json.loads(path.read_text())
        if not backfill(doc):
            continue
        changed += 1
        val = doc["cpu"]["scaling_efficiency"]
        print(f"{path}: scaling_efficiency = {val}")
        if not args.dry_run:
            path.write_text(json.dumps(doc, indent=2) + "\n")

    verb = "would change" if args.dry_run else "changed"
    print(f"{verb} {changed} of {len(paths)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_backfill.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Dry-run against real data, then apply**

```bash
python3 tools/backfill_scaling.py results/ --dry-run
```

Expected: 10 lines, each `results/.../*.json: scaling_efficiency = 0.9xx`, then `would change 10 of 10 files`.

If any file reports a value outside 0.0–1.5, stop and investigate before applying — that indicates a bad `vcpu` in the inventory fragment, not a scaling result.

```bash
python3 tools/backfill_scaling.py results/
git diff --stat results/
```

Expected: 10 files changed, 10 insertions, 10 deletions.

Verify the values landed and are sane:

```bash
jq -r '[input_filename, (.cpu.scaling_efficiency|tostring)] | @tsv' results/*/*/*.json
```

Expected: no `null` values.

- [ ] **Step 6: Commit**

```bash
git add tools/backfill_scaling.py tests/test_backfill.py results/
git commit -m "fix: backfill scaling_efficiency into the 10 published results

The inputs were recorded correctly all along; only jnum's regex dropped
the computed value. Recoverable without re-running, which matters because
some of these VMs no longer exist.

scaling_efficiency separates physical cores from SMT siblings sold as
cores, and it was silently absent from every published result."
```

---

### Task 4: Replace `redis-cli --intrinsic-latency` with `cyclictest`

Spec §5.1. `redis-cli --intrinsic-latency` emits only a running max and an average. From a real result: `avg = 0.0565 us, max = 1642 us` — a ~30,000x skew. The average describes the loop, not the stalls; the max is worst-of-60-million-samples, an extreme-value statistic that only grows with sample size. Every VM is preempted at least once per minute, so **no threshold on this tool can discriminate** — it measures "is this a VM?".

`cyclictest` emits a latency histogram, which yields real percentiles.

**Files:**
- Modify: `bench/05-latency.sh` (full rewrite)
- Test: `tests/test_stages.py`

**Interfaces:**
- Consumes: `emit_json`, `jnum`, `need`, `warn`, `log` from `lib.sh`; `run_bash` and `@pytest.mark.docker` from Task 1.
- Produces: fragment `frag-latency.json` with shape
  `{"cpu": {"stall_p99_us": <num|null>, "stall_p999_us": <num|null>, "stall_max_us": <num|null>, "stall_samples": <num|null>, "intrinsic_latency_max_us": <num|null>, "intrinsic_latency_avg_us": <num|null>}}`.
  The two `intrinsic_latency_*` keys are retained as legacy informational (spec §9.2) and are emitted as `null` — the tool that produced them is gone. Plan 2 bands `stall_p999_us` only.

- [ ] **Step 1: Write the failing test**

Create `tests/test_stages.py`:

```python
"""Container smoke tests for bench stages.

These prove a stage runs and emits a well-formed fragment. They prove
NOTHING about hardware -- numbers from a container on a laptop describe
the laptop's hypervisor, not a provider. Same rule as the CI smoke job.
"""
import json
import subprocess

import pytest

IMAGE = "p99bench-test"


def run_stage(repo_root, script: str, env: dict, frag: str) -> dict:
    """Run one stage in the container and return its parsed fragment."""
    env_args = []
    for k, v in {"P99_WORK": "/tmp/p99work", **env}.items():
        env_args += ["-e", f"{k}={v}"]
    proc = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{repo_root}:/p99bench", *env_args, IMAGE,
         "bash", "-c", f"bash /p99bench/bench/{script} >/dev/null 2>&1; "
                       f"cat /tmp/p99work/frag-{frag}.json"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"stage failed: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


@pytest.mark.docker
def test_latency_stage_emits_stall_percentiles(repo_root):
    frag = run_stage(repo_root, "05-latency.sh",
                     {"P99_LATENCY_DURATION": "5"}, "latency")
    cpu = frag["cpu"]
    for key in ("stall_p99_us", "stall_p999_us", "stall_max_us", "stall_samples"):
        assert key in cpu, f"missing {key}"
        assert cpu[key] is not None, f"{key} is null -- parser missed"
        assert isinstance(cpu[key], (int, float))


@pytest.mark.docker
def test_latency_percentiles_are_ordered(repo_root):
    # p99 <= p99.9 <= max is a property of any correct percentile parser,
    # and holds regardless of what the host's latency actually is.
    cpu = run_stage(repo_root, "05-latency.sh",
                    {"P99_LATENCY_DURATION": "5"}, "latency")["cpu"]
    assert cpu["stall_p99_us"] <= cpu["stall_p999_us"] <= cpu["stall_max_us"]


@pytest.mark.docker
def test_latency_stage_retains_legacy_fields_as_null(repo_root):
    # Spec 9.2: legacy fields are retained so old and new results share a
    # schema, but the tool that produced them is gone, so they are null.
    cpu = run_stage(repo_root, "05-latency.sh",
                    {"P99_LATENCY_DURATION": "5"}, "latency")["cpu"]
    assert cpu["intrinsic_latency_max_us"] is None
    assert cpu["intrinsic_latency_avg_us"] is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_stages.py -v -k latency
```

Expected: 3 FAIL — `KeyError: 'stall_p99_us'`.

- [ ] **Step 3: Write the implementation**

Replace `bench/05-latency.sh` entirely:

```bash
#!/usr/bin/env bash
# 05-latency.sh - scheduler stall measurement. Needs no running service.
# Emits cpu.stall_* fragment.
#
# WHY NOT redis-cli --intrinsic-latency
# -------------------------------------
# It reports only a running max and an average, and those two numbers cannot
# support a threshold. A real result read avg=0.0565us, max=1642us: a ~30,000x
# skew. The average describes the loop, not the stalls. The max is
# worst-of-60-million-samples - an extreme-value statistic that grows with
# sample count, so every VM fails it eventually and the metric ends up
# measuring "is this a VM?" rather than "is this VM good?". Every one of the
# first 10 published results failed the old <=200us bar; best measured was
# 1642us. A metric no machine can pass is not a metric.
#
# cyclictest emits a latency histogram, which gives real percentiles. p99.9 is
# what a single-threaded process actually feels, and unlike max it converges
# rather than drifting upward with runtime.
#
# WHY SCHED_OTHER (-p 0) AND NOT RT PRIORITY
# ------------------------------------------
# cyclictest is usually run at -p 99 to characterise RT kernels. That would
# measure the hypervisor's best case. Redis and Node run at normal priority,
# so normal priority is what we measure - same scheduling class the old
# redis-cli loop ran in.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need jq

DURATION="${P99_LATENCY_DURATION:-60}"

# Histogram ceiling in us. Published VMs stall 1.6-6.5ms, so 30ms leaves
# headroom. Anything above lands in "Histogram Overflows" and is handled below.
HIST_MAX="${P99_STALL_HIST_MAX:-30000}"

# 250us between samples -> ~240k samples in 60s, so p99.9 rests on ~240
# observations rather than a handful. Default 1000us would leave 60.
INTERVAL="${P99_STALL_INTERVAL_US:-250}"

if ! command -v cyclictest >/dev/null 2>&1; then
  warn "cyclictest not found (apt install rt-tests). Skipping stall measurement."
  emit_json latency "$(jq -n '{cpu: {
      stall_p99_us: null, stall_p999_us: null, stall_max_us: null,
      stall_samples: null,
      intrinsic_latency_max_us: null, intrinsic_latency_avg_us: null
  }}')"
  exit 0
fi

log "Scheduler stalls: ${DURATION}s cyclictest histogram (no Redis server required)"

# -q quiet, -m mlockall (keep our pages resident so we measure the scheduler
# and not a page fault), -p 0 normal priority, -t 1 one thread (a
# single-threaded process is the thing under study), -h histogram ceiling.
OUT=$(cyclictest -q -m -p 0 -t 1 -i "$INTERVAL" -D "$DURATION" -h "$HIST_MAX" 2>&1 || true)

# Histogram lines are "<bucket_us> <count>"; trailing summary lines start '#'.
# Percentiles come from the cumulative distribution. Overflows are counted
# separately by cyclictest and are, by definition, above HIST_MAX - they must
# be added to the total or every percentile is computed against a short
# denominator and reads optimistically low.
read -r P99 P999 MAXV SAMPLES <<<"$(printf '%s' "$OUT" | awk -v hist_max="$HIST_MAX" '
  /^[0-9]+[ \t]+[0-9]+/ { b[n] = $1 + 0; c[n] = $2 + 0; total += $2; n++ }
  /^# Histogram Overflows:/ { for (i = 4; i <= NF; i++) overflow += $i + 0 }
  /^# Max Latencies:/       { for (i = 4; i <= NF; i++) if ($i + 0 > maxv) maxv = $i + 0 }
  END {
    grand = total + overflow
    if (grand == 0) { print "  "; exit }
    t99 = grand * 0.99; t999 = grand * 0.999
    cum = 0
    for (i = 0; i < n; i++) {
      cum += c[i]
      if (!got99  && cum >= t99)  { p99  = b[i]; got99  = 1 }
      if (!got999 && cum >= t999) { p999 = b[i]; got999 = 1 }
    }
    # A percentile not reached inside the histogram lives in the overflow
    # bucket. We know only that it exceeds hist_max, so report null rather
    # than pinning it to the ceiling and understating a bad host.
    if (!got99)  p99  = "null"
    if (!got999) p999 = "null"
    print p99, p999, (maxv ? maxv : "null"), grand
  }
')"

emit_json latency "$(jq -n \
  --argjson p99 "$(jnum "$P99")" \
  --argjson p999 "$(jnum "$P999")" \
  --argjson max "$(jnum "$MAXV")" \
  --argjson n "$(jnum "$SAMPLES")" \
  '{cpu: {
      stall_p99_us: $p99,
      stall_p999_us: $p999,
      stall_max_us: $max,
      stall_samples: $n,
      intrinsic_latency_max_us: null,
      intrinsic_latency_avg_us: null
  }}')"

echo
echo "=== Scheduler latency ==="
echo "p99:     ${P99:-n/a} us"
echo "p99.9:   ${P999:-n/a} us"
echo "max:     ${MAXV:-n/a} us   (context only - grows with runtime, not banded)"
echo "samples: ${SAMPLES:-n/a}"
echo
echo "A single-threaded process is frozen for the whole duration of a stall."
echo "p99.9 is what Redis and each Node event loop actually feel."
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker build -t p99bench-test tests/
python3 -m pytest tests/test_stages.py -v -k latency
```

Expected: 3 passed.

Inspect a real fragment to confirm the numbers are plausible, not just well-formed:

```bash
docker run --rm -v "$PWD:/p99bench" -e P99_WORK=/tmp/p99work -e P99_LATENCY_DURATION=10 \
  p99bench-test bash -c "bash /p99bench/bench/05-latency.sh; cat /tmp/p99work/frag-latency.json"
```

Expected: `stall_samples` ≈ 40,000 for a 10s run at 250 µs; `stall_p99_us` ≤ `stall_p999_us` ≤ `stall_max_us`.

Shellcheck:

```bash
docker run --rm -v "$PWD:/p99bench" -w /p99bench koalaman/shellcheck-alpine:stable \
  shellcheck -e SC1091 bench/05-latency.sh
```

Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add bench/05-latency.sh tests/test_stages.py
git commit -m "feat: measure stall percentiles with cyclictest, not intrinsic-latency

redis-cli --intrinsic-latency reports only max and avg. Max is
worst-of-60M-samples: an extreme-value statistic that grows with runtime,
so it measures 'is this a VM?' not 'is this VM good?'. All 10 published
results failed the <=200us bar; best measured was 1642us.

cyclictest gives a histogram, so p99/p99.9 are real. Runs at SCHED_OTHER
because Redis and Node do -- RT priority would measure the hypervisor's
best case, not the application's.

Overflow counts fold into the percentile denominator; a percentile beyond
the histogram ceiling reports null rather than pinning to the ceiling and
understating a bad host.

New dependency: rt-tests."
```

---

### Task 5: Fix the RAM working set to exceed LLC

Spec §5.2. Hetzner reports **207,409 MB/s** on a single-populated-slot EPYC Genoa. DDR5-4800 single channel peaks near 38 GB/s. The cause is in `03-ram.sh:25`: `--memory-block-size=1M`. In sysbench, block size **is** the per-thread working set, so 1 MB sits entirely in L2. This is a cache benchmark wearing a RAM label, and it is why the `>= 15,000` bar has never failed anything — every machine clears it by ~13x.

**Files:**
- Modify: `bench/03-ram.sh`
- Test: `tests/test_stages.py`

**Interfaces:**
- Consumes: `run_stage` from Task 4's test module.
- Produces: fragment `frag-ram.json` gains `ram.bw_read_mbs` and `ram.bw_block_bytes` (the working set actually used — without it the number is uninterpretable and unreproducible). All existing `ram.*` fields are retained unchanged as legacy (spec §9.2).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stages.py`:

```python
@pytest.mark.docker
def test_ram_stage_emits_bandwidth_field(repo_root):
    ram = run_stage(repo_root, "03-ram.sh", {"P99_RAM_TOTAL": "2G"}, "ram")["ram"]
    assert ram["bw_read_mbs"] is not None
    assert isinstance(ram["bw_read_mbs"], (int, float))


@pytest.mark.docker
def test_ram_working_set_exceeds_llc(repo_root):
    # The whole point of the fix: block size IS the per-thread working set in
    # sysbench, and the old 1M sat in L2. Assert we are past any plausible LLC.
    ram = run_stage(repo_root, "03-ram.sh", {"P99_RAM_TOTAL": "2G"}, "ram")["ram"]
    assert ram["bw_block_bytes"] >= 128 * 1024 * 1024


@pytest.mark.docker
def test_ram_stage_retains_legacy_fields(repo_root):
    # Spec 9.2: the old cache-resident number keeps its name and meaning so
    # published results stay readable. It is simply no longer banded.
    ram = run_stage(repo_root, "03-ram.sh", {"P99_RAM_TOTAL": "2G"}, "ram")["ram"]
    assert "seq_read_mbs" in ram
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_stages.py -v -k ram
```

Expected: 2 FAIL — `KeyError: 'bw_read_mbs'`. The legacy test passes already.

- [ ] **Step 3: Write the implementation**

In `bench/03-ram.sh`, insert after the `mem()` function definition (after line 22):

```bash
# Last-level cache size in bytes. sysbench's --memory-block-size IS the
# per-thread working set, so a block that fits in cache measures cache. The
# old 1M block reported 207 GB/s on a single-channel DDR5 host whose theoretical
# peak is ~38 GB/s - that was L2 bandwidth wearing a RAM label, and it is why
# the >=15 GB/s threshold never failed anything.
#
# Read the largest cache index sysfs exposes; fall back to a pessimistic 32M,
# which is larger than most LLCs and therefore still safely out of cache.
llc_bytes() {
  local biggest=0 size unit v
  for f in /sys/devices/system/cpu/cpu0/cache/index*/size; do
    [[ -r "$f" ]] || continue
    size=$(cat "$f")                 # e.g. "32768K", "32M"
    unit="${size: -1}"
    v="${size%?}"
    case "$unit" in
      K) v=$((v * 1024));;
      M) v=$((v * 1024 * 1024));;
      *) v="${size//[!0-9]/}";;
    esac
    (( v > biggest )) && biggest=$v
  done
  (( biggest == 0 )) && biggest=$((32 * 1024 * 1024))
  printf '%s' "$biggest"
}

LLC=$(llc_bytes)
# 4x LLC so the working set cannot be held even with a generous replacement
# policy, floored at 128M for hosts that under-report cache. Capped so that
# BLOCK * threads stays under a quarter of RAM - this must not swap, and a
# swapping run measures the disk.
RAM_BYTES=$(awk '/MemTotal/ {print $2 * 1024; exit}' /proc/meminfo)
BLOCK=$((LLC * 4))
(( BLOCK < 134217728 )) && BLOCK=134217728
MAX_BLOCK=$((RAM_BYTES / 4 / CORES))
(( BLOCK > MAX_BLOCK )) && BLOCK=$MAX_BLOCK

# Total bytes moved. Must be well above BLOCK*threads or the run is over
# before the memory subsystem reaches steady state.
RAM_TOTAL="${P99_RAM_TOTAL:-50G}"

log "RAM: bandwidth (working set ${BLOCK}B/thread, LLC ${LLC}B)"
BW_R=$(mem read seq "$BLOCK" "$CORES" "$RAM_TOTAL")
```

Then extend the `emit_json ram` call — add the two new `--argjson` lines and the two new object keys, leaving every existing key untouched:

```bash
emit_json ram "$(jq -n \
  --arg speed "$SPEED" --arg mtype "$MTYPE" \
  --argjson slots "$(jnum "$SLOTS")" \
  --argjson sr "$(jnum "$SEQ_R")" --argjson sw "$(jnum "$SEQ_W")" \
  --argjson rr "$(jnum "$RND_R")" --argjson rw "$(jnum "$RND_W")" \
  --argjson nl "$(jnum "$NUMA_L")" --argjson nr "$(jnum "$NUMA_R")" \
  --argjson bwr "$(jnum "$BW_R")" \
  --argjson block "$(jnum "$BLOCK")" \
  '{ram: {
      configured_speed: (if $speed == "" then null else $speed end),
      type: (if $mtype == "" then null else $mtype end),
      populated_slots: $slots,
      bw_read_mbs: $bwr,
      bw_block_bytes: $block,
      seq_read_mbs: $sr,
      seq_write_mbs: $sw,
      rnd_read_mbs: $rr,
      rnd_write_mbs: $rw,
      numa_local_mbs: $nl,
      numa_remote_mbs: $nr
  }}')"
```

Update the summary block — replace the `"seq read:"` line and add a legacy marker:

```bash
echo
echo "=== RAM summary ==="
jq -r '.ram |
  "reported:     \(.type // "?") @ \(.configured_speed // "?"), \(.populated_slots // "?") slots",
  "bandwidth:    \(.bw_read_mbs // "n/a") MiB/s   (working set \(.bw_block_bytes // "?")B/thread)",
  "seq read:     \(.seq_read_mbs // "n/a") MiB/s   (legacy: 1M block, cache-resident, not banded)",
  "seq write:    \(.seq_write_mbs // "n/a") MiB/s   (legacy)",
  "rnd read 8k:  \(.rnd_read_mbs // "n/a") MiB/s   (legacy: 8k block, L1-resident, not banded)",
  "rnd write 8k: \(.rnd_write_mbs // "n/a") MiB/s   (legacy)",
  "numa local:   \(.numa_local_mbs // "n/a") MiB/s",
  "numa remote:  \(.numa_remote_mbs // "n/a") MiB/s"
' "$P99_WORK/frag-ram.json"
echo
echo "Reference: DDR4-3200 dual channel ~35-45 GB/s. DDR5-4800 dual ~60-70 GB/s."
echo "A DDR4-3200 host delivering 12 GB/s is running single channel."
echo "bw_read_mbs above ~100 GB/s means the working set is still in cache - file a bug."
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_stages.py -v -k ram
```

Expected: 3 passed.

Sanity-check the number against physics — this is the whole point of the task:

```bash
docker run --rm -v "$PWD:/p99bench" -e P99_WORK=/tmp/p99work -e P99_RAM_TOTAL=4G \
  p99bench-test bash -c "bash /p99bench/bench/03-ram.sh >/dev/null 2>&1; \
    jq '.ram | {bw_read_mbs, bw_block_bytes, legacy_seq_read_mbs: .seq_read_mbs}' \
      /tmp/p99work/frag-ram.json"
```

Expected: `bw_read_mbs` **far below** `legacy_seq_read_mbs`. The legacy field reads cache; the new one reads memory. If they are close, the working set is still cache-resident and the fix has not worked.

Note the container reports the Docker VM's cache, not a real host's — the ratio is the signal here, not either number.

Shellcheck:

```bash
docker run --rm -v "$PWD:/p99bench" -w /p99bench koalaman/shellcheck-alpine:stable \
  shellcheck -e SC1091 bench/03-ram.sh
```

Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add bench/03-ram.sh tests/test_stages.py
git commit -m "feat: measure RAM bandwidth outside cache as ram.bw_read_mbs

sysbench's --memory-block-size IS the per-thread working set. The old 1M
block sat in L2, so a single-channel DDR5 host reported 207 GB/s against a
~38 GB/s theoretical peak -- a cache benchmark wearing a RAM label. That is
why the >=15 GB/s bar never failed anything: every machine cleared it 13x.

New working set is 4x LLC (read from sysfs), floored at 128M and capped at
RAM/4/threads so it cannot swap. Emits bw_block_bytes alongside, because the
bandwidth number is uninterpretable without the working set that produced it.

New field name per spec 9.2: the measurement changed, so the name changes.
Legacy ram.* fields retained, no longer banded."
```

---

### Task 6: Add `cpu.tls_handshakes_s`

Spec §5.4 / §6.3. SSL checks are handshake-bound (ECDSA sign/verify), not bulk-crypto-bound. `aes_256_gcm_mbs` was standing in for this and getting it wrong — it reads 37,717 MB/s against a `>= 2000` bar, 18x over, on every machine with AES-NI, i.e. all of them.

**Files:**
- Modify: `bench/02-cpu.sh`
- Test: `tests/test_stages.py`

**Interfaces:**
- Consumes: `run_stage` from Task 4's test module.
- Produces: fragment `frag-cpu.json` gains `cpu.tls_handshakes_s` (float, ECDSA P-256 sign ops/sec, single-threaded). Existing `cpu.*` keys unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stages.py`:

```python
@pytest.mark.docker
def test_cpu_stage_emits_tls_handshakes(repo_root):
    cpu = run_stage(repo_root, "02-cpu.sh", {"P99_CPU_QUICK": "1"}, "cpu")["cpu"]
    assert cpu["tls_handshakes_s"] is not None
    assert cpu["tls_handshakes_s"] > 0


@pytest.mark.docker
def test_cpu_stage_emits_scaling_efficiency(repo_root):
    # Regression guard for the jnum bug fixed in Task 2: this was null in all
    # 10 published results because bc prints ".977" and jnum rejected it.
    cpu = run_stage(repo_root, "02-cpu.sh", {"P99_CPU_QUICK": "1"}, "cpu")["cpu"]
    assert cpu["scaling_efficiency"] is not None
    assert 0 < cpu["scaling_efficiency"] <= 1.5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_stages.py -v -k cpu
```

Expected: `test_cpu_stage_emits_tls_handshakes` FAILs with `KeyError: 'tls_handshakes_s'`.

Note: these tests need `P99_CPU_QUICK` to keep the stage under a minute; add that knob in Step 3.

- [ ] **Step 3: Write the implementation**

In `bench/02-cpu.sh`, add the runtime knob directly after `CORES=$(nproc)` (line 11):

```bash
# Container/CI runs only need to prove the parsers work. A real run must not
# use this - 5s of sysbench measures noise.
SB_TIME=30
STRESS_TIME=65
if [[ -n "${P99_CPU_QUICK:-}" ]]; then
  warn "P99_CPU_QUICK set - runtimes cut to seconds. NOT a measurement."
  SB_TIME=2
  STRESS_TIME=6
fi
```

Then replace the hard-coded `--time=30` on both sysbench calls with `--time="$SB_TIME"`, the `--timeout 65s` with `--timeout "${STRESS_TIME}s"`, and `--time=65` with `--time="$STRESS_TIME"`.

Add the TLS handshake measurement after the SHA-256 block (after line 82):

```bash
# TLS handshake rate. SSL/HTTPS checks are bound by the asymmetric handshake
# (ECDSA sign + verify), not by bulk cipher throughput - a probe node opens a
# new connection per check and almost never transfers enough bytes for
# aes_256_gcm_mbs to matter. P-256 because it is what essentially every modern
# certificate uses.
#
# Single-threaded on purpose: this feeds worker_probe, where the question is
# how fast one check can complete, not how many cores can be thrown at it.
#
# openssl prints:
#   sign    verify    sign/s verify/s
#   0.0000s 0.0000s   45678.1  123456.7
# Take sign/s ($3): signing is the expensive half and the one a client waits on.
TLS=""
if command -v openssl >/dev/null 2>&1; then
  log "CPU: ECDSA P-256 handshakes (SSL check rate)"
  TLS=$(openssl speed -seconds 3 ecdsap256 2>/dev/null \
        | awk '/^ *256 bits ecdsa \(nistp256\)/ {print $(NF-1); exit}')
  # Label and column layout have both moved across OpenSSL 1.1/3.x. Fall back
  # to the generic ecdsa row rather than silently recording null.
  if [[ -z "$TLS" ]]; then
    TLS=$(openssl speed -seconds 3 ecdsap256 2>/dev/null \
          | awk '/ecdsa/ && /nistp256/ {print $(NF-1); exit}')
  fi
fi
```

Add to the `emit_json cpu` call — one new `--argjson` and one new key:

```bash
emit_json cpu "$(jq -n \
  --argjson st "$(jnum "$ST")" \
  --argjson mt "$(jnum "$MT")" \
  --argjson scale "$(jnum "$SCALE")" \
  --argjson ci "$(jnum "$CLOCK_IDLE")" \
  --argjson cl "$(jnum "$CLOCK_LOAD")" \
  --argjson steal "$(jnum "$STEAL")" \
  --argjson aes "$(jnum "$AES")" \
  --argjson sha "$(jnum "$SHA")" \
  --argjson tls "$(jnum "$TLS")" \
  '{cpu: {
      single_thread_eps: $st,
      multi_thread_eps: $mt,
      scaling_efficiency: $scale,
      clock_idle_mhz: $ci,
      clock_under_load_mhz: $cl,
      steal_pct_under_load: $steal,
      aes_256_gcm_mbs: $aes,
      sha256_mbs: $sha,
      tls_handshakes_s: $tls
  }}')"
```

Add to the summary block, after the `aes-256-gcm` line:

```bash
  "tls handshakes:  \(.tls_handshakes_s // "n/a") /s   (ECDSA P-256 sign, 1 thread)",
```

And mark AES as context in the same block by replacing its line:

```bash
  "aes-256-gcm:     \(.aes_256_gcm_mbs // "n/a") MB/s   (context: absent AES-NI would be remarkable)",
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_stages.py -v -k cpu
```

Expected: 2 passed.

Confirm the parser survives this host's OpenSSL rather than trusting the fallback:

```bash
docker run --rm p99bench-test bash -c "openssl speed -seconds 1 ecdsap256 2>/dev/null | tail -4"
```

Expected: a row containing `nistp256` with sign/s and verify/s columns. If the value the stage recorded is `null`, the awk needs adjusting to this OpenSSL's layout — do not widen it to accept non-numbers.

Shellcheck:

```bash
docker run --rm -v "$PWD:/p99bench" -w /p99bench koalaman/shellcheck-alpine:stable \
  shellcheck -e SC1091 bench/02-cpu.sh
```

Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add bench/02-cpu.sh tests/test_stages.py
git commit -m "feat: measure ECDSA P-256 handshake rate as cpu.tls_handshakes_s

SSL checks are bound by the asymmetric handshake, not bulk cipher
throughput -- a probe opens a connection per check and transfers almost
nothing. aes_256_gcm_mbs was standing in for this and getting it wrong:
37,717 MB/s against a >=2000 bar, 18x over, on every machine with AES-NI.

Single-threaded because worker_probe asks how fast one check completes.
AES demoted to context in the summary. Adds P99_CPU_QUICK for container
tests, which warns loudly that it is not a measurement."
```

---

### Task 7: New stage — 15-minute CPU sustained load

Spec §5.5 / §2.6. This is the stage that catches the Prague failure mode. `01b-steady.sh` catches disk burst credits; there is no CPU equivalent, so a short sysbench run measures the CPU **credit balance** — the same trap the disk tests were built to escape. Prague's Playwright node failed 52% of checks constantly, day and night, with even passing runs 3x slower: a node pinned at a throttled baseline. Steal time did not catch it (every OVH box reads 0.1–0.2%).

**Files:**
- Create: `bench/02b-cpu-steady.sh`
- Modify: `bench/run-all.sh`
- Test: `tests/test_stages.py`

**Interfaces:**
- Consumes: `emit_json`, `jnum`, `need`, `log`, `warn` from `lib.sh`.
- Produces: fragment `frag-cpu-steady.json` with shape
  `{"cpu": {"steady_state": {"degradation_pct": <num|null>, "first_min_eps": <num|null>, "last_min_eps": <num|null>, "duration_s": <num>, "steal_pct": <num|null>}}}`.
  `run-all.sh` gains `--skip-cpu-steady` and env knob `P99_CPU_STEADY_MIN` (default 15).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stages.py`:

```python
@pytest.mark.docker
def test_cpu_steady_emits_degradation(repo_root):
    frag = run_stage(repo_root, "02b-cpu-steady.sh",
                     {"P99_CPU_STEADY_MIN": "1"}, "cpu-steady")
    steady = frag["cpu"]["steady_state"]
    assert steady["degradation_pct"] is not None
    assert steady["first_min_eps"] is not None
    assert steady["last_min_eps"] is not None


@pytest.mark.docker
def test_cpu_steady_degradation_sign_is_drop_not_gain(repo_root):
    # degradation_pct must be positive when throughput FALLS, matching
    # disk.steady_state.degradation_pct, which the bands read as "lte".
    # A sign flip here would grade a throttled host as excellent.
    #
    # MUST run with at least 2 minutes: at MIN=1 the first and last samples
    # are the same array element, degradation is identically 0, and this
    # assertion holds no matter what the implementation does.
    frag = run_stage(repo_root, "02b-cpu-steady.sh",
                     {"P99_CPU_STEADY_MIN": "2"}, "cpu-steady")
    steady = frag["cpu"]["steady_state"]
    first, last = steady["first_min_eps"], steady["last_min_eps"]
    assert first != last, (
        "first and last minute are identical -- the series is not being "
        "sampled per-minute, so this test cannot see a sign error"
    )
    expected = (first - last) / first * 100
    assert abs(steady["degradation_pct"] - expected) < 0.5


@pytest.mark.docker
def test_cpu_steady_reports_positive_degradation_when_throughput_falls():
    # Pure unit check of the sign convention, independent of hardware: feed
    # the shell's own expression a falling series and assert the sign. A
    # container cannot be made to throttle on demand, so the container tests
    # above can never prove this direction.
    import subprocess
    out = subprocess.run(
        ["bash", "-c", 'echo "scale=2; (1000 - 400) / 1000 * 100" | bc'],
        capture_output=True, text=True,
    )
    assert float(out.stdout) == 60.0, "falling throughput must give positive degradation"


@pytest.mark.docker
def test_cpu_steady_nests_under_cpu_not_top_level(repo_root):
    # run-all.sh deep-merges fragments; this must land at cpu.steady_state so
    # the band path in thresholds.yaml resolves.
    frag = run_stage(repo_root, "02b-cpu-steady.sh",
                     {"P99_CPU_STEADY_MIN": "1"}, "cpu-steady")
    assert set(frag.keys()) == {"cpu"}
    assert "steady_state" in frag["cpu"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_stages.py -v -k steady
```

Expected: 3 FAIL — the stage does not exist, `run_stage` asserts on a non-zero exit.

- [ ] **Step 3: Write the implementation**

Create `bench/02b-cpu-steady.sh`:

```bash
#!/usr/bin/env bash
# 02b-cpu-steady.sh - sustained CPU load. Detects burst-credit throttling.
# Emits cpu.steady_state fragment.
#
# WHY THIS EXISTS
# ---------------
# Cloud block storage grants burst credits, so 01b-steady.sh runs 30 minutes to
# find the disk a customer actually gets rather than the credit balance. CPU
# credits work the same way on budget VPS, and until now nothing here measured
# them: every CPU number in this suite came from a 30-second sysbench run, which
# is exactly the window a credit budget covers.
#
# The failure mode is real and was diagnosed in production before this stage
# existed. A Playwright probe node failed 52% of its checks against 2% on two
# peer nodes running the same checks. Its plain HTTP checks were fine, so egress
# was healthy. It failed constantly - 2-21 fails/hr around the clock - not in
# spikes, and even its PASSING runs took 46s against 12.8s on peers. That is a
# node pinned at a throttled baseline, and steal time did not see it: every box
# in that fleet reported 0.1-0.2% steal.
#
# WHY 15 MINUTES AND NOT 30
# -------------------------
# CPU credit budgets are granted in seconds-to-minutes, not hours, so 15 minutes
# exhausts typical schemes at half the wall-clock cost of the disk stage.
#
# WHY THIS CANNOT SHARE THE DISK STEADY WINDOW
# --------------------------------------------
# fio saturating the disk makes these numbers measure I/O wait. The two would be
# indistinguishable, which is worse than not measuring either.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need sysbench
need jq

CORES=$(nproc)
MINUTES="${P99_CPU_STEADY_MIN:-15}"
DURATION=$((MINUTES * 60))

log "CPU sustained load: ${MINUTES} min at $CORES threads"
log "This finds the CPU you actually get, not the credit balance."

# One sysbench per minute, back to back. Reporting per-interval throughput this
# way (rather than parsing sysbench's own interim reports) keeps the parser
# independent of sysbench's --report-interval output format, which has moved
# between versions and has already broken parsers in this repo once.
EPS_SERIES=()
for ((m = 0; m < MINUTES; m++)); do
  e=$(sysbench cpu --cpu-max-prime=20000 --threads="$CORES" --time=60 run 2>/dev/null \
      | awk '/events per second/ {print $4}')
  EPS_SERIES+=("$e")
  log "  minute $((m + 1))/$MINUTES: ${e:-parse-failed} eps"
done

# Steal during the final minute, when credits are gone and the hypervisor's
# real allocation is visible. An idle VM steals nothing; a throttled one may
# steal a great deal precisely here.
STEAL=""
if command -v mpstat >/dev/null 2>&1; then
  sysbench cpu --cpu-max-prime=20000 --threads="$CORES" --time=15 run >/dev/null 2>&1 &
  LOAD_PID=$!
  STEAL=$(mpstat 1 10 2>/dev/null | awk '
    /%steal/ && !col { for (i = 1; i <= NF; i++) if ($i == "%steal") col = i - 1 }
    /^Average/ && col { print $col; exit }
  ')
  wait "$LOAD_PID" 2>/dev/null || true
else
  warn "mpstat missing (apt install sysstat) - steal not measured"
fi

FIRST="${EPS_SERIES[0]}"
LAST="${EPS_SERIES[${#EPS_SERIES[@]} - 1]}"

# Positive = throughput fell. Same sign convention as
# disk.steady_state.degradation_pct, which the bands read with op "lte". A sign
# flip here would grade a throttled host as excellent.
DEG=""
if [[ -n "$FIRST" && -n "$LAST" ]] && (( $(echo "$FIRST > 0" | bc -l) )); then
  DEG=$(echo "scale=2; ($FIRST - $LAST) / $FIRST * 100" | bc 2>/dev/null || echo "")
fi

emit_json cpu-steady "$(jq -n \
  --argjson deg "$(jnum "$DEG")" \
  --argjson first "$(jnum "$FIRST")" \
  --argjson last "$(jnum "$LAST")" \
  --argjson dur "$(jnum "$DURATION")" \
  --argjson steal "$(jnum "$STEAL")" \
  '{cpu: {steady_state: {
      degradation_pct: $deg,
      first_min_eps: $first,
      last_min_eps: $last,
      duration_s: $dur,
      steal_pct: $steal
  }}}')"

echo
echo "=== CPU steady state (${MINUTES} min) ==="
echo "first minute: ${FIRST:-n/a} eps"
echo "last minute:  ${LAST:-n/a} eps"
echo "degradation:  ${DEG:-n/a} %   (positive = throughput fell)"
echo "steal at end: ${STEAL:-n/a} %"
echo
echo "A large drop means the short CPU tests describe a machine you do not have."
echo "This is the signal steal time misses: a host can throttle you to baseline"
echo "without ever reporting steal."
```

Make it executable:

```bash
chmod +x bench/02b-cpu-steady.sh
```

Now wire it into `bench/run-all.sh`. Add the flag variable to the declaration block (line 21-23):

```bash
PROVIDER="" PRODUCT="" REGION="" PRICE="null" BILLING="null"
SUBMITTER="" NOTES="" TIER="" SKIP_STEADY=0 STEADY_ONLY=0
SKIP_NETWORK=0 SKIP_CPU_STEADY=0
```

Add the option to the arg parser, before the `-h|--help` line:

```bash
    --skip-cpu-steady) SKIP_CPU_STEADY=1; shift;;
```

Add to the usage comment block at the top (after the `--skip-network` line, keeping it inside the `sed -n '2,21p'` range that `--help` prints — extend that range to `'2,23p'` in the `-h|--help` case):

```bash
#   --skip-cpu-steady do not run the 15 min CPU sustained test (result marked
#                     incomplete; playwright_node and worker_probe grade "?")
```

Add the stage to the run sequence, after `run_stage 05-latency.sh`:

```bash
  if (( SKIP_CPU_STEADY )); then
    warn "CPU steady state skipped - playwright_node and worker_probe will grade '?'"
  else
    run_stage 02b-cpu-steady.sh
  fi
```

Update the banner near the top to reflect the new total runtime — replace the `log "Target: ..."` line's neighbourhood by adding after it:

```bash
log "Expect ~60 min: 30 disk steady + 15 CPU steady + ~15 for everything else."
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_stages.py -v -k steady
```

Expected: 3 passed. (Takes ~3 min: three container runs at `P99_CPU_STEADY_MIN=1`.)

Verify `run-all.sh` still parses and the new flag is wired:

```bash
bash -n bench/run-all.sh && echo "syntax OK"
grep -n "skip-cpu-steady\|02b-cpu-steady" bench/run-all.sh
```

Expected: `syntax OK`, then three hits — the usage comment, the arg case, and the run_stage call.

Shellcheck both:

```bash
docker run --rm -v "$PWD:/p99bench" -w /p99bench koalaman/shellcheck-alpine:stable \
  shellcheck -e SC1091 bench/02b-cpu-steady.sh bench/run-all.sh
```

Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add bench/02b-cpu-steady.sh bench/run-all.sh tests/test_stages.py
git commit -m "feat: add 15-minute CPU sustained stage to catch burst throttling

Disk had 01b-steady.sh for burst credits; CPU had nothing, so every CPU
number came from a 30s sysbench run -- exactly the window a credit budget
covers. We were measuring the credit balance.

This is the metric that catches a resource-starved node. A Playwright probe
failed 52% of checks vs 2% on peers, constantly, day and night, with even
passing runs 3x slower -- a node pinned at a throttled baseline. Steal did
not see it: that whole fleet reported 0.1-0.2% steal.

15 min because CPU credits are granted in seconds-to-minutes. Cannot share
the disk steady window: fio saturating the disk would make these numbers
measure I/O wait. Run goes from ~45 to ~60 min; --skip-cpu-steady opts out."
```

---

### Task 8: Dependencies, CI, and docs

The new `rt-tests` dependency must reach anyone who runs the suite, and CI must exercise the new fields. Per the Global Constraints, a new metric needs a schema field, a threshold rule, and a THRESHOLDS.md row — Plan 2 delivers those. This task records the informational-only notes and the deps.

**Files:**
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `bench/lib.sh` (version bump)
- Modify: `.github/workflows/validate.yml`
- Modify: `results/README.md`

**Interfaces:**
- Consumes: all prior tasks' fields.
- Produces: `P99BENCH_VERSION="0.2.0"` — spec §9.3 keys the "needs re-run" message off `tool_version >= 0.2.0`, so Plan 2 reads this exact string.

- [ ] **Step 1: Bump the tool version**

In `bench/lib.sh`, line 6:

```bash
P99BENCH_VERSION="0.2.0"
```

This is load-bearing, not cosmetic: spec §9.3 grades a profile `?` with `needs re-run (tool >= 0.2.0)` when a metric this plan adds is absent, and Plan 2 compares against this string.

- [ ] **Step 2: Update the install line and runtime in README.md**

Replace the apt line in the Quickstart block:

```bash
apt update && apt install -y fio sysbench stress-ng rt-tests smartmontools dmidecode \
  numactl redis-tools jq bc sysstat curl iputils-ping python3-yaml
```

Replace the paragraph beginning "Takes about 45 minutes":

```markdown
Takes about 60 minutes: 30 for the sustained disk test, 15 for the sustained CPU
test, ~15 for everything else. Needs ~20 GB free and a machine with nothing else
running on it.
```

In the Scripts table, add the new row after `02-cpu.sh` and update `05-latency.sh`:

```markdown
| `02b-cpu-steady.sh` | 15 min sustained CPU → **burst credit exhaustion** | 15 min |
```

```markdown
| `05-latency.sh` | **scheduler stall percentiles** (no Redis needed) | 1 min |
```

- [ ] **Step 3: Update CONTRIBUTING.md**

In the "Run it" section, after the `--skip-steady` paragraph, add:

```markdown
Do **not** pass `--skip-cpu-steady` either. Without it, `playwright_node` and
`worker_probe` grade `?` — a 30-second CPU test measures the burst credit
balance, not the machine you get, which is the same reason `--skip-steady` is
refused for disk.
```

- [ ] **Step 4: Fix the stale results/README.md**

Spec §9.4: it still claims the committed results are examples to delete, but the git history (`result: first real runs`, `result: ovh waw and ovh zrh from new machines`) shows they are real submissions. Replace the "## The files currently here are EXAMPLES" section and its list with:

```markdown
## These are real submissions

Every file here is a real measurement of a real machine. They are not examples
and must not be deleted. Results are immutable: grades are recomputed from
`schema/thresholds.yaml` on every render, but the measured numbers never change.

Results measured with tool_version < 0.2.0 predate the metric-integrity fixes
and carry no `cpu.stall_*`, `cpu.steady_state`, `cpu.tls_handshakes_s` or
`ram.bw_read_mbs`. They still grade fully for `postgres_oltp` and
`timescale_ingest`; profiles needing the newer metrics grade `?` until the
machine is re-run. That is intended — a grade invented from data that was never
measured would be worse than no grade.
```

- [ ] **Step 5: Add CI coverage for the new fields**

In `.github/workflows/validate.yml`, in the `smoke` job's "Install bench deps" step, add `rt-tests`:

```yaml
      - name: Install bench deps
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y fio sysbench jq bc redis-tools curl iputils-ping rt-tests
          pip install pyyaml
```

Replace the "Smoke test latency script" step:

```yaml
      - name: Smoke test latency script
        env:
          P99_LATENCY_DURATION: 5
        run: |
          sudo -E bash bench/05-latency.sh
          # Percentiles must exist and be ordered. This is the assertion the
          # old max-only metric could not support.
          sudo jq -e '.cpu.stall_p999_us != null' "$P99_WORK/frag-latency.json"
          sudo jq -e '.cpu.stall_p99_us <= .cpu.stall_p999_us' "$P99_WORK/frag-latency.json"
          sudo jq -e '.cpu.stall_p999_us <= .cpu.stall_max_us' "$P99_WORK/frag-latency.json"
```

Add a new step after it:

```yaml
      - name: Smoke test CPU steady script
        env:
          P99_CPU_STEADY_MIN: 1
        run: |
          sudo -E bash bench/02b-cpu-steady.sh
          sudo jq -e '.cpu.steady_state.degradation_pct != null' "$P99_WORK/frag-cpu-steady.json"
```

Add a job to run the Python tests:

```yaml
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install -r requirements-dev.txt
      - name: Run tests
        # Docker-marked stage tests are covered by the smoke job above; here we
        # run the tier that needs no benchmark tools.
        run: python3 -m pytest tests/ -v -m "not docker"
```

- [ ] **Step 6: Verify**

```bash
python3 -m pytest tests/ -v -m "not docker"
```

Expected: 14 passed (9 in `test_lib.py`, 5 in `test_backfill.py`); the docker-marked stage tests report as skipped or deselected.

```bash
python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/validate.yml')); print('workflow YAML OK')"
grep -n "0.2.0" bench/lib.sh
docker run --rm -v "$PWD:/p99bench" -w /p99bench koalaman/shellcheck-alpine:stable \
  shellcheck -e SC1091 bench/*.sh
```

Expected: `workflow YAML OK`; version hit on line 6; shellcheck silent.

- [ ] **Step 7: Commit**

```bash
git add README.md CONTRIBUTING.md bench/lib.sh .github/workflows/validate.yml results/README.md
git commit -m "docs: rt-tests dep, 60 min runtime, tool version 0.2.0

Version bump is load-bearing: spec 9.3 keys the 'needs re-run' message off
tool_version >= 0.2.0, so results predating the metric fixes are legible as
such rather than silently grading against metrics they never collected.

CI asserts stall percentiles exist and are ordered -- an assertion the old
max-only metric could not support -- and exercises the new CPU steady stage.

results/README.md claimed the committed results were examples to delete.
Git history says otherwise; they are real submissions."
```

---

## Definition of done

- [ ] `python3 -m pytest tests/ -v` — all pass (docker tier included)
- [ ] `shellcheck -e SC1091 bench/*.sh` — silent
- [ ] All 10 results have a non-null `cpu.scaling_efficiency`
- [ ] A full `sudo ./bench/run-all.sh` on a real Linux VM emits `cpu.stall_p999_us`, `cpu.steady_state.degradation_pct`, `cpu.tls_handshakes_s`, `ram.bw_read_mbs`
- [ ] On that run, `ram.bw_read_mbs` is far below the legacy `ram.seq_read_mbs` (proves the working set escaped cache)
- [ ] `validate.py` still passes on `results/` (the backfill must not break the v1 schema — if it does, that is a Plan 2 schema change, not a fix here)

## What this plan deliberately does not do

- **No banding.** No `thresholds.yaml` changes, no grades. Plan 2.
- **No `storage_class`.** Derived in `tools/`, not `bench/` — see spec §10 item 6.
- **No schema v2.** `verdict` → `grades` and the renamed-field schema are Plan 2.
- **No render changes.** Plan 3.
- **No recalibration.** The provisional bands (spec §11) need a corpus measured with these very stages, which cannot exist until this plan ships and real runs happen. Spec Phase 5.
- **`ram.rnd_read_mbs` stays cache-resident.** It uses an 8k block, so like the old `seq_read_mbs` it measures L1, not memory. The spec only calls for fixing the read-bandwidth path, so it stays legacy and unbanded. Worth a follow-up issue; out of scope here.
