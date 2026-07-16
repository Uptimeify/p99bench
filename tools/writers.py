"""Render results/**/*.json into every published artifact: the compact
RESULTS.md index, the machine-readable export, and the per-provider detail
pages. render.py is now just a thin CLI wrapper (build_all + --check) around
this module.

  * RESULTS.md -- the front door. One row per (provider, region, product),
    the grades, the storage class, and the flagship fsync p99.9. No per-run
    detail: that is what made one flat file unreviewable at scale.
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

DISCLAIMER = (
    "*Every number below is a measurement of specific machines at specific times. "
    "Providers vary by region, by hardware generation within a region, and by who "
    "else is on the host. Read [METHODOLOGY.md](METHODOLOGY.md) before drawing "
    "conclusions, and [THRESHOLDS.md](THRESHOLDS.md) before disagreeing with a "
    "grade.*"
)

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

def _bound_by_counts(rs: list[dict]) -> dict[str, int]:
    """How many of this product's runs were bound by each metric, keyed
    "[profile] metric" (or "[profile] metric (reason)" when the bound metric
    couldn't be graded rather than failed). Exact per-run counts, not an
    approximation from the rolled-up worst grade -- this is what lets
    write_index_md's "why runs failed" table be sourced from bound_by without
    needing the raw runs itself.
    """
    counts: dict[str, int] = defaultdict(int)
    for r in rs:
        for p in PROFILES:
            entry = dig(r, f"grades.profiles.{p}") or {}
            grade, bound = entry.get("grade"), entry.get("bound_by")
            if not bound:
                continue
            if grade == "F":
                counts[f"[{p}] {bound}"] += 1
            elif grade == "?":
                # Distinct from an F: bound_by here names the metric that
                # COULD NOT be graded, not one that failed.
                reason = entry.get("reason", "not measured")
                counts[f"[{p}] {bound} ({reason})"] += 1
    return dict(counts)


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
            "bound_by_counts": _bound_by_counts(rs),
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


# --------------------------------------------------------------------------
# Task 4: RESULTS.md becomes the index
# --------------------------------------------------------------------------

def write_index_md(rows: list[dict]) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_index_md(rows)
    return buf.getvalue()


def _print_index_md(rows: list[dict]) -> None:
    print("<!-- AUTOGENERATED by tools/render.py - do not edit. -->")
    print("<!-- Edit results/*.json and re-run: python3 tools/render.py -->")
    print()
    print("# Results")
    print()

    if not rows:
        print("No results submitted yet. See [CONTRIBUTING.md](CONTRIBUTING.md).")
        return

    total_runs = sum(r["runs"] for r in rows)
    total_machines = sum(r["machines"] for r in rows)
    providers = sorted({r["provider"] for r in rows})
    print(f"{total_runs} runs across {total_machines} machines at "
          f"{len(providers)} provider{'s' if len(providers) != 1 else ''}.")
    print()
    print(DISCLAIMER)
    print()
    detail_links = ", ".join(f"[{p}](results/{p}/README.md)" for p in providers)
    print(f"Per-provider detail: {detail_links}. Machine-readable export:")
    print("[data/index.json](data/index.json) / [data/index.csv](data/index.csv).")
    print()

    # -------------------------------------------------------------- reading guide
    print("## How to read this table")
    print()
    print("One row per product and region. `fsync p99.9 worst` is the number that")
    print("decides whether a database is viable here: your slowest commits are the")
    print("ones users notice, so worst matters more than median here.")
    print()
    print("Grades are **A-F, the worst across all runs** for that product. A machine")
    print("that grades A at 03:00 and F at 18:00 is a machine that grades F.")
    print()
    print("**`?` is unmeasured, not bad.** Most `cpu` cells and every `ram` cell")
    print("read `?` on this corpus because those hosts predate the stages that")
    print("measure `cpu.stall_*`, `cpu.steady_state`, `cpu.tls_verify_s`, and")
    print("`ram.bw_read_mbs` -- re-running with today's tooling replaces a `?` with a")
    print("real grade, it is not a rebanding. See")
    print("[THRESHOLDS.md](THRESHOLDS.md#provisional-bands).")
    print()
    print("`disk`/`cpu`/`ram`/`net` are the four measured categories; `pg` through")
    print("`nuxt` are the seven workload profiles graded from them. `net` stays")
    print("informational here too -- only `worker_probe` (`probe`) and")
    print("`playwright_node` (`pw`) actually grade the network, so a bad `net` cell")
    print("on any other profile's row is context, not a cause.")
    print()

    # ------------------------------------------------------------------------ index
    print("## Index")
    print()
    cols = (["Provider", "Region", "Product", "Class", "Machines", "Runs",
             "fsync p99.9 worst", "disk", "cpu", "ram", "net"]
            + [PROFILE_ABBR[p] for p in PROFILES])
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        cat, prof = r["categories"], r["profiles"]
        cells = [
            r["provider"], r["region"], f"`{r['product']}`", r["storage_class"],
            str(r["machines"]), str(r["runs"]), fmt_us(r["fsync_p999_us_worst"]),
            MARK[cat["disk"]], MARK[cat["cpu"]], MARK[cat["ram"]], MARK[cat["network"]],
        ] + [MARK[prof[p]] for p in PROFILES]
        print("| " + " | ".join(cells) + " |")
    print()

    # ---------------------------------------------------------------- network
    # Deliberately its own section rather than folded into the category
    # columns: only worker_probe and playwright_node grade any of it, and
    # implying the rest do too would misrepresent every other profile's row.
    print("## Network")
    print()
    print("Throughput and latency to fixed reference targets are measured on every")
    print("run but not summarized in this index -- that per-target detail lives on")
    print("each provider's page. **Only `worker_probe` and `playwright_node` grade")
    print("any of it** (`loss_pct`, `rtt_jitter_ratio`) -- for those two profiles the")
    print("network *is* the workload. Everyone else's `net` column stays")
    print("informational. See [THRESHOLDS.md](THRESHOLDS.md#known-gaps).")
    print()

    # --------------------------------------------------------------- failures
    print("## Why runs failed")
    print()
    print("Computed from [schema/thresholds.yaml](schema/thresholds.yaml), not written")
    print("by hand. Each profile names the metric that bound its grade -- disagree with")
    print("a grade? The thing to argue about is the threshold, in")
    print("[THRESHOLDS.md](THRESHOLDS.md).")
    print()
    bound_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        for k, c in r.get("bound_by_counts", {}).items():
            bound_counts[k] += c
    if bound_counts:
        print("| Binding constraint | Runs affected |")
        print("|---|---|")
        for k, c in sorted(bound_counts.items(), key=lambda x: -x[1]):
            print(f"| {k} | {c} |")
    else:
        print("No binding constraints across submitted runs.")
    print()

    print("---")
    print()
    print("## Raw data")
    print()
    print("Every number above comes from a JSON file under [`results/`](results/),")
    print("laid out as `results/<provider>/<region>/`. Nothing here is hand-written.")
    print("If a number looks wrong, open the file and check it. Per-run detail --")
    print("per-machine tables, host and time variance, binding constraints -- lives")
    print(f"on each provider's page: {detail_links}.")
    print()
    print("`host_id` is a salted hash of the machine's `/etc/machine-id`. It links runs")
    print("on the same VM together and carries no significance outside this dataset.")
