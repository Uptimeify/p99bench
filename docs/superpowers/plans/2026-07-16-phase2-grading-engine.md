# p99bench Phase 2: Grading Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single pass/marginal/fail verdict with A–F grades per capability category and per workload profile, computed from VM-grounded bands, so the output can tell two machines apart.

**Architecture:** `schema/thresholds.yaml` becomes the single source of truth for bands (defined once per metric), categories (which metrics describe `disk`/`cpu`/`ram`/`network`), and profiles (which metrics each workload reads). `tools/grade.py` is a pure function from a result plus that file to a `grades` block: band lookup, worst-wins rollup, the binding constraint named. CI recomputes and diffs, exactly as today, so a hand-edited grade still fails the build.

**Tech Stack:** Python 3 (stdlib + `pyyaml` + `jsonschema`), pytest. No new dependencies. Nothing in `bench/` changes — this phase never touches a benchmark.

## Global Constraints

Copied from `docs/superpowers/specs/2026-07-16-p99bench-graded-categories-design.md` and the repo's existing doctrine:

- **Grades are a pure function of measured numbers and a versioned file.** Never a human judgement. CI recomputes and rejects any file whose stored grade differs (spec §4.3, §9.1).
- **No curve.** A grade never depends on who else submitted. Bands are absolute (§4.3).
- **Non-compensatory.** Worst-wins. Never average within a category (§4.2).
- **Precedence: F beats ?.** Any rule at `F` -> `F`; else any missing `required: true` rule -> `?`; else the worst band present (§4.2).
- **Storage class is a facet, never a curve.** It explains a grade, never softens one (§4.5).
- **Network gets a threshold only in a profile whose workload IS the network** (§6.5). `worker_probe` and `playwright_node` read network fields. `postgres_oltp`, `timescale_ingest`, `patroni_member`, `redis_sentinel`, `nuxt_ssr` read none.
- **Price is recorded, never graded.** Unchanged (§3).
- **Results are immutable measurements; grades are derived and always current** (§9.1). Migrating the derived block is expected; changing a measured number is not.
- **A metric that produces one grade across the whole corpus is either broken or quiet, and the author must say which** (§4.4). Enforced mechanically in Task 5.
- **Provisional bands must be labelled** (§11): `cpu.stall_p999_us`, `cpu.steady_state.degradation_pct`, `cpu.tls_verify_s`, `ram.bw_read_mbs` have no corpus behind them.
- `tools/` is Python and must never be needed on a benchmarked host; `bench/` stays pure bash.

## Notes for the implementer

**This plan must leave CI green on its own.** Phase 1 shipped a schema that rejected every result its own stages produced, because nothing tested a new result end-to-end. That bug reached the final review. The same trap is bigger here: `render.py` reads `.verdict` in four places, `schema_version` is `const: "1.0"`, and CI diffs both `verdict.py --in-place` and `render.py` output. So renaming `verdict` -> `grades` is **atomic** with migrating the 10 results and updating the renderer. Task 8 is not optional polish; without it the branch is red.

State you inherit from Phase 1 (branch `design/graded-categories`):
- New fields emitted and declared in the schema: `cpu.stall_p99_us`, `cpu.stall_p999_us`, `cpu.stall_max_us`, `cpu.stall_samples`, `cpu.steady_state.*`, `cpu.tls_verify_s`, `cpu.tls_sign_s`, `ram.bw_read_mbs`, `ram.bw_block_bytes`.
- The 10 published results are **v1**: they carry `verdict`, `schema_version: "1.0"`, real `cpu.intrinsic_latency_max_us`, cache-resident `ram.seq_read_mbs`, and none of the new fields.
- `P99BENCH_VERSION = "0.2.0"`. Spec §9.3 keys "needs re-run (tool >= 0.2.0)" off this exact string.
- 26 non-docker tests pass. Do not break any.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `schema/thresholds.yaml` | **rewritten**: bands per metric, categories, profiles | 1, 4 |
| `schema/bands.schema.json` | JSON Schema for thresholds.yaml itself — a typo in a band is a silent mis-grade | 1 |
| `tools/grade.py` | **new** (replaces `tools/verdict.py`): band lookup, rollup, storage_class | 2, 3 |
| `tools/verdict.py` | **deleted** — the name lies once it emits grades | 2 |
| `tests/test_grade.py` | band lookup, rollup precedence, bound_by | 2 |
| `tests/test_storage_class.py` | class derivation from fsync latency-per-op | 3 |
| `tests/test_profiles.py` | all 7 profiles resolve; cluster profiles declare their gap | 4 |
| `tests/test_band_doctrine.py` | §4.4 enforced: every metric broken, quiet, or provisional | 5 |
| `schema/result.schema.json` | v2: `grades` replaces `verdict`, `schema_version` -> "2.0" | 6 |
| `tools/migrate_v1_v2.py` | one-shot: recompute grades, bump schema_version, drop verdict | 6 |
| `tools/validate.py` | grades check, `bands_version` check, legacy tolerance | 7 |
| `tools/render.py` | minimal compat: read `grades`, keep current layout | 8 |
| `.github/workflows/validate.yml` | `verdict.py` -> `grade.py` | 8 |
| `THRESHOLDS.md` | rewritten: every band, reasoning, confidence | 8 |

---

### Task 1: The band data model

Bands are a property of the **metric** (physics), not of the profile. `postgres_oltp` and `redis_sentinel` both read `disk.wal_fsync.p999_us` and must agree on what a `B` means. Defining bands once, and having profiles reference metrics by name, is what makes "adding a profile is a YAML edit" true (spec §4.1).

A typo in a band bound is a silent mis-grade with no stack trace, so the file gets its own schema.

**Files:**
- Rewrite: `schema/thresholds.yaml`
- Create: `schema/bands.schema.json`
- Create: `tests/test_grade.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `schema/thresholds.yaml` v2 with top-level keys `bands_version: "2.0"`, `metrics: {}`, `categories: {}`, `profiles: {}`. Task 2's `grade.py` reads exactly this shape.

- [ ] **Step 1: Write the failing test**

Create `tests/test_grade.py`:

```python
import json
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())
BANDS_SCHEMA = json.loads((ROOT / "schema" / "bands.schema.json").read_text())


def test_thresholds_file_matches_its_own_schema():
    # A typo in a band bound is a silent mis-grade with no stack trace, so the
    # rules file is itself validated.
    jsonschema.Draft202012Validator(BANDS_SCHEMA).validate(THRESHOLDS)


def test_bands_are_monotonic_in_the_direction_of_the_op():
    # For op lte (lower is better) bounds must ascend A<B<C<D; for gte they must
    # descend. A transposed pair silently grades good hosts badly and vice
    # versa, and nothing else would catch it.
    for name, m in THRESHOLDS["metrics"].items():
        vals = [m["bands"][g] for g in ("A", "B", "C", "D")]
        if m["op"] == "lte":
            assert vals == sorted(vals), f"{name}: lte bands must ascend, got {vals}"
        else:
            assert vals == sorted(vals, reverse=True), (
                f"{name}: gte bands must descend, got {vals}"
            )


def test_every_metric_declares_why_and_confidence():
    for name, m in THRESHOLDS["metrics"].items():
        assert m.get("why"), f"{name} has no reasoning"
        assert m.get("confidence") in ("high", "medium", "low"), name


def test_every_category_metric_exists():
    for cat, metrics in THRESHOLDS["categories"].items():
        for path in metrics:
            assert path in THRESHOLDS["metrics"], f"{cat} references unknown {path}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_grade.py -v
```

Expected: FAIL — `FileNotFoundError: schema/bands.schema.json`.

- [ ] **Step 3: Write the schema for the rules file**

Create `schema/bands.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "p99bench band definitions",
  "description": "Schema for schema/thresholds.yaml. A typo in a band bound is a silent mis-grade, so the rules file is validated like a result file is.",
  "type": "object",
  "required": ["bands_version", "metrics", "categories", "profiles"],
  "additionalProperties": false,
  "properties": {
    "bands_version": { "type": "string" },
    "metrics": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "required": ["op", "bands", "why", "confidence"],
        "additionalProperties": false,
        "properties": {
          "op": { "enum": ["lte", "gte"] },
          "bands": {
            "type": "object",
            "required": ["A", "B", "C", "D"],
            "additionalProperties": false,
            "properties": {
              "A": { "type": "number" },
              "B": { "type": "number" },
              "C": { "type": "number" },
              "D": { "type": "number" }
            }
          },
          "unit": { "type": "string" },
          "why": { "type": "string" },
          "confidence": { "enum": ["high", "medium", "low"] },
          "provisional": {
            "type": "boolean",
            "description": "No corpus behind these bands yet (spec 11). Exempt from the doctrine check in tests/test_band_doctrine.py until real data exists."
          },
          "quiet": {
            "type": "boolean",
            "description": "Reachable in both directions, but the current corpus happens to be clean, so it yields one grade. Declared per spec 4.4 -- the opposite of a broken threshold, which no machine can ever pass."
          },
          "means": {
            "type": "object",
            "description": "Optional per-grade plain-English meaning, rendered in reports.",
            "additionalProperties": { "type": "string" }
          }
        }
      }
    },
    "categories": {
      "type": "object",
      "additionalProperties": { "type": "array", "items": { "type": "string" } }
    },
    "profiles": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "required": ["label", "rules"],
        "additionalProperties": false,
        "properties": {
          "label": { "type": "string" },
          "description": { "type": "string" },
          "network_half_unmeasured": {
            "type": "boolean",
            "description": "Cluster profiles grade only what one host decides. Commit latency in a sync Patroni cluster is local fsync + inter-node RTT; we measure the first term only, and must say so (spec 7.3)."
          },
          "rules": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["metric", "required"],
              "additionalProperties": false,
              "properties": {
                "metric": { "type": "string" },
                "required": { "type": "boolean" }
              }
            }
          }
        }
      }
    }
  }
}
```

- [ ] **Step 4: Write the metrics half of thresholds.yaml**

Replace `schema/thresholds.yaml` entirely. This step writes `bands_version`, `metrics` and `categories`; Task 4 adds `profiles`.

Every bound below is copied verbatim from spec §6. Do not round, reorder, or "improve" them — they are anchored to workload physics and validated against the corpus, and §6.6 records which grades each produces.

```yaml
# p99bench bands
#
# Single source of truth for grades. THRESHOLDS.md documents the reasoning;
# this file is what the code reads. Changing a number here changes every grade
# on the next render, and CI will regenerate them all.
#
# Bands are a property of the METRIC, not of a profile: postgres_oltp and
# redis_sentinel both read wal_fsync.p999_us and must agree on what a B means.
# Profiles reference metrics by name, which is why adding a profile is a YAML
# edit and needs no new measurement.
#
# Semantics:
#   op: lte -> lower is better. value <= bands.A -> A, <= bands.B -> B, ... else F.
#   op: gte -> higher is better. value >= bands.A -> A, >= bands.B -> B, ... else F.
#   F is implicit: worse than the D bound.
#
# Rollup is worst-wins and never averages (spec 4.2). Precedence: any F -> F;
# else any missing required rule -> ?; else the worst band present.
#
# Bands are ABSOLUTE, never a curve (spec 4.3). A grade must not depend on who
# else submitted: if the whole field is bad, the best of a bad lot is still bad.

bands_version: "2.0"

metrics:

  # --- disk ----------------------------------------------------------------

  disk.wal_fsync.p999_us:
    op: lte
    unit: us
    bands: { A: 1000, B: 3000, C: 10000, D: 50000 }
    confidence: high
    why: >
      The flagship. Every COMMIT waits on one fdatasync, alone, with no queue to
      hide behind. p99.9 is what the slowest transactions feel; the mean
      describes a commit nobody complains about.
    means:
      A: "local-NVMe/PLP class; tail invisible"
      B: "solid VM; ~300+ commits/s single-writer"
      C: "modest OLTP; 1-in-1000 commits stalls 10ms"
      D: "batch only; not a transactional host"
      F: "durability path broken"

  disk.wal_fsync.iops:
    op: gte
    unit: iops
    bands: { A: 5000, B: 1000, C: 333, D: 100 }
    confidence: medium
    why: >
      Latency-anchored, not IOPS-anchored: at QD1 this is ~1/mean-latency, so it
      describes the typical commit where p99.9 describes the tail. Bounds are
      200us / 1ms / 3ms / 10ms per durable write.

  disk.rand_read_8k.p99_us:
    op: lte
    unit: us
    bands: { A: 500, B: 2000, C: 5000, D: 15000 }
    confidence: high
    why: >
      8k is the Postgres page size; index lookups are random reads. A query
      doing 100 lookups at 5ms p99 has a real chance of one slow read.

  disk.rand_read_8k.iops:
    op: gte
    unit: iops
    bands: { A: 100000, B: 50000, C: 20000, D: 5000 }
    confidence: low
    why: "Generational marker rather than a workload requirement. Advisory."

  disk.rand_write_8k.iops:
    op: gte
    unit: iops
    bands: { A: 50000, B: 20000, C: 10000, D: 3000 }
    confidence: low
    why: "Checkpoint flush rate. Advisory."

  disk.seq_write.bw_mbs:
    op: gte
    unit: MB/s
    bands: { A: 1000, B: 500, C: 200, D: 100 }
    confidence: medium
    why: "Chunk writes and compression output are sequential and bulky."

  disk.seq_read.bw_mbs:
    op: gte
    unit: MB/s
    bands: { A: 2000, B: 1000, C: 500, D: 200 }
    confidence: low
    why: "Continuous aggregate refresh reads whole chunks. Advisory."

  disk.steady_state.degradation_pct:
    op: lte
    unit: pct
    bands: { A: 5, B: 15, C: 30, D: 50 }
    confidence: medium
    quiet: true
    why: >
      Burst credits. A 60s run measures the credit balance; 30 minutes measures
      the machine you will actually run. Quiet in the current corpus (0.0-2.0%
      across all 10 runs) -- reachable in both directions, the hosts simply do
      not throttle. Contrast a broken threshold, which no machine can ever pass.

  # --- cpu -----------------------------------------------------------------

  cpu.single_thread_eps:
    op: gte
    unit: events/s
    bands: { A: 1400, B: 1000, C: 700, D: 400 }
    confidence: medium
    why: >
      Redis, each Node worker, and each Postgres backend are bounded by one
      core. Contemporary server silicon lands ~1600-1800. This metric already
      caught a 4.7x starvation on ovh/waw (356 vs ~1600) that steal time missed.

  cpu.scaling_efficiency:
    op: gte
    unit: ratio
    bands: { A: 0.85, B: 0.70, C: 0.55, D: 0.40 }
    confidence: medium
    why: >
      multi / (single * cores). ~0.9 means physical cores; ~0.6 means SMT
      siblings sold as cores; well below means sharing physical cores with
      other tenants.

  cpu.steal_pct_under_load:
    op: lte
    unit: pct
    bands: { A: 0.5, B: 2, C: 5, D: 10 }
    confidence: high
    quiet: true
    why: >
      C is a correctness line, not a performance one: past ~5% Patroni
      heartbeats start missing TTLs and a failover fires because the host was
      busy, not because anything was wrong. Quiet in the current corpus
      (0.0-0.24%) -- these hosts genuinely do not steal. Keep it: it is
      insurance that fires on an oversubscribed host, and its silence is itself
      a finding.

  cpu.stall_p999_us:
    op: lte
    unit: us
    bands: { A: 100, B: 500, C: 2000, D: 10000 }
    confidence: low
    provisional: true
    why: >
      PROVISIONAL -- no corpus. Redis is one thread; each Node worker is one
      event loop. A stall is dead time for every client, with no other core to
      absorb it. Measured at SCHED_OTHER because that is the class Redis and
      Node actually run in. Recalibrate once real data exists (spec 11).

  cpu.steady_state.degradation_pct:
    op: lte
    unit: pct
    bands: { A: 5, B: 15, C: 30, D: 50 }
    confidence: low
    provisional: true
    why: >
      PROVISIONAL -- no corpus. CPU burst credits, the same trap the disk
      steady test exists for. This is the metric that catches a node pinned at
      a throttled baseline -- the Prague failure mode, which steal time did not
      see. Recalibrate once real data exists (spec 11).

  cpu.tls_verify_s:
    op: gte
    unit: ops/s
    bands: { A: 30000, B: 15000, C: 7000, D: 3000 }
    confidence: low
    provisional: true
    why: >
      PROVISIONAL -- no corpus. SSL checks are handshake-bound. A probe is the
      TLS CLIENT, and the client VERIFIES (the server signs) -- and verify is
      the expensive half, 2 scalar mults vs 1, so it is ~3x slower than sign.
      Do not swap this for tls_sign_s. Recalibrate once real data exists.

  # --- ram -----------------------------------------------------------------

  ram.bw_read_mbs:
    op: gte
    unit: MB/s
    bands: { A: 40000, B: 25000, C: 15000, D: 8000 }
    confidence: low
    provisional: true
    why: >
      PROVISIONAL -- and it cannot be calibrated from the existing corpus at
      all: those 10 numbers were measured with a 1M working set that sat in L2,
      so they describe cache, not memory, and cannot calibrate their own
      replacement. Anchored to DDR generation instead: DDR4-3200 dual ~40 GB/s,
      DDR5-4800 dual ~76 GB/s; single channel halves it. Recalibrate (spec 11).

  host.ram_mb:
    op: gte
    unit: MB
    bands: { A: 16384, B: 8192, C: 4096, D: 2048 }
    confidence: medium
    why: >
      Graded for playwright_node only. Each Chromium is ~300-500 MB, so a 2 GB
      VPS cannot run 4 concurrent browsers regardless of core speed. A sizing
      fact, and actionable.

  # --- network (worker profiles only -- see spec 6.5) -----------------------

  network.loss_pct:
    op: lte
    unit: pct
    bands: { A: 0.01, B: 0.1, C: 0.5, D: 2 }
    confidence: medium
    why: >
      DERIVED, not chosen. An ICMP check sending 3 packets and declaring "down"
      on total loss false-alarms at rate p^3. At p=10% that is 1-in-1000 checks;
      at one check per minute, 1.4 false alarms per day. The corpus contains
      exactly this case: ovh/zrh -> hetzner-ash at 10% loss.

  network.dns_ms:
    op: lte
    unit: ms
    bands: { A: 5, B: 20, C: 50, D: 100 }
    confidence: medium
    why: "Every HEAD/GET check pays DNS before it starts."

  network.rtt_jitter_ratio:
    op: lte
    unit: ratio
    bands: { A: 1.1, B: 1.5, C: 2.0, D: 5.0 }
    confidence: low
    why: "rtt_p99 / rtt_p50. Timing-sensitive checks care about the spread, not the mean."

categories:
  disk:
    - disk.wal_fsync.p999_us
    - disk.wal_fsync.iops
    - disk.rand_read_8k.p99_us
    - disk.rand_read_8k.iops
    - disk.rand_write_8k.iops
    - disk.seq_write.bw_mbs
    - disk.seq_read.bw_mbs
    - disk.steady_state.degradation_pct
  cpu:
    - cpu.single_thread_eps
    - cpu.scaling_efficiency
    - cpu.steal_pct_under_load
    - cpu.stall_p999_us
    - cpu.steady_state.degradation_pct
    - cpu.tls_verify_s
  ram:
    - ram.bw_read_mbs
  network:
    - network.loss_pct
    - network.dns_ms
    - network.rtt_jitter_ratio
```

Note `network.*` metric paths are per-target in the result (`network.targets[].loss_pct`), not scalars. Task 2 defines how they reduce; do not invent a reduction here.

- [ ] **Step 5: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_grade.py -v
```

Expected: 4 passed. If `test_bands_are_monotonic_in_the_direction_of_the_op` fails, a band pair is transposed — fix the YAML, not the test.

- [ ] **Step 6: Commit**

```bash
git add schema/thresholds.yaml schema/bands.schema.json tests/test_grade.py
git commit -m "feat: band data model, bands defined once per metric

Bands are a property of the metric, not of a profile: postgres_oltp and
redis_sentinel both read wal_fsync.p999_us and must agree what a B means.
Profiles reference metrics by name, which is what makes adding a profile a
YAML edit needing no new measurement.

thresholds.yaml now has its own JSON Schema. A typo in a band bound is a
silent mis-grade with no stack trace -- the rules file gets validated like a
result file does. A monotonicity test catches transposed bounds, which
nothing else would.

Provisional bands (stall, cpu steady, tls_verify, ram bw) are labelled as
such per spec 11: no corpus stands behind them yet."
```

---

### Task 2: The grading engine

**Files:**
- Create: `tools/grade.py`
- Delete: `tools/verdict.py`
- Modify: `tests/test_grade.py`

**Interfaces:**
- Consumes: `schema/thresholds.yaml` v2 from Task 1.
- Produces:
  - `grade_metric(value, metric_def) -> str` — one of `"A"`,`"B"`,`"C"`,`"D"`,`"F"`,`"?"`.
  - `dig(obj, path) -> Any | None` — dotted-path walk, `None` if absent.
  - `reduce_network(result, path) -> float | None` — worst value across `network.targets[]`.
  - `rollup(graded: dict[str, str], required: dict[str, bool]) -> tuple[str, str | None]` — returns `(grade, bound_by_metric_or_None)`.
  - `compute(result, thresholds) -> dict` — the `grades` block.
  - CLI: `python3 tools/grade.py <file>.json [--in-place]`.

Task 7 (`validate.py`) and Task 8 (`render.py`, CI) all import or invoke exactly these names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_grade.py`:

```python
import sys

sys.path.insert(0, str(ROOT / "tools"))
from grade import (  # noqa: E402
    compute, grade_metric, reduce_network, rollup,
)

LTE = {"op": "lte", "bands": {"A": 1000, "B": 3000, "C": 10000, "D": 50000}}
GTE = {"op": "gte", "bands": {"A": 1400, "B": 1000, "C": 700, "D": 400}}


def test_grade_metric_lte_boundaries_are_inclusive():
    assert grade_metric(1000, LTE) == "A"
    assert grade_metric(1001, LTE) == "B"
    assert grade_metric(50000, LTE) == "D"
    assert grade_metric(50001, LTE) == "F"


def test_grade_metric_gte_boundaries_are_inclusive():
    assert grade_metric(1400, GTE) == "A"
    assert grade_metric(1399, GTE) == "B"
    assert grade_metric(400, GTE) == "D"
    assert grade_metric(399, GTE) == "F"


def test_grade_metric_missing_is_question_mark():
    assert grade_metric(None, LTE) == "?"


def test_real_corpus_values_grade_as_spec_66_says():
    # Spec 6.6 pins the grade matrix this corpus must produce. These are real
    # published numbers; if a band edit changes them, that is the system
    # working -- but it must be a deliberate edit, not a drift.
    assert grade_metric(1875.97, LTE) == "B"    # hetzner/hel-1 best fsync p99.9
    assert grade_metric(8355.84, LTE) == "C"    # ovh/prg
    assert grade_metric(117964.8, LTE) == "F"   # ovh/zrh
    assert grade_metric(459276.29, LTE) == "F"  # windcloud
    assert grade_metric(356.25, GTE) == "F"     # ovh/waw single_thread_eps
    assert grade_metric(1661.19, GTE) == "A"    # hetzner/hel-1


def test_rollup_worst_wins():
    g = {"a": "A", "b": "C", "c": "B"}
    assert rollup(g, {"a": True, "b": True, "c": True}) == ("C", "b")


def test_rollup_f_beats_question_mark():
    # Spec 4.2 precedence. A host with a 459ms fsync is F whether or not its
    # stall was measured -- grading is non-compensatory, so no unmeasured
    # metric could rescue it. And ? must not be a hiding place: if a missing
    # metric outranked a measured failure, skipping a stage would upgrade an F
    # to a ?, a better-looking cell obtained by running LESS of the suite.
    g = {"fsync": "F", "stall": "?"}
    grade, bound = rollup(g, {"fsync": True, "stall": True})
    assert grade == "F"
    assert bound == "fsync"


def test_rollup_question_mark_when_required_missing_and_no_failure():
    g = {"fsync": "B", "stall": "?"}
    assert rollup(g, {"fsync": True, "stall": True})[0] == "?"


def test_rollup_skips_missing_optional_rule():
    g = {"fsync": "B", "advisory": "?"}
    assert rollup(g, {"fsync": True, "advisory": False}) == ("B", "fsync")


def test_rollup_names_the_binding_constraint():
    g = {"fsync": "D", "reads": "B"}
    assert rollup(g, {"fsync": True, "reads": True}) == ("D", "fsync")


def test_reduce_network_takes_the_worst_target():
    # One bad path is a bad path. Averaging loss across targets would hide the
    # exact 10% outlier the corpus contains (ovh/zrh -> hetzner-ash).
    result = {"network": {"reachable": True, "targets": [
        {"id": "a", "loss_pct": 0.0},
        {"id": "b", "loss_pct": 10.0},
    ]}}
    assert reduce_network(result, "network.loss_pct") == 10.0


def test_reduce_network_none_when_unreachable():
    assert reduce_network({"network": {"reachable": False}}, "network.loss_pct") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_grade.py -v -k "grade_metric or rollup or reduce"
```

Expected: FAIL — `ModuleNotFoundError: No module named 'grade'`.

- [ ] **Step 3: Write the engine**

Create `tools/grade.py`:

```python
#!/usr/bin/env python3
"""Compute grades from schema/thresholds.yaml.

A grade is never a human judgement. It is a pure function of the measured
numbers and the published bands. If you disagree with a grade, argue about the
band in THRESHOLDS.md -- do not edit the grade. CI recomputes every grade and
rejects any file whose stored block differs.

This replaces tools/verdict.py. The old name lied once the output stopped being
a single verdict.

Usage:
    python3 tools/grade.py results/hetzner/foo.json --in-place
    python3 tools/grade.py results/hetzner/foo.json           # print only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS_PATH = ROOT / "schema" / "thresholds.yaml"

GRADES = ("A", "B", "C", "D", "F")
# Worst-wins ordering among real grades. "?" is deliberately absent: it is not a
# point on this scale, it is the absence of one, and it is handled by explicit
# precedence in rollup() rather than by comparison.
RANK = {g: i for i, g in enumerate(GRADES)}

# Storage class boundaries, in microseconds per durable write (spec 4.5).
# Derived from measured fsync latency-per-op, never from provider marketing.
# A facet, never a curve: it explains a grade, it never softens one.
STORAGE_CLASS_BOUNDS = [
    (300, "local-nvme"),
    (1500, "net-fast"),
    (10000, "net-slow"),
]
STORAGE_CLASS_WORST = "degraded"


def dig(obj: dict, path: str):
    """Walk a dotted path, returning None if anything along the way is absent."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def reduce_network(result: dict, path: str):
    """Worst value across network targets for a `network.<field>` metric.

    Worst, not mean. Every host measures the SAME fixed targets so distance is a
    constant, which is what makes the numbers comparable at all -- and one bad
    path is a bad path. Averaging would bury the 10% loss outlier the corpus
    already contains (ovh/zrh -> hetzner-ash) under three clean paths.
    """
    net = result.get("network") or {}
    if not net.get("reachable"):
        return None
    field = path.split(".", 1)[1]
    values = []
    for target in net.get("targets") or []:
        if not target.get("reachable", True):
            continue
        if field == "rtt_jitter_ratio":
            p50, p99 = target.get("rtt_p50_ms"), target.get("rtt_p99_ms")
            if isinstance(p50, (int, float)) and isinstance(p99, (int, float)) and p50 > 0:
                values.append(p99 / p50)
            continue
        v = target.get(field)
        if isinstance(v, (int, float)):
            values.append(v)
    if not values:
        return None
    # Every network metric is "lower is worse when higher", so worst == max.
    return max(values)


def metric_value(result: dict, path: str):
    if path.startswith("network."):
        return reduce_network(result, path)
    return dig(result, path)


def grade_metric(value, metric_def: dict) -> str:
    """Band lookup for one metric. Returns A-F, or ? when unmeasured."""
    if value is None or isinstance(value, bool):
        return "?"
    if not isinstance(value, (int, float)):
        return "?"
    bands, op = metric_def["bands"], metric_def["op"]
    for g in ("A", "B", "C", "D"):
        bound = bands[g]
        if (op == "lte" and value <= bound) or (op == "gte" and value >= bound):
            return g
    return "F"


def rollup(graded: dict[str, str], required: dict[str, bool]) -> tuple[str, str | None]:
    """Worst-wins rollup. Returns (grade, binding_metric).

    Precedence (spec 4.2):
      1. any rule at F            -> F
      2. else any required rule ? -> ?
      3. else the worst band present

    F beats ? on purpose. A host with a 459ms fsync p99.9 is F whether or not
    its stall was measured -- grading is non-compensatory, so no unmeasured
    metric could rescue it, and reporting ? would discard a fact we hold. It
    also stops ? being a hiding place: if a missing metric outranked a measured
    failure, skipping a stage would upgrade an F to a ?, a better-looking cell
    obtained by running less of the suite.
    """
    failures = [m for m, g in graded.items() if g == "F"]
    if failures:
        return ("F", failures[0])

    missing_required = [m for m, g in graded.items() if g == "?" and required.get(m)]
    if missing_required:
        return ("?", missing_required[0])

    scored = {m: g for m, g in graded.items() if g in RANK}
    if not scored:
        return ("?", None)
    binding = max(scored, key=lambda m: RANK[scored[m]])
    return (scored[binding], binding)


def storage_class(result: dict) -> str | None:
    """Derive storage class from measured fsync latency-per-op (spec 4.5).

    Derived here rather than emitted by bench/ because it is a DERIVED value,
    not a measurement, and because the published v1 results can never grow a new
    field -- computing it alongside grades covers every result, old and new.
    """
    iops = dig(result, "disk.wal_fsync.iops")
    if not isinstance(iops, (int, float)) or iops <= 0:
        return None
    us_per_op = 1_000_000 / iops
    for bound, name in STORAGE_CLASS_BOUNDS:
        if us_per_op < bound:
            return name
    return STORAGE_CLASS_WORST


def _grade_rules(result, thresholds, rule_specs):
    graded, required, detail = {}, {}, {}
    for rule in rule_specs:
        path = rule["metric"]
        mdef = thresholds["metrics"][path]
        value = metric_value(result, path)
        g = grade_metric(value, mdef)
        graded[path] = g
        required[path] = rule["required"]
        detail[path] = {"value": value, "grade": g}
    return graded, required, detail


def compute(result: dict, thresholds: dict) -> dict:
    """Return the `grades` block: categories, profiles, storage_class."""
    tool_version = (result.get("run") or {}).get("tool_version")

    out = {
        "bands_version": thresholds["bands_version"],
        "storage_class": storage_class(result),
        "categories": {},
        "profiles": {},
    }

    # Categories describe the machine: every metric in the category, all
    # treated as required, because a category grade with a hole in it is not a
    # description of anything.
    for cat, paths in thresholds["categories"].items():
        specs = [{"metric": p, "required": True} for p in paths]
        graded, required, detail = _grade_rules(result, thresholds, specs)
        grade, bound = rollup(graded, required)
        out["categories"][cat] = {
            "grade": grade,
            "bound_by": bound,
            "metrics": detail,
        }

    # Profiles are opinions that consume category metrics.
    for name, profile in thresholds["profiles"].items():
        graded, required, _ = _grade_rules(result, thresholds, profile["rules"])
        grade, bound = rollup(graded, required)
        entry = {"grade": grade, "bound_by": bound}

        if grade == "?":
            # Say WHY it is unknown. A result measured before 0.2.0 simply does
            # not carry the newer metrics; that is a re-run, not a defect.
            entry["reason"] = (
                f"{bound} not measured (required)"
                if tool_version and tool_version >= "0.2.0"
                else "needs re-run (tool >= 0.2.0)"
            )
        if profile.get("network_half_unmeasured"):
            # Commit latency in a sync cluster is local fsync + inter-node RTT.
            # We measure the first term only. A grade that silently covers half
            # an equation is worse than no grade (spec 7.3).
            entry["network_half_unmeasured"] = True
        out["profiles"][name] = entry

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result", type=Path)
    ap.add_argument("--in-place", action="store_true", help="write grades back into the file")
    args = ap.parse_args()

    thresholds = yaml.safe_load(THRESHOLDS_PATH.read_text())
    result = json.loads(args.result.read_text())
    grades = compute(result, thresholds)

    if args.in_place:
        result["grades"] = grades
        args.result.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote grades to {args.result}", file=sys.stderr)
    else:
        print(json.dumps(grades, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_grade.py -v
```

Expected: all pass (4 from Task 1 + 11 new).

- [ ] **Step 5: Leave `tools/verdict.py` in place for now**

Do **not** delete it in this task. `tools/validate.py:29` does `from verdict import compute`, and `tests/test_validate.py` imports `validate` — deleting `verdict.py` here would break the suite for Tasks 3-6 and only heal at Task 7. Task 7 deletes it in the same commit that repoints `validate.py`, so the suite is green at every commit.

Both engines coexist for five tasks. That is fine: nothing imports `grade.py` yet except its own tests, and `verdict.py` keeps `validate.py` working meanwhile.

- [ ] **Step 6: Commit**

```bash
git add tools/grade.py tests/test_grade.py
git commit -m "feat: grading engine -- bands, worst-wins rollup, binding constraint

Replaces tools/verdict.py, whose name lied once the output stopped being a
single word. Grades stay a pure function of measured numbers and a versioned
file, so CI can still reject a hand-edited block.

Precedence is F over ?: a host with a 459ms fsync is F whether or not its
stall was measured, since grading is non-compensatory and no unmeasured
metric could rescue it. It also stops ? being a hiding place -- otherwise
skipping a stage would upgrade an F to a ?, a better-looking cell obtained by
running less of the suite.

Network metrics reduce across targets by WORST, not mean. Every host measures
the same fixed targets, so one bad path is a bad path; averaging would bury
the 10% loss outlier the corpus already contains under three clean paths.

verdict.py stays for now: validate.py imports it, and deleting it here would
break the suite until Task 7. Task 7 removes it in the same commit that
repoints validate.py, so every commit is green."
```

---

### Task 3: storage_class

Spec §4.5 / §10 item 6. Derived in `tools/`, not `bench/`: it is a derived value, not a measurement, and the 10 published results were produced by v1 scripts that can never grow a new field. Computing it alongside grades covers every result, old and new.

`storage_class()` already landed in Task 2 (`grade.py`). This task proves it against the real corpus and pins the boundaries.

**Files:**
- Create: `tests/test_storage_class.py`

**Interfaces:**
- Consumes: `storage_class(result) -> str | None` from `tools/grade.py`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage_class.py`:

```python
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from grade import storage_class  # noqa: E402


def _r(iops):
    return {"disk": {"wal_fsync": {"iops": iops}}}


def test_class_boundaries():
    # Boundaries in us per durable write: <300 local-nvme, <1500 net-fast,
    # <10000 net-slow, else degraded.
    assert storage_class(_r(1_000_000 / 200)) == "local-nvme"
    assert storage_class(_r(1_000_000 / 800)) == "net-fast"
    assert storage_class(_r(1_000_000 / 4000)) == "net-slow"
    assert storage_class(_r(1_000_000 / 14000)) == "degraded"


def test_class_declines_without_the_input():
    assert storage_class({}) is None
    assert storage_class(_r(None)) is None
    assert storage_class(_r(0)) is None


def test_real_corpus_splits_into_the_regimes_spec_24_describes():
    # Spec 2.4: converting fsync IOPS to latency-per-op splits the corpus into
    # three physics regimes. This pins that the derivation actually reproduces
    # them from the published files, rather than from a table in a document.
    seen = {}
    for p in sorted((ROOT / "results").rglob("*.json")):
        d = json.loads(p.read_text())
        loc = f"{d['provider']['name']}/{d['provider']['region']}"
        seen.setdefault(loc, set()).add(storage_class(d))

    assert seen["hetzner/hel-1"] == {"net-fast"}     # 721-839 us/fsync
    assert seen["ovh/waw"] == {"net-fast"}           # 630 us
    assert seen["ovh/prg"] == {"net-slow"}           # 3360-3390 us
    assert seen["ovh/zrh"] == {"net-slow"}           # 4316-5562 us
    assert seen["windcloud/enge-sande"] == {"degraded"}  # 13945 us


def test_no_published_host_is_local_nvme():
    # Not a bug -- a fact worth pinning. Every host in this corpus is on network
    # storage. If a local-NVMe host ever lands, this test fails and someone must
    # look, because it changes what the fsync bands are being read against.
    classes = {
        storage_class(json.loads(p.read_text()))
        for p in (ROOT / "results").rglob("*.json")
    }
    assert "local-nvme" not in classes
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_storage_class.py -v
```

Expected: the boundary tests pass (Task 2 shipped the function); run this to confirm the **corpus** tests pass too. If `test_real_corpus_splits_into_the_regimes_spec_24_describes` fails, the boundaries in `STORAGE_CLASS_BOUNDS` do not reproduce spec §2.4 — fix the constant, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_storage_class.py
git commit -m "test: pin storage_class against the real corpus

Spec 2.4 claims fsync latency-per-op splits the corpus into three physics
regimes. This proves the derivation reproduces them from the published files
rather than from a table in a document.

Also pins that no published host is local-nvme. Not a bug -- a fact. If one
ever lands, this fails and someone must look, because it changes what the
fsync bands are being read against."
```

---

### Task 4: The seven profiles

**Files:**
- Modify: `schema/thresholds.yaml` (append `profiles:`)
- Create: `tests/test_profiles.py`

**Interfaces:**
- Consumes: `metrics` from Task 1, `compute()` from Task 2.
- Produces: `profiles` block with the 7 profiles of spec §7.

- [ ] **Step 1: Write the failing test**

Create `tests/test_profiles.py`:

```python
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from grade import compute  # noqa: E402

THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())

EXPECTED = {
    "postgres_oltp", "timescale_ingest", "patroni_member", "redis_sentinel",
    "worker_probe", "playwright_node", "nuxt_ssr",
}
# Spec 6.5: a network metric may carry a threshold ONLY in a profile whose
# workload IS the network. Everything else reads no network field.
NETWORK_READERS = {"worker_probe", "playwright_node"}


def test_all_seven_profiles_present():
    assert set(THRESHOLDS["profiles"]) == EXPECTED


def test_every_profile_rule_references_a_defined_metric():
    for name, p in THRESHOLDS["profiles"].items():
        for rule in p["rules"]:
            assert rule["metric"] in THRESHOLDS["metrics"], (
                f"{name} references undefined metric {rule['metric']}"
            )


def test_only_worker_profiles_read_network():
    # The doctrine narrowing, made executable. THRESHOLDS.md rejects network
    # thresholds because "a database host needs 500 Mbit/s" cannot be derived
    # from any workload requirement. That reasoning holds for a database and
    # collapses for a probe, where the network IS the workload -- false-alarm
    # rate is a computable function of packet loss. This test stops the
    # exception from quietly spreading back to the database profiles.
    for name, p in THRESHOLDS["profiles"].items():
        reads_net = any(r["metric"].startswith("network.") for r in p["rules"])
        assert reads_net == (name in NETWORK_READERS), (
            f"{name}: network rules are only legitimate where the workload is "
            f"the network itself (spec 6.5)"
        )


def test_cluster_profiles_declare_their_unmeasured_half():
    # Spec 7.3: commit latency in a sync Patroni cluster is local fsync +
    # inter-node RTT. We measure the first term only. A grade that silently
    # covers half an equation is worse than no grade.
    for name in ("patroni_member", "redis_sentinel"):
        assert THRESHOLDS["profiles"][name].get("network_half_unmeasured") is True


def test_v1_results_grade_postgres_and_timescale_but_not_the_new_profiles():
    # Spec 9.3. The existing corpus keeps answering the disk-bound database
    # questions it was built for, and honestly declines the CPU-sustained and
    # stall questions it never measured. Fabricating a playwright grade from
    # data that never measured a playwright workload is the failure this whole
    # redesign exists to correct.
    doc = json.loads(
        (ROOT / "results" / "hetzner" / "hel-1" / "2026-07-16T1012-cpx32.json").read_text()
    )
    g = compute(doc, THRESHOLDS)["profiles"]

    assert g["postgres_oltp"]["grade"] != "?"
    assert g["timescale_ingest"]["grade"] != "?"
    for name in ("patroni_member", "redis_sentinel", "worker_probe",
                 "playwright_node", "nuxt_ssr"):
        assert g[name]["grade"] == "?", f"{name} graded from data it never had"
        assert "re-run" in g[name]["reason"]


def test_ovh_waw_and_zrh_read_as_opposite_failures():
    # Spec 2.5 -- the whole point. Same product, same price, opposite failures.
    # Today both render as `fail fail fail fail`, indistinguishable. If this
    # test ever passes trivially (both categories the same grade), the redesign
    # has lost its reason to exist.
    waw = json.loads(
        (ROOT / "results" / "ovh" / "waw" / "2026-07-16T1017-vps-1-lz-2026.json").read_text()
    )
    zrh = json.loads(
        (ROOT / "results" / "ovh" / "zrh" / "2026-07-16T1024-vps-1-lz-2026.json").read_text()
    )
    waw_g = compute(waw, THRESHOLDS)["categories"]
    zrh_g = compute(zrh, THRESHOLDS)["categories"]

    # waw: healthy disk, broken CPU (single_thread_eps 356 vs ~1600)
    assert waw_g["cpu"]["bound_by"] == "cpu.single_thread_eps"
    assert waw_g["cpu"]["grade"] == "F"
    # zrh: fine CPU, destroyed disk (fsync p99.9 137ms)
    assert zrh_g["disk"]["bound_by"] == "disk.wal_fsync.p999_us"
    assert zrh_g["disk"]["grade"] == "F"
    # The distinction the old suite could not express:
    assert waw_g["disk"]["grade"] != waw_g["cpu"]["grade"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_profiles.py -v
```

Expected: FAIL — `KeyError: 'profiles'`.

- [ ] **Step 3: Append the profiles to thresholds.yaml**

```yaml
profiles:

  postgres_oltp:
    label: "PostgreSQL OLTP"
    description: >
      Classic transactional Postgres. Commit latency is bounded by fsync; index
      lookups by random read; checkpoints by random write.
    rules:
      - { metric: disk.wal_fsync.p999_us,           required: true }
      - { metric: disk.wal_fsync.iops,              required: true }
      - { metric: disk.rand_read_8k.p99_us,         required: true }
      - { metric: disk.rand_read_8k.iops,           required: false }
      - { metric: disk.rand_write_8k.iops,          required: false }
      - { metric: cpu.steal_pct_under_load,         required: true }
      - { metric: disk.steady_state.degradation_pct, required: false }

  timescale_ingest:
    label: "TimescaleDB ingest"
    description: >
      High-rate inserts plus background compression and continuous aggregates.
      Sequential throughput and memory bandwidth matter more than for plain OLTP.
    rules:
      - { metric: disk.wal_fsync.p999_us,           required: true }
      - { metric: disk.seq_write.bw_mbs,            required: true }
      - { metric: disk.seq_read.bw_mbs,             required: false }
      - { metric: disk.rand_write_8k.iops,          required: false }
      # required: false so a v1 result still grades -- it predates the LLC fix
      # and carries no bw_read_mbs (spec 9.3).
      - { metric: ram.bw_read_mbs,                  required: false }
      - { metric: cpu.steal_pct_under_load,         required: true }
      - { metric: disk.steady_state.degradation_pct, required: false }

  patroni_member:
    label: "Patroni cluster member"
    description: >
      A Postgres node in a Patroni cluster. Grades ONLY what one host decides.
      Commit latency here is local fsync + inter-node RTT; the second term needs
      two hosts under your control and a far longer observation window than a
      benchmark run, so it is not measured and not approximated (spec 7.3).
    network_half_unmeasured: true
    rules:
      - { metric: disk.wal_fsync.p999_us,           required: true }
      # Steal is a correctness rule here, not a performance one: it delays the
      # leader's heartbeats, and a delayed heartbeat past the TTL fires a
      # failover because the host was busy, not because anything was wrong.
      - { metric: cpu.steal_pct_under_load,         required: true }
      - { metric: cpu.stall_p999_us,                required: true }
      - { metric: cpu.single_thread_eps,            required: true }
      - { metric: cpu.steady_state.degradation_pct, required: true }

  redis_sentinel:
    label: "Redis (Sentinel member, AOF durable)"
    description: >
      Single-threaded, latency-critical, in-memory, as a Sentinel cluster
      member. Evaluated without a running Redis. Grades only the local half;
      Sentinel election stability also depends on inter-node RTT (spec 7.3).
    network_half_unmeasured: true
    rules:
      - { metric: cpu.stall_p999_us,                required: true }
      - { metric: cpu.single_thread_eps,            required: true }
      # Applies even to appendfsync no. Conservative on purpose: you rarely know
      # at procurement time which durability setting you will end up needing.
      - { metric: disk.wal_fsync.p999_us,           required: true }
      - { metric: cpu.steal_pct_under_load,         required: true }
      - { metric: cpu.steady_state.degradation_pct, required: true }

  worker_probe:
    label: "Monitoring probe (HTTP/SSL/ICMP/SMTP/SSH/FTP/TCP)"
    description: >
      A node running synthetic checks against the outside world. The network is
      the workload here, which is the only reason network fields carry
      thresholds at all (spec 6.5).
    rules:
      - { metric: cpu.single_thread_eps,            required: true }
      - { metric: cpu.stall_p999_us,                required: true }
      - { metric: cpu.steady_state.degradation_pct, required: true }
      - { metric: cpu.tls_verify_s,                 required: true }
      - { metric: network.loss_pct,                 required: false }
      - { metric: network.dns_ms,                   required: false }
      - { metric: network.rtt_jitter_ratio,         required: false }

  playwright_node:
    label: "Playwright / browser synthetics"
    description: >
      Chromium is multi-process and memory-hungry, so this is the one profile
      where RAM capacity is primary. cpu.steady_state is the binding metric for
      the Prague failure mode: a node pinned at a throttled baseline renders ~3x
      slower, which surfaces as browser-check timeouts while plain HTTP checks
      stay green (spec 2.6).
    rules:
      - { metric: cpu.steady_state.degradation_pct, required: true }
      - { metric: cpu.scaling_efficiency,           required: true }
      - { metric: cpu.single_thread_eps,            required: true }
      - { metric: cpu.stall_p999_us,                required: true }
      - { metric: host.ram_mb,                      required: true }
      - { metric: ram.bw_read_mbs,                  required: false }
      - { metric: network.loss_pct,                 required: false }
      - { metric: network.dns_ms,                   required: false }

  nuxt_ssr:
    label: "Nuxt / Node SSR"
    description: >
      Single-threaded event loop per worker. Sensitive to scheduler stalls and
      single-core speed; largely indifferent to disk.
    rules:
      - { metric: cpu.single_thread_eps,            required: true }
      - { metric: cpu.stall_p999_us,                required: true }
      - { metric: cpu.steal_pct_under_load,         required: false }
```

Note `cpu.aes_256_gcm_mbs` is **absent from every profile**. That is spec §5.4 finally delivered: it read 37,717 MB/s against a `>= 2000` bar, 18x over, on every machine with AES-NI — zero discriminating power. It is still recorded by `bench/02-cpu.sh` as context; it is simply no longer graded. Phase 1 demoted it only in prose.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_profiles.py tests/test_grade.py -v
```

Expected: all pass. `test_ovh_waw_and_zrh_read_as_opposite_failures` is the one that matters — it is the redesign's reason to exist.

- [ ] **Step 5: Commit**

```bash
git add schema/thresholds.yaml tests/test_profiles.py
git commit -m "feat: seven profiles, and AES finally out of grading

Adds patroni_member, redis_sentinel, worker_probe, playwright_node.
redis_sentinel replaces redis_aof: same local physics, honest name.

Cluster profiles declare network_half_unmeasured. Commit latency in a sync
Patroni cluster is local fsync + inter-node RTT; we measure the first term
only. A grade that silently covers half an equation is worse than no grade.

Only worker_probe and playwright_node read network fields, enforced by test.
'A database host needs 500 Mbit/s' cannot be derived from any workload
requirement -- but a probe's false-alarm rate is a computable function of
packet loss, because there the network IS the workload.

cpu.aes_256_gcm_mbs appears in no profile. Spec 5.4 delivered: 37,717 MB/s
against a >=2000 bar on every machine with AES-NI is not a grade input. Still
recorded as context. Phase 1 only demoted it in prose."
```

---

### Task 5: Make the broken-vs-quiet doctrine executable

Spec §4.4 exists because this project shipped three thresholds no VM could ever pass, and nobody noticed until the corpus was analysed. A doctrine that lives only in prose will be violated again. This test makes it mechanical.

**Files:**
- Create: `tests/test_band_doctrine.py`

**Interfaces:**
- Consumes: `grade_metric`, `metric_value` from `tools/grade.py`; `schema/thresholds.yaml`.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_band_doctrine.py`:

```python
"""Spec 4.4, made executable.

This project shipped three thresholds no VM could ever pass -- fsync IOPS
>= 15000 against a best-measured 1588, rand-read p99 <= 1ms against 2408us,
intrinsic latency <= 200us against 1642us. Every one of 10 runs failed all
three, so three of four profiles could never return anything but `fail` and the
verdict column carried no information. Nobody noticed until someone analysed the
corpus.

A doctrine that lives only in prose gets violated again. So:

  broken threshold -- unreachable by construction, on every machine, in any
                      plausible corpus. Dead forever. Must never ship.
  quiet metric     -- reachable in both directions; this corpus merely happens
                      to be clean. Keep it: it is insurance that fires on a bad
                      host, and its silence is itself a finding.

The two look identical from inside a single corpus -- one grade, every time. The
difference is intent, so the author must DECLARE it. This test forces that
declaration.
"""
import collections
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from grade import grade_metric, metric_value  # noqa: E402

THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())
CORPUS = [json.loads(p.read_text()) for p in sorted((ROOT / "results").rglob("*.json"))]


def _grades_for(path, mdef):
    return collections.Counter(
        grade_metric(metric_value(doc, path), mdef) for doc in CORPUS
    )


def test_corpus_is_not_empty():
    # Every assertion below is vacuous without data. Guard explicitly rather
    # than letting the suite go green on an empty results/ directory.
    assert len(CORPUS) >= 10


def test_no_band_set_is_unreachable_by_every_host():
    """No metric may grade F (or A) across the entire corpus by construction."""
    offenders = []
    for path, mdef in THRESHOLDS["metrics"].items():
        if mdef.get("provisional"):
            continue  # no corpus behind it yet -- Task 5 of spec 10 recalibrates
        counts = _grades_for(path, mdef)
        measured = {g: n for g, n in counts.items() if g != "?"}
        if not measured:
            continue
        if set(measured) == {"F"}:
            offenders.append(
                f"{path}: every measured host grades F -- either the band is "
                f"unreachable (the fsync-IOPS-15000 mistake) or the field is broken"
            )
    assert not offenders, "\n".join(offenders)


def test_single_grade_metrics_are_declared_quiet_or_provisional():
    """A metric yielding one grade must say which kind it is."""
    offenders = []
    for path, mdef in THRESHOLDS["metrics"].items():
        if mdef.get("provisional") or mdef.get("quiet"):
            continue
        counts = _grades_for(path, mdef)
        measured = {g: n for g, n in counts.items() if g != "?"}
        if len(measured) == 1:
            grade = next(iter(measured))
            offenders.append(
                f"{path}: produces only '{grade}' across all {len(CORPUS)} runs. "
                f"Declare `quiet: true` (reachable both ways, corpus happens "
                f"clean) or `provisional: true` (no corpus yet) -- or fix the "
                f"bands. A metric that cannot tell two machines apart is not "
                f"earning its place (spec 4.4)."
            )
    assert not offenders, "\n".join(offenders)


def test_metrics_declared_quiet_really_are_quiet():
    """A `quiet: true` that starts discriminating must lose the label.

    Otherwise `quiet` rots into a blanket exemption from the doctrine, which is
    how the original mistake survived.
    """
    offenders = []
    for path, mdef in THRESHOLDS["metrics"].items():
        if not mdef.get("quiet") or mdef.get("provisional"):
            continue
        measured = {g for g in _grades_for(path, mdef) if g != "?"}
        if len(measured) > 1:
            offenders.append(
                f"{path}: declared quiet but produces {sorted(measured)}. "
                f"It discriminates now -- drop the label."
            )
    assert not offenders, "\n".join(offenders)


def test_the_discriminating_metrics_still_discriminate():
    """Spec 6.6 pins that 7 metrics produce 3-4 distinct grades on this corpus.

    This is the redesign's headline claim. If a band edit collapses one of them
    to a single grade, that is a regression in the thing the project exists to
    do, and it must not pass silently.
    """
    expect_at_least_two = [
        "disk.wal_fsync.p999_us", "disk.wal_fsync.iops",
        "disk.rand_read_8k.p99_us", "disk.rand_read_8k.iops",
        "disk.rand_write_8k.iops", "disk.seq_write.bw_mbs",
        "cpu.single_thread_eps",
    ]
    for path in expect_at_least_two:
        measured = {g for g in _grades_for(path, THRESHOLDS["metrics"][path]) if g != "?"}
        assert len(measured) >= 2, (
            f"{path} collapsed to {sorted(measured)} -- it used to tell machines "
            f"apart (spec 6.6). A band edit broke the discriminating power."
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_band_doctrine.py -v
```

Expected: FAIL initially if any metric is undeclared. Read the failure message — it names the metric and tells you the three options. **Do not add `quiet: true` reflexively to silence it.** Ask which of the three it really is: does the band discriminate on real hardware (fix the bands), is the corpus merely clean (quiet), or is there no corpus yet (provisional)?

- [ ] **Step 3: Reconcile thresholds.yaml**

Expected declarations after Task 1's bands, on this corpus:
- `disk.steady_state.degradation_pct` -> `quiet: true` (0.0–2.0% across all 10)
- `cpu.steal_pct_under_load` -> `quiet: true` (0.0–0.24%)
- `cpu.stall_p999_us`, `cpu.steady_state.degradation_pct`, `cpu.tls_verify_s`, `ram.bw_read_mbs` -> `provisional: true` (absent from v1 results)
- `cpu.scaling_efficiency`, `host.ram_mb`, `network.*` -> check what the corpus actually produces; declare honestly.

If `cpu.scaling_efficiency` yields a single grade (all 10 backfilled values fall in 0.953–1.018, i.e. all `A`), that is **quiet**, not broken: 0.40 and 0.55 are reachable on SMT-sold-as-cores hosts. Declare it and say why in `why`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_band_doctrine.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_band_doctrine.py schema/thresholds.yaml
git commit -m "test: make the broken-vs-quiet doctrine executable

This project shipped three thresholds no VM could pass -- fsync IOPS >= 15000
against a best-measured 1588, rand-read p99 <= 1ms against 2408us, intrinsic
latency <= 200us against 1642us. All 10 runs failed all three, so three of
four profiles could never return anything but fail. Nobody noticed until the
corpus was analysed.

Prose doctrine gets violated again, so this makes it mechanical. A metric
producing one grade across the corpus must declare which kind it is: quiet
(reachable both ways, corpus happens clean -- keep it, its silence is a
finding) or provisional (no corpus yet). Undeclared single-grade metrics fail
CI.

Also guards the reverse: a quiet label that starts discriminating must be
dropped, so `quiet` cannot rot into a blanket exemption -- which is how the
original mistake survived."
```

---

### Task 6: Schema v2 and migrating the 10 results

`schema_version` is `const: "1.0"` and the `verdict` node has `additionalProperties: false` over the four old profile names. Renaming to `grades` is therefore atomic with migrating the published results: leave either half undone and CI is red.

Results are immutable **measurements**; `verdict`/`grades` is derived and always current (spec §9.1). Regenerating the derived block is expected. **No measured number changes in this task** — a test enforces that.

**Files:**
- Modify: `schema/result.schema.json`
- Create: `tools/migrate_v1_v2.py`
- Create: `tests/test_migrate.py`
- Modify: `tests/test_validate.py` (its fixture pins the old shape — see below)
- Modify: `results/*/*/*.json` (derived block only, via the tool)

**You must also update `tests/test_validate.py`.** Phase 1 added it, and its
`RESULT_020` fixture hardcodes `"schema_version": "1.0"` and `"verdict": None`.
Both become invalid here. Change the fixture to `"schema_version": "2.0"` and
replace `"verdict": None` with `"grades": None`.

Do **not** weaken that test to make it pass. It exists because Phase 1 shipped a
schema that rejected every result its own stages produced, and nothing caught it
— it is the only end-to-end check that a freshly-measured result validates. Keep
both of its assertions intact; only the fixture's shape changes.

**Interfaces:**
- Consumes: `compute()` from `tools/grade.py`.
- Produces: `migrate(result: dict, thresholds: dict) -> bool` — mutates in place, returns True if changed. CLI: `python3 tools/migrate_v1_v2.py results/ [--dry-run]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_migrate.py`:

```python
import copy
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from migrate_v1_v2 import MEASURED_SECTIONS, migrate  # noqa: E402

THRESHOLDS = yaml.safe_load((ROOT / "schema" / "thresholds.yaml").read_text())


def _v1():
    return json.loads(
        (ROOT / "results" / "hetzner" / "hel-1" / "2026-07-16T1012-cpx32.json").read_text()
    )


def test_migrate_replaces_verdict_with_grades_and_bumps_version():
    doc = _v1()
    assert migrate(doc, THRESHOLDS) is True
    assert doc["schema_version"] == "2.0"
    assert "verdict" not in doc
    assert doc["grades"]["bands_version"] == THRESHOLDS["bands_version"]


def test_migrate_changes_no_measured_number():
    # The contract of spec 9.1: measurements are immutable, grades are derived.
    # A migration that quietly edited a measured value would destroy the one
    # thing this repo is for.
    before = _v1()
    after = copy.deepcopy(before)
    migrate(after, THRESHOLDS)
    for section in MEASURED_SECTIONS:
        assert after.get(section) == before.get(section), f"{section} was altered"


def test_migrate_is_idempotent():
    doc = _v1()
    migrate(doc, THRESHOLDS)
    once = copy.deepcopy(doc)
    assert migrate(doc, THRESHOLDS) is False
    assert doc == once
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_migrate.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'migrate_v1_v2'`.

- [ ] **Step 3: Write the migration tool**

Create `tools/migrate_v1_v2.py`:

```python
#!/usr/bin/env python3
"""One-shot migration of published results from schema 1.0 to 2.0.

What changes: the DERIVED block only. `verdict` (one word per profile) becomes
`grades` (A-F per category and per profile), and schema_version goes to "2.0".

What does not change: any measured number. Results are immutable measurements;
grades are derived and always current (spec 9.1). This tool must never touch
run/provider/host/disk/cpu/ram/network/app, and tests/test_migrate.py enforces
that.

Why migrate rather than support both shapes: two schemas means two code paths in
validate.py and render.py forever, and a stale verdict could hide in the seam.
The measurements survive untouched; only the derivation is regenerated, which is
exactly what CI does on every threshold change anyway.

Usage:
    python3 tools/migrate_v1_v2.py results/ [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: pip install pyyaml")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grade import compute  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS_PATH = ROOT / "schema" / "thresholds.yaml"

# Every section holding measured data. The migration must leave all of these
# byte-identical; only the derived block is regenerated.
MEASURED_SECTIONS = (
    "run", "provider", "host", "disk", "cpu", "ram", "network", "app",
)


def migrate(result: dict, thresholds: dict) -> bool:
    """v1 -> v2 in place. Returns True if the document changed."""
    if result.get("schema_version") == "2.0" and "verdict" not in result:
        return False

    result.pop("verdict", None)
    result["schema_version"] = "2.0"
    result["grades"] = compute(result, thresholds)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    thresholds = yaml.safe_load(THRESHOLDS_PATH.read_text())
    paths = sorted(args.target.rglob("*.json")) if args.target.is_dir() else [args.target]

    changed = 0
    for path in paths:
        doc = json.loads(path.read_text())
        if not migrate(doc, thresholds):
            continue
        changed += 1
        profiles = doc["grades"]["profiles"]
        summary = " ".join(f"{n}={p['grade']}" for n, p in sorted(profiles.items()))
        print(f"{path}\n  {doc['grades']['storage_class']}  {summary}")
        if not args.dry_run:
            path.write_text(json.dumps(doc, indent=2) + "\n")

    verb = "would migrate" if args.dry_run else "migrated"
    print(f"{verb} {changed} of {len(paths)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Update the result schema**

In `schema/result.schema.json`:

1. `schema_version`: change `{"type": "string", "const": "1.0"}` to `{"type": "string", "const": "2.0"}`.
2. Replace the whole `verdict` property with `grades`:

```json
"grades": {
  "type": ["object", "null"],
  "description": "COMPUTED by tools/grade.py from schema/thresholds.yaml. Never hand-written; CI recomputes and rejects any file whose block differs.",
  "additionalProperties": false,
  "required": ["bands_version", "categories", "profiles"],
  "properties": {
    "bands_version": { "type": "string" },
    "storage_class": {
      "type": ["string", "null"],
      "enum": ["local-nvme", "net-fast", "net-slow", "degraded", null],
      "description": "Derived from measured fsync latency-per-op. A facet, never a curve: it explains a grade, it never softens one."
    },
    "categories": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "required": ["grade", "bound_by", "metrics"],
        "additionalProperties": false,
        "properties": {
          "grade": { "$ref": "#/$defs/gradeValue" },
          "bound_by": { "type": ["string", "null"] },
          "metrics": {
            "type": "object",
            "additionalProperties": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "value": { "type": ["number", "null"] },
                "grade": { "$ref": "#/$defs/gradeValue" }
              }
            }
          }
        }
      }
    },
    "profiles": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "required": ["grade"],
        "additionalProperties": false,
        "properties": {
          "grade": { "$ref": "#/$defs/gradeValue" },
          "bound_by": { "type": ["string", "null"] },
          "reason": { "type": "string" },
          "network_half_unmeasured": { "type": "boolean" }
        }
      }
    }
  }
}
```

3. In `$defs`, replace `verdictValue` with:

```json
"gradeValue": {
  "type": ["string", "null"],
  "enum": ["A", "B", "C", "D", "F", "?", null]
}
```

Leave every measured-field definition exactly as it is, including the legacy ones (`cpu.intrinsic_latency_max_us`, `ram.seq_read_mbs`). v1 results keep those fields after migration — only the derived block changes.

- [ ] **Step 5: Run tests, then dry-run against real data**

```bash
python3 -m pytest tests/test_migrate.py -v
```

Expected: 3 passed.

```bash
python3 tools/migrate_v1_v2.py results/ --dry-run
```

Expected: 10 files listed, each with a storage_class and per-profile grades; `would migrate 10 of 10 files`.

**Read this output before applying.** Sanity-check it against spec §6.6: hetzner/hel-1 should not be `degraded`; ovh/zrh's `postgres_oltp` should be `F`; the new profiles should be `?`. If anything contradicts the spec's own table, stop and investigate — a wrong band or a wrong path is far cheaper to find here than after 10 files are rewritten.

- [ ] **Step 6: Apply and verify**

```bash
python3 tools/migrate_v1_v2.py results/
git diff --stat results/
```

Verify no measured number moved — this is the load-bearing check:

```bash
git diff results/ | grep -E '^[-+]' | grep -vE '^[-+]{3}' \
  | grep -vE '"(grades|bands_version|storage_class|categories|profiles|grade|bound_by|value|reason|network_half_unmeasured|schema_version)"' \
  | grep -vE '^[-+]\s*[]{}[]' | head
```

Expected: **no output.** Any line here is a measured value the migration touched, which is a bug — stop and fix `migrate()`.

- [ ] **Step 7: Commit**

```bash
git add schema/result.schema.json tools/migrate_v1_v2.py tests/test_migrate.py tests/test_validate.py results/
git commit -m "feat: schema v2, grades replace verdict, migrate the 10 results

Atomic on purpose. schema_version was const 1.0 and the verdict node had
additionalProperties:false over the four old profile names, so renaming
without migrating leaves CI red. Phase 1 already shipped one half-migration
(a schema that rejected every result its own stages produced); not repeating
it.

Only the derived block changes. Results are immutable measurements; grades
are derived and always current, and CI regenerates them on every threshold
change anyway. A test asserts no measured section moved."
```

---

### Task 7: validate.py

**Files:**
- Modify: `tools/validate.py`
- Create: `tests/test_validate_grades.py`

**Interfaces:**
- Consumes: `compute` from `tools/grade.py`.
- Produces: no new public names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_validate_grades.py`:

```python
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import validate as V  # noqa: E402

SAMPLE = ROOT / "results" / "hetzner" / "hel-1" / "2026-07-16T1012-cpx32.json"


def test_hand_edited_grade_is_rejected(tmp_path):
    # The trust property. A project publishing provider comparisons has an
    # obvious temptation to nudge them; the only real defence is making a nudge
    # fail the build.
    doc = json.loads(SAMPLE.read_text())
    doc["grades"]["profiles"]["postgres_oltp"]["grade"] = "A"
    p = tmp_path / "x.json"
    p.write_text(json.dumps(doc))
    errs = V.check_policy(SAMPLE, doc)
    assert any("do not hand-edit" in e.lower() or "computes" in e for e in errs)


def test_stale_bands_version_is_rejected(tmp_path):
    doc = json.loads(SAMPLE.read_text())
    doc["grades"]["bands_version"] = "1.9"
    errs = V.check_policy(SAMPLE, doc)
    assert any("bands_version" in e for e in errs)


def test_unmodified_published_results_pass():
    doc = json.loads(SAMPLE.read_text())
    assert V.check_policy(SAMPLE, doc) == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_validate_grades.py -v
```

Expected: FAIL — `ImportError: cannot import name 'compute' from 'verdict'` (Task 2 deleted it).

- [ ] **Step 3: Update validate.py and delete the old engine**

Change the import:

```python
from grade import compute  # noqa: E402
```

Then delete `tools/verdict.py` **in this same commit** — its name lies once the
output is grades, and this is the first moment nothing imports it:

```bash
git rm tools/verdict.py
```

Deleting it earlier would have broken `validate.py` (and `tests/test_validate.py`
through it) for five tasks. Doing it here keeps every commit green.

Replace the verdict-check block in `check_policy` with:

```python
    # Grades must be reproducible from the published bands. This is the trust
    # property, not a lint: a project publishing provider comparisons has an
    # obvious temptation to nudge them, and the only real defence is making a
    # nudge fail the build in public.
    if data.get("grades"):
        stored = data["grades"]
        if stored.get("bands_version") != THRESHOLDS["bands_version"]:
            errs.append(
                f"grades.bands_version is '{stored.get('bands_version')}' but "
                f"schema/thresholds.yaml is '{THRESHOLDS['bands_version']}'. "
                f"Re-run tools/grade.py --in-place."
            )
        expected = compute(data, THRESHOLDS)
        if stored != expected:
            for name, exp in expected["profiles"].items():
                got = stored.get("profiles", {}).get(name, {}).get("grade")
                if got != exp["grade"]:
                    errs.append(
                        f"grades.profiles.{name} is '{got}' but the bands compute "
                        f"'{exp['grade']}'. Do not hand-edit grades; run "
                        f"tools/grade.py --in-place."
                    )
            for name, exp in expected["categories"].items():
                got = stored.get("categories", {}).get(name, {}).get("grade")
                if got != exp["grade"]:
                    errs.append(
                        f"grades.categories.{name} is '{got}' but the bands "
                        f"compute '{exp['grade']}'."
                    )
            if stored.get("storage_class") != expected["storage_class"]:
                errs.append(
                    f"grades.storage_class is '{stored.get('storage_class')}' but "
                    f"the measured fsync latency computes "
                    f"'{expected['storage_class']}'."
                )
```

Keep every existing policy check unchanged: the directory/provider match, `FILENAME_RE`, the `disk.steady_state.degradation_pct` requirement, the `disk.wal_fsync.p999_us` requirement, and `run.submitter`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_validate_grades.py -v
python3 tools/validate.py results/
```

Expected: 3 passed; `10/10 valid`.

- [ ] **Step 5: Commit**

```bash
git add tools/validate.py tests/test_validate_grades.py
git rm --cached tools/verdict.py 2>/dev/null || true
git commit -m "feat: validate grades and bands_version, not verdicts

The trust property survives the rename: a hand-edited grade still fails the
build, and a stale bands_version is now caught explicitly rather than
surfacing as a confusing per-profile mismatch."
```

---

### Task 8: render.py, CI, and THRESHOLDS.md

Without this the branch is red: `render.py` reads `.verdict` in four places and CI diffs its output. **This is a minimal compatibility pass, not the output redesign** — RESULTS.md keeps its current layout. The index, per-provider pages and `data/index.json` are Plan 3.

**Files:**
- Modify: `tools/render.py`
- Modify: `.github/workflows/validate.yml`
- Rewrite: `THRESHOLDS.md`

**Interfaces:**
- Consumes: everything above.
- Produces: RESULTS.md regenerated with grades.

- [ ] **Step 1: Repoint render.py**

Change `PROFILES` to the seven names from spec §7. Replace `worst_verdict` with a `worst_grade` that reads `r["grades"]["profiles"][p]["grade"]`, ranking worst-wins over `A<B<C<D<F` with `?` as unknown. Update `MARK` to map grades to display strings. Update the reasons section (line ~391) to read `bound_by` from `grades` instead of `verdict.reasons` — the flat `reasons` list no longer exists.

Keep untouched: the median-and-worst-never-a-mean rule, `MIN_RUNS_FOR_SPREAD = 3`, the time-variance vs host-variance split, and the network section (still informational in the table — only `worker_probe`/`playwright_node` grade it).

Add `storage_class` as a column in the summary table, per spec §8.

- [ ] **Step 2: Regenerate and eyeball**

```bash
python3 tools/render.py > RESULTS.md
git diff --stat RESULTS.md
head -40 RESULTS.md
```

**Read it.** Spec §2.5 is the acceptance test a diff cannot check: `ovh/waw` and `ovh/zrh` must now read as *different* machines — waw CPU-bound, zrh disk-bound — where before both were `fail fail fail fail`. If the table still cannot tell them apart, the redesign has not landed and something upstream is wrong.

- [ ] **Step 3: Update CI**

In `.github/workflows/validate.yml`, replace `tools/verdict.py` with `tools/grade.py` in the "Verify verdicts match thresholds" step, and rename the step to "Verify grades match bands". The rest of the job (the tmp-copy + diff pattern) is unchanged.

Add the new test files to the pytest job's scope (it already runs `tests/ -m "not docker"`, so no change needed — verify).

- [ ] **Step 4: Rewrite THRESHOLDS.md**

Per the Global Constraints, a metric needs a row with reasoning and a confidence level or it does not land. Rewrite the document around the band model:

- Explain the A–F bands and worst-wins, including the F-beats-? precedence and why (§4.2).
- One table per category: metric, A/B/C/D bounds, reasoning, confidence — sourced from `why`/`confidence` in `thresholds.yaml`. Do not let the two drift; the YAML is the source of truth and this document explains it.
- A **Provisional bands** section listing the four metrics with no corpus (§11) and stating plainly that they must be recalibrated once real data exists.
- A **Quiet metrics** section explaining the broken-vs-quiet doctrine (§4.4) and naming which metrics are declared quiet and why.
- Update **Known gaps**: network is no longer unjudged everywhere — it is judged in `worker_probe`/`playwright_node` only, and say why that narrowing is principled (§6.5).
- Keep the "Changing a threshold" section, updating `verdict.py` -> `grade.py`.

- [ ] **Step 5: Full verification**

```bash
python3 -m pytest tests/ -q -m "not docker"
python3 tools/validate.py results/
python3 tools/render.py > /tmp/R.md && diff -q RESULTS.md /tmp/R.md && echo "RESULTS.md current"
for f in results/*/*/*.json; do
  tmp=$(mktemp); cp "$f" "$tmp"; python3 tools/grade.py "$tmp" --in-place 2>/dev/null
  diff -q "$f" "$tmp" >/dev/null || echo "MISMATCH $f"; rm -f "$tmp"
done; echo "grade check done"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/validate.yml')); print('workflow OK')"
grep -rn "verdict" tools/ .github/ --include=*.py --include=*.yml | grep -v "^tools/render.py:.*# " || echo "no stale verdict references"
```

Expected: all tests pass; `10/10 valid`; RESULTS.md current; no grade mismatches; workflow OK; no stale references.

- [ ] **Step 6: Commit**

```bash
git add tools/render.py .github/workflows/validate.yml RESULTS.md THRESHOLDS.md
git commit -m "feat: render grades, repoint CI, rewrite THRESHOLDS.md

Minimal compat pass, not the output redesign -- RESULTS.md keeps its current
layout; the index, per-provider pages and data/index.json are Plan 3. But it
has to land here, because render.py reads .verdict and CI diffs its output:
leaving it would ship a red branch, which is the trap Phase 1 already hit
once.

RESULTS.md now shows ovh/waw and ovh/zrh as what they are -- same product,
same price, opposite failures, one CPU-bound and one disk-bound -- where
before both read `fail fail fail fail`. That distinction is the whole reason
for the redesign."
```

---

## Definition of done

- [ ] `python3 -m pytest tests/ -q -m "not docker"` — all pass
- [ ] `python3 tools/validate.py results/` — 10/10 valid
- [ ] Every result's stored `grades` matches `tools/grade.py` output (CI's trust check)
- [ ] `RESULTS.md` is current per `render.py`
- [ ] No `tools/verdict.py`, and no stale `verdict` references in `tools/` or CI
- [ ] `ovh/waw` and `ovh/zrh` render as different machines (spec §2.5 — the acceptance test)
- [ ] `THRESHOLDS.md` has a row per band with reasoning and confidence, and labels the four provisional metrics
- [ ] `tests/test_band_doctrine.py` passes with every metric declared broken-free, quiet, or provisional

## What this plan deliberately does not do

- **No output redesign.** RESULTS.md keeps its current shape. The index, per-provider `results/<provider>/README.md`, and `data/index.json` are Plan 3 (spec §8).
- **No recalibration.** The four provisional bands need a corpus measured with Phase 1's stages, which cannot exist until someone runs them on real hardware (spec §11, Phase 5). They ship labelled.
- **No new measurement.** `bench/` is untouched. If a profile needs a metric that does not exist, that is a Phase 1 change, not this one.
- **No price in grades.** Unchanged (§3).
- **No multi-host measurement.** `patroni_member` and `redis_sentinel` grade the local half and declare the gap (§7.3).

## Known consequences — expected, not bugs

**Most categories will read `?` on the current corpus, and that is correct.**
Category rollup treats every metric in the category as required (a category
grade with a hole in it is not a description of anything). The 10 published
results predate Phase 1's stages, so:

| Category | On v1 results | Why |
|---|---|---|
| `disk` | **graded** | every disk metric already existed |
| `network` | **graded** | already existed |
| `cpu` | `?` — unless an `F` is present | `stall_p999`, `cpu.steady_state`, `tls_verify_s` are absent; `F` beats `?`, so `ovh/waw` still reads `F` (bound by `single_thread_eps`) |
| `ram` | `?` on all 10 | its only metric is `bw_read_mbs`, and the LLC fix means no published result has it |

So the first RESULTS.md after this plan shows graded disk and network, a mostly-
`?` cpu column, and an all-`?` ram column. That is the honest report: Phase 1
changed *what is measured*, and no v1 run measured those things. The fix is
re-running hosts on tool >= 0.2.0, not softening the rollup.

Do **not** "solve" the `?` ram column by adding `ram.seq_read_mbs` to the ram
category. That is the cache-resident legacy metric — the entire bug §5.2 fixed.
An `A` sourced from an L2 benchmark is worse than an honest `?`.

Watch for one thing when reviewing the rendered table: if `disk` also collapsed
to `?`, something is wrong upstream — every disk metric exists in v1 results, and
disk is the flagship category.

## Known risk

`ram.bw_read_mbs` is `required: false` in `timescale_ingest` specifically so v1 results still grade. Once results measured on tool >= 0.2.0 arrive, revisit: a TimescaleDB host's memory bandwidth is not really optional for aggregation-heavy work, and the flag exists to serve the migration, not the workload. Left as-is deliberately — flipping it now would grade every published result `?` on a metric none of them could have measured.
