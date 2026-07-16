"""render.py's "stall" columns must read cpu.stall_p999_us, not the legacy
redis-cli field.

Field spec 5.1 killed cpu.intrinsic_latency_max_us for measuring "is this a
VM?" and spec 9.2 renamed the replacement to cpu.stall_* specifically because
"reusing the old names would put two different metrics in one column -- the
same incomparability the project already refuses". render.py's "stall
max"/"stall worst" columns fed from intrinsic_latency_max_us put the branch
back in that exact hole: v1 rows show redis-cli numbers and v2 rows show
nothing (v2 always emits intrinsic_latency_max_us: null) under one header.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import writers as R  # noqa: E402
from aggregate import spread  # noqa: E402


def _minimal_run(cpu):
    return {
        "run": {
            "host_id": "abcdef012345",
            "timestamp_utc": "2026-07-16T10:00:00Z",
            "local_hour": 10,
        },
        "disk": {},
        "cpu": cpu,
        "grades": {"profiles": {}},
    }


def test_run_row_reads_stall_p999_us():
    r = _minimal_run({"stall_p999_us": 555.0, "intrinsic_latency_max_us": None})
    row = R.render_run_row(r)
    assert "555 us" in row


def test_run_row_does_not_fall_back_to_legacy_intrinsic_latency():
    # A v1 result: no stall_p999_us was ever measured, but the legacy field
    # carries a real redis-cli number. The column must show "-", not silently
    # substitute the incomparable legacy metric to avoid an empty cell.
    r = _minimal_run({"stall_p999_us": None, "intrinsic_latency_max_us": 8888.0})
    row = R.render_run_row(r)
    assert "8.9 ms" not in row
    cells = [c.strip() for c in row.strip("| \n").split("|")]
    # Column order per render_run_row: Machine, Date, Hour, fsync p99.9,
    # rand-read p99, steal, stall, steady drop, then one cell per profile.
    stall_cell = cells[6]
    assert stall_cell == "-", f"expected '-' for an unmeasured stall, got {stall_cell!r}"


def test_summary_spread_reads_stall_p999_us_not_legacy_field():
    runs = [
        _minimal_run({"stall_p999_us": 100.0, "intrinsic_latency_max_us": None}),
        _minimal_run({"stall_p999_us": 200.0, "intrinsic_latency_max_us": None}),
        _minimal_run({"stall_p999_us": 9999.0, "intrinsic_latency_max_us": None}),
    ]
    _, worst = spread(runs, "cpu.stall_p999_us")
    assert worst == "10.0 ms"
