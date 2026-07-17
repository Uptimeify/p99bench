"""Container smoke tests for bench stages.

These prove a stage runs and emits a well-formed fragment. They prove
NOTHING about hardware -- numbers from a container on a laptop describe
the laptop's hypervisor, not a provider. Same rule as the CI smoke job.
"""
import json
import re
import subprocess

import pytest

IMAGE = "p99bench-test"


def run_stage(repo_root, script: str, env: dict, frag: str) -> dict:
    """Run one stage in the container and return its parsed fragment."""
    env_args = []
    for k, v in {"P99_WORK": "/tmp/p99work", **env}.items():
        env_args += ["-e", f"{k}={v}"]
    proc = subprocess.run(
        # --cap-add=SYS_NICE: cyclictest calls sched_setscheduler even at
        # -p 0 (it "runs all threads at the same priority" under -h), which
        # needs CAP_SYS_NICE or a nonzero RLIMIT_RTPRIO. Docker's default
        # capability set has neither, so cyclictest exits 1 with "Unable to
        # change scheduling policy!" before it ever gets to mlockall. This
        # is a container-only concession for the test rig; the real script
        # runs on bare metal and needs no such grant.
        ["docker", "run", "--rm", "--cap-add=SYS_NICE",
         "-v", f"{repo_root}:/p99bench", *env_args, IMAGE,
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
def test_latency_overflow_counts_toward_percentile_denominator(repo_root):
    # This is the one guard on the line that decides whether p99bench
    # understates a bad host. cyclictest reports samples above the
    # histogram ceiling on a separate "# Histogram Overflows:" line, and
    # the parser MUST fold that count into the percentile denominator
    # (grand = total + overflow in 05-latency.sh). Drop the overflow term
    # and the denominator goes short: percentiles get computed against
    # only the in-histogram samples, so a bad host with heavy tail stalls
    # reads a deceptively low, "clean" p99 instead of the truth. That is
    # exactly the failure this whole project exists to prevent.
    #
    # We force the bug's precondition -- real overflow -- by clamping the
    # histogram ceiling (P99_STALL_HIST_MAX) to 600us, well under this
    # container's typical stall (empirically ~850-900us here, and *very*
    # bursty besides). Confirmed empirically over repeated runs at this
    # ceiling: the in-histogram "# Total:" stays small but reliably
    # nonzero (single digits to low tens out of ~5000 samples) while "#
    # Histogram Overflows:" claims the rest (~99.6-99.9%). Both ends of
    # that range matter. If overflow were only a percent or two (as it is
    # at higher ceilings on this noisy VM), a good run could occasionally
    # land its 99th percentile back inside the histogram even under a
    # CORRECT parser, making the test flaky. If the ceiling were low
    # enough to push the in-histogram total to exactly 0, a BROKEN parser
    # would hit the unrelated "no data" branch and emit null anyway -- a
    # false pass that proves nothing. At 600us, a CORRECT parser (grand =
    # total + overflow) can never reach the 99th percentile inside the
    # histogram and must emit null, while a BROKEN parser (grand = total
    # alone, no overflow term) always has a nonzero total to compute
    # against and returns a real bucket number instead -- silently. This
    # test only asserts on that overflow behaviour (null vs. a number),
    # never on a latency magnitude, since magnitudes measured in a
    # container are noise.
    cpu = run_stage(repo_root, "05-latency.sh",
                    {"P99_LATENCY_DURATION": "5", "P99_STALL_HIST_MAX": "600"},
                    "latency")["cpu"]
    assert cpu["stall_p99_us"] is None, (
        "stall_p99_us came back as a number under a deliberately tiny "
        "histogram ceiling -- the overflow count is not reaching the "
        "percentile denominator, so a bad host's tail stalls would be "
        "silently dropped instead of reported"
    )
    assert cpu["stall_p999_us"] is None


def _extract_latency_awk(repo_root) -> str:
    """Pull the percentile-parsing awk program straight out of 05-latency.sh.

    Extracting the real program (rather than hand-copying it into the test)
    means this test breaks the moment the shipped parser regresses, instead
    of silently testing a stale mirror of it.
    """
    script = (repo_root / "bench" / "05-latency.sh").read_text()
    m = re.search(r"\| awk '(.*?)'\)\"", script, re.S)
    assert m, "could not locate the percentile-parsing awk program in 05-latency.sh"
    return m.group(1)


def test_latency_awk_does_not_lose_histogram_bucket_zero(repo_root):
    # Not @pytest.mark.docker: this feeds a synthetic cyclictest-shaped
    # histogram straight to the real awk program via a plain `awk` subprocess
    # -- no cyclictest, no container.
    #
    # Regression test for the b[n]/c[n] subscript bug: on the very first
    # matching histogram line awk's uninitialised `n` has numeric value 0 but
    # STRING value "" (nothing has done arithmetic on it yet), so b[n]/c[n]
    # write to b[""]/c[""] instead of b[0]/c[0]. The cumulative loop
    # `for (i = 0; i < n; i++)` then reads integer subscripts "0", "1", ...
    # and never sees bucket 0's count again -- it silently drops out of every
    # percentile, even though `total` (a scalar, not an array) still counts
    # it correctly.
    #
    # The numbers below reproduce the exact failure mode from the branch
    # review: bucket 0 holds 0.2% of 100000 samples, there is zero overflow,
    # and every sample sits inside the histogram ceiling. That 0.2% is enough
    # to push the buggy cumulative sum (which tops out at total - bucket0)
    # short of the 99.9th-percentile target, while still leaving enough
    # margin that the 99th percentile resolves to the same bucket either way
    # -- p99 stays right by coincidence, p999 does not. Null is supposed to
    # mean "beyond the histogram ceiling"; here it would mean the opposite:
    # so fast that most samples piled into bucket 0.
    awk_program = _extract_latency_awk(repo_root)

    histogram = "\n".join([
        "0 200",
        "1 50",
        "2 50",
        "3 50",
        "4 98950",
        "5 600",
        "6 100",
        "# Histogram Overflows: 0",
        "# Max Latencies: 00007",
    ]) + "\n"

    proc = subprocess.run(
        ["awk", awk_program], input=histogram, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    p99, p999, maxv, grand = proc.stdout.split()

    assert grand == "100000", f"unexpected total sample count: {grand!r}"
    assert p99 == "4", f"unexpected p99 bucket: {p99!r}"
    assert p999 != "null", (
        "stall_p999_us came back null for a histogram where every sample "
        "(including bucket 0) is inside the ceiling and there is zero "
        "overflow -- bucket 0 is being silently dropped from the "
        "cumulative percentile sum (see the b[n]/c[n] subscript comment in "
        "05-latency.sh)"
    )
    assert p999 == "5", f"unexpected p999 bucket: {p999!r}"


@pytest.mark.docker
def test_latency_stage_retains_legacy_fields_as_null(repo_root):
    # Spec 9.2: legacy fields are retained so old and new results share a
    # schema, but the tool that produced them is gone, so they are null.
    cpu = run_stage(repo_root, "05-latency.sh",
                    {"P99_LATENCY_DURATION": "5"}, "latency")["cpu"]
    assert cpu["intrinsic_latency_max_us"] is None
    assert cpu["intrinsic_latency_avg_us"] is None


def test_latency_stage_uses_policy_other_not_dash_p_zero(repo_root):
    # Not @pytest.mark.docker: this reads the script source, no container
    # needed, and must run everywhere so nobody re-introduces the bug
    # silently on a machine without Docker.
    #
    # -p 0 looks like "normal priority" but is not: cyclictest is a
    # real-time tool that defaults to SCHED_FIFO and clamps whatever -p
    # value it's given upward, so "-p 0" actually starts the measuring
    # thread at SCHED_FIFO priority 2 (confirmed live with `chrt -p` against
    # the measuring thread, not the main thread). A SCHED_FIFO thread
    # preempts exactly the contention this stage exists to detect, so it
    # silently understates stalls -- the hypervisor's best case again, via a
    # different door. --policy=other is the only flag that actually puts the
    # thread in SCHED_OTHER, the class Redis and Node run in. The bug is
    # invisible in the fragment output (the histogram still parses fine
    # either way), so this has to assert on the source directly.
    script = (repo_root / "bench" / "05-latency.sh").read_text()
    assert "--policy=other" in script, \
        "cyclictest must be invoked with --policy=other to measure SCHED_OTHER"
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not re.search(r"(?<!\S)-p\s*0(?!\S)", line), \
            f"found bare -p 0 (clamps to SCHED_FIFO prio 2, not SCHED_OTHER): {line!r}"


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
def test_ram_working_set_tracks_llc_via_fixture(repo_root):
    # The assertion above (>= 128M) is satisfied by the hardcoded floor
    # alone: this container's real sysfs exposes no cache/index*/size files,
    # so llc_bytes() always takes its 32M fallback, and 4 * 32M == 128M
    # exactly -- it would pass even if llc_bytes() returned 0 or inverted
    # its unit maths. Driving llc_bytes() with a fixture LLC whose 4x product
    # does NOT collide with the floor actually proves the multiply ran, end to
    # end through the real script.
    #
    # The fixture is 256M, not the 64M it was written with: the floor moved
    # 128M -> 512M in d236a0e, which swallowed 4 * 64M = 256M and left this
    # test asserting a number the script can no longer produce. It was red
    # from that commit until 2026-07-17 and nobody saw it, because every test
    # in this file is @pytest.mark.docker and CI runs -m "not docker". Only an
    # LLC above 128M outranks the floor now: 256M -> 4x -> 1 GiB.
    fixture = "/p99bench/tests/fixtures/cache/256m"
    ram = run_stage(repo_root, "03-ram.sh", {
        "P99_RAM_TOTAL": "2G",
        "P99_CACHE_ROOT": fixture,
        # Generous and decoupled from the container's real specs, so this
        # test is purely about the LLC->block sizing, not the RAM cap
        # (Finding 1's cap-interaction is covered by a separate test below).
        "P99_RAM_BYTES": str(64 * 1024 ** 3),
        "P99_CORES": "1",
    }, "ram")["ram"]
    assert ram["bw_block_bytes"] == 256 * 1024 * 1024 * 4


@pytest.mark.docker
def test_ram_bandwidth_null_when_cap_forces_working_set_below_cache(repo_root):
    # The RAM-fraction cap exists so the working set can't swap, but on a
    # small/many-core host it can shrink BLOCK back down until it fits in
    # cache again -- silently recreating the exact bug this script exists to
    # fix (a cache number reported as RAM bandwidth). 1 vCPU / 128 MiB against
    # a (fixture) 64 MiB LLC: the cap allows 64M, and a 64M total working set
    # cannot clear a 64 MiB cache. The script must refuse to report a
    # bandwidth number it knows is wrong.
    #
    # The shape here is deliberately extreme. It used to be 8 vCPU / 2 GiB,
    # which was cache-bound only because the cap was RAM/4 AND the guard
    # compared one thread's block against the whole LLC. Both changed: the cap
    # is RAM/2 (so 4 x 512M fits the 4 vCPU / 8 GB shape this suite actually
    # targets) and the guard compares the TOTAL working set. Under those, 8
    # vCPU / 2 GiB yields 8 x 128M = 1 GiB, which clears a 64 MiB LLC 16x over
    # and is a perfectly good measurement.
    fixture = "/p99bench/tests/fixtures/cache/64m"
    ram = run_stage(repo_root, "03-ram.sh", {
        "P99_RAM_TOTAL": "2G",
        "P99_CACHE_ROOT": fixture,
        "P99_RAM_BYTES": str(128 * 1024 ** 2),
        "P99_CORES": "1",
    }, "ram")["ram"]
    assert ram["bw_read_mbs"] is None, (
        "bw_read_mbs came back as a number with the working set capped "
        "below 2x LLC -- this is reporting cache bandwidth as RAM bandwidth"
    )
    # bw_block_bytes must still show what was actually attempted.
    assert ram["bw_block_bytes"] == 64 * 1024 * 1024


@pytest.mark.docker
def test_ram_stage_retains_legacy_fields(repo_root):
    # Spec 9.2: the old cache-resident number keeps its name and meaning so
    # published results stay readable. It is simply no longer banded.
    ram = run_stage(repo_root, "03-ram.sh", {"P99_RAM_TOTAL": "2G"}, "ram")["ram"]
    assert "seq_read_mbs" in ram


@pytest.mark.docker
def test_cpu_stage_emits_tls_verify_s(repo_root):
    # tls_verify_s is the PRIMARY metric: a worker_probe is the TLS client,
    # and the client verifies (see bench/02-cpu.sh for the full argument).
    # Shape only -- this container may be arm64, where the magnitude is
    # meaningless.
    cpu = run_stage(repo_root, "02-cpu.sh", {"P99_CPU_QUICK": "1"}, "cpu")["cpu"]
    assert cpu["tls_verify_s"] is not None
    assert cpu["tls_verify_s"] > 0


@pytest.mark.docker
def test_cpu_stage_emits_tls_sign_s(repo_root):
    # tls_sign_s is recorded as context only (no profile grades it today) --
    # a host might later be graded as a TLS *server*, where signing is what
    # it pays. Shape only, same reasoning as above.
    cpu = run_stage(repo_root, "02-cpu.sh", {"P99_CPU_QUICK": "1"}, "cpu")["cpu"]
    assert cpu["tls_sign_s"] is not None
    assert cpu["tls_sign_s"] > 0


@pytest.mark.docker
def test_cpu_stage_tls_verify_is_slower_than_sign(repo_root):
    # Tripwire for the exact bug this fix addresses: ECDSA verify is two
    # scalar multiplications, sign is one, so verify/s must always be LOWER
    # than sign/s on every platform. If someone later swaps the awk columns
    # back (e.g. "simplifies" this to sign/s under the old, wrong belief
    # that signing is the expensive half), this test catches it -- it is a
    # real algorithmic invariant, not a magnitude assertion.
    cpu = run_stage(repo_root, "02-cpu.sh", {"P99_CPU_QUICK": "1"}, "cpu")["cpu"]
    assert cpu["tls_verify_s"] < cpu["tls_sign_s"]


@pytest.mark.docker
def test_cpu_stage_emits_scaling_efficiency(repo_root):
    # Regression guard for the jnum bug fixed in Task 2: this was null in all
    # 10 published results because bc prints ".977" and jnum rejected it.
    cpu = run_stage(repo_root, "02-cpu.sh", {"P99_CPU_QUICK": "1"}, "cpu")["cpu"]
    assert cpu["scaling_efficiency"] is not None
    assert 0 < cpu["scaling_efficiency"] <= 1.5


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


def test_run_all_help_lists_every_option_it_parses(repo_root):
    """--help must actually print, and must document every flag run-all parses.

    Two bugs this guards, both of which shipped:

    1. run-all.sh does `cd "$(dirname "$0")"` and then read its own comment block
       back out of "$0". After the cd a relative $0 no longer resolves, so
       `bash bench/run-all.sh --help` -- the invocation CONTRIBUTING.md documents
       -- printed a sed error to stderr, nothing to stdout, and exited 0.
    2. The help text was a hard-coded line range (`sed -n '2,21p'`). Adding an
       option silently desynced the range from the options.

    Needs no container: this is a text/CLI property.
    """
    import subprocess

    # Invoke by RELATIVE path from the repo root, exactly as CONTRIBUTING.md
    # documents (`sudo ./bench/run-all.sh ...`). This is load-bearing: an
    # absolute $0 survives the script's `cd` and hides bug 1 entirely, so a test
    # using an absolute path passes against the broken code.
    proc = subprocess.run(
        ["bash", "bench/run-all.sh", "--help"],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    assert proc.returncode == 0
    assert proc.stdout.strip(), "--help printed nothing (the cd/$0 bug)"

    src = (repo_root / "bench" / "run-all.sh").read_text()
    # Every long option the arg parser matches, e.g. `--skip-cpu-steady) ...`
    parsed = set(re.findall(r"^\s*(--[a-z-]+)\)", src, re.M))
    # Required options are documented in the Usage example line, not the
    # Options list, so exclude the ones the parser takes as mandatory.
    required = {"--provider", "--product", "--region", "--price", "--billing"}
    for opt in sorted(parsed - required):
        assert opt in proc.stdout, f"{opt} is parsed but undocumented in --help"


def test_steal_parser_reads_steal_not_the_column_beside_it(repo_root):
    """mpstat's %steal must be located by offset from the END of the line.

    The header's leading timestamp is one field in a 24-hour locale
    ("18:56:02 CPU %usr ...") and two in a 12-hour one ("06:56:02 PM CPU ...").
    The Average: line always has exactly one leading field. So an index counted
    from the left is correct in one locale and off by one in the other -- the
    original `col = i - 1` printed %soft as %steal on a 24-hour host. A
    plausible small number, wearing the name of the metric that decides whether
    Patroni spuriously fails over.

    Both stages parse this identically, so both are pinned. Needs no container:
    the bug is in the awk, and real mpstat output reproduces it exactly.
    """
    import subprocess

    # Real `mpstat 1 2` output, 24-hour locale. %soft=0.10, %steal=0.00 --
    # deliberately different so an off-by-one cannot pass by coincidence.
    sample = (
        "Linux 6.12.67 (host) \t07/16/26 \t_aarch64_\t(5 CPU)\n"
        "\n"
        "18:56:02     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle\n"
        "18:56:03     all    1.39    0.00    1.39    0.00    0.00    0.20    0.00    0.00    0.00   97.02\n"
        "Average:     all    0.40    0.00    0.10    0.00    0.00    0.10    0.00    0.00    0.00   99.40\n"
    )
    for script in ("02-cpu.sh", "02b-cpu-steady.sh"):
        src = (repo_root / "bench" / script).read_text()
        # Find the awk program by CONTENT, not by position: the first literal
        # "mpstat" in these files is inside a comment, so anchoring on it picks
        # up the wrong block.
        blocks = [b for b in re.findall(r"awk\s*'(.*?)'", src, re.S)
                  if "%steal" in b and "Average" in b]
        assert len(blocks) == 1, (
            f"{script}: expected exactly one %steal awk program, found {len(blocks)}"
        )
        out = subprocess.run(["awk", blocks[0]], input=sample,
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        got = out.stdout.strip()
        assert got == "0.00", (
            f"{script}: steal parser returned {got!r}; %steal is 0.00 and %soft "
            f"is 0.10 -- returning 0.10 means it is reading the column beside it"
        )


def test_disk_stage_measures_random_read_at_qd1(repo_root):
    # Not @pytest.mark.docker: CI runs -m "not docker", so a container-only
    # assertion is no gate at all -- that is exactly how the sysbench
    # power-of-two bug reached three hosts. This reads the source.
    #
    # disk.rand_read_8k_qd1.p99_us must be measured with ONE outstanding read.
    # The QD128 job next to it cannot answer the question its band asks: at a
    # fixed queue depth, Little's law pins latency to QD/IOPS, so that p99 is
    # ~1.07x (128/IOPS) across the corpus -- a restatement of the IOPS number,
    # not an independent tail. Only QD1 measures what one index lookup costs.
    # psync (not libaio) for the same reason wal_fsync uses it: Postgres reads
    # via pread, and the engine is part of what is being measured.
    script = (repo_root / "bench" / "01-disk.sh").read_text()
    job = [ln for ln in script.splitlines()
           if "rand-read-8k-qd1" in ln and ln.strip().startswith(("fio", "FIO"))]
    assert job, "01-disk.sh must run a rand-read-8k-qd1 job"
    line = job[0]
    assert "--iodepth=1" in line, f"QD1 read job must be iodepth=1: {line!r}"
    assert "--numjobs=1" in line, f"QD1 read job must be numjobs=1: {line!r}"
    assert "psync" in line, f"QD1 read job must use psync, as Postgres does: {line!r}"
