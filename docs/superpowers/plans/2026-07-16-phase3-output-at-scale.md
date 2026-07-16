# p99bench Phase 3: Output at Scale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn one flat `RESULTS.md` into a set that stays reviewable and traceable at hundreds of providers — a compact index, generated per-provider pages, and a machine-readable export — and finish the documentation the grading redesign left stale.

**Architecture:** `render.py` stops being a print-to-stdout script and becomes a generator that writes four artifacts and can verify them (`--check`) for CI. Aggregation (the median-and-worst-never-a-mean doctrine) splits into its own module so it can be tested directly rather than through rendered markdown.

**Tech Stack:** Python 3 (stdlib + `pyyaml`), pytest. No new dependencies. Nothing in `bench/` changes; no grading logic changes.

## Global Constraints

From `docs/superpowers/specs/2026-07-16-p99bench-graded-categories-design.md` and the repo's doctrine:

- **Generated files are never hand-edited.** CI regenerates and fails the PR if the committed copy differs. That is a trust property, not a lint.
- **Median AND worst, never a mean** (§8.2). A machine fine at 03:00 and unusable at 18:00 must read as exactly that, not as a mediocre average.
- **`MIN_RUNS_FOR_SPREAD = 3`.** Two points is a line segment, not a distribution.
- **Time variance and host variance are different questions** and must be reported separately. Same `host_id`, different hours = noisy neighbours. Different `host_id`, same product = fleet spread. Averaging them together answers neither.
- **Worst grade across runs per product.** A machine that passes at 03:00 and fails at 18:00 is a machine that fails.
- **Generated JSON must not live under `results/`** (§8.1). `validate.py` and `render.py` both discover files with `rglob("*.json")` over `results/`; a generated `results/index.json` would be picked up and fail validation. Hence `data/`.
- **`README.md` files ARE safe inside `results/`** — invisible to that glob, and being in-tree is the point: GitHub renders them when browsing `results/<provider>/`.
- **Provider and region stay separate directory levels.** Folding them into `ovh-zrh` would make "how is OVH overall?" permanently unanswerable.
- **Network stays informational in the tables.** Only `worker_probe`/`playwright_node` grade it (§6.5).
- **Price is recorded, never graded.**
- `tools/` is Python and must never be needed on a benchmarked host; `bench/` stays pure bash.

## Notes for the implementer

**Every plan in this project so far has been bitten by a half-migrated state.** Phase 1 shipped a schema that rejected every result its own stages produced. Phase 2's final review found `bench/run-all.sh` still calling a tool Task 7 had deleted — silently, so a submitter would get an ungraded result and a rejected PR. Both were seams between tasks that no per-task review looked at.

This plan's seam is the **stdout contract**. Today `render.py` prints RESULTS.md and CI does:

```yaml
python3 tools/render.py > /tmp/RESULTS.md
diff -q RESULTS.md /tmp/RESULTS.md
```

`CONTRIBUTING.md:75` documents the same. The moment `render.py` writes four artifacts, that contract breaks — and it breaks *quietly*, because `render.py > RESULTS.md` would still produce a file, just the wrong one. Task 5 repoints CI and Task 6 the docs; do not leave them for later.

State you inherit (branch `design/graded-categories`, 38 commits ahead of main, unmerged):
- Results are **v2**: `schema_version: "2.0"`, no `verdict`, a `grades` block with `bands_version`, `storage_class`, `categories: {disk,cpu,ram,network}` (each `grade`/`bound_by`/`metrics`), and `profiles` (7, each `grade`/`bound_by`, optionally `reason`, `network_half_unmeasured`).
- `tools/grade.py` exports `compute(result, thresholds)`. `tools/verdict.py` is gone.
- `tools/render.py` is 473 lines, already reads `grades`, already prints a summary + per-product detail + network + "why runs failed" sections.
- 81 non-docker tests pass. Do not break any.
- `cpu` grades read `?` on most rows and `ram` on all 10 — those hosts predate the Phase 1 stages. That is the honest report; do not paper over it.

Inspect a real grades block before writing any renderer:

```bash
python3 -c "import json;print(json.dumps(json.load(open('results/ovh/zrh/2026-07-16T1024-vps-1-lz-2026.json'))['grades'],indent=2))"
```

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `tools/aggregate.py` | **new**: load, group by product/host, spread, worst-grade. The variance doctrine, testable directly. | 1 |
| `tools/render.py` | CLI + `--check` + orchestration; delegates rendering | 1, 2, 3, 4 |
| `tools/writers.py` | **new**: the four artifacts (index md, provider md, json, csv) | 2, 3, 4 |
| `tests/test_aggregate.py` | the doctrine: median+worst, never mean; MIN_RUNS; variance split | 1 |
| `tests/test_render_check.py` | `--check` catches a stale artifact | 1, 5 |
| `tests/test_writers.py` | index rows, provider pages, export shape | 2, 3, 4 |
| `data/index.json`, `data/index.csv` | **new**, generated | 2 |
| `results/<provider>/README.md` | **new**, generated | 3 |
| `RESULTS.md` | becomes the compact index | 4 |
| `.github/workflows/validate.yml` | `--check` replaces the stdout diff | 5 |
| `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `METHODOLOGY.md` | stale refs + new layout | 6 |

---

### Task 1: Split aggregation out, add `--check`, prove the refactor changes nothing

Refactor first, prove it is inert, *then* change the output. Doing both at once means a diff in RESULTS.md and no way to tell whether it came from the refactor or the redesign.

**Files:**
- Create: `tools/aggregate.py`
- Modify: `tools/render.py`
- Modify: `tests/test_render.py` (it imports the functions you are moving — see below)
- Create: `tests/test_aggregate.py`
- Create: `tests/test_render_check.py`

**`tests/test_render.py` already exists and will break.** It does `import render as R`
and calls `R.spread(...)` and `R.render_run_row(...)`. Moving `spread` into
`aggregate.py` breaks the first immediately; Task 3 moves `render_run_row` into
`writers.py` and breaks the second.

Repoint its imports as the functions move. **Do not delete or weaken those tests** —
they exist because Phase 2's final whole-branch review caught `render.py` feeding the
"stall" column from `cpu.intrinsic_latency_max_us`, the metric spec §5.1 killed, under
the new name's label. They are the guard on that fix.

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `load_all() -> list[dict]`
  - `by_product(runs) -> dict[tuple[str,str,str], list[dict]]` — keyed `(provider, region, product)`
  - `by_host(runs) -> dict[str, list[dict]]` — keyed `host_id`
  - `worst_grade(runs, profile) -> str`
  - `worst_category(runs, category) -> str`
  - `spread(runs, path) -> tuple[str, str]` — `(median_str, worst_str)`; median is `"-"` below `MIN_RUNS_FOR_SPREAD`
  - `MIN_RUNS_FOR_SPREAD = 3`
  - `render.py` gains `--check` (exit 1 + name every stale artifact) and `--write` (default).

Tasks 2-4 import from `tools/aggregate.py`; Task 5's CI calls `--check`.

- [ ] **Step 1: Capture the current output as the refactor's contract**

```bash
python3 tools/render.py > /tmp/before-refactor.md
wc -l /tmp/before-refactor.md
```

Keep that file. Step 5 diffs against it. This is the whole safety of the task.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_aggregate.py`:

```python
"""The reporting doctrine, tested directly rather than through rendered markdown.

These rules are not formatting preferences. There are two distinct sources of
variance and they support different conclusions:

  time variance (same host_id, different hours)  -> noisy neighbours
  host variance (different host_id, same product) -> the fleet is not uniform

A mean over both says "roughly 3ms" and answers neither. Worst case is the
honest summary, because the tail is what users experience.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from aggregate import (  # noqa: E402
    MIN_RUNS_FOR_SPREAD, by_host, by_product, load_all, spread, worst_grade,
)


def _run(host, hour, fsync, grade="A"):
    return {
        "run": {"host_id": host, "local_hour": hour, "tool_version": "0.2.0"},
        "provider": {"name": "p", "region": "r", "product": "x"},
        "disk": {"wal_fsync": {"p999_us": fsync}},
        "grades": {"profiles": {"postgres_oltp": {"grade": grade}},
                   "categories": {"disk": {"grade": grade}}},
    }


def test_spread_reports_median_and_worst_never_a_mean():
    # 1ms, 2ms, 60ms. A mean would say 21ms -- a machine that does not exist.
    # The median says 2ms (typical) and the worst says 60ms (what users feel).
    runs = [_run("h", 3, 1000), _run("h", 12, 2000), _run("h", 18, 60000)]
    med, worst = spread(runs, "disk.wal_fsync.p999_us")
    assert "2" in med and "60" in worst
    assert "21" not in med and "21" not in worst, "a mean leaked into the report"


def test_no_spread_below_three_runs():
    # Two points is a line segment, not a distribution.
    runs = [_run("h", 3, 1000), _run("h", 18, 60000)]
    med, worst = spread(runs, "disk.wal_fsync.p999_us")
    assert med == "-", "computed a median from fewer than 3 runs"
    assert "60" in worst, "worst must still be reported from any number of runs"


def test_min_runs_for_spread_is_three():
    assert MIN_RUNS_FOR_SPREAD == 3


def test_worst_grade_across_runs_wins():
    # A machine that passes at 03:00 and fails at 18:00 is a machine that fails.
    runs = [_run("h", 3, 1000, "A"), _run("h", 18, 60000, "F")]
    assert worst_grade(runs, "postgres_oltp") == "F"


def test_worst_grade_treats_unknown_as_unknown_not_as_good():
    runs = [_run("h", 3, 1000, "A"), _run("h", 18, 1000, "?")]
    assert worst_grade(runs, "postgres_oltp") == "?"


def test_by_host_separates_time_variance_from_host_variance():
    runs = [_run("h1", 3, 1000), _run("h1", 18, 60000), _run("h2", 10, 2000)]
    hosts = by_host(runs)
    assert len(hosts) == 2
    assert len(hosts["h1"]) == 2, "same machine, different hours = time variance"


def test_by_product_groups_provider_region_product():
    runs = [_run("h1", 3, 1000), _run("h2", 10, 2000)]
    key = ("p", "r", "x")
    assert list(by_product(runs)) == [key]
    assert len(by_product(runs)[key]) == 2


def test_load_all_reads_the_real_corpus():
    runs = load_all()
    assert len(runs) >= 10
    assert all("grades" in r for r in runs), "a v1 result leaked into the corpus"
```

Create `tests/test_render_check.py`:

```python
"""--check is the CI contract: generated files are never hand-edited."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _render(*args):
    return subprocess.run(
        [sys.executable, "tools/render.py", *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )


def test_check_passes_on_a_clean_tree():
    assert _render("--check").returncode == 0, _render("--check").stdout


def test_check_fails_and_names_the_file_when_an_artifact_is_stale(tmp_path):
    target = ROOT / "RESULTS.md"
    original = target.read_text()
    try:
        target.write_text(original + "\nhand-edited\n")
        proc = _render("--check")
        assert proc.returncode != 0, "--check passed on a hand-edited artifact"
        assert "RESULTS.md" in proc.stdout + proc.stderr, "did not name the stale file"
    finally:
        target.write_text(original)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_aggregate.py tests/test_render_check.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'aggregate'`, and `--check` unrecognised.

- [ ] **Step 4: Implement**

Create `tools/aggregate.py` by MOVING the existing functions out of `render.py` — `load_all`, `dig`, `spread`, `worst_grade`, `profile_grade`, `by_host`/`by_product` grouping, `MIN_RUNS_FOR_SPREAD` — and adding `worst_category` alongside `worst_grade`. Do not rewrite their logic; this step is a move, and Step 5 proves it.

Carry the docstrings across. The comments explaining *why* median-and-worst rather than a mean, and why `MIN_RUNS_FOR_SPREAD = 3`, are the reasoning future readers need; losing them in a move is how doctrine quietly rots.

In `tools/render.py`, add the CLI:

```python
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate every published artifact from results/."
    )
    ap.add_argument(
        "--check", action="store_true",
        help="verify the committed artifacts match what results/ would generate; "
             "exit 1 and name each stale file. This is what CI runs.",
    )
    args = ap.parse_args()

    runs = aggregate.load_all()
    artifacts = build_all(runs)   # {Path: str}

    if args.check:
        stale = [p for p, body in artifacts.items()
                 if not p.exists() or p.read_text() != body]
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}", file=sys.stderr)
        if stale:
            print("run: python3 tools/render.py", file=sys.stderr)
            return 1
        return 0

    for p, body in artifacts.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        print(f"wrote {p.relative_to(ROOT)}", file=sys.stderr)
    return 0
```

For this task `build_all` returns exactly one entry — `{ROOT / "RESULTS.md": <the current output>}` — so the refactor stays inert. Tasks 2-4 add entries.

Note this changes the invocation contract: `render.py` now WRITES rather than printing. Task 5 repoints CI and Task 6 the docs. Until then CI is red on the stdout diff — expected, and healed by Task 5.

- [ ] **Step 5: Prove the refactor is inert**

This is the point of the task:

```bash
python3 tools/render.py
diff /tmp/before-refactor.md RESULTS.md && echo "IDENTICAL - refactor is inert"
```

Expected: **no diff.** If there is one, the move changed behaviour — fix it now, while the cause is one commit wide. Do not proceed with a diff outstanding.

```bash
python3 -m pytest tests/test_aggregate.py tests/test_render_check.py -v
python3 -m pytest tests/ -q -m "not docker"
```

Expected: the new tests pass; 81 + 10 pass overall.

- [ ] **Step 6: Commit**

```bash
git add tools/aggregate.py tools/render.py tests/test_aggregate.py tests/test_render_check.py tests/test_render.py RESULTS.md
git commit -m "refactor: split aggregation out of render, add --check

Refactor first, prove it inert, then change the output -- otherwise a diff in
RESULTS.md has two possible causes and no way to tell them apart. Verified:
byte-identical output before and after.

The variance doctrine (median AND worst, never a mean; no spread below 3 runs;
time variance vs host variance kept separate) now lives in aggregate.py and is
tested directly instead of through rendered markdown. Those rules are the
reason this project reports what it does -- a mean over both variance types
says 'roughly 3ms' and answers neither question.

render.py now WRITES rather than prints, because it is about to produce four
artifacts and a stdout redirect cannot express that. CI is red on the old
stdout diff until Task 5 repoints it."
```

---

### Task 2: `data/index.json` and `data/index.csv`

The readable front door does not scale to querying, and the raw tree does not scale to reading. The export serves the first without touching the second.

**Files:**
- Create: `tools/writers.py`
- Modify: `tools/render.py`
- Create: `tests/test_writers.py`
- Create (generated): `data/index.json`, `data/index.csv`

**Interfaces:**
- Consumes: `tools/aggregate.py`.
- Produces:
  - `index_rows(runs) -> list[dict]` — one row per `(provider, region, product)`, the shared shape behind the index table, the export, and the provider pages.
  - `write_index_json(rows) -> str`, `write_index_csv(rows) -> str`.

Row keys (fixed — Tasks 3-4 and the CSV header depend on them):
`provider`, `region`, `product`, `storage_class`, `machines`, `runs`,
`fsync_p999_us_median`, `fsync_p999_us_worst`, `price_eur_month`,
`categories` (`{disk,cpu,ram,network}` -> grade), `profiles` (7 names -> grade).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_writers.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_writers.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'writers'`.

- [ ] **Step 3: Implement**

Create `tools/writers.py` with `DATA_DIR = ROOT / "data"`, `index_rows`, `write_index_json`, `write_index_csv`.

`write_index_json` emits an object, not a bare array — a top-level object leaves room to version the export:

```json
{
  "bands_version": "2.0",
  "generated_from": "results/",
  "results": [ ... ]
}
```

Do NOT put a timestamp in it. The artifact is diffed by CI; a timestamp would make every run stale and train people to ignore the check.

Sort rows deterministically by `(provider, region, product)`. CSV flattens `categories`/`profiles` to `cat_<name>`/`prof_<name>` columns.

Wire both into `render.py`'s `build_all`.

- [ ] **Step 4: Run tests and generate**

```bash
python3 -m pytest tests/test_writers.py -v
python3 tools/render.py
head -30 data/index.json && head -3 data/index.csv
```

Expected: tests pass; both files written.

Confirm the export did not land where the validator will trip on it:

```bash
python3 tools/validate.py results/
```

Expected: `10/10 valid` — not 11, and no error about `data/index.json`.

- [ ] **Step 5: Commit**

```bash
git add tools/writers.py tools/render.py tests/test_writers.py data/
git commit -m "feat: data/index.json and data/index.csv

The readable front door does not scale to querying and the raw tree does not
scale to reading; the export serves the first without touching the second.

Lives in data/, not results/: validate.py and render.py both discover result
files with rglob('*.json') over results/, so a generated results/index.json
would be picked up and rejected as a malformed result. A test pins that.

Carries the flagship fsync p99.9 alongside the grades -- letter grades tie
constantly at scale, and the index has to stay sortable by the number that
actually decides whether a database is viable. No timestamp: the artifact is
diffed by CI, and a timestamp would make every run stale and train people to
ignore the check."
```

---

### Task 3: Per-provider pages

**Files:**
- Modify: `tools/writers.py`, `tools/render.py`
- Modify: `tests/test_writers.py`
- Modify: `tests/test_render.py` (`render_run_row` moves here — repoint its import; do
  not weaken those tests, they guard the stall-column fix from Phase 2's final review)
- Create (generated): `results/<provider>/README.md`

**Interfaces:**
- Consumes: `index_rows`, `tools/aggregate.py`.
- Produces: `write_provider_page(provider, runs) -> str`; `provider_pages(runs) -> dict[Path, str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_writers.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_writers.py -v -k provider
```

Expected: FAIL — `cannot import name 'write_provider_page'`.

- [ ] **Step 3: Implement**

`write_provider_page(provider, runs)` renders, per provider:

- a heading and a one-line summary (regions, products, machines, runs)
- a per-product section carrying what today's RESULTS.md "Detail" section carries: price, boot-volume-vs-data-volume, per-machine run tables, the time-variance and host-variance statements, and the `<details>`-wrapped full run list
- a "what bound each grade" line per product, sourced from `grades.*.bound_by`

Move that rendering out of `render.py` rather than duplicating it — after this task, `RESULTS.md` no longer carries per-run detail (Task 4), so the code has exactly one home.

Keep the existing prose that explains the two variance types. It is the reason the tables look the way they do, and it belongs next to them.

Wire `provider_pages` into `build_all`.

- [ ] **Step 4: Run tests and generate**

```bash
python3 -m pytest tests/test_writers.py -v
python3 tools/render.py
ls results/*/README.md
head -40 results/ovh/README.md
```

**Read the OVH page.** It must show waw and zrh as different machines with different binding constraints. If a reader cannot tell them apart from that page, the detail layer has lost what the index promises.

- [ ] **Step 5: Commit**

```bash
git add tools/writers.py tools/render.py tests/test_writers.py tests/test_render.py results/
git commit -m "feat: generated per-provider pages

Detail moves out of RESULTS.md so a threshold change touches one provider's
page instead of rewriting the whole document, and a PR diff stays local to the
provider it concerns.

They live inside results/ deliberately: README.md is invisible to the
rglob('*.json') the validator uses, and being in-tree means GitHub renders the
page when you browse results/<provider>/.

Each product says what bound its grade. A grade without its binding constraint
is a letter with no lead -- and naming it is exactly what the old single-word
verdict could never do."
```

---

### Task 4: RESULTS.md becomes the index

**Files:**
- Modify: `tools/writers.py`, `tools/render.py`
- Modify: `tests/test_writers.py`
- Modify (generated): `RESULTS.md`

**Interfaces:**
- Consumes: `index_rows`, `provider_pages`.
- Produces: `write_index_md(rows) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_writers.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_writers.py -v -k index
```

Expected: FAIL — `cannot import name 'write_index_md'`.

- [ ] **Step 3: Implement**

`write_index_md(rows)` renders `RESULTS.md` as:

- the autogenerated banner and the existing disclaimer (keep it — it is the honest framing, not boilerplate)
- one row per `(provider, region, product)`:

```
| Provider | Region | Product | Class | Machines | Runs | fsync p99.9 worst | disk | cpu | ram | net | pg | ts | patroni | redis | probe | pw | nuxt |
```

- a short reading guide: what the grades mean, that `?` means unmeasured rather than bad, and that the worst across runs is what is shown
- links into `results/<provider>/README.md` and `data/index.json`
- the network section (still informational — only worker profiles grade it)
- the "why runs failed" roll-up, sourced from `bound_by`

Delete the per-run detail rendering from `RESULTS.md`; it now lives on the provider pages (Task 3).

Keep the `?`-means-unmeasured note prominent. Most `cpu` cells and every `ram` cell read `?` on the current corpus, and a reader must not mistake that for a bad grade — it means those hosts predate the stages that measure it.

- [ ] **Step 4: Run tests and generate**

```bash
python3 -m pytest tests/test_writers.py -v
python3 tools/render.py
python3 tools/render.py --check && echo "check passes on a fresh render"
cat RESULTS.md | head -30
wc -l RESULTS.md
```

**Read it.** The acceptance test a diff cannot check: waw and zrh must read as different machines. Also confirm the file got *shorter* — that was the point.

- [ ] **Step 5: Commit**

```bash
git add tools/writers.py tools/render.py tests/test_writers.py RESULTS.md
git commit -m "feat: RESULTS.md becomes a compact index

One flat file was 180 lines at 5 products. At several hundred it is
unreviewable, and a threshold change rewrites all of it in one diff. Detail now
lives on per-provider pages; this is the front door.

Rows carry the category grades, the profile grades, the storage class and the
flagship fsync p99.9 -- letters tie constantly at scale, so the number has to
stay there for sorting.

Says plainly that ? means unmeasured, not bad. Most cpu cells and every ram
cell read ? on the current corpus because those hosts predate the stages that
measure them; a reader must not read that as a failing grade."
```

---

### Task 5: CI

Without this the branch is red: CI still does `render.py > /tmp/RESULTS.md`, which now writes RESULTS.md to stdout-nothing and diffs an empty file.

**Files:**
- Modify: `.github/workflows/validate.yml`
- Modify: `tests/test_render_check.py`

**Interfaces:**
- Consumes: `render.py --check`.
- Produces: nothing.

- [ ] **Step 1: Update the workflow**

Replace the "Check RESULTS.md is up to date" step with:

```yaml
      - name: Check generated artifacts are current
        # Generated files are never hand-edited: RESULTS.md, the per-provider
        # pages and the data export are all a pure function of results/ plus
        # schema/thresholds.yaml. --check names every stale file at once
        # instead of failing on the first diff.
        run: |
          python3 tools/render.py --check
```

Verify the `pytest` job still picks up the new test files (it runs `tests/ -m "not docker"`, so it should — confirm rather than assume).

- [ ] **Step 2: Add a test that CI cannot silently stop checking**

Append to `tests/test_render_check.py`:

```python
def test_ci_runs_render_check():
    # The generated-files-are-never-hand-edited property is only real if CI
    # enforces it. A workflow edit that drops this is a silent loss of the
    # trust property, so pin it.
    wf = (ROOT / ".github" / "workflows" / "validate.yml").read_text()
    assert "render.py --check" in wf


def test_ci_no_longer_uses_the_stdout_contract():
    # render.py writes files now. `render.py > RESULTS.md` would produce an
    # empty RESULTS.md and a passing diff against... nothing.
    wf = (ROOT / ".github" / "workflows" / "validate.yml").read_text()
    assert "render.py >" not in wf
```

- [ ] **Step 3: Verify**

```bash
python3 -m pytest tests/test_render_check.py -v
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/validate.yml')); print('workflow OK')"
python3 tools/render.py --check && echo "artifacts current"
```

Prove `--check` is a real tripwire, not decoration:

```bash
cp RESULTS.md /tmp/R.bak
echo "hand-edited" >> RESULTS.md
python3 tools/render.py --check; echo "exit=$? (must be 1)"
cp /tmp/R.bak RESULTS.md
python3 tools/render.py --check; echo "exit=$? (must be 0)"
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/validate.yml tests/test_render_check.py
git commit -m "feat: CI verifies every generated artifact with render.py --check

render.py writes four files now, so the old stdout diff was not just wrong but
silently wrong -- `render.py > /tmp/RESULTS.md` would compare against an empty
file and pass.

--check names every stale artifact at once rather than failing on the first.
Two tests pin that CI keeps running it: the never-hand-edited property is only
real while CI enforces it."
```

---

### Task 6: Documentation

The grading redesign left the docs describing a model that no longer exists. A benchmark whose README advertises the wrong output is not a small thing — this is the project's front door, and it invites providers to submit.

**Files:**
- Modify: `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `METHODOLOGY.md`

**Interfaces:** none.

- [ ] **Step 1: Find every stale claim**

```bash
grep -rn "redis_aof\|verdict\.py\|pass/marginal/fail\|marginal\|render.py >" \
  README.md CONTRIBUTING.md CLAUDE.md METHODOLOGY.md
```

Fix each. Known at time of writing: `README.md` (3), `CLAUDE.md` (2), `CONTRIBUTING.md` (1). Re-run the grep — Tasks 1-5 may have added more.

- [ ] **Step 2: README.md**

- The "What you get" sample output block still shows `pass/marginal/fail` and `redis_aof`. Replace with a real grades summary — generate one and paste it, do not invent it:
  `python3 tools/grade.py results/ovh/zrh/2026-07-16T1024-vps-1-lz-2026.json`
- The verdict paragraph ("The verdict is not an opinion...") is still true in substance — it is the trust property — but says `verdict` and points at `thresholds.yaml` for pass/fail lines. Reword for grades and bands; keep the argument, which is the best paragraph in the file.
- Add the new layout: `RESULTS.md` is the index, `results/<provider>/README.md` is the detail, `data/index.json` is the export.
- The Scripts table gained `02b-cpu-steady.sh` in Phase 1 — verify it is still accurate.

- [ ] **Step 3: CONTRIBUTING.md**

- Line ~75: `python3 tools/render.py > RESULTS.md` is now wrong. It is `python3 tools/render.py`, and it writes RESULTS.md, the provider pages and the export.
- The "What CI will reject" list still says "A `verdict` that does not match what `thresholds.yaml` computes" and "RESULTS.md not regenerated". Update to grades and to all generated artifacts.
- Add: generated artifacts must be committed with the result.

- [ ] **Step 4: CLAUDE.md**

Its "Verdicts are code, not opinion" section describes `verdict.py`, `pass < marginal < unknown < fail`, and profiles that no longer exist. Rewrite for the band model, the categories/profiles split, `grade.py`, and the F-beats-`?` precedence. Keep the section's shape — it is a map for future readers, and the surrounding architecture notes are still accurate.

- [ ] **Step 5: METHODOLOGY.md**

Phase 2 fixed its two broken claims. What remains is the doctrine the redesign added, which METHODOLOGY.md is the right home for (spec §10 item 15):

- §4.4 broken-vs-quiet: a metric that produces one grade across the corpus is either broken (unreachable — never ship) or quiet (reachable, corpus happens clean — keep, its silence is a finding). Say that this project shipped three unreachable thresholds and nobody noticed until the corpus was analysed, and that a test now enforces the distinction.
- §4.5 storage class as a facet, never a curve.
- §6.5 why network is graded only where the workload IS the network.

Do not restate the bands — THRESHOLDS.md owns those. METHODOLOGY.md owns the reasoning.

- [ ] **Step 6: Verify**

```bash
grep -rn "redis_aof\|verdict\.py\|render.py >" README.md CONTRIBUTING.md CLAUDE.md METHODOLOGY.md \
  || echo "no stale references"
python3 -m pytest tests/ -q -m "not docker"
python3 tools/render.py --check && echo "artifacts current"
```

- [ ] **Step 7: Commit**

```bash
git add README.md CONTRIBUTING.md CLAUDE.md METHODOLOGY.md
git commit -m "docs: describe the model that actually exists

The redesign left the front door advertising pass/marginal/fail, redis_aof, and
a verdict.py that no longer exists -- on a project that invites providers to
submit results. README's sample output now comes from a real grade.py run.

CONTRIBUTING's 'render.py > RESULTS.md' would now write an empty file.

METHODOLOGY gains the doctrine the redesign added and it is the right home for:
broken-vs-quiet (this project shipped three thresholds no VM could pass and
nobody noticed until the corpus was analysed -- a test enforces the distinction
now), storage class as a facet never a curve, and why network is graded only
where the workload is the network. THRESHOLDS.md owns the numbers; this owns
the reasoning."
```

---

## Definition of done

- [ ] `python3 -m pytest tests/ -q -m "not docker"` — all pass
- [ ] `python3 tools/render.py --check` — exit 0 on a fresh tree, exit 1 naming the file on a hand-edited one
- [ ] `python3 tools/validate.py results/` — 10/10 valid, and `data/index.json` is NOT picked up as a result
- [ ] `RESULTS.md` is shorter than before and carries no per-run detail
- [ ] `results/ovh/README.md` and `results/hetzner/README.md` exist and are generated
- [ ] `data/index.json` and `data/index.csv` exist, deterministic, no timestamp
- [ ] waw and zrh read as different machines in the index, in the OVH page, and in the export
- [ ] No `mean`/`average` anywhere in a generated artifact
- [ ] No stale `redis_aof` / `verdict.py` / `render.py >` references in the docs
- [ ] CI runs `render.py --check`

## What this plan deliberately does not do

- **No grading changes.** No bands, no profiles, no `grade.py`. If a grade changes, something is wrong.
- **No `bench/` changes.** No new measurement.
- **No recalibration.** The four provisional bands (spec §11) need a corpus measured with Phase 1's stages, which cannot exist until someone runs them on real hardware. Spec Phase 5.
- **No per-region pages.** Per-provider is the right granularity now; per-region is the escape hatch if one provider outgrows a page, and needs no other change.
- **No web UI, no hosted dashboard.** `data/index.json` exists so someone else can build that.

## Known risk

`--check` compares whole-file text. If any writer becomes nondeterministic — dict ordering, float repr, a stray timestamp — CI goes red on a clean tree and the team learns to distrust the check. `test_json_export_is_valid_and_stable` guards the export; the markdown writers are guarded only by `--check` itself passing twice in a row. If you see a spurious stale, fix the nondeterminism rather than loosening the comparison.
