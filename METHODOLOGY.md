# Methodology

This document explains why p99bench measures what it measures. It is the part of
the project with the longest shelf life: hardware and providers change, the
reasoning does not.

## The core claim

**Provider performance marketing describes throughput. Database viability is
decided by tail latency.** These are different things, and a machine can be
excellent at one while being useless at the other.

An example from a real run: a VPS advertising NVMe storage delivered 7,512
random-read IOPS with a p99 of 18 ms. The mean latency was 4 ms — unremarkable.
The p99.9 was 34 ms. That machine will serve a benchmark happily and stall your
transactions in production, and no summary statistic short of a percentile will
tell you.

## The metrics that decide verdicts

### 1. fsync latency at queue depth 1

**The single most important number in this suite.**

When PostgreSQL commits a transaction, it writes to the WAL and calls
`fdatasync()`. It then waits. Not for a queue to drain, not for 32 parallel
operations to complete — for one durable write to one file. Redis with
`appendfsync always` does the same thing per write; with `everysec` it does it
once per second while blocking.

This is why `--iodepth=32` benchmarks are misleading for databases. Queue depth
lets a device pipeline work and produces impressive IOPS numbers. A commit
cannot use queue depth. It is one operation, alone, and its latency is the floor
on your commit rate.

```
fio --name=wal-sync --direct=1 --sync=1 --ioengine=psync \
    --rw=write --bs=8k --iodepth=1 --numjobs=1 --fdatasync=1
```

`psync` rather than `libaio` because that is what Postgres actually does.
`fdatasync=1` after every write. `iodepth=1` because there is nothing to
pipeline. This is the test where consumer SSDs with volatile write caches, and
network-attached storage with a round trip in the path, stop looking like
enterprise hardware.

We report p99.9 rather than mean because the mean tells you about a transaction
nobody complains about.

### 2. Scheduler stall worst case

Redis is single-threaded. So is each Node worker's event loop. When the
hypervisor takes the CPU away for 10 ms, every client of that process waits
10 ms. There is no other core to pick up the work, because there is only one
thread doing the work.

`redis-cli --intrinsic-latency N` runs a tight loop and records the longest gap
between iterations. It needs no Redis server — it opens no socket. On good bare
metal the worst case over 60 seconds is under 30 µs. On an oversubscribed
hypervisor we have measured 10.7 ms: roughly 200,000× the average iteration time.

This metric is nearly free to collect and is the best cheap signal that a host is
oversold. It is also invisible to every throughput benchmark, because throughput
benchmarks average it away.

### 3. Steal time under load

`%steal` is the fraction of time a vCPU was runnable but the hypervisor scheduled
someone else. It must be measured **under full load** — an idle VM steals
nothing, which is why casual checks miss it.

For a Patroni cluster this is not a performance metric but a correctness one.
Steal time delays the leader's heartbeats. Delayed heartbeats past the TTL
trigger a failover. A failover that happens because the host was busy, not
because anything was wrong, is strictly worse than the contention it came from.

### 4. Sustained load over 30 minutes

Most cloud block storage grants a burst credit budget: full performance for some
tens of seconds, then throttling to a baseline that can be an order of magnitude
lower. This is documented behaviour on major clouds, not a trick, but it does
mean that **every short benchmark passes**.

A 60-second fio run measures the credit balance. A 30-minute run measures the
machine you will actually be running. We report the drop from the first minute to
the last; anything past 50% means the numbers in the short tests describe a
machine that does not exist for you.

This is the most expensive test in the suite and the least skippable. Results
submitted without it are rejected.

### 5. Single-core throughput

Redis, each Node worker, Anubis's proof-of-work check, and PostgreSQL's
per-connection backend are all bounded by one core. Multi-core scores are close
to irrelevant for them.

We also report scaling efficiency (`multi / (single × cores)`). Around 0.9 means
physical cores. Around 0.6 means SMT siblings sold as cores. Much below that
means you are sharing physical cores with other tenants.

### 6. Uplink to fixed reference targets (informational)

Speedtest tools pick the closest, best-connected server available. That is the
right design for "is my connection working" and the wrong one here: it measures
a different path for every host, so the resulting numbers cannot go in a shared
table. A Hetzner box measuring against a Nuremberg server and an OVH box
measuring against a Zurich server are not comparable, however similar the two
numbers look side by side.

So p99bench measures every host against the *same* targets. Distance becomes a
known constant instead of a hidden variable, and a low number means something
specific: this provider's peering toward that destination is poor. That is a
property of the provider, which is the thing under study.

Ookla is recorded too when `--with-ookla` is passed, as context rather than as a
measurement -- it is the number people recognise, and it is a useful sanity
check on the link itself. It is never required, partly because it is not
comparable and partly because Ookla's CLI is licensed for personal,
non-commercial use, and this project explicitly invites submissions from
providers.

`worker_probe` and `playwright_node` grade `network.loss_pct` and
`network.rtt_jitter_ratio`; every other profile reads no network field. See
THRESHOLDS.md for why.

## Measurement decisions

### `--direct=1` is not negotiable

Without `O_DIRECT`, fio measures the page cache. Every provider looks identical
and excellent, because you are benchmarking RAM. Every fio invocation here uses
`--direct=1`.

### Working set larger than RAM

Even with `O_DIRECT`, a file small enough to sit in the storage backend's own
cache produces optimistic numbers. Default footprint is 4 GB per job × 4 jobs =
16 GB, and `01-disk.sh` warns when that is under 2× system RAM.

### 8k block size

PostgreSQL's page size. Not 4k, not 1M. Benchmarks that report 4k random IOPS are
measuring something adjacent to, but not the same as, what a database does.

### Data files removed between phases

Each fio phase cleans up before the next starts. Otherwise the fifth test runs on
a nearly full filesystem, which on SSDs means degraded garbage collection and a
measurement of free space rather than hardware.

### Load check before starting

`run-all.sh` refuses to start on a machine with a load average over 0.5. A
benchmark run alongside a workload measures the workload.

## What this benchmark cannot tell you

Being explicit about this matters more than the results.

**It cannot tell you about a provider.** One run measures one instance, on one
host, with one set of neighbours, at one hour. Providers differ across regions,
across hardware generations within a region, and across racks. Aggregating fewer
than three runs is anecdote with a mean attached, and `tools/render.py` refuses
to do it.

This is also why results are keyed by `host_id` and reported as median plus worst
case rather than as an average. There are two distinct sources of variance and
they support different conclusions:

- **Time variance** (same `host_id`, different hours) is about neighbours. If a
  machine swings 8x between 03:00 and 18:00, the hardware is fine and the host is
  oversold.
- **Host variance** (different `host_id`, same product and region) is about the
  provider. If two VMs of the same product differ 7x, the fleet is not uniform
  and which machine you get is luck.

A mean over both says "roughly 3 ms" and tells you nothing about either. Worst
case is the honest summary, because the tail is what your users experience.

**It cannot tell you about ECC.** On a VM, `dmidecode` reports what the
hypervisor's SMBIOS table claims. That claim is not evidence. The only in-guest
proof is EDAC exposing live error counters, which essentially no VM does — which
is why we record `ecc_verifiable` separately from `ecc_claimed`. If ECC matters
to you, get it in a contract, not from this tool.

**It cannot tell you about inter-node network.** `06-network.sh` measures the
uplink from one host to fixed public targets. It says nothing about the RTT
between two of your nodes, which is what actually bounds commit latency in a
synchronous Patroni cluster or decides whether Redis Sentinel elections are
stable. That needs two hosts under your control and a much longer observation
window than a benchmark run -- a single 100-second ping cannot see a BGP flap or
an evening congestion pattern. Out of scope, and deliberately not approximated.

**It cannot predict your workload.** The profiles in `thresholds.yaml` are
informed generalisations. A read-mostly Postgres with a working set that fits in
RAM will be happy on hardware that fails `postgres_oltp`, because it will rarely
touch the disk. The verdicts are a filter for obviously unsuitable hardware, not
a substitute for testing your actual application.

**Time of day matters and one run cannot see it.** Noisy neighbours have
schedules. This is why `run.local_hour` is a required field and why we ask for
three runs across different times.

## Why the verdict is code

Verdicts are computed by `tools/grade.py` from `schema/thresholds.yaml`. They
are never written by hand, and CI rejects any result file whose stored verdict
does not match what the thresholds compute.

This is deliberate. A project that publishes provider comparisons has an obvious
temptation to nudge them, and the only real defence is to make nudging visible:
the rule is in a versioned file, the computation is reproducible, and changing a
verdict requires changing a threshold in a PR where everyone can see it.

It also relocates disagreement somewhere productive. "OVH's B2-7 shouldn't fail"
is unfalsifiable. "5 ms is the wrong p99.9 ceiling for postgres_oltp, here is
why" is a conversation with an answer.