# Thresholds

Every band in this project lives in
[`schema/thresholds.yaml`](schema/thresholds.yaml). This document explains where
each one came from and how confident we are in it. `tools/grade.py` reads the
YAML, never this file — if the two ever disagree, the YAML is right and this
document is stale and should be fixed.

**These numbers are opinions.** Well-reasoned ones, we hope, but opinions. They
are in a versioned file precisely so you can disagree with them in public. If you
have data suggesting a band is wrong, open an issue — that is a better
contribution than another result file.

## How grades work

A **metric** (`disk.wal_fsync.p999_us`, `cpu.single_thread_eps`, ...) has five
bands, A through F. A/B/C/D each carry a bound; F is implicit — worse than the D
bound, never a number of its own. Bands belong to the metric, not to a profile:
`postgres_oltp` and `redis_sentinel` both read `wal_fsync.p999_us` and must agree
on what a B means there.

Two things consume metrics:

- **Categories** (`disk`, `cpu`, `ram`, `network`) describe the machine,
  workload-agnostic. Every metric in a category is required — a category grade
  with a hole in it does not describe anything.
- **Profiles** (`postgres_oltp`, `timescale_ingest`, `patroni_member`,
  `redis_sentinel`, `worker_probe`, `playwright_node`, `nuxt_ssr`) are opinions
  that consume category metrics, each with its own `required: true/false` per
  rule.

Both roll up **worst-wins**, never an average: **the worst band among a
category's or profile's metrics is the grade**, reported together with the
metric that bound it (`bound_by`). A machine with a superb sequential write
speed and a 118 ms fsync p99.9 is not "on average fine" for Postgres — it is
unusable for Postgres, and averaging would hide exactly that. This is the same
non-compensatory stance the project has always taken; only the number of bands
changed, from three (pass/marginal/fail) to five (A/B/C/D/F).

**A rule marked `required: true` whose value is missing makes the grade `?`**
rather than letting it pass by omission. A `required: false` rule that is
missing is simply skipped, and the rollup proceeds over what remains — this is
what lets a profile still grade on a result that predates a newer measurement
(see [Known consequences](#known-gaps) below).

### F beats `?`

Precedence, in order:

1. any rule grades **F** → **F**
2. else any `required: true` rule is missing → **`?`**
3. else → the worst band actually present

**F outranks `?` on purpose**, even when a required rule is also missing. Two
reasons:

- **It is more true.** A host with a 118 ms fsync p99.9 is F for `postgres_oltp`
  whether or not its scheduler stall was ever measured. Grading is
  non-compensatory, so no unmeasured metric could have rescued it — reporting
  `?` there would discard a fact we hold.
- **`?` must not become a hiding place.** If a missing metric outranked a
  measured failure, skipping a benchmark stage would turn an F into a `?` — a
  strictly better-looking cell in the table, obtained by running *less* of the
  suite. A grading rule that rewards measuring less is a rule a submitter will
  eventually find.

### Storage class

Every result also carries `storage_class`, **derived from measured fsync
latency-per-op** (`1e6 / disk.wal_fsync.iops`), never from what a provider calls
the disk:

| Class | µs per durable write |
|---|---|
| `local-nvme` | < 300 |
| `net-fast` | 300 – 1,500 |
| `net-slow` | 1,500 – 10,000 |
| `degraded` | > 10,000 |

It is a **facet, never a curve**: it explains a grade — "this is a network-
storage machine" — and never softens one. A `net-slow` host that fails
`postgres_oltp` still fails `postgres_oltp`; grading it against its own class
would excuse exactly what this project exists to expose.

## disk

Every disk metric already existed before Phase 1, so this category grades on
all 10 published results.

| Metric | A | B | C | D | F | Reasoning | Confidence |
|---|---|---|---|---|---|---|---|
| `disk.wal_fsync.p999_us` | ≤ 1 ms | ≤ 3 ms | ≤ 10 ms | ≤ 50 ms | > 50 ms | The flagship. Every `COMMIT` waits on one `fdatasync`, alone, with no queue to hide behind. p99.9 is what the slowest transactions feel; the mean describes a commit nobody complains about. | **High** |
| `disk.wal_fsync.iops` | ≥ 5,000 | ≥ 1,000 | ≥ 333 | ≥ 100 | < 100 | Latency-anchored, not IOPS-anchored: at QD1 this is ~1/mean-latency, so it describes the typical commit where p99.9 describes the tail. Bounds are 200 µs / 1 ms / 3 ms / 10 ms per durable write. | Medium |
| `disk.rand_read_8k.p99_us` | ≤ 500 µs | ≤ 2 ms | ≤ 5 ms | ≤ 15 ms | > 15 ms | 8k is the Postgres page size; index lookups are random reads. A query doing 100 lookups at 5 ms p99 has a real chance of one slow read. | **High** |
| `disk.rand_read_8k.iops` | ≥ 100,000 | ≥ 50,000 | ≥ 20,000 | ≥ 5,000 | < 5,000 | Generational marker rather than a workload requirement. Advisory. | Low |
| `disk.rand_write_8k.iops` | ≥ 50,000 | ≥ 20,000 | ≥ 10,000 | ≥ 3,000 | < 3,000 | Checkpoint flush rate. Advisory. | Low |
| `disk.seq_write.bw_mbs` | ≥ 1,000 MB/s | ≥ 500 MB/s | ≥ 200 MB/s | ≥ 100 MB/s | < 100 MB/s | Chunk writes and compression output are sequential and bulky. | Medium |
| `disk.seq_read.bw_mbs` | ≥ 2,000 MB/s | ≥ 1,000 MB/s | ≥ 500 MB/s | ≥ 200 MB/s | < 200 MB/s | Continuous aggregate refresh reads whole chunks. Advisory: a generational marker rather than a figure derived from a workload requirement — `timescale_ingest` reads it as `required: false` for that reason. On the current corpus it produces both A and D (hetzner and ovh/waw clear 5.7–7.4 GB/s, ovh/prg and ovh/zrh sit pinned at 300 MB/s): it discriminates, so it is neither broken nor quiet. | Low |
| `disk.steady_state.degradation_pct` | ≤ 5% | ≤ 15% | ≤ 30% | ≤ 50% | > 50% | Burst credits. A 60 s run measures the credit balance; 30 minutes measures the machine you will actually run. **Quiet** in the current corpus (0.0–2.0% across all 10 runs) — see [Quiet metrics](#quiet-metrics). | Medium |

## cpu

`stall_p999_us`, `steady_state.degradation_pct`, and `tls_verify_s` are new in
Phase 1 and carry no corpus yet — see
[Provisional bands](#provisional-bands). The other three grade fully.

| Metric | A | B | C | D | F | Reasoning | Confidence |
|---|---|---|---|---|---|---|---|
| `cpu.single_thread_eps` | ≥ 1,400 | ≥ 1,000 | ≥ 700 | ≥ 400 | < 400 | Redis, each Node worker, and each Postgres backend are bounded by one core. Contemporary server silicon lands ~1,600–1,800. This metric already caught a 4.7x starvation on `ovh/waw` (356 vs ~1,600) that steal time missed entirely. | Medium |
| `cpu.scaling_efficiency` | ≥ 0.85 | ≥ 0.70 | ≥ 0.55 | ≥ 0.40 | < 0.40 | `multi / (single * cores)`. ~0.9 means physical cores; ~0.6 means SMT siblings sold as cores; well below means sharing physical cores with other tenants. **Quiet** — all 10 backfilled values fall in 0.953–1.018 (all A), because every host measured so far hands out physical cores. | Medium |
| `cpu.steal_pct_under_load` | ≤ 0.5% | ≤ 2% | ≤ 5% | ≤ 10% | > 10% | C is a correctness line, not a performance one: past ~5%, Patroni heartbeats start missing TTLs and a failover fires because the host was busy, not because anything was wrong. **Quiet** — these hosts genuinely do not steal (0.0–0.24%). | **High** |
| `cpu.stall_p999_us` | ≤ 100 µs | ≤ 500 µs | ≤ 2 ms | ≤ 10 ms | > 10 ms | **PROVISIONAL — no corpus.** Redis is one thread; each Node worker is one event loop. A stall is dead time for every client, with no other core to absorb it. Measured at `SCHED_OTHER` because that is the class Redis and Node actually run in. | Low |
| `cpu.steady_state.degradation_pct` | ≤ 5% | ≤ 15% | ≤ 30% | ≤ 50% | > 50% | **PROVISIONAL — no corpus.** CPU burst credits, the same trap the disk steady test exists for. This is the metric that catches a node pinned at a throttled baseline — the Prague failure mode (§2.6), which steal time did not see. | Low |
| `cpu.tls_verify_s` | ≥ 30,000 | ≥ 15,000 | ≥ 7,000 | ≥ 3,000 | < 3,000 | **PROVISIONAL — no corpus.** SSL checks are handshake-bound. A probe is the TLS *client*, and the client *verifies* (the server signs) — verify is the expensive half, 2 scalar mults vs 1, so it is ~3x slower than sign. Do not swap this for `tls_sign_s`. | Low |

## ram

| Metric | A | B | C | D | F | Reasoning | Confidence |
|---|---|---|---|---|---|---|---|
| `ram.bw_read_mbs` | ≥ 40,000 MB/s | ≥ 25,000 MB/s | ≥ 15,000 MB/s | ≥ 8,000 MB/s | < 8,000 MB/s | **PROVISIONAL — and it cannot be calibrated from the existing corpus at all**: those 10 numbers were measured with a 1M working set that sat in L2, so they describe cache, not memory, and cannot calibrate their own replacement. Anchored to DDR generation instead: DDR4-2666 dual ~43 GB/s, DDR5-4800 dual ~76 GB/s; single channel halves it. | Low |
| `host.ram_mb` *(not in the `ram` category — read directly by `playwright_node`)* | ≥ 16,384 | ≥ 8,192 | ≥ 4,096 | ≥ 2,048 | < 2,048 | Each Chromium is ~300–500 MB, so a 2 GB VPS cannot run 4 concurrent browsers regardless of core speed. A sizing fact, and actionable. | Medium |

`ram` is the only category with a single metric, and that metric is
provisional, so **`ram` grades `?` on every one of the 10 published results.**
That is correct, not a bug: the LLC fix means no published result carries a
valid `ram.bw_read_mbs` (see [Known consequences](#known-gaps) in RESULTS.md).
Do not "solve" this by substituting `ram.seq_read_mbs` — that is the
cache-resident legacy metric §5.2 exists to stop using; an A sourced from an L2
benchmark is worse than an honest `?`.

## network — worker profiles only

**`network.loss_pct` and `network.rtt_jitter_ratio` are read by exactly two
profiles: `worker_probe` and `playwright_node`.**
`postgres_oltp`, `timescale_ingest`, `patroni_member`, `redis_sentinel`, and
`nuxt_ssr` read neither. Throughput (`mbps`) is never graded by any profile,
in any category.

| Metric | A | B | C | D | F | Reasoning | Confidence |
|---|---|---|---|---|---|---|---|
| `network.loss_pct` | ≤ 0.01% | ≤ 0.1% | ≤ 0.5% | ≤ 2% | > 2% | Derived, not chosen: an ICMP check sending 3 packets and declaring "down" on total loss false-alarms at rate `p^3`. At `p` = 10%, that is 1-in-1000 checks; at one check per minute, 1.4 false alarms per day. The corpus contains exactly this case: `ovh/zrh -> hetzner-ash` at 10% loss. | Medium |
| `network.rtt_jitter_ratio` | ≤ 1.1 | ≤ 1.5 | ≤ 2.0 | ≤ 5.0 | > 5.0 | `rtt_p99_ms / rtt_p50_ms`. Timing-sensitive checks care about the spread, not the mean. | Low |

`network.dns_ms` has a band in `schema/thresholds.yaml` but **no category or
profile reads it** — it is recorded and reported, never graded.
`06-network.sh` measures it as one `curl` `time_namelookup` per target: a
single uncached first lookup, n=1, no warming, and the worst-of-four rollup
this project uses everywhere else would then report *worst-of-four-cold-
first-lookups* as if it were a property of the host. The corpus disagrees:
ovh/waw, one run, one resolver, four targets: 1.86 / 81.07 / 109.60 / 149.45
ms — an 80x spread that is authoritative-NS distance and cache state, not the
machine. Grading it would repeat the extreme-value mistake this redesign
exists to fix. The band stays in the YAML, unused, so a future fix to
`06-network.sh` (repeated lookups, warm and cold separated) has a calibrated
starting point instead of nothing.

Why only these two profiles: see [Known gaps](#known-gaps) below — this is a
narrowing of the project's long-standing "no verdict reads network" stance, not
a reversal of it.

## Provisional bands

These four metrics ship with a band **and no corpus behind it**. They are
listed here explicitly so nobody mistakes a plausible-looking number for a
calibrated one, and so the follow-up is not forgotten:

| Metric | Why provisional |
|---|---|
| `cpu.stall_p999_us` | New tool (`cyclictest`, replacing the broken `redis-cli --intrinsic-latency` max-only stat). No result has run it yet. |
| `cpu.steady_state.degradation_pct` | New 15-minute sustained-load stage. No result has run it yet. |
| `cpu.tls_verify_s` | New metric. No result has run it yet. |
| `ram.bw_read_mbs` | New metric, and the LLC working-set fix means the 10 existing results cannot calibrate it even retroactively — they measured cache, not memory. |

**These must be recalibrated once real data exists.** Writing them as High
confidence today would repeat the mistake this redesign exists to fix: three
of the original thresholds (`wal_fsync.iops >= 15,000`, `rand_read.p99 <= 1ms`,
`intrinsic_latency <= 200us`) were unreachable by any VM in the corpus, and
every one of 10 runs failed all three before anyone noticed. A provisional
label is the safeguard against that happening again — do not promote one of
these four to a non-provisional confidence level without a corpus behind it,
however reasonable the number looks on paper.

## Quiet metrics

A metric that produces the same grade across the entire corpus is either
**broken** or **quiet**, and the difference is intent, not appearance — from
inside a single corpus the two look identical, one grade every time.

- **Broken threshold** — unreachable by construction, on every machine, in any
  plausible corpus. This is what happened to the original `wal_fsync.iops`,
  `rand_read.p99`, and `intrinsic_latency` bounds: dead forever, and they were
  deleted or rebanded rather than kept.
- **Quiet metric** — reachable in both directions; this corpus merely happens
  to be clean. **Keep it.** It is insurance that fires on a bad host, and its
  silence is itself a finding: it says these ten machines genuinely do not
  exhibit the failure mode, not that the metric cannot see one.

`tests/test_band_doctrine.py` makes this executable: any metric that yields one
grade across the corpus and is not declared `quiet: true` or `provisional: true`
fails CI. Declared quiet today:

- **`disk.steady_state.degradation_pct`** (0.0–2.0% across all 10 runs) — none
  of the hosts measured so far throttle under sustained load, which is
  good news, not a dead metric.
- **`cpu.scaling_efficiency`** (0.953–1.018, all A) — every host measured so
  far hands out physical cores rather than SMT siblings sold as whole cores.
- **`cpu.steal_pct_under_load`** (0.0–0.24%) — these hosts genuinely do not
  steal cycles from the VM. C (5%) is a Patroni-TTL correctness line, not a
  performance one, and it is reachable on an oversubscribed host even though
  none in this corpus is.

## Known gaps

- **Network is judged only where the network *is* the workload.** Before this
  redesign, nothing read the network fields at all: "a database host needs
  500 Mbit/s" cannot be derived from any workload requirement — it would be a
  number chosen to look authoritative, and that reasoning still holds for
  `postgres_oltp`, `timescale_ingest`, `patroni_member`, `redis_sentinel`, and
  `nuxt_ssr`. It **collapses for a monitoring probe or a browser check, where
  the network is the thing being tested** — `worker_probe` runs HTTP/SSL/
  ICMP/SMTP/SSH/FTP/TCP checks, and `playwright_node` loads real pages, so a
  lossy or high-jitter path is the failure mode itself, not an excuse for one.
  This is a principled narrowing of the old blanket exclusion, not an
  abandonment of it: throughput (`mbps`) still has no workload-derived floor
  and stays ungraded everywhere, including in those two profiles.
- **`cpu` reads `?` on most of the current corpus, `ram` on all of it.** The 10
  published results predate Phase 1's stages and carry none of
  `cpu.stall_p999_us`, `cpu.steady_state.degradation_pct`, `cpu.tls_verify_s`,
  or `ram.bw_read_mbs`. `disk` and `network` grade fully because every metric
  they need already existed. The fix is re-running hosts on tool ≥ 0.2.0, not
  softening the rollup — see [Provisional bands](#provisional-bands).
- **No thresholds for object storage, queues, or search.** Contributions
  welcome.
- **`nuxt_ssr` is the weakest profile.** Web apps tolerate a lot. It is close
  to "is this machine broken?" rather than a meaningful fitness test.
- **Nothing accounts for price.** A machine that grades F on `postgres_oltp` at
  €5/mo is a different proposition from one that grades F at €200/mo. Price is
  recorded in every result but deliberately excluded from grades — we do not
  want to be in the business of deciding what a millisecond is worth to you.
- **Bands are absolute, never a curve.** As hardware improves, an A gets
  easier to reach. Revisiting these annually is the plan. A curve (grading
  against the rest of the corpus) was considered and rejected: if the whole
  field is bad, the best of a bad lot would still get an A, verdicts would
  shift every time an unrelated party submitted a result, and a provider could
  flood submissions with bad machines to lift its own relative rank.
- **`storage_class` explains, it does not excuse.** A `net-slow` host that
  fails `postgres_oltp` still fails it; the class is recorded so a reader can
  ask "is this a disk-bound problem or a CPU-bound problem", not so a bad grade
  can be argued away as "well, it's network storage".

## Changing a threshold

1. Open an issue with the number, the proposed number, and why.
2. If accepted, change `schema/thresholds.yaml` and this document together —
   the YAML is what the code reads; this document only explains it, and the two
   must not drift.
3. CI recomputes every grade in `results/` with `tools/grade.py` and
   regenerates `RESULTS.md`. **Existing results may flip.** That is the system
   working: a grade is a function of the data and the bands, and if the bands
   change, so does the output. We do not grandfather old grades.
