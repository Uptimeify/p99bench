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
