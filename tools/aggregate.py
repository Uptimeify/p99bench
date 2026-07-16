"""Load and aggregate results/**/*.json. No rendering here.

The central idea here is that there are two different kinds of variance, and
collapsing them into one number destroys the information:

  * TIME VARIANCE  -- same host_id, different hours. Noisy neighbours.
    Answers: "is this machine consistent throughout the day?"

  * HOST VARIANCE  -- different host_id, same product+region. Fleet spread.
    Answers: "does this provider hand out consistent machines?"

Averaging either one away is how you end up with a table claiming a provider is
fine when it is fine at 03:00 and unusable at 18:00. So: median AND worst,
always, and never a mean.

This module owns that doctrine and is tested directly (tests/test_aggregate.py)
rather than only through rendered markdown, so the reasoning survives even if
every artifact that consumes it changes shape.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

# Below this many runs we do not compute a spread. Two points is not a
# distribution, it is a line segment.
MIN_RUNS_FOR_SPREAD = 3


def fmt_us(v) -> str:
    """Microseconds stop being readable past a few thousand."""
    if v is None:
        return "-"
    if v >= 1000:
        return f"{v/1000:.1f} ms"
    return f"{v:.0f} us"


def dig(o, path, default=None):
    cur = o
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur or cur[p] is None:
            return default
        cur = cur[p]
    return cur


def load_all(root: Path = RESULTS) -> list[dict]:
    out = []
    if not root.exists():
        return out
    for f in sorted(root.rglob("*.json")):
        try:
            d = json.loads(f.read_text())
            d["_path"] = str(f.relative_to(ROOT))
            out.append(d)
        except json.JSONDecodeError:
            print(f"skipping unparseable {f}", file=sys.stderr)
    return out


def by_product(runs: list[dict]) -> dict[tuple[str, str, str], list[dict]]:
    """Group runs by (provider, region, product) -- one row per offering.

    This is the grouping that answers "should I use this product", as
    distinct from by_host below, which answers "which specific machine did I
    get".
    """
    out = defaultdict(list)
    for r in runs:
        p = r["provider"]
        out[(p["name"], p["region"], p["product"])].append(r)
    return out


def by_host(runs: list[dict]) -> dict[str, list[dict]]:
    """Group runs by host_id.

    Multiple runs against the same host_id are the same physical machine
    measured at different hours -- TIME VARIANCE. Comparing across host_ids
    within a product is HOST VARIANCE. Keeping this grouping separate from
    by_product is what lets both questions stay answerable instead of being
    blended into one number.
    """
    out = defaultdict(list)
    for r in runs:
        out[r["run"]["host_id"]].append(r)
    return out


def profile_grade(r: dict, profile: str) -> str:
    return dig(r, f"grades.profiles.{profile}.grade") or "?"


def category_grade(r: dict, category: str) -> str:
    return dig(r, f"grades.categories.{category}.grade") or "?"


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


def worst_category(runs: list[dict], category: str) -> str:
    """Worst grade across runs for a single category (disk, cpu, ...).

    Same doctrine as worst_grade, applied to a category rollup instead of a
    profile rollup: a machine that grades its disk A at 03:00 and F at 18:00
    is a machine whose disk grades F.
    """
    order = ["A", "B", "C", "D", "?", "F"]
    seen = [category_grade(r, category) for r in runs]
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
