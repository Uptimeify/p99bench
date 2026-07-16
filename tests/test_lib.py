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
