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


# --------------------------------------------------------------------------
# Task 3: results/<provider>/README.md
# --------------------------------------------------------------------------

def render_run_row(r: dict) -> str:
    cells = [
        f"`{r['run']['host_id'][:6]}`",
        r["run"]["timestamp_utc"][:10],
        f"{r['run']['local_hour']:02d}h",
        fmt_us(dig(r, "disk.wal_fsync.p999_us")),
        fmt_us(dig(r, "disk.rand_read_8k.p99_us")),
        fmt_pct(dig(r, "cpu.steal_pct_under_load")),
        fmt_us(dig(r, "cpu.stall_p999_us")),
        fmt_pct(dig(r, "disk.steady_state.degradation_pct")),
    ] + [MARK[aggregate.profile_grade(r, p)] for p in PROFILES]
    return "| " + " | ".join(cells) + " |"


def category_bound_by(rs: list[dict], category: str) -> str | None:
    """The metric that decided the worst grade this category saw across rs.

    Mirrors aggregate.worst_category's doctrine (worst grade wins), then
    reports the bound_by that came with that grade -- a grade without its
    binding constraint is a letter with no lead.
    """
    worst = worst_category(rs, category)
    for r in rs:
        entry = dig(r, f"grades.categories.{category}") or {}
        if entry.get("grade") == worst and entry.get("bound_by"):
            return entry["bound_by"]
    return None


def write_provider_page(provider: str, runs: list[dict]) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_provider_page(provider, runs)
    return buf.getvalue()


def _print_provider_page(provider: str, runs: list[dict]) -> None:
    print("<!-- AUTOGENERATED by tools/render.py - do not edit. -->")
    print("<!-- Edit results/*.json and re-run: python3 tools/render.py -->")
    print()
    print(f"# {provider}")
    print()

    if not runs:
        print("No results submitted yet.")
        return

    hosts = {r["run"]["host_id"] for r in runs}
    regions = sorted({r["provider"]["region"] for r in runs})
    products = sorted({r["provider"]["product"] for r in runs})
    print(f"{len(runs)} run{'s' if len(runs) != 1 else ''} across "
          f"{len(hosts)} machine{'s' if len(hosts) != 1 else ''} in "
          f"{len(regions)} region{'s' if len(regions) != 1 else ''} "
          f"({', '.join(regions)}), {len(products)} product"
          f"{'s' if len(products) != 1 else ''}.")
    print()
    print("[Back to the index](../../RESULTS.md) - "
          "[machine-readable export](../../data/index.json)")
    print()

    by_product = aggregate.by_product(runs)
    for (name, region, product), rs in sorted(by_product.items()):
        by_host = defaultdict(list)
        for r in rs:
            by_host[r["run"]["host_id"]].append(r)
        n_hosts = len(by_host)

        print(f"## {region} / `{product}`")
        print()

        meta = [f"{len(rs)} run{'s' if len(rs) != 1 else ''}",
                f"{n_hosts} machine{'s' if n_hosts != 1 else ''}",
                f"storage class `{storage_classes(rs)}`"]
        price = dig(rs[0], "provider.price_eur_month")
        if price:
            meta.append(f"{price:.2f} EUR/mo")
        if dig(rs[0], "provider.storage_tier"):
            meta.append(f"storage: {rs[0]['provider']['storage_tier']}")
        boot = dig(rs[0], "disk.is_boot_volume")
        if boot is True:
            meta.append("**boot volume**")
        elif boot is False:
            meta.append("dedicated data volume")
        print(" - ".join(meta))
        print()

        # A grade without its binding constraint is a letter with no lead.
        print("**What bound each grade**")
        print()
        for c in CATEGORIES:
            grade = worst_category(rs, c)
            bound = category_bound_by(rs, c)
            if bound:
                print(f"- `{c}`: {MARK[grade]} -- bound by `{bound}`")
        print()

        # TIME VARIANCE: one machine, several hours.
        for hid, hrs in sorted(by_host.items()):
            if len(hrs) < 2:
                continue
            hrs.sort(key=lambda r: r["run"]["timestamp_utc"])
            med, worst = spread(hrs, "disk.wal_fsync.p999_us")
            print(f"**Machine `{hid[:6]}`** - {len(hrs)} runs at {hour_range(hrs)}")
            print()
            if len(hrs) < MIN_RUNS_FOR_SPREAD:
                print(f"Fewer than {MIN_RUNS_FOR_SPREAD} runs, so no spread is computed. "
                      f"Worst fsync p99.9 seen: {worst}.")
            else:
                print(f"fsync p99.9 across the day: median {med}, worst {worst}.")
                vals = [dig(r, "disk.wal_fsync.p999_us") for r in hrs]
                vals = [v for v in vals if v is not None]
                if vals and min(vals) and max(vals) / min(vals) >= 2:
                    print()
                    print(f"> {max(vals)/min(vals):.1f}x swing on the same machine depending on "
                          "the hour. That is a neighbour, not the hardware.")
            print()

        # HOST VARIANCE: several machines, same product.
        if n_hosts >= 2:
            print(f"**Across {n_hosts} machines**")
            print()
            per_host = {}
            for hid, hrs in by_host.items():
                vals = [dig(r, "disk.wal_fsync.p999_us") for r in hrs]
                vals = [v for v in vals if v is not None]
                if vals:
                    per_host[hid] = max(vals)
            vals = list(per_host.values())
            if len(vals) >= 2:
                lo, hi = min(vals), max(vals)
                factor = hi / lo if lo else 0
                line = f"Worst fsync p99.9 per machine ranges {fmt_us(lo)} to {fmt_us(hi)}"
                line += f" ({factor:.1f}x spread)." if factor >= 1.5 else "."
                print(line)
                if factor >= 2:
                    print()
                    print("> A 2x or larger spread between machines of the same product means")
                    print("> this fleet is not uniform. Which machine you get is luck.")
            print()

        print("<details>")
        print(f"<summary>All {len(rs)} run{'s' if len(rs) != 1 else ''}</summary>")
        print()
        run_cols = (["Machine", "Date", "Hour", "fsync p99.9", "rand-read p99", "steal",
                     "stall p99.9", "steady drop"] + [PROFILE_ABBR[p] for p in PROFILES])
        print("| " + " | ".join(run_cols) + " |")
        print("|" + "---|" * len(run_cols))
        for r in sorted(rs, key=lambda x: x["run"]["timestamp_utc"]):
            print(render_run_row(r))
        print()
        notes = [(r["run"]["host_id"][:6], r["run"]["timestamp_utc"][:10], r["run"]["notes"])
                 for r in sorted(rs, key=lambda x: x["run"]["timestamp_utc"])
                 if dig(r, "run.notes")]
        if notes:
            for hid, date, note in notes:
                print(f"- `{hid}` {date}: {note}")
            print()
        print("</details>")
        print()


def provider_pages(runs: list[dict]) -> dict[Path, str]:
    """One results/<provider>/README.md per provider present in runs.

    README.md is invisible to the rglob("*.json") the validator and render.py
    use to discover results, and being in-tree means GitHub renders the page
    when you browse results/<provider>/.
    """
    pages = {}
    for provider in sorted({r["provider"]["name"] for r in runs}):
        prs = [r for r in runs if r["provider"]["name"] == provider]
        pages[RESULTS_DIR / provider / "README.md"] = write_provider_page(provider, prs)
    return pages
