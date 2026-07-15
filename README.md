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

## Quickstart

```bash
# Debian 13 / Ubuntu 24.04
apt update && apt install -y fio sysbench stress-ng smartmontools dmidecode \
  numactl redis-tools jq bc sysstat python3-yaml

git clone https://github.com/Uptimeify/p99bench && cd p99bench/bench

sudo ./run-all.sh \
  --provider hetzner --product CPX41 --region fsn1 \
  --price 29.90 --billing monthly --submitter yourhandle
```

Takes about 45 minutes, 30 of which are the sustained load test. Needs ~20 GB
free and a machine with nothing else running on it.

To benchmark a dedicated data volume instead of the boot disk:

```bash
sudo ./run-all.sh --target /mnt/data --provider ... 
```

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
`redis-cli --intrinsic-latency` ships in `redis-tools`, opens no socket, and is
the cheapest good detector of an oversubscribed hypervisor there is.

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
| `03-ram.sh` | bandwidth, 8k random, NUMA locality | ~3 min |
| `05-latency.sh` | **scheduler stalls** (no Redis needed) | 1 min |
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