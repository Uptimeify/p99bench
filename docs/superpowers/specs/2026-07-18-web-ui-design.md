# p99bench Web UI — Design

**Date:** 2026-07-18
**Status:** Approved for planning
**Author:** brainstorming session

## Purpose

A published web UI to explore p99bench results: filter, compare, and visualize
per-host tail-latency grades. Complements the existing generated Markdown
(`RESULTS.md`, per-provider READMEs) with an interactive surface — sortable
tables, per-host detail, charts.

Non-goal: change how anyone contributes. Results still land as JSON via Git PR;
the UI is a pure *consumer* of the same data the existing pipeline publishes.

## Constraints & principles

- **Contribution stays Git.** No submission UI, no backend, no database. A new
  result appears on the site only after its JSON is merged to `main`.
- **UI is a consumer, never a generator.** `tools/render.py` remains the single
  source of `data/index.json` and the per-provider aggregates. The UI build
  must not recompute grades or re-aggregate — if it did, the site could diverge
  from what CI's `render.py --check` guarantees. The UI reads committed files.
- **Static only.** No runtime data fetch, no server. Every page pre-rendered.
- **Honor the variance doctrine.** Where the UI aggregates across runs it shows
  median *and* worst, never a mean, and hides a spread below
  `MIN_RUNS_FOR_SPREAD = 3` (mirrors `tools/aggregate.py`).
- **No invented data.** Sustained performance exists in the result JSON as
  `first_min` / `last_min` / `degradation_pct` only — there is **no** per-second
  time series. The UI shows first-vs-last degradation, not a curve.

## Hosting & rebuild

- **GitHub Pages**, served from this repo (`Uptimeify/p99bench`).
  - `site: https://uptimeify.github.io`, `base: /p99bench`.
- New workflow `.github/workflows/deploy-site.yml`, separate from `validate.yml`:
  - Triggers: `push` to `main`, `schedule` (nightly cron), `workflow_dispatch`.
  - Steps: checkout → setup-node → `npm ci` (in `web/`) → `astro build` →
    upload `web/dist` artifact → deploy to Pages.
  - The deploy job **consumes** the committed `data/index.json`; it does not run
    `render.py`. A stale index is a visible failure of CI's existing
    `render.py --check`, not a silent site/CI divergence.
- On-push gives near-instant updates on merge; nightly cron is a backstop;
  manual dispatch rebuilds on demand.

## Architecture

New `web/` directory = an Astro project, isolated from `bench/` and `tools/`.

**Data plumbing (build-time, no Python):**
- Comparison table reads `../data/index.json` (the aggregated summary:
  provider, region, product, storage_class, price, category + profile grades,
  fsync p99.9 median/worst, run/machine counts, `bound_by_counts`).
- Detail and chart pages glob the full result files
  (`import.meta.glob('../results/**/*.json')`), which carry every metric plus
  its embedded per-metric grade (`grades.categories.<cat>.metrics.<m>.{grade,value}`)
  and `bound_by`.
- Chart-shaped data is reshaped into component props at build time; the client
  never fetches JSON.

**Full static SSG.** Every result gets its own pre-rendered page via
`getStaticPaths` — shareable URLs, deep-linkable, good SEO. (Rejected: SPA with
client fetch — worse deep-links; MD-only — no filtering/charts.)

## Pages

| Route | Source | Content |
|---|---|---|
| `/` | `data/index.json` | Filterable + sortable comparison grid. Columns: provider, region, product, storage_class, price, category grades (disk/cpu/ram/network), profile grades. Filters: provider, storage_class, profile, minimum grade, price range. Grade rendered as A–F / `?` chips. Client-side filter/sort over embedded JSON (no fetch). |
| `/host/<provider>/<region>/<stamp>` | full result JSON via `getStaticPaths` | Every metric grouped by domain (disk, cpu, ram, network), each with its embedded grade and value. Shows `bound_by` (which metric set each category/profile grade). Run metadata (host_id, submitter, timestamp, price). Sustained shown as first-vs-last + degradation_pct. |
| `/provider/<name>` | aggregated from that provider's results | Median **and** worst spread per metric/grade; spread hidden below 3 runs. Interactive version of the current per-provider README. Links to each host page. |
| `/charts` (or a charts section) | index.json + full JSONs | See below. |

### Charts (Chart.js)

- **Price vs. grade scatter** — price_eur_month against a chosen category/profile
  grade; spot cheap-and-good vs expensive-and-bad.
- **fsync p99.9 distribution** — bar chart of `fsync_p999_us_worst` across hosts,
  log-friendly (values span ~1.8 ms to ~242 ms).
- **Grade heatmap** — provider/region rows × profile columns, cell colored by
  grade. (Hand-rolled SVG acceptable here if Chart.js is awkward.)
- **Sustained degradation** — first-min vs last-min bars (+ `degradation_pct`)
  for disk IOPS and cpu eps. Explicitly not a time series.

Chart datasets are precomputed at build time and passed as props.

## Design vibe

Data-heavy, grade-forward. A–F color scale (green → red), `?` neutral grey.
Monospace numerics, dense tables, high information density. Consistent with the
project's "declassified benchmark report" character. Grade color mapping is a
single shared module reused by table chips, heatmap cells, and detail grades.

## Testing

- Build must succeed against the current `results/` and `data/index.json`.
- A smoke check that `getStaticPaths` produces one page per result file and the
  page count matches the number of JSONs under `results/`.
- Grade color mapping unit-tested (A–F + `?` all mapped; unknown → grey).
- Filter/sort logic (pure functions over the row list) unit-tested.
- Lightweight: this is a consumer of validated data — no need to re-test grades
  or schema (CI already does via `validate.py` / `grade.py` / `render.py --check`).

## Non-goals (YAGNI)

- No result-submission UI (Git PR only).
- No backend, database, search service, or runtime data fetch.
- No user accounts.
- No per-second sustained curves (data does not exist).
- No recomputation of grades or aggregates in the UI build.

## Open items for the plan

- Exact Astro version + minimal deps (`astro`, `chart.js`; no CSS framework
  unless the plan justifies one).
- Whether charts live on `/` , a dedicated `/charts`, or per-provider pages —
  plan to pick placement.
- `base`-path handling for all internal links (project-page subpath).
