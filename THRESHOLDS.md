# Thresholds

Every pass/fail number in this project lives in
[`schema/thresholds.yaml`](schema/thresholds.yaml). This document explains where
each one came from and how confident we are in it.

**These numbers are opinions.** Well-reasoned ones, we hope, but opinions. They
are in a versioned file precisely so you can disagree with them in public. If you
have data suggesting a threshold is wrong, open an issue — that is a better
contribution than another result file.

## How verdicts work

Each workload profile has rules. Each rule compares one measured value against a
`pass` and a `marginal` bound:

- meets `pass` → **pass**
- meets `marginal` but not `pass` → **marginal**
- meets neither → **fail**

**The worst rule decides the profile.** One `fail` fails the profile. This is
intentional: these metrics are not compensatory. A machine with excellent
throughput and 10 ms scheduler stalls is not "on average fine" for Redis; it is
unusable for Redis, and averaging would hide that.

A rule marked `required: true` whose value is missing makes the whole profile
`unknown` rather than letting it pass by omission.

## postgres_oltp

Transactional PostgreSQL. Commit latency bounded by fsync, index lookups by
random read, checkpoints by random write.

| Rule | pass | marginal | Reasoning | Confidence |
|---|---|---|---|---|
| `disk.wal_fsync.p999_us` | ≤ 2 ms | ≤ 5 ms | At 5 ms p99.9, one commit in a thousand takes 5 ms. At 1,000 commits/s that is one stalled transaction per second, every second. Past that, connection pools start queueing and the problem compounds. | **High** |
| `disk.wal_fsync.iops` | ≥ 15k | ≥ 5k | Ceiling on single-connection commit rate. Below 5k, a single writer cannot exceed 5k TPS regardless of CPU. | Medium |
| `disk.rand_read_8k.p99_us` | ≤ 1 ms | ≤ 5 ms | 8k = Postgres page size. A query doing 100 index lookups at 5 ms p99 has a meaningful chance of one slow read; that is a 5 ms query that should have been 1 ms. | **High** |
| `disk.rand_read_8k.iops` | ≥ 100k | ≥ 30k | Generational marker: modern NVMe clears 100k easily. Below 30k means throttling or network storage. | Low — advisory |
| `disk.rand_write_8k.iops` | ≥ 50k | ≥ 15k | Checkpoint flush rate. Roughly half of read capability on healthy NVMe. | Low — advisory |
| `cpu.steal_pct_under_load` | ≤ 2% | ≤ 5% | Above 5%, Patroni heartbeats start missing TTLs. This is a correctness threshold, not a performance one. | **High** |
| `disk.steady_state.degradation_pct` | ≤ 10% | ≤ 50% | Past 50%, burst credits have run out and the short tests describe a machine you do not have. | Medium |

**Weakest link:** the `rand_read_8k.iops` and `rand_write_8k.iops` bounds are
generational rather than derived from a workload requirement. They are marked
`required: false` so they cannot alone fail a profile.

## timescale_ingest

High-rate inserts, background compression, continuous aggregate refresh. Adds
sequential throughput and memory bandwidth to the OLTP picture.

| Rule | pass | marginal | Reasoning | Confidence |
|---|---|---|---|---|
| `disk.wal_fsync.p999_us` | ≤ 2 ms | ≤ 5 ms | Same as OLTP: inserts still commit. | **High** |
| `disk.seq_write.bw_mbs` | ≥ 500 | ≥ 200 | Chunk writes and compression output are bulky and sequential. Below 200 MB/s, compression jobs start overlapping the next chunk. | Medium |
| `disk.seq_read.bw_mbs` | ≥ 2000 | ≥ 500 | Continuous aggregate refresh reads whole chunks. | Low — advisory |
| `disk.rand_write_8k.iops` | ≥ 50k | ≥ 15k | Heap updates during ingest. | Low — advisory |
| `ram.seq_read_mbs` | ≥ 15 GB/s | ≥ 8 GB/s | Aggregation is memory-bandwidth bound. DDR4-3200 dual channel is 35–45 GB/s; 8 GB/s indicates single channel or heavy sharing. | Medium |
| `cpu.steal_pct_under_load` | ≤ 2% | ≤ 5% | As above. | **High** |
| `disk.steady_state.degradation_pct` | ≤ 10% | ≤ 50% | Ingest is *definitionally* sustained load. Burst credits are irrelevant to it. | **High** |

## redis_aof

Single-threaded, latency-critical, in-memory, with AOF durability. **Evaluated
without a running Redis.**

| Rule | pass | marginal | Reasoning | Confidence |
|---|---|---|---|---|
| `cpu.intrinsic_latency_max_us` | ≤ 200 µs | ≤ 1 ms | Redis is one thread. A stall is dead time for every connected client, with no other core to absorb it. Good bare metal is under 30 µs; 200 µs is already generous. | **High** |
| `cpu.single_thread_eps` | ≥ 1000 | ≥ 600 | Redis throughput is bounded by one core. sysbench events/sec is a proxy, calibrated against contemporary server silicon. | Medium — proxy metric |
| `disk.wal_fsync.p999_us` | ≤ 2 ms | ≤ 5 ms | `appendfsync always` blocks on the same path as WAL. `everysec` blocks once per second. Same physics. | **High** |
| `cpu.steal_pct_under_load` | ≤ 2% | ≤ 5% | Sentinel elections are as heartbeat-sensitive as Patroni. | **High** |

**Note:** the fsync rule applies even to `appendfsync no` deployments. This is
conservative on purpose — you rarely know at procurement time which durability
setting you will end up needing.

**Weakest link:** `single_thread_eps` is a synthetic prime-computation proxy for
a workload that is mostly pointer chasing and network syscalls. It correlates,
but not tightly. Better suggestions welcome.

## nuxt_ssr

Node/Nuxt server-side rendering. One event loop per worker. Sensitive to stalls
and single-core speed; largely indifferent to disk.

| Rule | pass | marginal | Reasoning | Confidence |
|---|---|---|---|---|
| `cpu.single_thread_eps` | ≥ 1000 | ≥ 600 | SSR is single-threaded per worker. | Medium |
| `cpu.intrinsic_latency_max_us` | ≤ 1 ms | ≤ 5 ms | Event loop blocking. More tolerant than Redis because HTTP clients already expect tens of milliseconds. | Medium |
| `cpu.steal_pct_under_load` | ≤ 5% | ≤ 10% | No consensus protocol to break; degrades gracefully. Advisory. | Medium |
| `cpu.aes_256_gcm_mbs` | ≥ 2000 | ≥ 500 | TLS termination. Missing AES-NI would be remarkable on modern hardware and worth knowing about. | Low — advisory |

## Known gaps

- **No thresholds for object storage, queues, or search.** Contributions welcome.
- **`nuxt_ssr` is the weakest profile.** Web apps tolerate a lot. It is close to
  "is this machine broken?" rather than a meaningful fitness test.
- **Nothing accounts for price.** A machine that fails `postgres_oltp` at €5/mo is
  a different proposition from one that fails at €200/mo. Price is recorded in
  every result but deliberately excluded from verdicts — we do not want to be in
  the business of deciding what a millisecond is worth to you.
- **Thresholds are absolute, not relative to the field.** As hardware improves,
  passing gets easier. Revisiting these annually is the plan.

## Changing a threshold

1. Open an issue with the number, the proposed number, and why.
2. If accepted, change `schema/thresholds.yaml` and this document together.
3. CI recomputes every verdict in `results/` and regenerates `RESULTS.md`.
   **Existing results may flip.** That is the system working: the verdict is a
   function of the data and the rules, and if the rules change, so does the
   output. We do not grandfather old verdicts.
