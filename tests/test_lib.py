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


# llc_bytes() -- last-level-cache size from sysfs, used by 03-ram.sh to size
# the RAM bandwidth working set. Lives in lib.sh (not 03-ram.sh) specifically
# so it can be unit tested here without a container: on this test host (and
# in the Docker test image) /sys/devices/system/cpu/cpu0/cache/index*/size
# does not exist, so the K/M-unit-conversion branch -- the code path that
# actually runs on every real x86 production host -- was previously verified
# only by hand-tracing. P99_CACHE_ROOT is the injection seam that lets these
# fixtures drive it deterministically.

FIXTURES = __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "cache"


def test_llc_bytes_parses_kilobytes(run_bash):
    out = run_bash("llc_bytes", env={"P99_CACHE_ROOT": str(FIXTURES / "single_32768k")})
    assert out.stdout == str(32 * 1024 * 1024)


def test_llc_bytes_parses_megabytes(run_bash):
    out = run_bash("llc_bytes", env={"P99_CACHE_ROOT": str(FIXTURES / "single_32m")})
    assert out.stdout == str(32 * 1024 * 1024)


def test_llc_bytes_parses_gigabytes(run_bash):
    # "G" branch added by this fix; previously only K and M were handled.
    out = run_bash("llc_bytes", env={"P99_CACHE_ROOT": str(FIXTURES / "gigabyte")})
    assert out.stdout == str(1024 * 1024 * 1024)


def test_llc_bytes_picks_largest_of_several_indexes(run_bash):
    # 32K, 512K, 32768K present (L1/L2/L3) -- must take the L3 (largest).
    out = run_bash("llc_bytes", env={"P99_CACHE_ROOT": str(FIXTURES / "multi")})
    assert out.stdout == str(32 * 1024 * 1024)


def test_llc_bytes_falls_back_when_directory_missing(run_bash):
    out = run_bash("llc_bytes", env={"P99_CACHE_ROOT": str(FIXTURES / "does_not_exist")})
    assert out.stdout == str(32 * 1024 * 1024)


def test_llc_bytes_skips_malformed_entries_without_crashing(run_bash):
    # One empty size file, one non-numeric one. Neither should crash or
    # contribute a bogus value; with no valid entry, falls back to 32M.
    out = run_bash("llc_bytes", env={"P99_CACHE_ROOT": str(FIXTURES / "malformed")})
    assert out.returncode == 0
    assert out.stdout == str(32 * 1024 * 1024)


def test_llc_bytes_warns_and_ignores_unrecognized_unit(run_bash):
    # Confirmed live before this fix: "32KB" -> 32 (should be 32768), because
    # the case-statement default stripped non-digit characters instead of
    # scaling, silently producing a too-small value -- the exact failure
    # class this parser exists to prevent. Now: warn and ignore the entry
    # rather than contribute a wrong number. Only entry present, so this
    # falls back to the pessimistic 32M default.
    out = run_bash("llc_bytes", env={"P99_CACHE_ROOT": str(FIXTURES / "unrecognized_unit")})
    assert out.stdout == str(32 * 1024 * 1024)
    assert "unrecognized" in out.stderr.lower()


def test_lib_forces_c_locale(run_bash):
    """Every stage parses English, dot-decimal tool output.

    On a German-locale Debian host mpstat prints "Durchschn.:" instead of
    "Average:" (so the summary row is never found) AND decimal commas -- "0,13"
    not "0.13" -- which jnum correctly rejects as non-numeric. Both are silent:
    cpu.steal_pct_under_load simply comes back null on a host that looks fine.
    steal is required by four profiles.

    Real failure, real host, found only because someone read the run output.
    """
    out = run_bash('printf "%s|%s" "$LC_ALL" "$LANG"')
    assert out.stdout == "C|C", (
        f"lib.sh must force the C locale; got {out.stdout!r}. Without it any "
        f"metric parsed from a localised tool silently nulls."
    )


def test_jnum_rejects_a_decimal_comma(run_bash):
    # Documents WHY the C locale matters: this is what a German-locale mpstat
    # hands the parser. Rejecting it is correct -- jnum cannot know whether the
    # comma is a decimal point or a thousands separator -- which is exactly why
    # the locale must be forced upstream rather than the parser widened.
    assert run_bash('jnum "0,13"').stdout == "null"

# ram_block_bytes() -- per-thread sysbench block for the RAM bandwidth run.
# Lives in lib.sh (not 03-ram.sh) so CI tests it: the container tests that
# cover 03-ram.sh are all @pytest.mark.docker, and CI runs -m "not docker",
# so the sizing arithmetic had no gate CI actually executes. It shipped a
# non-power-of-two block to three real hosts before anyone noticed.
#
# sysbench REQUIRES a power-of-two block. sb_memory.c, memory_init():
#   if (memory_block_size < SIZEOF_SIZE_T ||
#       (memory_block_size & (memory_block_size - 1)) != 0)
#     log_text(LOG_FATAL, "Invalid value for memory-block-size: %s", ...);
# A FATAL prints no "MiB/sec" line, so mem() returned empty and bw_read_mbs
# came back null -- on every host whose RAM cap trimmed the block off a
# power of two. Real shapes below, from the 2026-07-17 runs.

MIB = 1024 * 1024


def _block(run_bash, llc, cores, ram):
    return int(run_bash(f"ram_block_bytes {llc} {cores} {ram}").stdout)


def _is_pow2(n):
    return n > 0 and (n & (n - 1)) == 0


def test_ram_block_is_power_of_two_hetzner_cpx32(run_bash):
    # 4 vCPU / 7756 MB, 32 MiB L3. The old RAM/4 cap produced 508355840
    # (484.8 MiB, not a power of two) -> sysbench FATAL -> bw_read_mbs null.
    block = _block(run_bash, 32 * MIB, 4, 8133693440)
    assert _is_pow2(block), f"{block} is not a power of two; sysbench will FATAL"
    assert block == 512 * MIB


def test_ram_block_is_power_of_two_ovh_vps(run_bash):
    # 4 vCPU / 7946 MB, 64 MiB L3. Old cap gave 520807936. Same failure.
    block = _block(run_bash, 64 * MIB, 4, 8332926976)
    assert _is_pow2(block), f"{block} is not a power of two; sysbench will FATAL"
    assert block == 512 * MIB


def test_ram_block_is_power_of_two_across_odd_host_shapes(run_bash):
    # MemTotal is never a round number (firmware reserves an arbitrary
    # slice), so the cap must never be trusted to land on a power of two.
    for ram_mb in (1993, 2047, 3971, 7756, 7946, 11991, 16301, 32612):
        for cores in (1, 2, 4, 8, 16):
            block = _block(run_bash, 32 * MIB, cores, ram_mb * MIB)
            assert _is_pow2(block) or block == 0, \
                f"ram_mb={ram_mb} cores={cores} -> {block}, not a power of two"


def test_ram_block_never_exceeds_half_of_ram(run_bash):
    # The cap exists so the working set cannot swap; a swapping run measures
    # the disk. Half of RAM, per the sizing policy: 4 x 512M = 2 GiB on a
    # 7.57 GiB host is 26%.
    for ram_mb in (2047, 7756, 11991):
        for cores in (1, 2, 4, 8, 16):
            block = _block(run_bash, 32 * MIB, cores, ram_mb * MIB)
            assert block * cores <= (ram_mb * MIB) // 2, \
                f"ram_mb={ram_mb} cores={cores}: working set exceeds RAM/2"


def test_ram_block_keeps_512m_floor_when_it_fits(run_bash):
    # 512M is not arbitrary: a CPX32 reports 98 GB/s at 128M and 66 GB/s at
    # both 512M and 1G, so 128M over-reports by ~48%. It converges at 512M.
    assert _block(run_bash, 32 * MIB, 4, 64 * 1024**3) == 512 * MIB


def test_ram_block_tracks_llc_when_llc_is_large(run_bash):
    # 4x LLC, when that exceeds the floor. 256 MiB L3 -> 1 GiB block.
    assert _block(run_bash, 256 * MIB, 2, 64 * 1024**3) == 1024 * MIB


def test_ram_block_rounds_up_a_non_power_of_two_llc(run_bash):
    # lscpu reports whatever CPUID says, and a shared-L3 guest can report an
    # odd slice (e.g. 96 MiB). 4x = 384M must round UP to 512M, never down --
    # rounding down would put the working set back inside the cache it is
    # sized to escape.
    assert _block(run_bash, 96 * MIB, 2, 64 * 1024**3) == 512 * MIB


def test_ram_block_shrinks_below_the_floor_rather_than_lying(run_bash):
    # 8 vCPU / 2 GiB is a common budget-VPS shape: RAM/2/8 caps at 128M, well
    # under the 512M floor. Return the capped power of two anyway and let the
    # caller's cache guard decide -- this function's job is "largest safe
    # power of two", not "is this measurement meaningful".
    assert _block(run_bash, 32 * MIB, 8, 2 * 1024**3) == 128 * MIB


def test_ram_block_is_zero_when_no_legal_block_exists(run_bash):
    # sysbench also rejects a block smaller than sizeof(size_t). A shape this
    # degenerate cannot be measured at all, so say so with 0 rather than
    # emitting a block sysbench will FATAL on.
    assert _block(run_bash, 32 * MIB, 16, 64) == 0


# median() -- used by 06-network.sh for dns_warm_p50_ms. It lives in lib.sh so
# CI tests it: the network stage needs real egress, which neither the test
# container nor a dev sandbox has, so its own smoke test only runs in CI. The
# arithmetic must not be the part that is untested -- that is precisely how the
# sysbench power-of-two bug and the 1K NUMA default both reached real hosts.

def _median(run_bash, values):
    return run_bash(f'printf "%s\\n" {values} | median').stdout


def test_median_of_five(run_bash):
    assert _median(run_bash, "5 1 3 2 4") == "3"


def test_median_sorts_numerically_not_lexically(run_bash):
    # A lexical sort puts "100" before "9", which would report 100 as the
    # median of these five. DNS timings routinely straddle that boundary
    # (0.8ms cached vs 149ms cold), so this is not hypothetical.
    assert _median(run_bash, "9 100 8 7 6") == "8"


def test_median_of_one(run_bash):
    assert _median(run_bash, "42.5") == "42.5"


def test_median_of_empty_is_empty(run_bash):
    # Every lookup failed. Print nothing; jnum turns that into null.
    assert run_bash('printf "" | median').stdout == ""


def test_median_ignores_non_numeric_lines(run_bash):
    # A failed curl contributes nothing, but a changed -w format could emit a
    # word. Same doctrine as jnum: drop it rather than average it in.
    assert _median(run_bash, "2 oops 1 3") == "2"


def test_median_of_even_count_takes_the_lower_middle(run_bash):
    # Documented choice: no interpolation, so the reported value is always one
    # that was actually measured.
    assert _median(run_bash, "1 2 3 4") == "2"


# numa_node_cpus() -- how many CPUs one NUMA node has. Drives the thread count
# for the locality test in 03-ram.sh.
#
# Why this exists: --cpunodebind=0 restricts the run to node 0's CPUs, so
# asking for CORES threads oversubscribes every multi-node host. On windcloud
# (4 vCPU / 2 nodes) it ran 4 threads on 2 cores, and both the local and the
# remote measurement came back CPU-bound at ~17,400 MiB/s -- about half the
# unpinned 30,763 -- which compressed the local/remote gap toward zero and
# made the NUMA penalty unmeasurable. A test that cannot see the thing it
# measures is the same failure as measuring cache and calling it RAM.
#
# P99_NUMA_HW is the injection seam: real runs read `numactl --hardware`.

HW_2NODE = """available: 2 nodes (0-1)
node 0 cpus: 0 1
node 0 size: 5995 MB
node 0 free: 5012 MB
node 1 cpus: 2 3
node 1 size: 5996 MB
node 1 free: 5533 MB
node distances:
node   0   1
  0:  10  20
  1:  20  10"""

HW_BIG = """available: 2 nodes (0-1)
node 0 cpus: 0 1 2 3 4 5 6 7 8 9 10 11
node 0 size: 64318 MB
node 1 cpus: 12 13 14 15 16 17 18 19 20 21 22 23
node 1 size: 64502 MB"""


def test_numa_node_cpus_counts_one_nodes_cpus(run_bash, tmp_path):
    hw = tmp_path / "hw2"; hw.write_text(HW_2NODE)
    out = run_bash("numa_node_cpus 0", env={"P99_NUMA_HW": str(hw)})
    assert out.stdout == "2", "node 0 has cpus 0 and 1 -- exactly 2"


def test_numa_node_cpus_reads_the_requested_node(run_bash, tmp_path):
    hw = tmp_path / "hw2"; hw.write_text(HW_2NODE)
    assert run_bash("numa_node_cpus 1", env={"P99_NUMA_HW": str(hw)}).stdout == "2"


def test_numa_node_cpus_handles_a_wide_node(run_bash, tmp_path):
    # 12 CPUs on one line -- must count, not take the last id or the line count.
    hw = tmp_path / "hwbig"; hw.write_text(HW_BIG)
    assert run_bash("numa_node_cpus 0", env={"P99_NUMA_HW": str(hw)}).stdout == "12"


def test_numa_node_cpus_is_zero_when_the_node_is_absent(run_bash, tmp_path):
    # Caller must be able to tell "no such node" from "one CPU".
    hw = tmp_path / "hw2"; hw.write_text(HW_2NODE)
    assert run_bash("numa_node_cpus 7", env={"P99_NUMA_HW": str(hw)}).stdout == "0"
