import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import aggregate  # noqa: E402
from writers import index_rows, write_index_csv, write_index_json  # noqa: E402
from writers import write_provider_page  # noqa: E402
from conftest import load_corpus  # noqa: E402

PROFILES = ["postgres_oltp", "timescale_ingest", "patroni_member",
            "redis_sentinel", "worker_probe", "playwright_node", "nuxt_ssr"]


def test_one_row_per_product_region():
    rows = index_rows(load_corpus())
    keys = [(r["provider"], r["region"], r["product"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate product rows"
    assert ("ovh", "waw", "vps-1-lz-2026") in keys
    assert ("ovh", "zrh", "vps-1-lz-2026") in keys


def test_row_carries_categories_and_profiles():
    row = next(r for r in index_rows(load_corpus()) if r["region"] == "zrh")
    assert set(row["categories"]) == {"disk", "cpu", "ram", "network"}
    assert set(row["profiles"]) == set(PROFILES)


def test_row_carries_the_flagship_number_for_sorting():
    # Letter grades tie constantly at scale. The index must stay sortable by the
    # number that decides whether a database is viable here.
    row = next(r for r in index_rows(load_corpus()) if r["region"] == "zrh")
    assert isinstance(row["fsync_p999_us_worst"], (int, float))
    assert row["fsync_p999_us_worst"] > 100000  # zrh really is that bad


def test_waw_and_zrh_are_distinguishable_in_the_export():
    # The redesign's reason to exist, at the export layer: same product, same
    # price, opposite failures. If a consumer of index.json cannot tell them
    # apart, the export has flattened the thing the project exists to surface.
    rows = {r["region"]: r for r in index_rows(load_corpus())
            if r["product"] == "vps-1-lz-2026"}
    waw, zrh = rows["waw"], rows["zrh"]
    assert waw["storage_class"] == "net-fast"
    assert zrh["storage_class"] == "net-slow"
    assert waw["categories"]["cpu"] == "F"      # CPU-bound
    assert zrh["categories"]["disk"] == "F"     # disk-bound
    assert waw["categories"]["disk"] != zrh["categories"]["disk"]


def test_json_export_is_valid_and_stable():
    rows = index_rows(load_corpus())
    body = write_index_json(rows)
    parsed = json.loads(body)
    assert parsed["bands_version"]
    assert len(parsed["results"]) == len(rows)
    assert write_index_json(rows) == body, "export is not deterministic"


def test_csv_export_has_a_header_and_flattens_grades():
    rows = index_rows(load_corpus())
    parsed = list(csv.DictReader(io.StringIO(write_index_csv(rows))))
    assert len(parsed) == len(rows)
    assert "cat_disk" in parsed[0] and "prof_postgres_oltp" in parsed[0]


def test_export_lives_outside_results_tree():
    # validate.py and render.py both discover result files with
    # rglob("*.json") over results/. A generated results/index.json would be
    # picked up and fail validation as a malformed result.
    from writers import DATA_DIR
    assert "results" not in DATA_DIR.parts


def test_provider_page_covers_every_region_and_machine():
    runs = load_corpus()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    for region in ("prg", "waw", "zrh"):
        assert region in page
    # host_id links runs on one VM together; the page must expose it, because
    # "this machine is inconsistent" and "this provider's machines are
    # inconsistent" are different findings.
    assert "c7d6f7" in page


def test_provider_page_reports_variance_separately():
    runs = load_corpus()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    low = page.lower()
    assert "machine" in low
    assert "worst" in low
    assert "mean" not in low, "a mean leaked into a provider page"


def test_provider_page_names_the_binding_constraint():
    # A grade without its binding constraint is a letter with no lead. The page
    # must say WHAT bound it, which is the information the old single-word
    # verdict could never carry.
    runs = load_corpus()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    assert "wal_fsync.p999_us" in page   # binds zrh
    assert "single_thread_eps" in page   # binds waw


def test_provider_pages_land_beside_the_raw_data():
    from writers import provider_pages
    pages = provider_pages(load_corpus())
    paths = {str(p.relative_to(ROOT)) for p in pages}
    assert "results/ovh/README.md" in paths
    assert "results/hetzner/README.md" in paths


def test_provider_pages_are_invisible_to_the_result_glob():
    # README.md inside results/ is safe precisely because the validator globs
    # *.json. Being in-tree is the point: GitHub renders it when browsing the
    # directory.
    assert not list((ROOT / "results").rglob("README.md*.json"))
    from writers import provider_pages
    assert all(p.name == "README.md" for p in provider_pages(load_corpus()))


def test_index_is_compact_and_links_to_provider_pages():
    from writers import write_index_md
    body = write_index_md(index_rows(load_corpus()))
    assert "results/ovh/README.md" in body or "results/ovh" in body
    # The index must not carry per-run detail -- that is what made one flat file
    # unreviewable at scale.
    assert "<details>" not in body


def test_index_row_shows_categories_profiles_and_class():
    from writers import write_index_md
    body = write_index_md(index_rows(load_corpus()))
    for col in ("disk", "cpu", "ram", "net", "Class"):
        assert col in body


def test_index_shows_waw_and_zrh_as_different_machines():
    # The acceptance test for the whole redesign, at the index layer. Under v1
    # these two rows -- same product, same price -- both read `fail fail fail
    # fail`. If the index cannot separate them, nothing downstream can.
    from writers import write_index_md
    body = write_index_md(index_rows(load_corpus()))
    rows = [ln for ln in body.splitlines() if "vps-1-lz-2026" in ln]
    waw = next(ln for ln in rows if "| waw " in ln)
    zrh = next(ln for ln in rows if "| zrh " in ln)
    assert waw != zrh
    assert "net-fast" in waw and "net-slow" in zrh


def test_index_carries_no_mean():
    from writers import write_index_md
    body = write_index_md(index_rows(load_corpus())).lower()
    assert "mean" not in body
    assert "average" not in body


# --------------------------------------------------------------------------
# Network detail belongs on provider pages (fixing the Phase 3 data-loss bug)
# --------------------------------------------------------------------------

def test_provider_page_has_per_target_network_table():
    # Every provider page must render the per-target throughput/RTT table --
    # this is the detail RESULTS.md's "## Network" section claims lives here.
    runs = load_corpus()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    for target in ("hetzner-fsn1", "hetzner-hel1", "ovh-gra", "hetzner-ash"):
        assert target in page, f"target {target} missing from ovh provider page"
    assert "Mb/s" in page or "Gb/s" in page


def test_provider_page_does_not_skip_unreachable_targets():
    # reachable: false is an HTTP-status flag, not "nothing measured" -- such
    # a target can still carry a real dns_ms/rtt_p50_ms/loss_pct. A Phase 2
    # bug skipped these rows entirely, which could only ever flatter a grade.
    # All 10 published results have exactly this on ovh-gra: mbps is null but
    # RTT is real, so the honest cell is "-" throughput beside a real RTT --
    # never a dropped row.
    runs = load_corpus()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    for section in page.split("## "):
        if not section.startswith(("prg", "waw", "zrh")):
            continue
        assert "ovh-gra" in section, "ovh-gra row dropped from a product's network table"
        # the row must carry a real RTT, not just a bare throughput dash with
        # nothing beside it
        line = next(ln for ln in section.splitlines() if "ovh-gra" in ln)
        assert "ms" in line, f"ovh-gra row lost its RTT: {line!r}"


def test_provider_page_reports_packet_loss():
    # This is the exact measurement design spec 6.5 uses to DERIVE the
    # network.loss_pct band (an ICMP check false-alarms at p^3). The corpus's
    # only real network outlier must be visible on the provider page.
    runs = load_corpus()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    assert "10.00%" in page or "10.0%" in page
    assert "hetzner-ash" in page
    # scoped to the zrh section specifically
    zrh_section = page.split("## zrh")[1].split("## ")[0]
    assert "10.00%" in zrh_section or "10.0%" in zrh_section


def test_provider_page_network_table_has_no_mean():
    runs = load_corpus()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    low = page.lower()
    assert "mean" not in low
    assert "average" not in low


def test_results_md_network_pointer_is_true():
    # RESULTS.md's "## Network" section points readers at the provider pages
    # for per-target detail -- that claim must actually be true.
    from writers import write_index_md
    runs = load_corpus()
    body = write_index_md(index_rows(runs))
    network_section = body.split("## Network")[1].split("## ")[0]
    assert "provider" in network_section.lower() or "results/" in network_section
    # and the detail must genuinely exist on the ovh page
    ovh_page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    assert "hetzner-ash" in ovh_page
    assert "10.00%" in ovh_page or "10.0%" in ovh_page
