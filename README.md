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
| 60-second benchmark results | 30-minute sustained load | An AWS gp2 volume bursts at full speed for ~33 minutes. Every short benchmark passes; your database runs longer than that. |
| "dedicated resources" | scheduler stall p99.9 | Redis and Node are single-threaded. A 10 ms stall is 10 ms of frozen service. A *max* only grows with runtime, so it measures "is this a VM?"; a percentile converges. |
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

A single JSON result file, plus a grade — **A through F, or `?` for not-yet-
measured** — per capability category (`disk`, `cpu`, `ram`, `network`) and per
workload profile (`postgres_oltp`, `timescale_ingest`, `patroni_member`,
`redis_sentinel`, `worker_probe`, `playwright_node`, `nuxt_ssr`). This is real
output, not a mock-up — `python3 tools/grade.py
tests/fixtures/corpus/ovh/zrh/2026-07-16T1024-vps-1-lz-2026.json` against a
real published run (the corpus behind this example now lives in
`tests/fixtures/corpus/` as calibration evidence; `results/` itself is clean
until the next submission — see [CONTRIBUTING.md](CONTRIBUTING.md)):

```json
{
  "bands_version": "2.0",
  "storage_class": "net-slow",
  "categories": {
    "disk": {
      "grade": "F",
      "bound_by": "disk.wal_fsync.p999_us",
      "metrics": {
        "disk.wal_fsync.p999_us": { "value": 137363.46, "grade": "F" },
        "disk.wal_fsync.iops": { "value": 179.79, "grade": "D" },
        "disk.rand_read_8k.p99_us": { "value": 18219.01, "grade": "F" },
        "disk.rand_read_8k.iops": { "value": 7512.39, "grade": "D" },
        "disk.rand_write_8k.iops": { "value": 5935.87, "grade": "D" },
        "disk.seq_write.bw_mbs": { "value": 300.47, "grade": "C" },
        "disk.seq_read.bw_mbs": { "value": 300.49, "grade": "D" },
        "disk.steady_state.degradation_pct": { "value": 0.38, "grade": "A" }
      }
    },
    "cpu": {
      "grade": "?",
      "bound_by": "cpu.stall_p999_us",
      "metrics": {
        "cpu.single_thread_eps": { "value": 1615.96, "grade": "A" },
        "cpu.scaling_efficiency": { "value": 0.97, "grade": "A" },
        "cpu.steal_pct_under_load": { "value": 0.12, "grade": "A" },
        "cpu.stall_p999_us": { "value": null, "grade": "?" },
        "cpu.steady_state.degradation_pct": { "value": null, "grade": "?" },
        "cpu.tls_verify_s": { "value": null, "grade": "?" }
      }
    },
    "ram": {
      "grade": "?",
      "bound_by": "ram.bw_read_mbs",
      "metrics": { "ram.bw_read_mbs": { "value": null, "grade": "?" } }
    },
    "network": {
      "grade": "A",
      "bound_by": "network.loss_pct",
      "metrics": {
        "network.loss_pct": { "value": 0, "grade": "A" },
        "network.rtt_jitter_ratio": { "value": 1.0277777777777777, "grade": "A" }
      }
    }
  },
  "profiles": {
    "postgres_oltp": { "grade": "F", "bound_by": "disk.wal_fsync.p999_us" },
    "timescale_ingest": { "grade": "F", "bound_by": "disk.wal_fsync.p999_us" },
    "patroni_member": { "grade": "F", "bound_by": "disk.wal_fsync.p999_us", "network_half_unmeasured": true },
    "redis_sentinel": { "grade": "F", "bound_by": "disk.wal_fsync.p999_us", "network_half_unmeasured": true },
    "worker_probe": { "grade": "?", "bound_by": "cpu.stall_p999_us", "reason": "needs re-run (tool >= 0.2.0)" },
    "playwright_node": { "grade": "?", "bound_by": "cpu.steady_state.degradation_pct", "reason": "needs re-run (tool >= 0.2.0)" },
    "nuxt_ssr": { "grade": "?", "bound_by": "cpu.stall_p999_us", "reason": "needs re-run (tool >= 0.2.0)" }
  }
}
```

`postgres_oltp`, `timescale_ingest`, `patroni_member` and `redis_sentinel` all
grade F here, bound by the same 137 ms fsync p99.9 — that is what `net-slow`
storage does under a durability-sensitive workload. `worker_probe`,
`playwright_node` and `nuxt_ssr` grade `?`, not a pass: this run predates the
tool version that measures scheduler stall, so there is nothing to grade yet.

The grade is not an opinion. It is a pure function of the measured numbers and
[`schema/thresholds.yaml`](schema/thresholds.yaml), which is public and
versioned: every band from A to F lives in that file, and a category's or a
profile's grade is the *worst* band among its metrics, never an average — a
superb sequential write speed does not buy back a 137 ms fsync tail. CI
recomputes every grade and rejects any file where a grade was hand-edited. If
you think a grade is wrong, the thing to argue about is the band.

## Results

Everything below is generated from [`results/`](results/) by
[`tools/render.py`](tools/render.py) and never edited by hand — CI runs
`render.py --check` and rejects a stale or hand-edited artifact:

- **[RESULTS.md](RESULTS.md)** — the index: one row per product and region.
- **`results/<provider>/README.md`** — the detail page for that provider:
  every product/region, every metric, every run. One appears per provider once
  a result for it is submitted (`results/` is clean right now, pending the
  first result on tool >= 0.2.0 — see [CONTRIBUTING.md](CONTRIBUTING.md)). For
  a worked example of the shape, see the retained tool-0.1.0 corpus at
  `tests/fixtures/corpus/ovh/README.md`.
- **[data/index.json](data/index.json)** / `data/index.csv` — the
  machine-readable export, for anyone building something on top of this data.

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

Every metric that drives a grade is measured on a bare machine. This is
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
- **[THRESHOLDS.md](THRESHOLDS.md)** — where every A–F band comes from, with a
  confidence level on each, and which four are provisional
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
provider selection and could not find it. Sponsorship does not buy a grade —
the bands are in a file you can read, the grade is a pure function of them, and
our own hardware gets graded by the same bands as everyone else's.