import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import aggregate  # noqa: E402
from writers import index_rows, write_index_csv, write_index_json  # noqa: E402
from writers import write_provider_page  # noqa: E402

PROFILES = ["postgres_oltp", "timescale_ingest", "patroni_member",
            "redis_sentinel", "worker_probe", "playwright_node", "nuxt_ssr"]


def test_one_row_per_product_region():
    rows = index_rows(aggregate.load_all())
    keys = [(r["provider"], r["region"], r["product"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate product rows"
    assert ("ovh", "waw", "vps-1-lz-2026") in keys
    assert ("ovh", "zrh", "vps-1-lz-2026") in keys


def test_row_carries_categories_and_profiles():
    row = next(r for r in index_rows(aggregate.load_all()) if r["region"] == "zrh")
    assert set(row["categories"]) == {"disk", "cpu", "ram", "network"}
    assert set(row["profiles"]) == set(PROFILES)


def test_row_carries_the_flagship_number_for_sorting():
    # Letter grades tie constantly at scale. The index must stay sortable by the
    # number that decides whether a database is viable here.
    row = next(r for r in index_rows(aggregate.load_all()) if r["region"] == "zrh")
    assert isinstance(row["fsync_p999_us_worst"], (int, float))
    assert row["fsync_p999_us_worst"] > 100000  # zrh really is that bad


def test_waw_and_zrh_are_distinguishable_in_the_export():
    # The redesign's reason to exist, at the export layer: same product, same
    # price, opposite failures. If a consumer of index.json cannot tell them
    # apart, the export has flattened the thing the project exists to surface.
    rows = {r["region"]: r for r in index_rows(aggregate.load_all())
            if r["product"] == "vps-1-lz-2026"}
    waw, zrh = rows["waw"], rows["zrh"]
    assert waw["storage_class"] == "net-fast"
    assert zrh["storage_class"] == "net-slow"
    assert waw["categories"]["cpu"] == "F"      # CPU-bound
    assert zrh["categories"]["disk"] == "F"     # disk-bound
    assert waw["categories"]["disk"] != zrh["categories"]["disk"]


def test_json_export_is_valid_and_stable():
    rows = index_rows(aggregate.load_all())
    body = write_index_json(rows)
    parsed = json.loads(body)
    assert parsed["bands_version"]
    assert len(parsed["results"]) == len(rows)
    assert write_index_json(rows) == body, "export is not deterministic"


def test_csv_export_has_a_header_and_flattens_grades():
    rows = index_rows(aggregate.load_all())
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
    runs = aggregate.load_all()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    for region in ("prg", "waw", "zrh"):
        assert region in page
    # host_id links runs on one VM together; the page must expose it, because
    # "this machine is inconsistent" and "this provider's machines are
    # inconsistent" are different findings.
    assert "c7d6f7" in page


def test_provider_page_reports_variance_separately():
    runs = aggregate.load_all()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    low = page.lower()
    assert "machine" in low
    assert "worst" in low
    assert "mean" not in low, "a mean leaked into a provider page"


def test_provider_page_names_the_binding_constraint():
    # A grade without its binding constraint is a letter with no lead. The page
    # must say WHAT bound it, which is the information the old single-word
    # verdict could never carry.
    runs = aggregate.load_all()
    page = write_provider_page("ovh", [r for r in runs if r["provider"]["name"] == "ovh"])
    assert "wal_fsync.p999_us" in page   # binds zrh
    assert "single_thread_eps" in page   # binds waw


def test_provider_pages_land_beside_the_raw_data():
    from writers import provider_pages
    pages = provider_pages(aggregate.load_all())
    paths = {str(p.relative_to(ROOT)) for p in pages}
    assert "results/ovh/README.md" in paths
    assert "results/hetzner/README.md" in paths


def test_provider_pages_are_invisible_to_the_result_glob():
    # README.md inside results/ is safe precisely because the validator globs
    # *.json. Being in-tree is the point: GitHub renders it when browsing the
    # directory.
    assert not list((ROOT / "results").rglob("README.md*.json"))
    from writers import provider_pages
    assert all(p.name == "README.md" for p in provider_pages(aggregate.load_all()))


def test_index_is_compact_and_links_to_provider_pages():
    from writers import write_index_md
    body = write_index_md(index_rows(aggregate.load_all()))
    assert "results/ovh/README.md" in body or "results/ovh" in body
    # The index must not carry per-run detail -- that is what made one flat file
    # unreviewable at scale.
    assert "<details>" not in body


def test_index_row_shows_categories_profiles_and_class():
    from writers import write_index_md
    body = write_index_md(index_rows(aggregate.load_all()))
    for col in ("disk", "cpu", "ram", "net", "Class"):
        assert col in body


def test_index_shows_waw_and_zrh_as_different_machines():
    # The acceptance test for the whole redesign, at the index layer. Under v1
    # these two rows -- same product, same price -- both read `fail fail fail
    # fail`. If the index cannot separate them, nothing downstream can.
    from writers import write_index_md
    body = write_index_md(index_rows(aggregate.load_all()))
    rows = [ln for ln in body.splitlines() if "vps-1-lz-2026" in ln]
    waw = next(ln for ln in rows if "| waw " in ln)
    zrh = next(ln for ln in rows if "| zrh " in ln)
    assert waw != zrh
    assert "net-fast" in waw and "net-slow" in zrh


def test_index_carries_no_mean():
    from writers import write_index_md
    body = write_index_md(index_rows(aggregate.load_all())).lower()
    assert "mean" not in body
    assert "average" not in body
