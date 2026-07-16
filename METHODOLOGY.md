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
"Why network is graded only where the workload is the network" below for the
reasoning, and THRESHOLDS.md for the bands.

## Grading doctrine

Three pieces of reasoning came out of the redesign that turned a single
pass/marginal/fail verdict into A-F grades per category and per profile. They
belong here rather than in THRESHOLDS.md: this document owns *why*, THRESHOLDS.md
owns the numbers.

### Broken vs quiet

A metric that produces the same grade on every published result is one of two
things, and from inside a single corpus the two are indistinguishable — one
grade, every time, either way. The difference is intent, and it has to be
declared, not inferred from the shape of the data:

- **Broken** — unreachable by construction, on every machine, in any plausible
  corpus. Never ship it.
- **Quiet** — reachable in both directions; this particular corpus merely
  happens to be clean. Keep it. Its silence is itself a finding: it says these
  machines genuinely do not exhibit the failure mode, not that the metric
  cannot see one.

This distinction exists because the project got it wrong once. The original
thresholds shipped with three bounds no VM in the corpus could ever pass:
`disk.wal_fsync.iops >= 15,000` against a best-measured 1,588, `rand_read_8k.p99
<= 1 ms` against a measured 2,408 µs, and `intrinsic_latency <= 200 µs` against
a measured 1,642 µs. Every one of the 10 published runs failed all three, which
meant three of the four v1 profiles could never return anything but `fail` —
the verdict column carried no information for most of what it claimed to
grade. Nobody noticed until someone sat down and analysed the corpus rather
than reading verdicts one file at a time.

A threshold that fails every host it will ever see is not a demanding
threshold. It is a broken one, and "the whole corpus fails this" and "the
whole corpus is clean on this" produce the identical symptom, so eyeballing a
result table cannot tell them apart. `tests/test_band_doctrine.py` makes the
distinction mechanical: any metric that yields a single grade across the
corpus must declare `quiet: true` or `provisional: true` in
`schema/thresholds.yaml`, or CI fails. See THRESHOLDS.md's "Quiet metrics"
section for the current list and why each entry there is quiet rather than
broken.

### Storage class is a facet, never a curve

Every result also carries a `storage_class` — `local-nvme`, `net-fast`,
`net-slow`, or `degraded` — derived from measured fsync latency-per-op, never
from what a provider calls the disk. Its job is to *explain* a grade: "this is
network-attached storage, and that is why the tail looks like this." It must
never *soften* one.

A `net-slow` host that fails `postgres_oltp` still fails `postgres_oltp`.
Grading a network-storage VM against its own class — curving the bar to
"acceptable for net-slow" — would excuse exactly what this project exists to
expose: that the provider sold tail latency unfit for a database, regardless of
which mechanism produced it. The class is context for the reader, not a second
chance for the machine.

### Why network is graded only where the workload IS the network

Before this redesign, no verdict read a network field at all, and for most
profiles that stance still holds. "A database host needs 500 Mbit/s" cannot be
derived from any workload requirement — it would be a number chosen to look
authoritative, not one earned by reasoning about what `postgres_oltp` or
`timescale_ingest` actually need from the network. So `postgres_oltp`,
`timescale_ingest`, `patroni_member`, `redis_sentinel`, and `nuxt_ssr` still
read no network field.

That reasoning collapses exactly where the network stops being incidental and
becomes the thing under test. `worker_probe` runs HTTP/SSL/ICMP/SMTP/SSH/FTP/TCP
checks; `playwright_node` loads real pages over a real connection. For both,
packet loss and jitter are not an excuse for a bad result — they are the
failure mode itself.

The bands for `network.loss_pct` follow from that, rather than being chosen to
look plausible. A monitoring check that sends 3 ICMP packets and declares a
target down only on total loss false-alarms at rate p³, where p is the
underlying packet-loss rate. At `p = 10%`, that is a 1-in-1,000 chance per
check; at one check per minute, that is 1.4 false alarms a day — enough to
train an on-call engineer to ignore the pager. The corpus contains exactly this
case: the `ovh/zrh -> hetzner-ash` path measures 10% loss, which is why loss
above 2% grades F for a probe workload. A monitoring tool that cries wolf more
than once an hour is not one anyone would trust, and that is a fact about the
workload, computed from the measurement — not an opinion about the provider.

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