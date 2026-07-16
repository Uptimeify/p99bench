"""Schema-layer regression test for validate.py.

Phase 1 (05-latency.sh, 02-cpu.sh, 02b-cpu-steady.sh, 03-ram.sh) started
emitting cpu.stall_*, cpu.tls_verify_s/tls_sign_s, cpu.steady_state, and
ram.bw_read_mbs/bw_block_bytes, but schema/result.schema.json set
additionalProperties: false on cpu and ram without declaring any of them.
That meant tools/validate.py -- the same validator CI runs on every submitted
result -- rejected every 0.2.0-shaped result the branch could produce, naming
the very fields the run was meant to add. This never surfaced in
`python3 tools/validate.py results/` because results/ only ever held v1
files with none of the new fields present.

This test is the end-to-end check that was missing: build a synthetic
0.2.0-shaped result with the new fields populated and assert the schema
layer accepts it.
"""
import sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from validate import SCHEMA  # noqa: E402

# Minimal but complete v1 result, extended with every field Phase 1 added.
# Mirrors the shape of a real file (see results/ovh/zrh/2026-07-15T2119-*.json)
# so this exercises the same required-field skeleton real submissions use.
RESULT_020 = {
    "schema_version": "1.0",
    "run": {
        "timestamp_utc": "2026-07-16T10:00:00Z",
        "host_id": "abcdef012345",
        "local_hour": 10,
        "duration_s": 3600,
        "tool_version": "0.2.0",
        "submitter": "test",
        "notes": None,
    },
    "provider": {
        "name": "testcloud",
        "product": "t1.small",
        "region": "xx1",
        "price_eur_month": 5.0,
        "billing": "monthly",
        "storage_tier": None,
    },
    "app": None,
    "cpu": {
        "single_thread_eps": 1700.0,
        "multi_thread_eps": 6400.0,
        "scaling_efficiency": 0.94,
        "clock_idle_mhz": 3100,
        "clock_under_load_mhz": 3100,
        "steal_pct_under_load": 0.1,
        "aes_256_gcm_mbs": 37000.0,
        "sha256_mbs": 6800.0,
        # Legacy fields: always null now that redis-cli --intrinsic-latency
        # is gone, but must still validate for old/new schema compatibility.
        "intrinsic_latency_max_us": None,
        "intrinsic_latency_avg_us": None,
        # New in 0.2.0:
        "stall_p99_us": 900.0,
        "stall_p999_us": 1600.0,
        "stall_max_us": 4200.0,
        "stall_samples": 240000,
        "tls_verify_s": 12000.0,
        "tls_sign_s": 4000.0,
        "steady_state": {
            "duration_s": 900,
            "first_min_eps": 1700.0,
            "last_min_eps": 1650.0,
            "degradation_pct": 2.9,
            "steal_pct": 0.2,
        },
    },
    "disk": {
        "device_model": "QEMU HARDDISK",
        "scheduler": "none",
        "rotational": False,
        "target_fs": "ext4",
        "is_boot_volume": True,
        "seq_read": {"bw_mbs": 300.0, "iops": 300.0, "p99_us": 100000.0},
        "seq_write": {"bw_mbs": 300.0, "iops": 300.0, "p99_us": 160000.0},
        "rand_read_8k": {"iops": 7500.0, "bw_mbs": 58.0, "p50_us": 16000.0,
                          "p99_us": 18000.0, "p999_us": 19000.0},
        "rand_write_8k": {"iops": 6200.0, "bw_mbs": 48.0, "p50_us": 17000.0,
                           "p99_us": 42000.0, "p999_us": 1600000.0},
        "mixed_8k": {"iops": 7500.0, "bw_mbs": 58.0, "p50_us": 16000.0,
                     "p99_us": 18000.0, "p999_us": 18700.0},
        "wal_fsync": {"iops": 214.0, "avg_us": 4600.0, "p50_us": 3750.0,
                      "p99_us": 13400.0, "p999_us": 118000.0, "max_us": 405000.0},
        "steady_state": {"duration_s": 1800, "first_min_iops": 10700.0,
                          "last_min_iops": 10700.0, "degradation_pct": 0.35,
                          "p99_us_first_min": 17000.0, "p99_us_last_min": 17000.0},
    },
    "host": {
        "virt": "microsoft",
        "vcpu": 4,
        "ram_mb": 7946,
        "cpu_model": "AMD EPYC-Genoa Processor",
        "cpu_governor": None,
        "numa_nodes": 1,
        "ecc_claimed": "Multi-bit ECC",
        "ecc_verifiable": False,
        "kernel": "6.12.95+deb13-cloud-amd64",
        "distro": "debian-13",
    },
    "network": {
        "reachable": True,
        "target_list_version": "1",
        "targets": [],
        "ookla": None,
    },
    "ram": {
        "configured_speed": "Unknown",
        "type": "RAM",
        "populated_slots": 1,
        "seq_read_mbs": 210000.0,
        "seq_write_mbs": 17000.0,
        "rnd_read_mbs": 15500.0,
        "rnd_write_mbs": 879.0,
        "numa_local_mbs": None,
        "numa_remote_mbs": None,
        # New in 0.2.0:
        "bw_read_mbs": 42000.0,
        "bw_block_bytes": 134217728,
    },
    "verdict": None,
}


def test_020_shaped_result_validates_against_schema():
    validator = jsonschema.Draft202012Validator(SCHEMA)
    errors = sorted(validator.iter_errors(RESULT_020), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors
    )


def test_020_new_cpu_and_ram_fields_are_declared_not_just_permissively_typed():
    # Guard against a schema that merely turned additionalProperties off/on
    # without actually declaring the fields -- assert each new field has its
    # own property definition, not just tolerance via additionalProperties.
    cpu_props = SCHEMA["properties"]["cpu"]["properties"]
    for key in ("stall_p99_us", "stall_p999_us", "stall_max_us",
                "stall_samples", "tls_verify_s", "tls_sign_s", "steady_state"):
        assert key in cpu_props, f"cpu.{key} not declared in result.schema.json"

    ram_props = SCHEMA["properties"]["ram"]["properties"]
    for key in ("bw_read_mbs", "bw_block_bytes"):
        assert key in ram_props, f"ram.{key} not declared in result.schema.json"
