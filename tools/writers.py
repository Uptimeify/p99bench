"""Render results/**/*.json into the compact index, the machine-readable
export, and the per-provider detail pages.

render.py owns RESULTS.md's overall shape (the front door); this module owns
everything RESULTS.md links out to:

  * data/index.json, data/index.csv -- the machine-readable export. Lives in
    data/, not results/: validate.py and render.py both discover result
    files with rglob("*.json") over results/, so a generated
    results/index.json would be picked up and rejected as a malformed
    result.
  * results/<provider>/README.md -- one page per provider, carrying the
    per-run detail RESULTS.md used to carry directly. README.md is invisible
    to the rglob("*.json") the validator uses, and being in-tree means
    GitHub renders the page when you browse results/<provider>/.

The variance doctrine (median AND worst, never a mean; time variance vs host
variance kept separate) lives in tools/aggregate.py -- see that module's
docstring. This module only renders what aggregate.py already computed.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import yaml

import aggregate

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())

CATEGORIES = ["disk", "cpu", "ram", "network"]
PROFILES = [
    "postgres_oltp", "timescale_ingest", "patroni_member", "redis_sentinel",
    "worker_probe", "playwright_node", "nuxt_ssr",
]
PROFILE_ABBR = {
    "postgres_oltp": "pg",
    "timescale_ingest": "ts",
    "patroni_member": "patroni",
    "redis_sentinel": "redis",
    "worker_probe": "probe",
    "playwright_node": "pw",
    "nuxt_ssr": "nuxt",
}

MARK = {"A": "A", "B": "B", "C": "C", "D": "D", "F": "F", "?": "?", None: "?"}

dig = aggregate.dig
fmt_us = aggregate.fmt_us
spread = aggregate.spread
worst_grade = aggregate.worst_grade
worst_category = aggregate.worst_category
MIN_RUNS_FOR_SPREAD = aggregate.MIN_RUNS_FOR_SPREAD


def fmt_pct(v) -> str:
    return "-" if v is None else f"{v:.1f}%"


def storage_classes(runs: list[dict]) -> str:
    """storage_class is derived per-run from measured fsync latency (spec 4.5).

    Runs of the same product normally agree, but nothing enforces that, so
    report whatever distinct classes were actually observed rather than
    silently picking the first.
    """
    classes = sorted({dig(r, "grades.storage_class") for r in runs
                       if dig(r, "grades.storage_class")})
    return "/".join(classes) if classes else "?"


def hour_range(runs: list[dict]) -> str:
    hours = sorted({r["run"]["local_hour"] for r in runs
                    if dig(r, "run.local_hour") is not None})
    return ", ".join(f"{h:02d}h" for h in hours) if hours else "?"


# --------------------------------------------------------------------------
# Task 2: data/index.json, data/index.csv
# --------------------------------------------------------------------------

def index_rows(runs: list[dict]) -> list[dict]:
    """One row per (provider, region, product) -- the shared shape behind the
    index table, the export, and the provider pages."""
    rows = []
    for (name, region, product), rs in aggregate.by_product(runs).items():
        n_hosts = len({r["run"]["host_id"] for r in rs})
        med, worst = spread(rs, "disk.wal_fsync.p999_us")
        vals = [dig(r, "disk.wal_fsync.p999_us") for r in rs]
        vals = [v for v in vals if v is not None]
        row = {
            "provider": name,
            "region": region,
            "product": product,
            "storage_class": storage_classes(rs),
            "machines": n_hosts,
            "runs": len(rs),
            "fsync_p999_us_median": None,
            "fsync_p999_us_worst": max(vals) if vals else None,
            "price_eur_month": dig(rs[0], "provider.price_eur_month"),
            "categories": {c: worst_category(rs, c) for c in CATEGORIES},
            "profiles": {p: worst_grade(rs, p) for p in PROFILES},
        }
        if len(vals) >= MIN_RUNS_FOR_SPREAD:
            row["fsync_p999_us_median"] = statistics.median(vals)
        rows.append(row)
    rows.sort(key=lambda r: (r["provider"], r["region"], r["product"]))
    return rows


def write_index_json(rows: list[dict]) -> str:
    doc = {
        "bands_version": THRESHOLDS["bands_version"],
        "generated_from": "results/",
        "results": rows,
    }
    return json.dumps(doc, indent=2, sort_keys=False) + "\n"


def write_index_csv(rows: list[dict]) -> str:
    fieldnames = (
        ["provider", "region", "product", "storage_class", "machines", "runs",
         "fsync_p999_us_median", "fsync_p999_us_worst", "price_eur_month"]
        + [f"cat_{c}" for c in CATEGORIES]
        + [f"prof_{p}" for p in PROFILES]
    )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        flat = {k: row[k] for k in
                ["provider", "region", "product", "storage_class", "machines",
                 "runs", "fsync_p999_us_median", "fsync_p999_us_worst",
                 "price_eur_month"]}
        for c in CATEGORIES:
            flat[f"cat_{c}"] = row["categories"][c]
        for p in PROFILES:
            flat[f"prof_{p}"] = row["profiles"][p]
        writer.writerow(flat)
    return buf.getvalue()
