# p99bench

**Your provider sells you averages. Your database dies at p99.9.**

A benchmark suite for deciding whether a server is fit to run a database,
a cache, or a latency-sensitive application — *before* you deploy anything on it.

It measures the numbers that actually decide that question, which are almost
never the numbers in the marketing material:

| What providers advertise | What p99bench measures | Why |
|---|---|---|
| "up to 200,000 IOPS" | fsync latency at QD1 | Every `COMMIT` waits on one `fdatasync`. Queue depth cannot hide it. |
| "NVMe SSD" | p99.9 latency, not mean | The mean is fine on almost every host. Your tail transactions are not. |
| "4 vCPU" | steal time under full load | A vCPU you do not get is not a vCPU. |
| 60-second benchmark results | 30-minute sustained load | Burst credits cover the first minute. Your database runs longer than that. |
| "dedicated resources" | scheduler stall worst case | Redis and Node are single-threaded. A 10 ms stall is 10 ms of frozen service. |
| "1 Gbit/s uplink" | throughput to *fixed* targets | A nearest-server speedtest measures a different path per host. Same targets everywhere, or the numbers cannot share a table. |

## Quickstart

```bash
# Debian 13 / Ubuntu 24.04
apt update && apt install -y fio sysbench stress-ng rt-tests smartmontools dmidecode \
  numactl redis-tools jq bc sysstat curl iputils-ping python3-yaml

git clone https://github.com/Uptimeify/p99bench && cd p99bench/bench

sudo ./run-all.sh \
  --provider hetzner --product CPX41 --region fsn1 \
  --price 29.90 --billing monthly --submitter yourhandle
```

Takes about 60 minutes: 30 for the sustained disk test, 15 for the sustained CPU
test, ~15 for everything else. Needs ~20 GB free and a machine with nothing else
running on it.

To benchmark a dedicated data volume instead of the boot disk:

```bash
sudo ./run-all.sh --target /mnt/data --provider ...
```

The network stage downloads ~400 MB from fixed reference targets. If the host has
no egress, that is recorded as `reachable: false` and the result stays valid — a
firewalled host is a real configuration, not a failed measurement. `--skip-network`
skips it entirely; `--with-ookla` adds an Ookla run as context (needs the
`speedtest` CLI, which is licensed for personal, non-commercial use).

## What you get

A single JSON file plus a verdict per workload profile:

```
postgres_oltp:    fail
timescale_ingest: fail
redis_aof:        fail
nuxt_ssr:         marginal

reasons:
  - [postgres_oltp] disk.wal_fsync.p999_us = 8200 > 5000
  - [redis_aof] cpu.intrinsic_latency_max_us = 10707 > 1000
  - [postgres_oltp] disk.steady_state.degradation_pct = 58.7 > 50
```

The verdict is not an opinion. It is a pure function of the measured numbers and
[`schema/thresholds.yaml`](schema/thresholds.yaml), which is public and versioned.
CI recomputes it and rejects any file where a verdict was hand-edited. If you
think a verdict is wrong, the thing to argue about is the threshold.

## Results

See **[RESULTS.md](RESULTS.md)** — generated from [`results/`](results/), never
edited by hand.

Results are laid out as `results/<provider>/<region>/`, and every run carries an
anonymous `host_id` so runs on the same VM link together. That lets `RESULTS.md`
separate two findings that look identical if you average them:

- **same machine, different hours** → noisy neighbours
- **different machines, same product** → the provider's fleet is not uniform

Both get reported with median **and** worst case, never a mean. A machine that is
fine at 03:00 and unusable at 18:00 should read as exactly that.

One run is still a data point about one VM at one hour, not a statement about the
provider. Three runs at different hours is the bar before anything gets a spread.

## No Redis or Postgres needed

Every metric that drives a verdict is measured on a bare machine. This is
deliberate: a benchmark that requires Redis to be installed cannot tell you
whether to install Redis.

Redis performance decomposes into single-core speed, scheduler stall behaviour
and AOF fsync latency — all three measurable without a server running.
Scheduler stalls come from `cyclictest` (ships in `rt-tests`), which opens no
socket and needs no Redis. It emits a latency histogram, run at normal
scheduling priority so it experiences the same contention Redis and Node
would, and we read the 99.9th percentile off it: `p99.9` is what a
single-threaded process actually feels, and unlike a running max it converges
instead of drifting upward the longer the test runs.

`04-app-optional.sh` runs `pgbench` and `redis-benchmark` if a service happens to
be reachable, but that is for validating the thresholds themselves, not for
procurement.

## Scripts

| Script | Measures | Time |
|---|---|---|
| `00-inventory.sh` | CPU, RAM, disk, virtualisation, ECC observability | seconds |
| `01-disk.sh` | seq/random throughput, **fsync p99.9** | ~7 min |
| `01b-steady.sh` | 30 min sustained load → burst credit exhaustion | 30 min |
| `02-cpu.sh` | single/multi core, clock under load, **steal time** | ~3 min |
| `02b-cpu-steady.sh` | 15 min sustained CPU → **burst credit exhaustion** | 15 min |
| `03-ram.sh` | bandwidth, 8k random, NUMA locality | ~3 min |
| `05-latency.sh` | **scheduler stall percentiles** (no Redis needed) | 1 min |
| `06-network.sh` | uplink + RTT to fixed targets (informational) | ~2 min |
| `04-app-optional.sh` | pgbench / redis-benchmark, only if already running | varies |

## Docs

- **[METHODOLOGY.md](METHODOLOGY.md)** — why these metrics, why `--direct=1`,
  why 30 minutes, and what this benchmark cannot tell you
- **[THRESHOLDS.md](THRESHOLDS.md)** — where every pass/fail number comes from
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — how to submit a result

## Contributing results

Yes, please — including results that contradict ours. Providers are explicitly
welcome to submit runs from their own hardware; that is a feature, not a
conflict. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Prior art

[YABS](https://github.com/masonr/yet-another-bench-script) and
[Geekbench](https://www.geekbench.com/) are good at what they do and this is not
a replacement. They answer "how fast is this machine?" p99bench answers a
narrower question: "will this machine's tail latency ruin my database?" That
turns out to need different measurements.

## License

Apache-2.0. See [LICENSE](LICENSE).

---

Started at [Uptimeify](https://uptimeify.io) because we needed it for our own
provider selection and could not find it. Sponsorship does not buy a verdict —
the thresholds are in a file you can read, and so is our own hardware's row in
the results table.