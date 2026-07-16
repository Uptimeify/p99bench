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
