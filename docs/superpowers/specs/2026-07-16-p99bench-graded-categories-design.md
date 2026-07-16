# p99bench: graded categories, VM-grounded bands, and a result set that scales

Status: approved design, not yet implemented
Date: 2026-07-16

## 1. The problem

p99bench currently emits one word per workload profile: `pass`, `marginal`,
`fail`, or `unknown`. Across the entire 10-run corpus, that word is `fail`
almost everywhere, which means the output carries no information. A benchmark
whose verdict column is constant is not measuring anything a reader can act on.

Three things are wrong, and they are different kinds of wrong:

1. **Some thresholds are unreachable by any VM.** They were calibrated against
   bare metal with a battery-backed write cache. Practically every reader is on
   a VM.
2. **Some metrics measure the wrong thing.** No threshold can fix a metric that
   is measuring CPU cache when it claims to measure RAM.
3. **The output shape is a single verdict.** Even with correct thresholds, one
   word per profile cannot answer "what *is* this machine?" — only "may I run
   Postgres on it?"

Separately, the result set is one flat `RESULTS.md`. At 5 products it is 180
lines. At several hundred it is unreviewable, and any threshold change rewrites
the entire file in one diff.

## 2. Evidence

All figures below are measured from the 10 results in `results/` as of
2026-07-15/16, not estimated.

### 2.1 Three rules no VM in the corpus can pass

| Rule | `pass` demands | Best VM measured | Gap |
|---|---|---|---|
| `disk.wal_fsync.iops` | >= 15,000 | 1,587.8 | 10x |
| `disk.rand_read_8k.p99_us` | <= 1,000 us | 2,408.4 | 2.4x |
| `cpu.intrinsic_latency_max_us` | <= 200 us | 1,642 | 8x |

These three alone guarantee that `postgres_oltp`, `redis_aof` and `nuxt_ssr`
can never return anything but `fail` on any VM, regardless of hardware quality.
They account for 30 of the failures in the current `RESULTS.md` "Why runs
failed" table.

### 2.2 Two rules everything passes trivially

| Rule | `pass` demands | Corpus range |
|---|---|---|
| `cpu.steal_pct_under_load` | <= 2% | 0.0 – 0.24% |
| `disk.steady_state.degradation_pct` | <= 10% | 0.0 – 2.0% |

Zero discriminating power in this corpus. Unlike 2.1 these are **reachable in
both directions** — see the doctrine in §4.4.

### 2.3 The measurements themselves are good

Corpus spread (max/min) per metric:

| Metric | Spread |
|---|---|
| `disk.wal_fsync.p999_us` | **244.8x** |
| `disk.wal_fsync.iops` | 22.1x |
| `disk.seq_write.bw_mbs` | 18.5x |
| `disk.rand_read_8k.iops` | 10.8x |
| `disk.rand_read_8k.p99_us` | 7.6x |
| `cpu.single_thread_eps` | 4.7x |
| `ram.seq_read_mbs` | 3.3x (but see §5.2 — measuring cache) |

The instrument works. The scoring on top of it is what is broken.

### 2.4 Storage class is a hidden variable

Converting `wal_fsync.iops` to latency-per-durable-write splits the corpus into
three physics regimes:

| Regime | us per fsync | Hosts |
|---|---|---|
| fast path | 630 – 840 | hetzner hel-1, ovh waw |
| network block storage | 3,360 – 5,562 | ovh prg, ovh zrh |
| degraded | 13,945 | windcloud enge-sande |

One absolute threshold across all three is a large part of why everything
fails.

### 2.5 The failure the current suite cannot express

`ovh/waw` and `ovh/zrh` are the same product at the same price, and fail in
**opposite** ways:

- `ovh/waw`: healthy disk, broken CPU (`single_thread_eps` = 356 vs ~1,600
  elsewhere, 4.7x down)
- `ovh/zrh`: healthy CPU, destroyed disk (`wal_fsync.p999_us` = 118–137 ms)

Today both render as `fail fail fail fail` — indistinguishable. Surfacing that
difference is the point of this redesign.

### 2.6 Field validation: the Prague Playwright node

Production data from uptimeify.io, same app under test from three probe
locations:

| Location | checks | fails | fail % | passing-run p50 |
|---|---|---|---|---|
| cz-prg | 329 | 171 | 52% | 42.0 s |
| pl-waw | 318 | 6 | 1.9% | 17.8 s |
| fi-hel | 285 | 6 | 2.1% | 12.5 s |

Diagnostic findings that shape this design:

- Prague's **plain HTTP checks were fine** (3.9% vs fi-hel 3.8%) — egress
  healthy, so this is not a network fault.
- Only **browser** checks failed, and even *passing* Prague runs were ~3x
  slower than peers. The node was resource-starved, not unstable.
- Failure was **constant, day and night** (2–21 fails/hr around the clock) — a
  persistent throttled baseline, not a load spike.
- **Steal time did not catch it.** Every OVH box in our corpus reads 0.1–0.2%
  steal. `single_thread_eps` did catch a 4.7x starvation on `ovh/waw`.

Conclusion: the metric that predicts a Prague-style node is **sustained CPU
throughput**, not steal. p99bench has a sustained test for disk burst credits
(`01b-steady.sh`) and **no CPU equivalent**, so a short sysbench run measures
the CPU credit balance — the same trap the disk tests were built to escape.

## 3. Goals and non-goals

### Goals

- Output that discriminates: a reader can tell two machines apart.
- Per-category grades so a reader sees *what the machine is*, not only whether
  one workload fits.
- Thresholds reachable by real VMs, anchored to workload physics.
- Profiles covering uptimeify's actual workloads.
- A result set that stays reviewable and traceable at hundreds of
  provider/region/product combinations.

### Non-goals

- **Price is still excluded from grades.** Recorded, never scored. Unchanged.
- **No curve.** Grades never depend on who else submitted (§4.3).
- **No multi-host measurement.** Cluster profiles grade the local half and name
  the gap (§7.3).
- **No compensatory scoring.** Worst-wins throughout (§4.2).

## 4. The framework

### 4.1 Two axes

The request mixed two questions. Separating them is what makes the design
scale.

**Capability categories** describe the machine, workload-agnostic:
`disk`, `cpu`, `ram`, `network`.

**Workload profiles** are opinions that consume category metrics:
`postgres_oltp`, `timescale_ingest`, `patroni_member`, `redis_sentinel`,
`worker_probe`, `playwright_node`, `nuxt_ssr`.

Categories answer "what is this machine". Profiles answer "what can it run".
Both grade A–F. Both roll up worst-wins. Both name the binding constraint.

Consequence that matters for maintenance: **adding a profile requires no new
measurement** if its metrics already exist. Profiles are a YAML edit.

### 4.2 Grading is non-compensatory

Each metric gets 5 bands (A/B/C/D/F). A category's grade is the **worst band
among its metrics**, reported together with the metric that bound it:

```
disk        C   bound by: wal_fsync.p999_us = 8.4ms
  fsync p99.9   8.4 ms   D  ~100 commits/s ceiling
  rand-read p99 18.2 ms  D
  seq write     300 MB/s C
  fsync IOPS    298      C

cpu         A   bound by: -
  1-thread eps  1580     A
  stall p99.9   5.1 ms   B
  steal @load   0.13%    A

profiles
  postgres_oltp    C  (disk-bound)
  playwright_node  A
```

This preserves the existing METHODOLOGY stance. A weighted 0–100 score was
rejected: a machine with 459 ms fsync and excellent sequential throughput would
score mid-70s on disk, and the average would hide the disqualifier — which is
the precise failure this project exists to prevent.

**`required` semantics carry over from v1 unchanged.** Each rule keeps its
`required: true|false` flag:

- missing value on a `required: true` rule -> the whole category/profile grades
  `?`. It never grades by omission.
- missing value on a `required: false` rule -> that rule is skipped; the rollup
  proceeds over the remaining rules.

This is what lets a profile survive a metric that a given tool version did not
collect (§9.3), and it is why the advisory-only rules of v1 stay advisory.

### 4.3 Bands are absolute, never a curve

Corpus-relative percentile grading was rejected for three reasons:

1. If the whole field is bad, the best of a bad lot gets an A. The absolute
   claim ("this will run your database") is destroyed.
2. Verdicts would shift as unrelated third parties submit.
3. It is attackable: a provider could flood submissions with bad machines to
   lift their own rank.

Grades stay a pure function of measured numbers and a versioned file. CI
recomputes and rejects hand-edits, exactly as today.

### 4.4 Doctrine: "broken threshold" vs "quiet metric"

A new rule, to prevent this class of bug recurring:

- **Broken threshold** — unreachable by construction, on every machine, in any
  plausible future corpus (`wal_fsync.iops >= 15,000`). Dead forever. Delete or
  reband.
- **Quiet metric** — reachable in both directions; this corpus merely happens
  to be clean (`steal` at 0–0.24%). **Keep.** It is insurance that fires on an
  oversubscribed host, and its silence is itself a finding.

A band set must be checked against the corpus before it ships. A metric that
produces one grade across all known data is either broken or quiet, and the
author must say which.

### 4.5 Storage class is a facet, never a curve

Each result is assigned `storage_class`, **derived from measured fsync
latency-per-op** (`1e6 / disk.wal_fsync.iops`), not from provider marketing:

| Class | us per durable write | Corpus members |
|---|---|---|
| `local-nvme` | < 300 | none yet |
| `net-fast` | 300 – 1,500 | hetzner hel-1 (721–839), ovh waw (630) |
| `net-slow` | 1,500 – 10,000 | ovh prg (3,360–3,390), ovh zrh (4,316–5,562) |
| `degraded` | > 10,000 | windcloud enge-sande (13,945) |

Class is recorded, filterable, and used to *explain* a grade. It never softens
one. A network-storage VM genuinely is worse for Postgres; grading it against
its own class would excuse exactly what this project exists to expose.

## 5. Metric integrity fixes (must land before calibration)

Calibrating a metric that measures the wrong thing produces well-tuned
nonsense. These are sequenced first.

### 5.1 `cpu.intrinsic_latency_max_us` — replace the tool

`redis-cli --intrinsic-latency` emits only a running max and an average. From a
real result: `avg = 0.0565 us, max = 1642 us` — a ~30,000x skew. The average
describes the loop, not the stalls. The max is an extreme-value statistic:
worst-of-60-million-samples, which only grows with sample size. Every VM is
preempted at least once per minute, so **this metric measures "is this a VM?",
not "is this VM good"**, and no threshold on this tool can discriminate.

**Fix:** replace with `cyclictest` (`rt-tests` package), which emits a latency
histogram. Derive `cpu.stall_p99_us`, `cpu.stall_p999_us`, `cpu.stall_max_us`.

Note `cyclictest` measures timer-wakeup latency rather than tight-loop
preemption. Both are valid hypervisor-stall proxies; cyclictest is the standard
tool and is the only one of the two that yields percentiles.

New dependency: `rt-tests`. Must be added to the README install line and CI.

### 5.2 `ram.seq_read_mbs` — working set must exceed LLC

Hetzner reports **207,409 MB/s (207 GB/s)** on a single-populated-slot EPYC
Genoa. DDR5-4800 single channel peaks near 38 GB/s. sysbench at its current
block size is resident in L1/L2 — this is a cache benchmark wearing a RAM
label. The `pass: >= 15,000` bar is cleared by ~13x by every machine, which is
why it has never failed anything.

**Fix:** size the working set above last-level cache. This **changes what is
measured**, so it also gets a new field name (§9.2).

### 5.3 `cpu.scaling_efficiency` — live bug, null everywhere

Null in all 10 results despite both inputs being present:
`6496.39 / (1661.19 x 4) = 0.98`. This is the metric that separates physical
cores from SMT siblings sold as cores, and it is silently absent.

**Fix:** repair the computation. **Backfillable into existing results without
re-running** — the inputs are already in the files.

### 5.4 `cpu.aes_256_gcm_mbs` — demote to informational

37,717 MB/s against a `pass: >= 2000` bar — 18x over. Every CPU of this decade
has AES-NI. It is a "is this hardware ancient" check, not a grade input. Keep
recording it; remove it from grading.

For TLS-handshake-bound workloads (SSL checks), bulk crypto throughput is the
wrong metric entirely — see `cpu.tls_handshakes_s` in §6.3.

### 5.5 New stage: CPU sustained load (15 min)

Mirrors `01b-steady.sh` for CPU. Measures single- and multi-core degradation
from minute 1 to minute 15, plus steal and clock under sustained load. Emits
`cpu.steady_state.degradation_pct`.

15 minutes rather than 30: CPU credit budgets are granted in seconds-to-minutes,
so 15 exhausts typical schemes at half the wall-clock cost of the disk stage.
Total run goes from ~45 min to ~60 min.

It **cannot** overlap the disk steady-state stage: fio saturating the disk would
make the CPU numbers measure I/O wait, and the two would be indistinguishable.

This is the stage that catches the Prague failure mode (§2.6).

### 5.6 Do not score clock speed

Asked directly during design; answered here for the record.

Every result in the corpus reports `clock_idle_mhz: 2400,
clock_under_load_mhz: 2400` — identical, because VMs do not expose real
cpufreq, so the field reports the nominal value. It measures nothing. Across
CPU generations, IPC differences mean MHz does not compare anyway.

On external CPU scoring systems: Geekbench and Passmark are proprietary with
licensing that conflicts with inviting provider submissions — the same trap that
keeps Ookla optional (see CONTRIBUTING.md). SPEC CPU is paid. The free,
redistributable options are CoreMark, 7-zip's benchmark, and sysbench.

**sysbench is already in the suite and already discriminating 4.7x.** Decision:
keep sysbench EPS as the CPU primary, fix `scaling_efficiency`, add the
sustained stage. No new CPU scoring dependency.

## 6. Band definitions

Every bound is a round latency or a workload statement. None is a percentile of
the corpus.

### 6.1 Disk

**`disk.wal_fsync.p999_us`** — the flagship. Read as a commit-tail ceiling.

| Grade | Bound | Means |
|---|---|---|
| A | <= 1,000 us | local-NVMe/PLP class; tail invisible |
| B | <= 3,000 us | solid VM; ~300+ commits/s single-writer |
| C | <= 10,000 us | modest OLTP; 1-in-1000 commits stalls 10 ms |
| D | <= 50,000 us | batch only; not a transactional host |
| F | > 50,000 us | durability path broken |

A is unreached in the current corpus but **reachable on real hardware** (local
NVMe fsyncs in 30–100 us). This is the test `>= 15,000 IOPS` failed.

**`disk.wal_fsync.iops`** — latency-anchored, not IOPS-anchored. At QD1 this is
~1/mean-latency, so it describes the typical commit where p99.9 describes the
tail.

| Grade | Bound | us/op |
|---|---|---|
| A | >= 5,000 | <= 200 |
| B | >= 1,000 | <= 1,000 |
| C | >= 333 | <= 3,000 |
| D | >= 100 | <= 10,000 |
| F | < 100 | > 10,000 |

**`disk.rand_read_8k.p99_us`** — A <= 500 us · B <= 2,000 · C <= 5,000 ·
D <= 15,000 · F > 15,000. 8k is the Postgres page size; index lookups are random
reads.

**`disk.rand_read_8k.iops`** — A >= 100,000 · B >= 50,000 · C >= 20,000 ·
D >= 5,000 · F < 5,000.

**`disk.rand_write_8k.iops`** — A >= 50,000 · B >= 20,000 · C >= 10,000 ·
D >= 3,000 · F < 3,000.

**`disk.seq_write.bw_mbs`** — A >= 1,000 · B >= 500 · C >= 200 · D >= 100 ·
F < 100.

**`disk.steady_state.degradation_pct`** — A <= 5 · B <= 15 · C <= 30 · D <= 50 ·
F > 50. Quiet metric in the current corpus (all A).

### 6.2 CPU

**`cpu.single_thread_eps`** — A >= 1,400 · B >= 1,000 · C >= 700 · D >= 400 ·
F < 400. Contemporary server silicon lands ~1,600–1,800.

**`cpu.steal_pct_under_load`** — A <= 0.5% · B <= 2% · C <= 5% · D <= 10% ·
F > 10%. C is the Patroni TTL correctness line, not a performance line. Quiet
metric in the current corpus (all A).

**`cpu.scaling_efficiency`** — A >= 0.85 (physical cores) · B >= 0.70 ·
C >= 0.55 (SMT siblings sold as cores) · D >= 0.40 · F < 0.40.

**`cpu.stall_p999_us`** *(provisional, §11)* — A <= 100 us · B <= 500 ·
C <= 2,000 · D <= 10,000 · F > 10,000.

**`cpu.steady_state.degradation_pct`** *(provisional, §11)* — A <= 5 · B <= 15 ·
C <= 30 · D <= 50 · F > 50.

### 6.3 New CPU metric: TLS handshakes

**`cpu.tls_handshakes_s`** — from `openssl speed ecdsap256`, runs in seconds.

SSL checks are handshake-bound (ECDSA sign/verify), not bulk-crypto-bound. This
is the metric `aes_256_gcm_mbs` was standing in for and getting wrong.

Bands provisional until first corpus.

### 6.4 RAM

**`ram.bw_read_mbs`** *(provisional, §11)* — A >= 40,000 · B >= 25,000 ·
C >= 15,000 · D >= 8,000 · F < 8,000. Anchored to DDR generation: DDR4-2666
dual-channel ~40 GB/s, DDR5-4800 dual-channel ~76 GB/s; single channel halves
it.

**`host.ram_mb`** — graded input to `playwright_node` only. Each Chromium is
~300–500 MB, so a 2 GB VPS cannot run 4 concurrent browsers regardless of core
speed. A sizing fact, and actionable.

### 6.5 Network — worker profiles only

This narrows, rather than abandons, the existing "no verdict reads network"
doctrine.

THRESHOLDS.md rejects network thresholds because *"a rule like 'a database host
needs 500 Mbit/s' cannot be derived from any workload requirement — it would be
a number chosen to look authoritative."* That reasoning is sound for a database
host. It **collapses for a monitoring probe, where the network is the
workload**.

**New stated rule: a network metric may carry a threshold only in a profile
whose workload is the network itself.** `worker_probe` and `playwright_node`
read these fields. `postgres_oltp`, `timescale_ingest`, `patroni_member`,
`redis_sentinel` and `nuxt_ssr` still read none.

**`network.loss_pct`** — A <= 0.01% · B <= 0.1% · C <= 0.5% · D <= 2% · F > 2%.

Derived, not chosen: an ICMP check sending 3 packets and declaring "down" on
total loss false-alarms at rate `p^3`. At `p` = 10%, that is 1-in-1000 checks;
at one check per minute, **1.4 false alarms per day**. The corpus contains
exactly this case — `ovh/zrh -> hetzner-ash` at 10% loss — which currently
appears only as a footnote and will now grade F.

**`network.dns_ms`** — A <= 5 · B <= 20 · C <= 50 · D <= 100 · F > 100. Every
HEAD/GET check pays DNS before it starts.

**`network.rtt_jitter_ratio`** (`rtt_p99_ms / rtt_p50_ms`) — A <= 1.1 · B <= 1.5
· C <= 2.0 · D <= 5.0 · F > 5.0.

Throughput (`mbps`) stays informational in every profile. No workload
requirement yields a Mbit/s floor.

### 6.6 Band validation against the corpus

Applying §6.1–6.2 bands to the 10 existing results:

```
host                    fsync   fsync   rr     rr     rw     seq    1thr   steal  steady
                        p99.9   iops    p99    iops   iops   write  eps
hetzner/hel-1            B       B      C      B      A      A      A      A      A
ovh/waw                  B       B      D      C      C      A      F      A      A
ovh/prg                  C/D     D      F      D      D      C      A      A      A
ovh/zrh                  F       D      F      D      D      C      A      A      A
windcloud/enge-sande     F       F      D      B      C      B      D      ?      A
```

Distinct grades produced: 4 on `wal_fsync.p999_us`; 3 on each of `fsync.iops`,
`rand_read.p99`, `rand_read.iops`, `rand_write.iops`, `seq_write.bw`,
`single_thread_eps`. `steal` and `steady_state` produce one grade — declared
**quiet**, not broken, per §4.4.

`ovh/waw` vs `ovh/zrh` now read as opposite failures (§2.5) rather than as two
identical `fail` rows.

## 7. Profiles

| Profile | Reads | Notes |
|---|---|---|
| `postgres_oltp` | fsync p99.9, fsync iops, rr p99, rr iops, rw iops, steal, disk steady | unchanged intent, rebanded |
| `timescale_ingest` | fsync p99.9, seq read/write, rw iops, ram bw, steal, disk steady | rebanded |
| `patroni_member` | fsync p99.9, steal (TTL), stall p99.9, 1-thr, cpu steady | network half unmeasured (§7.3) |
| `redis_sentinel` | stall p99.9, 1-thr, fsync p99.9, steal, cpu steady | replaces `redis_aof`; network half unmeasured |
| `worker_probe` | 1-thr, stall p99.9, cpu steady, tls_handshakes_s, dns_ms, loss_pct, jitter | HEAD/GET/SSL/ICMP/SMTP/SSH/FTP/TCP |
| `playwright_node` | multi-thr, scaling_eff, cpu steady, host.ram_mb, ram bw, stall, 1-thr | the Prague profile (§2.6) |
| `nuxt_ssr` | 1-thr, stall p99.9, steal | unchanged intent |

### 7.1 `redis_sentinel` replaces `redis_aof`

Same local physics. The rename reflects that the deployment being graded is a
Sentinel cluster member, and forces the honest statement in §7.3. The fsync rule
still applies to `appendfsync no` deployments — conservative on purpose, since
durability settings are rarely known at procurement time.

### 7.2 `playwright_node`

Chromium is multi-process and memory-hungry, so this is the one profile where
multi-core and RAM capacity are primary. `cpu.steady_state.degradation_pct` is
the binding metric for the Prague failure mode: a node at a permanently
throttled baseline renders ~3x slower, which shows up as browser-check timeouts
while plain HTTP checks stay green.

### 7.3 Cluster profiles measure the local half and say so

Commit latency in a synchronous Patroni cluster is `local fsync + inter-node
RTT`. p99bench measures the first term only.

`patroni_member` and `redis_sentinel` **must** emit
`network_half_unmeasured: true` and render the line *"network half unmeasured —
needs a peer"* alongside their grade. A grade that silently covers half an
equation is worse than no grade.

This preserves the existing METHODOLOGY position verbatim: inter-node network
"needs two hosts under your control and a much longer observation window than a
benchmark run... Out of scope, and deliberately not approximated." Optional
pairwise measurement was considered and rejected for this spec — it is a large
new subsystem (coordination, clock handling, sustained observation) with its own
doctrine to write.

Note the Prague incident was diagnosed entirely from the local half; network was
not the story (§2.6).

## 8. Output layout

```
RESULTS.md                          index - one row per (provider, region, product, class)
results/<provider>/README.md        generated detail; renders when browsing the dir on GitHub
results/<provider>/<region>/*.json  raw, untouched, immutable
data/index.json  data/index.csv     generated, queryable
```

`RESULTS.md` becomes a compact index:

```
| Provider | Region | Product   | Class     | disk | cpu | ram | net | pg | ts | redis | pw |
|----------|--------|-----------|-----------|------|-----|-----|-----|----|----|-------|----|
| hetzner  | hel-1  | CPX32     | net-fast  | B    | A   | A   | B   | B  | B  | A     | A  |
| ovh      | waw    | vps-1-lz  | net-fast  | B    | D   | C   | C   | C  | C  | D     | D  |
| ovh      | zrh    | vps-1-lz  | net-slow  | D    | A   | A   | C   | D  | D  | D     | A  |
```

The index also carries the flagship number (`fsync p99.9 worst`) so rows stay
sortable — letter grades alone tie constantly at scale.

Per-provider is the right granularity now. If one provider outgrows a single
page, the escape hatch is per-region, with no other change.

### 8.1 Constraint: generated JSON must not live under `results/`

Both `tools/validate.py` and `tools/render.py` discover files with
`rglob("*.json")` over `results/`. A generated `results/index.json` would be
picked up and fail validation. Hence `data/`.

`README.md` files are invisible to that glob, so per-provider pages are safe
inside the tree — and being inside the tree is the point, since GitHub renders
them when browsing `results/<provider>/`.

### 8.2 Existing reporting behaviour to preserve

`render.py`'s median-and-worst-never-a-mean rule, the `MIN_RUNS_FOR_SPREAD = 3`
floor, and the time-variance vs host-variance split all carry forward unchanged.
Grades roll up per product as the **worst across runs**, matching today's
"a machine that passes at 03:00 and fails at 18:00 is a machine that fails".

## 9. Versioning and migration

### 9.1 Measurements are immutable; grades are always current

Keep the existing doctrine: CI recomputes every grade from `thresholds.yaml` at
HEAD and diffs against the stored value. **Historical grades are not stored.**
Reproducing a 2026 grade in 2029 is a `git checkout` of the tag plus a
recompute, because results never change and the rules are versioned.

Storing grade history would create a second source of truth and a way to launder
a stale verdict past CI. Rejected.

Each result stores `grades` and `bands_version`; CI fails if `bands_version`
does not match HEAD. Same trust property, same hand-edit protection as today.

### 9.2 Renamed fields: changed measurement demands a changed name

§5.1 and §5.2 change *what is measured*, not merely how it is scored. Reusing
the old names would put two different metrics in one column — the same
incomparability the project already refuses for network targets
(`list_version`).

| Old | New | Reason |
|---|---|---|
| `ram.seq_read_mbs` | `ram.bw_read_mbs` | working set now exceeds LLC (§5.2) |
| `cpu.intrinsic_latency_max_us` | `cpu.stall_p99_us`, `cpu.stall_p999_us`, `cpu.stall_max_us` | new tool, real percentiles (§5.1) |
| `verdict` | `grades` | new output shape |

Old fields are retained in existing files, unbanded, marked legacy.
`schema_version` goes to `2.0`.

### 9.3 The 10 existing results stay, and most still grade

They are real measurements; deleting them would be the actual data loss.

| Metric group | Status |
|---|---|
| all `disk.*` | valid, fully graded — measurement unchanged |
| `cpu.single_thread_eps`, `multi_thread_eps`, `steal` | valid, fully graded |
| `cpu.scaling_efficiency` | **backfillable without re-running** — inputs already in the files (§5.3) |
| `cpu.stall_*`, `cpu.steady_state`, `cpu.tls_handshakes_s`, `ram.bw_read_mbs` | `?` — needs re-run on tool >= 0.2.0 |

Net effect, per profile:

| Profile | On existing v1 results | Why |
|---|---|---|
| `postgres_oltp` | **fully graded** | every metric it reads exists and is valid |
| `timescale_ingest` | **fully graded** | only `ram.bw_read_mbs` is missing, and it stays `required: false`, so the rollup proceeds (§4.2) |
| `patroni_member` | `?` | `stall_p999`, `cpu.steady_state` required, absent |
| `redis_sentinel` | `?` | `stall_p999` required, absent |
| `worker_probe` | `?` | `stall_p999`, `cpu.steady_state`, `tls_handshakes_s` required, absent |
| `playwright_node` | `?` | `cpu.steady_state` required, absent |
| `nuxt_ssr` | `?` | `stall_p999` required, absent |

So the existing corpus keeps answering the disk-bound database questions it was
built for, and honestly declines the CPU-sustained and stall questions it never
measured.

That is the honest outcome. The alternative — fabricating a Playwright grade
from data that never measured a Playwright workload — is the failure mode this
whole spec exists to correct.

A profile with a missing required metric grades `?` with
`needs re-run (tool >= 0.2.0)`, never a silent pass. This matches today's
`unknown` semantics.

### 9.4 Stale documentation to fix in this pass

`results/README.md` still states *"The files currently here are EXAMPLES...
Delete them before the first real submission"*, but the git history
(`result: first real runs`, `result: ovh waw and ovh zrh from new machines`)
shows they are real submissions. Fix.

## 10. Implementation sequence

Ordered so that metric fixes land before calibration — calibrating a broken
metric is how the current state arose.

**Phase 1 — metric integrity** (`bench/`)
1. Fix `cpu.scaling_efficiency` computation; backfill existing results.
2. Replace `05-latency.sh` with cyclictest; emit `stall_p99/p999/max`. Add
   `rt-tests` to README install line and CI.
3. Fix RAM working set to exceed LLC; emit `ram.bw_read_mbs`.
4. Add `cpu.tls_handshakes_s` via `openssl speed ecdsap256`.
5. New stage `02b-cpu-steady.sh` (15 min); wire into `run-all.sh`.

**Phase 2 — grading engine** (`tools/`, `schema/`)
6. Derive `storage_class` in `tools/`, not `bench/`. It is a *derived* value
   (`1e6 / disk.wal_fsync.iops`), not a measurement, and §4.5/§8 require it for
   the 10 existing results — which were produced by v1 scripts and can never
   emit a new field. Computing it alongside grades covers every result, old and
   new, and keeps derived values out of the raw record (§9.1).
7. `schema/thresholds.yaml` v2: bands, categories, profiles.
8. Rewrite `tools/verdict.py` -> band lookup, worst-wins rollup, binding
   constraint, `?` on missing required.
9. `schema/result.schema.json` v2.0: `grades`, renamed fields, legacy fields.
10. `tools/validate.py`: `bands_version` check, legacy-field tolerance.

**Phase 3 — profiles and calibration**
11. Encode all 7 profiles.
12. Validate every band set against the corpus; assert each metric is declared
    broken or quiet per §4.4.

**Phase 4 — output**
13. `tools/render.py`: index, per-provider pages, `data/index.{json,csv}`.
14. CI: regenerate and diff all generated artifacts.
15. Rewrite `THRESHOLDS.md` (every band + reasoning + confidence),
    `METHODOLOGY.md` (§4.4, §4.5, §6.5 doctrine), `README.md`,
    `results/README.md` (§9.4).

**Phase 5 — recalibration** (after first corpus on tool >= 0.2.0)
16. Replace provisional bands with corpus-checked ones (§11).

## 11. Provisional bands — explicitly Low confidence

These ship marked provisional in `THRESHOLDS.md` and **must** be recalibrated
once real data exists. Writing them as High confidence would repeat the original
sin.

| Metric | Why provisional |
|---|---|
| `cpu.stall_p999_us` | new tool; no corpus |
| `cpu.steady_state.degradation_pct` | new stage; no corpus |
| `cpu.tls_handshakes_s` | new metric; no corpus |
| `ram.bw_read_mbs` | **the LLC fix invalidates all 10 existing RAM numbers** — they measured cache, so they cannot calibrate the fixed metric |

## 12. What this design does not do

- Does not measure inter-node network; cluster profiles say so (§7.3).
- Does not score price (unchanged).
- Does not score clock speed (§5.6).
- Does not grade network throughput in any profile (§6.5).
- Does not grade on a curve (§4.3).
- Does not average within a category (§4.2).
- Does not predict a specific workload. Bands are a filter for unsuitable
  hardware, not a substitute for testing the real application. Unchanged from
  METHODOLOGY.

## 13. Risks

- **Scope.** Four workstreams in one spec (bench stages, grading engine,
  calibration, render pipeline). Phasing in §10 is the mitigation; phases 1–2
  are independently shippable.
- **Provisional bands ship graded.** Four metrics get bands with no corpus
  behind them. §11 is the mitigation; they are labelled, and phase 5 is not
  optional.
- **Every existing grade changes.** Expected and correct — the current ones are
  near-uniformly `fail` and carry no information.
- **New dependency (`rt-tests`).** Grows the install line. Accepted: no
  percentile-capable alternative ships in the current dependency set.
