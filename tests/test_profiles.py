import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from grade import compute  # noqa: E402
from conftest import CORPUS_DIR  # noqa: E402

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
    # Spec 9.3, updated for the worst-measured-is-a-lower-bound rewrite. The
    # existing corpus keeps fully answering the disk-bound database questions
    # it was built for (postgres_oltp, timescale_ingest: nothing required is
    # missing). The five profiles that DO read a metric this v1 tool never
    # collected (cpu.stall_p999_us and friends) no longer fabricate a "?" --
    # they carry a real grade off what WAS measured (fsync, single-thread eps,
    # RAM size, network jitter, ...) and are flagged `incomplete` instead. This
    # is the honest version of "declines the questions it never measured": say
    # what is known, and say plainly that more could still lower it. Silently
    # passing would be the old failure this redesign fixed; silently hiding a
    # known floor behind "?" was the NEW failure this rewrite fixes.
    doc = json.loads(
        (CORPUS_DIR / "hetzner" / "hel-1" / "2026-07-16T1012-cpx32.json").read_text()
    )
    g = compute(doc, THRESHOLDS)["profiles"]

    assert g["postgres_oltp"]["grade"] != "?"
    # postgres_oltp went incomplete on 2026-07-17: it requires
    # disk.rand_read_8k_qd1.p99_us, and no run has ever measured it (the QD1
    # read job is new in tool 0.2.1). The grade is still a real letter off
    # everything else -- that is the point of the rollup -- but it is now
    # honestly a floor. It stops being incomplete when 0.2.1 runs land, not
    # when a test is edited.
    assert g["postgres_oltp"]["incomplete"] is True
    assert "disk.rand_read_8k_qd1.p99_us" in g["postgres_oltp"]["missing"]
    # timescale_ingest never read a random-read metric -- unaffected.
    assert g["timescale_ingest"]["grade"] != "?"
    assert g["timescale_ingest"]["incomplete"] is False

    for name in ("patroni_member", "redis_sentinel", "worker_probe",
                 "playwright_node", "nuxt_ssr"):
        assert g[name]["grade"] != "?", (
            f"{name} grades '?' despite measured metrics -- "
            "the worst-measured floor was discarded"
        )
        assert g[name]["incomplete"] is True, f"{name} should flag missing cpu.stall_p999_us"
        assert "cpu.stall_p999_us" in g[name]["missing"]
        assert "reason" not in g[name], f"{name} is not '?', so it must carry no reason"


def test_ovh_waw_and_zrh_read_as_opposite_failures():
    # Spec 2.5 -- the whole point. Same product, same price, opposite failures.
    # Today both render as `fail fail fail fail`, indistinguishable. If this
    # test ever passes trivially (both categories the same grade), the redesign
    # has lost its reason to exist.
    waw = json.loads(
        (CORPUS_DIR / "ovh" / "waw" / "2026-07-16T1017-vps-1-lz-2026.json").read_text()
    )
    zrh = json.loads(
        (CORPUS_DIR / "ovh" / "zrh" / "2026-07-16T1024-vps-1-lz-2026.json").read_text()
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
