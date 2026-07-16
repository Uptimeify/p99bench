#!/usr/bin/env python3
"""Render results/**/*.json into RESULTS.md.

RESULTS.md is generated. Never edit it by hand -- CI regenerates it and fails
the PR if the committed file differs.

The central idea here is that there are two different kinds of variance, and
collapsing them into one number destroys the information:

  * TIME VARIANCE  -- same host_id, different hours. Noisy neighbours.
    Answers: "is this machine consistent throughout the day?"

  * HOST VARIANCE  -- different host_id, same product+region. Fleet spread.
    Answers: "does this provider hand out consistent machines?"

Averaging either one away is how you end up with a table claiming a provider is
fine when it is fine at 03:00 and unusable at 18:00. So: median AND worst,
always, and never a mean.

Usage:
    python3 tools/render.py > RESULTS.md
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

MARK = {"A": "A", "B": "B", "C": "C", "D": "D", "F": "F", "?": "?", None: "?"}
PROFILES = [
    "postgres_oltp", "timescale_ingest", "patroni_member", "redis_sentinel",
    "worker_probe", "playwright_node", "nuxt_ssr",
]
# Short column headers -- the long profile names do not fit a table cell.
PROFILE_ABBR = {
    "postgres_oltp": "pg",
    "timescale_ingest": "ts",
    "patroni_member": "patroni",
    "redis_sentinel": "redis",
    "worker_probe": "probe",
    "playwright_node": "pw",
    "nuxt_ssr": "nuxt",
}

# Below this many runs we do not compute a spread. Two points is not a
# distribution, it is a line segment.
MIN_RUNS_FOR_SPREAD = 3

DISCLAIMER = (
    "*Every number below is a measurement of specific machines at specific times. "
    "Providers vary by region, by hardware generation within a region, and by who "
    "else is on the host. Read [METHODOLOGY.md](METHODOLOGY.md) before drawing "
    "conclusions, and [THRESHOLDS.md](THRESHOLDS.md) before disagreeing with a "
    "grade.*"
)


def fmt_us(v) -> str:
    """Microseconds stop being readable past a few thousand."""
    if v is None:
        return "-"
    if v >= 1000:
        return f"{v/1000:.1f} ms"
    return f"{v:.0f} us"


def fmt_pct(v) -> str:
    return "-" if v is None else f"{v:.1f}%"


def fmt_mbps(v) -> str:
    if v is None:
        return "-"
    if v >= 1000:
        return f"{v/1000:.2f} Gb/s"
    return f"{v:.0f} Mb/s"


def dig(o, path, default=None):
    cur = o
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur or cur[p] is None:
            return default
        cur = cur[p]
    return cur


def load_all() -> list[dict]:
    out = []
    if not RESULTS.exists():
        return out
    for f in sorted(RESULTS.rglob("*.json")):
        try:
            d = json.loads(f.read_text())
            d["_path"] = str(f.relative_to(ROOT))
            out.append(d)
        except json.JSONDecodeError:
            print(f"skipping unparseable {f}", file=sys.stderr)
    return out


def profile_grade(r: dict, profile: str) -> str:
    return dig(r, f"grades.profiles.{profile}.grade") or "?"


def worst_grade(runs: list[dict], profile: str) -> str:
    """Worst grade across runs for a profile.

    Ranking matches grade.py's rollup precedence (spec 4.2): any F beats any
    ?, because a definite failure is more true than an unmeasured one, and ?
    in turn outranks a merely-worse measured band. A machine that grades A at
    03:00 and F at 18:00 is a machine that grades F.
    """
    order = ["A", "B", "C", "D", "?", "F"]
    seen = [profile_grade(r, profile) for r in runs]
    if not seen:
        return "?"
    return max(seen, key=lambda v: order.index(v) if v in order else 0)


def spread(runs: list[dict], path: str) -> tuple[str, str]:
    """(median, worst) for a latency-like metric.

    'Worst' is the maximum, so only latency-shaped paths belong here -- passing
    a throughput metric would report the best value as the worst.
    """
    vals = [dig(r, path) for r in runs]
    vals = [v for v in vals if v is not None]
    if not vals:
        return ("-", "-")
    if len(vals) < MIN_RUNS_FOR_SPREAD:
        # Refuse to print a median of one or two points. The worst case is still
        # meaningful -- it happened -- so report that and mark the median absent.
        return ("-", fmt_us(max(vals)))
    return (fmt_us(statistics.median(vals)), fmt_us(max(vals)))


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
    ] + [MARK[profile_grade(r, p)] for p in PROFILES]
    return "| " + " | ".join(cells) + " |"


def main() -> int:
    runs = load_all()

    print("<!-- AUTOGENERATED by tools/render.py - do not edit. -->")
    print("<!-- Edit results/*.json and re-run: python3 tools/render.py > RESULTS.md -->")
    print()
    print("# Results")
    print()

    if not runs:
        print("No results submitted yet. See [CONTRIBUTING.md](CONTRIBUTING.md).")
        return 0

    hosts = {r["run"]["host_id"] for r in runs}
    provs = {r["provider"]["name"] for r in runs}
    print(f"{len(runs)} runs across {len(hosts)} machines at {len(provs)} providers.")
    print()
    print(DISCLAIMER)
    print()

    by_product = defaultdict(list)
    for r in runs:
        p = r["provider"]
        by_product[(p["name"], p["region"], p["product"])].append(r)

    # ---------------------------------------------------------------- summary
    print("## Summary")
    print()
    print("One row per product and region. `fsync p99.9` is the number that decides")
    print("whether a database is viable here, and the worst case matters more than the")
    print("median: your slowest commits are the ones users notice.")
    print()
    summary_cols = (["Provider", "Region", "Product", "Storage", "Machines", "Runs",
                      "fsync p99.9 med", "fsync p99.9 worst", "stall p99.9 worst"]
                     + [PROFILE_ABBR[p] for p in PROFILES])
    print("| " + " | ".join(summary_cols) + " |")
    print("|" + "---|" * len(summary_cols))
    for (name, region, product), rs in sorted(by_product.items()):
        n_hosts = len({r["run"]["host_id"] for r in rs})
        med, worst = spread(rs, "disk.wal_fsync.p999_us")
        _, stall = spread(rs, "cpu.stall_p999_us")
        cells = [name, region, f"`{product}`", storage_classes(rs), str(n_hosts), str(len(rs)),
                 med, worst, stall] + [MARK[worst_grade(rs, p)] for p in PROFILES]
        print("| " + " | ".join(cells) + " |")
    print()
    print("Grades are the **worst** across all runs for that product. A machine that")
    print("grades A at 03:00 and F at 18:00 is a machine that grades F. `?` on")
    print("`patroni`/`redis`/`probe`/`pw`/`nuxt` mostly means the run predates Phase 1's")
    print("stages, not that the machine is fine -- see")
    print("[THRESHOLDS.md](THRESHOLDS.md#provisional-bands).")
    print()

    # ----------------------------------------------------------------- detail
    print("## Detail")
    print()

    for (name, region, product), rs in sorted(by_product.items()):
        by_host = defaultdict(list)
        for r in rs:
            by_host[r["run"]["host_id"]].append(r)
        n_hosts = len(by_host)

        print(f"### {name} / {region} / `{product}`")
        print()

        meta = [f"{len(rs)} run{'s' if len(rs) != 1 else ''}",
                f"{n_hosts} machine{'s' if n_hosts != 1 else ''}"]
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

    # ---------------------------------------------------------------- network
    # Deliberately its own section rather than a column in the summary tables:
    # only worker_probe and playwright_node grade any of it, and mixing it into
    # every profile's row would imply the rest do too.
    print("## Network")
    print()
    print("Throughput and latency to the **same fixed targets** from every host")
    print("([schema/network-targets.yaml](schema/network-targets.yaml)). Nearest-server")
    print("speedtests measure a different path per host and cannot be compared in one")
    print("table; these can. Distance is a known constant here, so a low number points")
    print("at the provider's peering rather than at geography.")
    print()
    print("**Only `worker_probe` and `playwright_node` grade any of this** (`loss_pct`,")
    print("`dns_ms`, `rtt_jitter_ratio`) -- because for those two profiles the network")
    print("*is* the workload. See [THRESHOLDS.md](THRESHOLDS.md#known-gaps). Throughput")
    print("(`mbps`, shown below) stays ungraded everywhere: no workload requirement")
    print("yields a Mbit/s floor.")
    print()

    net_runs = [r for r in runs if dig(r, "network.reachable") is True
                and dig(r, "network.targets")]
    if not net_runs:
        unreachable = [r for r in runs if dig(r, "network.reachable") is False]
        if unreachable:
            print(f"No network data: {len(unreachable)} run(s) had no egress to the")
            print("reference targets. That is a valid result, not a failure.")
        else:
            print("No network data submitted yet.")
        print()
    else:
        target_ids = []
        for r in net_runs:
            for tgt in r["network"]["targets"]:
                if tgt["id"] not in target_ids:
                    target_ids.append(tgt["id"])

        versions = {dig(r, "network.target_list_version") for r in net_runs}
        if len(versions) > 1:
            print(f"> Runs here used different target list versions ({', '.join(sorted(str(v) for v in versions))}).")
            print("> Numbers across versions are not comparable.")
            print()

        print("| Provider | Region | Product | " + " | ".join(target_ids) + " |")
        print("|---|---|---|" + "---|" * len(target_ids))
        by_prod_net = defaultdict(list)
        for r in net_runs:
            p_ = r["provider"]
            by_prod_net[(p_["name"], p_["region"], p_["product"])].append(r)
        for (name, region, product), rs in sorted(by_prod_net.items()):
            cells = [name, region, f"`{product}`"]
            for tid in target_ids:
                vals = []
                for r in rs:
                    for tgt in r["network"]["targets"]:
                        if tgt["id"] == tid and tgt.get("mbps") is not None:
                            vals.append(tgt["mbps"])
                if not vals:
                    cells.append("-")
                else:
                    med = statistics.median(vals)
                    rtts = []
                    for r in rs:
                        for tgt in r["network"]["targets"]:
                            if tgt["id"] == tid and tgt.get("rtt_p50_ms") is not None:
                                rtts.append(tgt["rtt_p50_ms"])
                    rtt_s = f" / {statistics.median(rtts):.0f}ms" if rtts else ""
                    cells.append(f"{fmt_mbps(med)}{rtt_s}")
            print("| " + " | ".join(cells) + " |")
        print()
        print("Median throughput / median RTT p50 per target. ")
        print()

        losses = []
        for r in net_runs:
            for tgt in r["network"]["targets"]:
                if tgt.get("loss_pct"):
                    losses.append((r["provider"]["name"], r["provider"]["region"],
                                   tgt["id"], tgt["loss_pct"]))
        if losses:
            print("**Packet loss observed**")
            print()
            print("| Provider | Region | Target | Loss |")
            print("|---|---|---|---|")
            for name, region, tid, loss in sorted(losses, key=lambda x: -x[3]):
                print(f"| {name} | {region} | {tid} | {loss:.2f}% |")
            print()
            print("Sustained loss above ~0.05% will hurt TCP throughput and replication.")
            print()

        ookla_runs = [r for r in runs if dig(r, "network.ookla")]
        if ookla_runs:
            print("<details>")
            print("<summary>Ookla results (context only, not comparable)</summary>")
            print()
            print("Ookla picks a nearby server, so each row measures a different path.")
            print("Useful as a sanity check on the link itself, useless for ranking.")
            print()
            print("| Provider | Region | Server | Down | Up | Idle latency | Loss |")
            print("|---|---|---|---|---|---|---|")
            for r in ookla_runs:
                o, p_ = r["network"]["ookla"], r["provider"]
                print(f"| {p_['name']} | {p_['region']} | {o.get('server','?')} | "
                      f"{fmt_mbps(o.get('down_mbps'))} | {fmt_mbps(o.get('up_mbps'))} | "
                      f"{o.get('idle_latency_ms','-')} ms | {o.get('loss_pct','-')}% |")
            print()
            print("</details>")
            print()

    # --------------------------------------------------------------- failures
    print("## Why runs failed")
    print()
    print("Computed from [schema/thresholds.yaml](schema/thresholds.yaml), not written")
    print("by hand. Each profile names the metric that bound its grade -- disagree with")
    print("a grade? The thing to argue about is the threshold, in")
    print("[THRESHOLDS.md](THRESHOLDS.md).")
    print()
    bound_counts = defaultdict(int)
    for r in runs:
        for p in PROFILES:
            entry = dig(r, f"grades.profiles.{p}") or {}
            grade, bound = entry.get("grade"), entry.get("bound_by")
            if not bound:
                continue
            if grade == "F":
                bound_counts[f"[{p}] {bound}"] += 1
            elif grade == "?":
                # Distinct from an F: bound_by here names the metric that
                # COULD NOT be graded, not one that failed. reason says why --
                # usually "needs re-run", which is a data gap, not a grade.
                reason = entry.get("reason", "not measured")
                bound_counts[f"[{p}] {bound} ({reason})"] += 1
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
    print("If a number looks wrong, open the file and check it.")
    print()
    print("`host_id` is a salted hash of the machine's `/etc/machine-id`. It links runs")
    print("on the same VM together and means nothing outside this dataset.")
    return 0


if __name__ == "__main__":
    sys.exit(main())