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
import re
import statistics
from collections import defaultdict
from pathlib import Path

import yaml

import aggregate

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())
NETWORK_TARGETS = yaml.safe_load((ROOT / "schema" / "network-targets.yaml").read_text())
NETWORK_TARGET_IDS = [t["id"] for t in NETWORK_TARGETS["targets"]]

# Sustained loss above this hurts TCP throughput and replication -- the
# threshold v1's "Packet loss observed" callout used, carried forward here.
LOSS_CALLOUT_THRESHOLD_PCT = 0.05

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

# A-F only -- "?" never gets the incomplete suffix. "?" already means "nothing
# measured", the maximal form of uncertainty; appending another "?" to it
# (`??`) would add noise, not information.
REAL_GRADES = {"A", "B", "C", "D", "F"}


def grade_cell(grade, incomplete: bool) -> str:
    """The rendered grade cell, e.g. `D?` for an incomplete D.

    `incomplete` means a required rule went unmeasured, so the grade is a
    FLOOR -- at least this bad, because grading is non-compensatory and a
    missing metric can only drag it further down, never lift it. The `?`
    suffix on a real letter is that caveat; see the index's "How to read
    this table" section for the reader-facing explanation.
    """
    mark = MARK[grade]
    if incomplete and grade in REAL_GRADES:
        return f"{mark}?"
    return mark

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


def fmt_mbps(v) -> str:
    if v is None:
        return "-"
    if v >= 1000:
        return f"{v/1000:.2f} Gb/s"
    return f"{v:.0f} Mb/s"


def fmt_rtt_ms(v) -> str:
    if v is None:
        return "-"
    return f"{v:.1f}ms" if v < 10 else f"{v:.0f}ms"


# --------------------------------------------------------------------------
# Doctrine-safe prose: schema/thresholds.yaml's own `why` text sometimes uses
# the statistical term "mean" to explain why a percentile beats it (e.g.
# disk.wal_fsync.p999_us: "the mean describes a commit nobody complains
# about"). This project's own doctrine forbids "mean"/"average" appearing in
# any generated artifact, and a test enforces it -- so the substitution
# happens here, at render time, on the copy that reaches the page.
# schema/thresholds.yaml itself is out of scope for this task and stays
# untouched; only the rendered prose is paraphrased.
# --------------------------------------------------------------------------


def fmt_metric_value(value, unit) -> str:
    """One metric's value, in its own unit -- ms not raw us, a rounded
    percentage, etc. None (not measured) renders as an em dash; the renderer
    must not invent a reason, since it cannot know whether a tool was
    missing or a parse failed.
    """
    if value is None:
        return "—"
    if unit == "us":
        return fmt_us(value)
    if unit == "ms":
        return f"{value:.1f}ms"
    if unit == "pct":
        return f"{value:.1f}%"
    if unit == "ratio":
        return f"{value:.3f}"
    if unit in ("MB/s", "MB"):
        return f"{value:,.0f} {unit}"
    if unit in ("iops", "events/s", "ops/s"):
        return f"{value:,.0f}"
    if unit:
        return f"{value:g} {unit}"
    return f"{value:g}"


def fmt_bands(mdef: dict) -> str:
    """A/B/C/D bounds in the metric's own direction and unit -- `op: lte`
    reads <=, `op: gte` reads >=, per thresholds.yaml's own semantics."""
    sym = "≤" if mdef["op"] == "lte" else "≥"
    unit = mdef.get("unit")
    return " / ".join(f"{sym}{fmt_metric_value(mdef['bands'][g], unit)}"
                       for g in ("A", "B", "C", "D"))


def _worst_metric_entry(rs: list[dict], category: str, path: str, op: str) -> dict:
    """{'value', 'grade'} for the run that is WORST on this one metric.

    'Worst' is max() for an `lte` metric (higher is worse) and min() for a
    `gte` metric (lower is worse) -- never a mean of the runs. This mirrors
    how the category/profile grades themselves roll up (aggregate.py's
    worst_category): a machine that fails at 18:00 is a machine that fails,
    so the metric table must show that failure, not smooth it away.
    """
    # NOT dig(): the metrics dict is keyed by the metric's full dotted path
    # as one flat string ("disk.wal_fsync.p999_us"), not nested levels --
    # dig() would split on "." and double the category prefix, silently
    # missing every lookup.
    entries = []
    for r in rs:
        cat = dig(r, f"grades.categories.{category}") or {}
        e = (cat.get("metrics") or {}).get(path)
        if e and e.get("value") is not None:
            entries.append(e)
    if not entries:
        return {"value": None, "grade": "?"}
    pick = max if op == "lte" else min
    return pick(entries, key=lambda e: e["value"])


def host_line(rs: list[dict]) -> str | None:
    """CPU model, vCPU, RAM, virt, kernel -- one line, not a table. A reader
    comparing two VPS needs to know one is a Xeon Gold 6126 with 4 vCPU /
    12 GB before any grade below means anything. Every run in a section
    describes the same product, so the first host block present is used.
    """
    host = None
    for r in rs:
        h = r.get("host")
        if h:
            host = h
            break
    if not host:
        return None
    parts = []
    if host.get("cpu_model"):
        parts.append(host["cpu_model"])
    if host.get("vcpu") is not None:
        parts.append(f"{host['vcpu']} vCPU")
    if host.get("ram_mb") is not None:
        parts.append(f"{host['ram_mb'] / 1024:.1f} GB RAM")
    if host.get("virt"):
        parts.append(host["virt"])
    if host.get("kernel"):
        parts.append(f"kernel {host['kernel']}")
    return " - ".join(parts) if parts else None


def print_category_metrics(rs: list[dict], category: str) -> None:
    """The per-metric value/grade/bands table for one category, plus a
    <details> block carrying each metric's `why` -- the reasoning behind a
    band is the most valuable thing this project has, so it must be
    reachable even though it does not belong in the table itself.
    """
    paths = THRESHOLDS["categories"][category]
    grade = worst_category(rs, category)
    incomplete = category_incomplete(rs, category)
    bound = category_bound_by(rs, category)
    bound_label = bound.split(".", 1)[1] if bound and "." in bound else bound

    # Deliberately NOT a markdown heading (`#...`): the region/product
    # sections above already split on the literal "## " prefix, and any run
    # of 2+ hashes followed by a space contains that substring too --
    # a "###" heading here would silently corrupt those splits.
    heading = f"**`{category}`** -- {grade_cell(grade, incomplete)}"
    if bound_label:
        heading += f", bound by `{bound_label}`"
    if incomplete:
        heading += " (incomplete -- a `?` row below was required and unmeasured; this grade is a floor)"
    print(heading)
    print()
    if len(rs) > 1:
        print(f"Worst value seen per metric across this section's {len(rs)} runs "
              "(never smoothed across them; grades roll up worst-wins).")
        print()

    print("| Metric | Value | Grade | Bands A/B/C/D | Plain-English |")
    print("|---|---|---|---|---|")
    has_provisional = False
    why_lines = []
    for path in paths:
        mdef = THRESHOLDS["metrics"][path]
        entry = _worst_metric_entry(rs, category, path, mdef["op"])
        value, g = entry["value"], entry["grade"]
        label = path.split(".", 1)[1] if "." in path else path
        if mdef.get("provisional"):
            has_provisional = True
            label += "*"
        value_cell = fmt_metric_value(value, mdef.get("unit"))
        grade_mark = MARK[g]
        metric_cell = f"`{label}`"
        if path == bound:
            metric_cell = f"**{metric_cell}**"
            value_cell = f"**{value_cell}**"
            grade_mark = f"**{grade_mark}**"
        explain = "not measured" if value is None else (mdef.get("means") or {}).get(g, "-")
        print(f"| {metric_cell} | {value_cell} | {grade_mark} | {fmt_bands(mdef)} | {explain} |")
        why = (mdef.get("why") or "").strip()
        if why:
            why_lines.append(f"- `{path}`: {why}")
    print()

    if has_provisional:
        print("*Provisional band -- no corpus behind it yet; see "
              "[THRESHOLDS.md](../../THRESHOLDS.md#provisional-bands).")
        print()

    if why_lines:
        print("<details>")
        print(f"<summary>Why these `{category}` metrics</summary>")
        print()
        for line in why_lines:
            print(line)
        print()
        print("</details>")
        print()


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
            "categories_incomplete": {c: category_incomplete(rs, c) for c in CATEGORIES},
            "profiles": {p: worst_grade(rs, p) for p in PROFILES},
            "profiles_incomplete": {p: profile_incomplete(rs, p) for p in PROFILES},
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
        fmt_us(dig(r, "disk.rand_read_8k_qd1.p99_us")),
        fmt_pct(dig(r, "cpu.steal_pct_under_load")),
        fmt_us(dig(r, "cpu.stall_p999_us")),
        fmt_pct(dig(r, "disk.steady_state.degradation_pct")),
    ] + [
        grade_cell(aggregate.profile_grade(r, p), bool(dig(r, f"grades.profiles.{p}.incomplete")))
        for p in PROFILES
    ]
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


def category_incomplete(rs: list[dict], category: str) -> bool:
    """Whether the run that set this category's worst-across-runs grade was
    itself incomplete (a required rule unmeasured). Mirrors
    category_bound_by's doctrine: the grade shown is the worst-wins grade, so
    the caveat shown must come from the SAME run that produced it, not from
    an unrelated run that happened to also be incomplete.
    """
    worst = worst_category(rs, category)
    for r in rs:
        entry = dig(r, f"grades.categories.{category}") or {}
        if entry.get("grade") == worst:
            return bool(entry.get("incomplete"))
    return False


def profile_incomplete(rs: list[dict], profile: str) -> bool:
    """Same doctrine as category_incomplete, for a workload profile."""
    worst = worst_grade(rs, profile)
    for r in rs:
        entry = dig(r, f"grades.profiles.{profile}") or {}
        if entry.get("grade") == worst:
            return bool(entry.get("incomplete"))
    return False


def network_target_stats(rs: list[dict]) -> tuple[dict[str, dict], bool]:
    """target_id -> {mbps, rtt_p50, rtt_p99, loss_pct}, plus whether that is a
    median or a worst-of-few.

    Follows aggregate.py's own doctrine (MIN_RUNS_FOR_SPREAD): with 3+ runs,
    report the median; below that, "two points is not a distribution" --
    statistics.median() on 2 points is silently their arithmetic mean, which
    is exactly the collapse this project's "median AND worst, never a mean"
    rule exists to forbid. So below the bar this reports the worst-of-few
    instead (lowest throughput, highest RTT) rather than averaging them.

    reachable is an HTTP-status flag (bench/06-network.sh: http_code == 200),
    NOT a "nothing measured" flag -- a target with reachable: false can still
    carry a real dns_ms/rtt_p50_ms/loss_pct (every published result has
    exactly this on ovh-gra: mbps is null, RTT is real). So this collects
    every target that appears, unconditionally on `reachable`; only mbps
    being present or absent decides whether throughput renders as a number
    or a dash. A Phase 2 bug skipped these rows outright, which could only
    ever flatter a grade -- do not reintroduce that by filtering on
    reachable here.

    loss_pct is always the max seen, regardless of the run count: a single
    10% loss event matters and averaging it against a run of zeros would
    erase it.
    """
    by_target: dict[str, list[dict]] = defaultdict(list)
    for r in rs:
        for t in (dig(r, "network.targets") or []):
            if t.get("id"):
                by_target[t["id"]].append(t)

    use_median = len(rs) >= MIN_RUNS_FOR_SPREAD

    def reduce(key: str, targets: list[dict], worst_fn):
        vals = [t[key] for t in targets if t.get(key) is not None]
        if not vals:
            return None
        return statistics.median(vals) if use_median else worst_fn(vals)

    out = {}
    for tid, ts in by_target.items():
        loss_vals = [t["loss_pct"] for t in ts if t.get("loss_pct") is not None]
        out[tid] = {
            "mbps": reduce("mbps", ts, min),          # worst = lowest throughput
            "rtt_p50": reduce("rtt_p50_ms", ts, max),  # worst = highest latency
            "rtt_p99": reduce("rtt_p99_ms", ts, max),
            "loss_pct": max(loss_vals) if loss_vals else None,
        }
    return out, use_median


def _rtt_p99_cell(rtt_p50, rtt_p99) -> str:
    """RTT p99 only where it adds signal beyond p50 -- a p99 within ~15% of
    p50 is noise, not jitter worth a reader's attention."""
    if rtt_p99 is None:
        return "-"
    if rtt_p50 is not None and rtt_p50 > 0 and (rtt_p99 - rtt_p50) / rtt_p50 < 0.15:
        return "-"
    return fmt_rtt_ms(rtt_p99)


def print_network_section(rs: list[dict]) -> None:
    """The per-target network table plus packet-loss callout for one
    (provider, region, product) section of a provider page.

    Every host in the corpus measures the SAME fixed reference targets
    (schema/network-targets.yaml) -- distance is a known constant, so a low
    number here points at this provider's peering, not at geography.
    Nearest-server speedtests measure a different path per host and cannot
    share a table; these can, which is why the framing lives once at the top
    of the page rather than being repeated per product.
    """
    stats, use_median = network_target_stats(rs)
    if not stats:
        return

    print("**Network**")
    print()
    print("| Target | Throughput | RTT p50 | RTT p99 |")
    print("|---|---|---|---|")
    for tid in NETWORK_TARGET_IDS:
        s = stats.get(tid)
        if s is None:
            continue
        print(f"| `{tid}` | {fmt_mbps(s['mbps'])} | {fmt_rtt_ms(s['rtt_p50'])} | "
              f"{_rtt_p99_cell(s['rtt_p50'], s['rtt_p99'])} |")
    print()
    if use_median:
        print(f"Median throughput / RTT per target across this section's {len(rs)} runs.")
    else:
        print(f"Fewer than {MIN_RUNS_FOR_SPREAD} runs, so no median is computed -- worst-case "
              "throughput / RTT shown per target instead (lowest throughput, highest RTT seen).")
    print()

    loss_rows = [(tid, s["loss_pct"]) for tid, s in stats.items()
                 if s["loss_pct"] is not None and s["loss_pct"] > LOSS_CALLOUT_THRESHOLD_PCT]
    if loss_rows:
        print("**Packet loss observed**")
        print()
        print("| Target | Loss |")
        print("|---|---|")
        for tid, loss in sorted(loss_rows, key=lambda x: -x[1]):
            print(f"| `{tid}` | {loss:.2f}% |")
        print()
        print(f"Sustained loss above ~{LOSS_CALLOUT_THRESHOLD_PCT}% will hurt TCP "
              "throughput and replication.")
        print()


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

    print("Every product/region section below includes throughput and RTT to")
    print("the **same fixed reference targets** every host in the corpus measures")
    print("([schema/network-targets.yaml](../../schema/network-targets.yaml)).")
    print("Nearest-server speedtests measure a different path per host and cannot")
    print("be compared in one table; these can. Distance is a known constant, so")
    print("a low number points at this provider's peering rather than at")
    print("geography. **Only `worker_probe` and `playwright_node` grade any of")
    print("it** (`loss_pct`, `rtt_jitter_ratio`) -- for those two profiles the")
    print("network *is* the workload. Throughput and `dns_ms` stay ungraded")
    print("everywhere. See [THRESHOLDS.md](../../THRESHOLDS.md#known-gaps).")
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

        hl = host_line(rs)
        if hl:
            print(f"**Host**: {hl}")
            print()

        # A grade without its numbers is a letter with no evidence.
        for c in CATEGORIES:
            print_category_metrics(rs, c)

        print_network_section(rs)

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
        run_cols = (["Machine", "Date", "Hour", "fsync p99.9", "rand-read p99 (QD1)", "steal",
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
    print("**`?` says nothing in that category/profile was measured at all** --")
    print("there is no lower bound to report. **`D?` (a letter followed by `?`)")
    print("says at least `D`: every metric that WAS measured rolled up to that")
    print("grade, but a required metric is still missing, so the true grade could")
    print("only be the same or worse -- never better, because grading is")
    print("non-compensatory. Re-running with today's tooling on hosts that predate")
    print("`cpu.stall_*`, `cpu.steady_state`, `cpu.tls_verify_s`, or `ram.bw_read_mbs`")
    print("replaces the `?` suffix with a final grade; it is not a rebanding. See")
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
        cat_inc, prof_inc = r["categories_incomplete"], r["profiles_incomplete"]
        cells = [
            r["provider"], r["region"], f"`{r['product']}`", r["storage_class"],
            str(r["machines"]), str(r["runs"]), fmt_us(r["fsync_p999_us_worst"]),
            grade_cell(cat["disk"], cat_inc["disk"]),
            grade_cell(cat["cpu"], cat_inc["cpu"]),
            grade_cell(cat["ram"], cat_inc["ram"]),
            grade_cell(cat["network"], cat_inc["network"]),
        ] + [grade_cell(prof[p], prof_inc[p]) for p in PROFILES]
        print("| " + " | ".join(cells) + " |")
    print()

    # ---------------------------------------------------------------- network
    # Deliberately its own section rather than folded into the category
    # columns: only worker_probe and playwright_node grade any of it, and
    # implying the rest do too would misrepresent every other profile's row.
    print("## Network")
    print()
    print("Throughput and latency to the **same fixed reference targets** are")
    print("measured on every run but not summarized in this index -- every host")
    print("measures the same targets, so that per-target detail is worth a table")
    print("of its own, and it lives on each provider's page (with a packet-loss")
    print("callout wherever loss exceeds ~0.05%): "
          f"{detail_links}. **Only `worker_probe`")
    print("and `playwright_node` grade any of it** (`loss_pct`, `rtt_jitter_ratio`)")
    print("-- for those two profiles the network *is* the workload. Everyone")
    print("else's `net` column stays informational. See")
    print("[THRESHOLDS.md](THRESHOLDS.md#known-gaps).")
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
