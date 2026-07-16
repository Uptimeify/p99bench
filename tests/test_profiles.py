import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from grade import compute  # noqa: E402

THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())

EXPECTED = {
    "postgres_oltp", "timescale_ingest", "patroni_member", "redis_sentinel",
    "worker_probe", "playwright_node", "nuxt_ssr",
}
# Spec 6.5: a network metric may carry a threshold ONLY in a profile whose
# workload IS the network. Everything else reads no network field.
NETWORK_READERS = {"worker_probe", "playwright_node"}


def test_all_seven_profiles_present():
    assert set(THRESHOLDS["profiles"]) == EXPECTED


def test_every_profile_rule_references_a_defined_metric():
    for name, p in THRESHOLDS["profiles"].items():
        for rule in p["rules"]:
            assert rule["metric"] in THRESHOLDS["metrics"], (
                f"{name} references undefined metric {rule['metric']}"
            )


def test_only_worker_profiles_read_network():
    # The doctrine narrowing, made executable. THRESHOLDS.md rejects network
    # thresholds because "a database host needs 500 Mbit/s" cannot be derived
    # from any workload requirement. That reasoning holds for a database and
    # collapses for a probe, where the network IS the workload -- false-alarm
    # rate is a computable function of packet loss. This test stops the
    # exception from quietly spreading back to the database profiles.
    for name, p in THRESHOLDS["profiles"].items():
        reads_net = any(r["metric"].startswith("network.") for r in p["rules"])
        assert reads_net == (name in NETWORK_READERS), (
            f"{name}: network rules are only legitimate where the workload is "
            f"the network itself (spec 6.5)"
        )


def test_cluster_profiles_declare_their_unmeasured_half():
    # Spec 7.3: commit latency in a sync Patroni cluster is local fsync +
    # inter-node RTT. We measure the first term only. A grade that silently
    # covers half an equation is worse than no grade.
    for name in ("patroni_member", "redis_sentinel"):
        assert THRESHOLDS["profiles"][name].get("network_half_unmeasured") is True


def test_v1_results_grade_postgres_and_timescale_but_not_the_new_profiles():
    # Spec 9.3. The existing corpus keeps answering the disk-bound database
    # questions it was built for, and honestly declines the CPU-sustained and
    # stall questions it never measured. Fabricating a playwright grade from
    # data that never measured a playwright workload is the failure this whole
    # redesign exists to correct.
    doc = json.loads(
        (ROOT / "results" / "hetzner" / "hel-1" / "2026-07-16T1012-cpx32.json").read_text()
    )
    g = compute(doc, THRESHOLDS)["profiles"]

    assert g["postgres_oltp"]["grade"] != "?"
    assert g["timescale_ingest"]["grade"] != "?"
    for name in ("patroni_member", "redis_sentinel", "worker_probe",
                 "playwright_node", "nuxt_ssr"):
        assert g[name]["grade"] == "?", f"{name} graded from data it never had"
        assert "re-run" in g[name]["reason"]


def test_ovh_waw_and_zrh_read_as_opposite_failures():
    # Spec 2.5 -- the whole point. Same product, same price, opposite failures.
    # Today both render as `fail fail fail fail`, indistinguishable. If this
    # test ever passes trivially (both categories the same grade), the redesign
    # has lost its reason to exist.
    waw = json.loads(
        (ROOT / "results" / "ovh" / "waw" / "2026-07-16T1017-vps-1-lz-2026.json").read_text()
    )
    zrh = json.loads(
        (ROOT / "results" / "ovh" / "zrh" / "2026-07-16T1024-vps-1-lz-2026.json").read_text()
    )
    waw_g = compute(waw, THRESHOLDS)["categories"]
    zrh_g = compute(zrh, THRESHOLDS)["categories"]

    # waw: healthy disk, broken CPU (single_thread_eps 356 vs ~1600)
    assert waw_g["cpu"]["bound_by"] == "cpu.single_thread_eps"
    assert waw_g["cpu"]["grade"] == "F"
    # zrh: fine CPU, destroyed disk (fsync p99.9 137ms)
    assert zrh_g["disk"]["bound_by"] == "disk.wal_fsync.p999_us"
    assert zrh_g["disk"]["grade"] == "F"
    # The distinction the old suite could not express:
    assert waw_g["disk"]["grade"] != waw_g["cpu"]["grade"]
