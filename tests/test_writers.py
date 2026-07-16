import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import aggregate  # noqa: E402
from writers import index_rows, write_index_csv, write_index_json  # noqa: E402

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
