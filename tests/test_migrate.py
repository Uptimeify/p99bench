import copy
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from migrate_v1_v2 import MEASURED_SECTIONS, migrate  # noqa: E402

THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())

# A frozen snapshot of results/hetzner/hel-1/2026-07-16T1012-cpx32.json as it
# was BEFORE this task migrated it (schema_version "1.0", "verdict" present).
# Deliberately NOT read from results/ at test time: this task's own migration
# rewrites that file in place, so a live read would go v1 -> v2 the moment
# `tools/migrate_v1_v2.py results/` is applied, and this test would then
# assert `migrate() is True` against a document that is already migrated.
# Freezing the pre-migration shape here keeps the test meaningful forever,
# independent of the corpus's current on-disk state.
V1_FIXTURE = json.loads(r"""
{
  "schema_version": "1.0",
  "run": {
    "timestamp_utc": "2026-07-16T10:12:50Z",
    "host_id": "ffe9c660efdd",
    "local_hour": 10,
    "duration_s": 2542,
    "tool_version": "0.1.0",
    "submitter": "uptimeify",
    "notes": null
  },
  "provider": {
    "name": "hetzner",
    "product": "CPX32",
    "region": "hel-1",
    "price_eur_month": 42.23,
    "billing": "monthly",
    "storage_tier": null
  },
  "app": null,
  "cpu": {
    "single_thread_eps": 1661.19,
    "multi_thread_eps": 6496.39,
    "scaling_efficiency": 0.977,
    "clock_idle_mhz": 2400,
    "clock_under_load_mhz": 2400,
    "steal_pct_under_load": 0.0,
    "aes_256_gcm_mbs": 37717.72,
    "sha256_mbs": 6526.94,
    "intrinsic_latency_max_us": 1642,
    "intrinsic_latency_avg_us": 0.0565
  },
  "disk": {
    "device_model": "QEMU HARDDISK",
    "scheduler": "none",
    "rotational": false,
    "target_fs": "ext4",
    "is_boot_volume": true,
    "seq_read": {
      "bw_mbs": 7439.39,
      "iops": 7439.39,
      "p99_us": 7241.73
    },
    "seq_write": {
      "bw_mbs": 5493.2,
      "iops": 5493.2,
      "p99_us": 9764.86
    },
    "rand_read_8k": {
      "iops": 79240.2,
      "bw_mbs": 619.06,
      "p50_us": 1581.06,
      "p99_us": 2506.75,
      "p999_us": 3391.49
    },
    "rand_write_8k": {
      "iops": 68172.38,
      "bw_mbs": 532.6,
      "p50_us": 1712.13,
      "p99_us": 3489.79,
      "p999_us": 17694.72
    },
    "mixed_8k": {
      "iops": 51139.75,
      "bw_mbs": 399.53,
      "p50_us": 1679.36,
      "p99_us": 3063.81,
      "p999_us": 4358.14
    },
    "wal_fsync": {
      "iops": 1386.21,
      "avg_us": 653.32,
      "p50_us": 643.07,
      "p99_us": 987.14,
      "p999_us": 1875.97,
      "max_us": 12006.89
    },
    "steady_state": {
      "duration_s": 1800,
      "first_min_iops": 71973.13,
      "last_min_iops": 71661.63,
      "degradation_pct": 0.43,
      "p99_us_first_min": 2159.96,
      "p99_us_last_min": 2453.78
    }
  },
  "host": {
    "virt": "kvm",
    "vcpu": 4,
    "ram_mb": 7756,
    "cpu_model": "AMD EPYC-Genoa Processor",
    "cpu_governor": null,
    "numa_nodes": 1,
    "ecc_claimed": "Multi-bit ECC",
    "ecc_verifiable": false,
    "kernel": "6.12.95+deb13-cloud-amd64",
    "distro": "debian-13"
  },
  "network": {
    "reachable": true,
    "target_list_version": "1",
    "targets": [
      {
        "id": "hetzner-fsn1",
        "reachable": true,
        "mbps": 783.1,
        "ttfb_ms": 108.808,
        "dns_ms": 1.31,
        "rtt_p50_ms": 25.2,
        "rtt_p99_ms": 25.4,
        "loss_pct": 0
      },
      {
        "id": "hetzner-hel1",
        "reachable": true,
        "mbps": 5817.66,
        "ttfb_ms": 22.276,
        "dns_ms": 1.266,
        "rtt_p50_ms": 0.474,
        "rtt_p99_ms": 0.687,
        "loss_pct": 0
      },
      {
        "id": "ovh-gra",
        "reachable": false,
        "mbps": 0,
        "ttfb_ms": 182.367,
        "dns_ms": 58.475,
        "rtt_p50_ms": 28.9,
        "rtt_p99_ms": 29.0,
        "loss_pct": 0
      },
      {
        "id": "hetzner-ash",
        "reachable": true,
        "mbps": 189.84,
        "ttfb_ms": 533.491,
        "dns_ms": 52.21,
        "rtt_p50_ms": 117,
        "rtt_p99_ms": 117,
        "loss_pct": 0
      }
    ],
    "ookla": null
  },
  "ram": {
    "configured_speed": "Unknown",
    "type": "RAM",
    "populated_slots": 1,
    "seq_read_mbs": 207409.12,
    "seq_write_mbs": 15201.99,
    "rnd_read_mbs": 15702.28,
    "rnd_write_mbs": 1106.48,
    "numa_local_mbs": null,
    "numa_remote_mbs": null
  },
  "verdict": {
    "reasons": [
      "[postgres_oltp] disk.wal_fsync.iops = 1386.21 < 5000",
      "[postgres_oltp] disk.rand_read_8k.p99_us = 2506.75 (marginal, want <= 1000)",
      "[postgres_oltp] disk.rand_read_8k.iops = 79240.2 (marginal, want >= 100000)",
      "[redis_aof] cpu.intrinsic_latency_max_us = 1642 > 1000",
      "[nuxt_ssr] cpu.intrinsic_latency_max_us = 1642 (marginal, want <= 1000)"
    ],
    "postgres_oltp": "fail",
    "timescale_ingest": "pass",
    "redis_aof": "fail",
    "nuxt_ssr": "marginal"
  }
}
""")


def _v1():
    return copy.deepcopy(V1_FIXTURE)


def test_migrate_replaces_verdict_with_grades_and_bumps_version():
    doc = _v1()
    assert migrate(doc, THRESHOLDS) is True
    assert doc["schema_version"] == "2.0"
    assert "verdict" not in doc
    assert doc["grades"]["bands_version"] == THRESHOLDS["bands_version"]


def test_migrate_changes_no_measured_number():
    # The contract of spec 9.1: measurements are immutable, grades are derived.
    # A migration that quietly edited a measured value would destroy the one
    # thing this repo is for.
    before = _v1()
    after = copy.deepcopy(before)
    migrate(after, THRESHOLDS)
    for section in MEASURED_SECTIONS:
        assert after.get(section) == before.get(section), f"{section} was altered"


def test_migrate_is_idempotent():
    doc = _v1()
    migrate(doc, THRESHOLDS)
    once = copy.deepcopy(doc)
    assert migrate(doc, THRESHOLDS) is False
    assert doc == once
