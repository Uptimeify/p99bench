# p99bench — session handoff

Written 2026-07-17, updated end of the 2026-07-17 evening session (tool 0.2.1,
`HEAD = 2669e2c`). Read this before touching anything; it will save you the
mistakes already made once.

## Where things are

- Branch `main`, everything pushed to `origin/main`. Working tree clean.
- **163 non-docker tests pass**: `python3 -m pytest tests/ -q -m "not docker"`
- Docker-marked tests need a container: `docker build -t p99bench-test tests/`
- **CI is green on `HEAD` (`2669e2c`)** — always confirm with
  `gh run watch $(gh run list --limit 1 --json databaseId -q '.[0].databaseId')
  --exit-status` before claiming done. This session claimed "ready" twice while
  CI was red; `gh` works in the sandbox even when `curl` does not.
- **shellcheck has no local binary here.** Run it the way CI does:
  `docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable -e SC1091
  bench/*.sh`. A bash change that passes locally can still fail the CI lint.
- `python3 tools/validate.py results/` → 6/6 valid (was 3/3; the fleet grew)
- `python3 tools/render.py --check` → exit 0 (CI runs this; generated files are
  never hand-edited)
- **No `Co-Authored-By` trailers anywhere in history, by owner's explicit
  instruction.** Strip before committing, not after — subagents re-add it by
  default. Check with `git log --grep='Co-Authored-By'` before every push.
- `CLAUDE.md` is gitignored (owner's choice). It is edited on disk but never
  committed. Leave that alone.

## Current corpus (tool 0.2.1, one run per host, 2026-07-17)

Six hosts, four providers. **One run each — not yet a calibrated corpus.**
`MIN_RUNS_FOR_SPREAD = 3`, so no host has a defensible spread yet, and grades
near a band edge can flip between runs (ovh/zrh RAM already swung 90→114 GB/s).

| host | disk | cpu | ram |
|---|---|---|---|
| hetzner/fsn-1 | C | F | B |
| hetzner/hel-1 | C | B | A |
| ovh/prg | D | B | A |
| ovh/waw | C | F | C |
| ovh/zrh | D | B | A |
| windcloud/enge-sande | F | F | B |

The grades discriminate hard (windcloud disk F to hetzner-hel1 RAM A), which is
the suite working. `cpu=F` on three hosts and `disk=D/F` on the OVH pair are
real findings, not gaps — every one is `incomplete: false`.

## What this project is

p99bench answers one narrow question: **will this server's tail latency ruin my
database?** It publishes provider comparisons and invites providers to submit
runs from their own hardware, so its entire value is being trustworthy about
hardware. `bench/` is pure bash (runs on a fresh host with no Python); `tools/`
is Python and only needed to grade, validate and render.

## What was built (three phases, all shipped)

The design contract is `docs/superpowers/specs/2026-07-16-p99bench-graded-categories-design.md`.
Read §4 (framework), §5 (metric integrity), §6 (bands), §7 (profiles), §9
(versioning). The three plans are in `docs/superpowers/plans/`.

**The problem it solved:** the suite emitted one word per workload
(`pass`/`marginal`/`fail`), and that word was `fail` on 10 of 10 published runs
— because three thresholds were unreachable by *any* VM (fsync IOPS ≥ 15,000 vs
a best-measured 1,588; rand-read p99 ≤ 1 ms vs 2,408 µs; intrinsic latency
≤ 200 µs vs 1,642 µs). The verdict column carried zero information.

**Phase 1 — metric integrity.** Four metrics measured the wrong thing:
- `intrinsic_latency_max_us` was an extreme-value statistic (worst-of-60M
  samples) that only grows with runtime → replaced with `cyclictest` histogram
  percentiles (`cpu.stall_p999_us`).
- `ram.seq_read_mbs` measured L2 cache (207 GB/s on single-channel DDR5) →
  replaced by `ram.bw_read_mbs` with an LLC-exceeding working set.
- `cpu.scaling_efficiency` was `null` everywhere: `bc` prints `.977`, and
  `jnum`'s regex demanded a leading digit.
- `aes_256_gcm_mbs` cleared its bar 18x on every machine → demoted, ungraded.
Plus a new 15-min CPU sustained stage (`02b-cpu-steady.sh`).

**Phase 2 — grading engine.** A–F bands per **capability category** (disk, cpu,
ram, network) and per **workload profile** (postgres_oltp, timescale_ingest,
patroni_member, redis_sentinel, worker_probe, playwright_node, nuxt_ssr).
Worst-wins, never averaged. `tools/verdict.py` → `tools/grade.py`.

**Phase 3 — output at scale.** `RESULTS.md` is a compact index; detail lives on
generated `results/<provider>/README.md`; `data/index.{json,csv}` is the export.
`render.py --check` is the CI gate.

## Doctrine — violate these and you have broken the project

- **Grades are a pure function of measured numbers + `schema/thresholds.yaml`.**
  CI recomputes and rejects hand-edits. This is a trust property, not a lint.
- **Worst-wins, never averaged.** A category with a 459 ms fsync and superb
  sequential throughput is not "roughly fine".
- **Median AND worst, never a mean.** Two variance types — same `host_id`
  different hours (noisy neighbours) vs different `host_id` same product (fleet
  spread) — answer different questions. A mean over both answers neither.
  `MIN_RUNS_FOR_SPREAD = 3`.
- **No curve.** A grade never depends on who else submitted.
- **Rollup precedence:** grade = worst *measured* band; `?` only when nothing
  was measured; `incomplete: true` + `missing: [...]` when a required metric is
  absent. The worst measured band is a **lower bound** — a missing metric can
  only drag it down. `?` must never be a hiding place.
- **Recording `null` loses one metric; passing garbage loses the whole run.**
  Never widen a parser to "just accept it".
- **A metric no machine can pass is not a metric.** §4.4's broken-vs-quiet
  doctrine is enforced mechanically by `tests/test_band_doctrine.py`.
- **Changed measurement demands a changed field name** (§9.2).
- **Price is recorded, never graded.**
- **Network is graded only where the workload IS the network** (§6.5) —
  `worker_probe` and `playwright_node` only.
- **Tests must never read a live file under `results/`.** Those are transient;
  use `tests/fixtures/`. This already broke CI once.

## The pattern that keeps biting — read this twice

**Every serious bug was invisible until real hardware or CI ran it.** Twelve so
far, all the same shape: something worked locally because the dev environment
already had what the target lacked, or because `results/` only ever held files
from the *previous* tool version.

1. Phase 1 shipped a schema with `additionalProperties: false` that rejected
   every field its own stages had just started emitting. `validate.py results/`
   passed, because `results/` held only v1 files.
2. Phase 2 deleted `tools/verdict.py` and left `bench/run-all.sh` calling it —
   guarded by `[[ -f ... ]]`, so it failed *silently* and produced no grades.
3. `run-all.sh` hardcoded `schema_version: "1.0"` after the schema moved to
   `const: "2.0"`. Every fresh run would have been rejected by our own validator.
4. The steal parser read `%soft`, not `%steal` (locale-dependent off-by-one).
5. A German-locale host (`LANG=de_DE.UTF-8`) printed `Durchschn.:` and decimal
   commas (`0,13`), silently nulling metrics. Fixed with `export LC_ALL=C` in
   `lib.sh`.
6. `llc_bytes()` read sysfs cache info that **does not exist in cloud guests** —
   so the LLC-aware RAM sizing never actually ran anywhere it mattered.

New this session — same pattern, and the reason the sizing math now lives in
`lib.sh` with CI-run unit tests instead of inline in the stages:

7. **sysbench requires a power-of-two `--memory-block-size`** and FATALs
   otherwise, printing no `MiB/sec` line. The old `RAM/4/CORES` cap produced
   arbitrary byte counts (508355840 on hetzner), so RAM nulled on every host
   that hit the cap. `2>/dev/null` on the sysbench call hid the FATAL for three
   full re-runs. Fixed: `ram_block_bytes()` in `lib.sh`, power-of-two by
   construction, 9 unit tests CI runs. The stages' own tests are all
   `@pytest.mark.docker` and **CI runs `-m "not docker"`** — the arithmetic had
   no gate CI executed. That is the through-line for bugs 7–11.
8. **`rand_read_8k.p99_us` was graded at QD128** (`--iodepth=32 --numjobs=4`)
   but banded on a QD1 "single index lookup" rationale. Little's law makes it
   ~`128/IOPS` — median ratio 1.08 across 13 runs, i.e. it just restated
   `rand_read_8k.iops`, and worst-wins took the harsher of the two. Replaced by
   `disk.rand_read_8k_qd1.p99_us` (new psync/iodepth=1 fio job); QD128 p99 kept,
   ungraded.
9. **The NUMA locality test measured L1.** It called sysbench with no block size
   and no oper → defaults (1K block, writes) → cache on both sides of the hop.
   windcloud published *remote faster than local* twice, which is impossible.
   Then the first fix used `nproc` threads while `--cpunodebind=0` confines the
   run to one node's CPUs — 4 threads on 2 cores, CPU-bound, gap still invisible.
   Only after threads came from `numa_node_cpus(0)` did the real 18% hop appear.
10. **`dns_ms` was one uncached lookup**, so worst-of-four made it
    authoritative-NS distance, not the host — ungradeable. Split into
    `dns_warm_p50_ms` (median of 5 cached, a host property) and `dns_first_ms`.
11. **`llc_bytes()`'s lscpu fallback was never tested and was
    environment-dependent.** Three fallback tests asserted the 32M last resort,
    which only fires when lscpu *also* yields nothing — they passed on macOS (no
    lscpu) and on a runner whose lscpu didn't parse, then went red on a runner
    whose `lscpu -B` returned a clean 260 MiB L3. A runner swap flipped three
    green tests red with no code change. Fixed with a `P99_LSCPU_OUT` seam and a
    `>= 1 MiB` sanity guard (a bare "256" from "256 MiB (8 instances)" was being
    taken as 256 *bytes*).
12. **Three `bw_mbs` bands said MB/s but compared MiB/s** (fio ÷1024, sysbench
    prints MiB/s). ~4.9% stricter than documented; zero grade flips across 13
    runs, so relabelled (not renamed — spec 9.2 reserves a rename for a changed
    measurement).

**Guard:** `tests/test_fresh_run_validates.py` pins the `bench/` ↔ `tools/`
contract (schema_version, `grade.py` invocation, steady durations). When you
change one side, check the other. `bench/run-all.sh` preflights every tool
before spending 60 minutes (`preflight_tools`). **The load-bearing lesson: any
arithmetic in `bench/` that only container tests cover is untested by CI. Push
it into `lib.sh` with a fixture-driven unit test (`P99_CACHE_ROOT`,
`P99_NUMA_HW`, `P99_LSCPU_OUT` are the injection-seam pattern), and add a CI
smoke assertion on the emitted field** (the disk and network smoke jobs now
assert `rand_read_8k_qd1.p99_us` and `dns_warm_p50_ms`).

## Open work, roughly in priority order

1. **Phase 5 — recalibrate the SIX provisional bands. NOW UNBLOCKED.** These
   ship `provisional: true` with a reasoned-not-calibrated band:
   `ram.bw_read_mbs`, `disk.rand_read_8k_qd1.p99_us`, `cpu.stall_p999_us`,
   `cpu.steady_state.degradation_pct`, `cpu.tls_verify_s`,
   `network.dns_warm_p50_ms`. Spec §11. The blocker is gone: a full **tool
   0.2.1 corpus now exists** (6 hosts, all metrics populated). What is NOT yet
   done: only **one run per host**, and `MIN_RUNS_FOR_SPREAD = 3`. Get 3+ runs
   per host at different hours *before* recalibrating, or you calibrate against
   single-sample noise (ovh/zrh RAM already moved 90→114 GB/s run-to-run).
   Live signal so far: RAM spans 24.8k–114k MiB/s, qd1 read 238–3817 µs — both
   discriminate cleanly.
2. **`network.dns_warm_p50_ms` — decide whether `worker_probe` grades it.** The
   measurement is now honest (bug 10), the band is parked and provisional, and
   no profile reads it. A monitoring probe's DNS *is* part of its workload, so
   it is the one profile with a workload-derived reason to grade it — but only
   after 0.2.1 runs show the band discriminates. A spec §11 call, not a guess.
3. **Spec §9.3 is narratively stale.** It describes v1 results grading `?` for
   five profiles; after the rollup fix they grade real letters with
   `incomplete: true`. Behaviour is better; the doc hasn't caught up.
4. **`results/README.md`, `CONTRIBUTING.md`** may need a pass now the 0.2.1
   corpus has landed and the field set changed (new `rand_read_8k_qd1`,
   `dns_warm_p50_ms`/`dns_first_ms`, `numa_*_read_mbs`).

Resolved this session (was open work): `network.dns_ms` ungradeability (bug 10),
the 100 GB/s RAM warning (raised to 150 in `2669e2c` — ovh/zrh reads a genuine
114 GB/s on 12-channel DDR5, and a cache-resident run lands near the ~200 GB/s
legacy number, so 150 separates real from leak).

## Re-run status

**Done.** The whole fleet was re-run on tool 0.2.1 at the end of this session
(the 6 files in `results/`, all `2026-07-17T152x`). Every fix above is baked in;
there is no pending "these need a re-run" debt. The next re-run is only to build
the ≥3-runs-per-host spread that Phase 5 calibration wants (open work #1) — same
hosts, different hours, no code change required.

## How to run

```bash
# on the host, as root
apt install -y fio sysbench stress-ng rt-tests smartmontools dmidecode \
  numactl redis-tools jq bc sysstat curl iputils-ping python3-yaml
cd ~/p99bench && git pull
sudo ./bench/run-all.sh --provider ovh --product vps-1-lz-2026 --region zrh \
  --price 7.49 --billing monthly --submitter pascha
# ~60 min: 30 disk steady + 15 cpu steady + ~15 rest

# on the workstation
scp root@HOST:'~/p99bench/bench/results-local/*.json' results/<provider>/<region>/
python3 tools/validate.py results/ && python3 tools/render.py
```

Do **not** pass `--skip-steady` or `--skip-cpu-steady`; validation rejects the
first and the second costs five profiles. `P99_STEADY_DURATION` exists for local
iteration but `validate.py` now rejects a non-standard duration — a 100 GiB AWS
gp2 bursts for ~33 min, so a 10-min run reports "no throttling" about a disk that
throttles.

## Working agreements from the last session

- Subagent-driven development with a review after every task, and a whole-branch
  review at the end. The whole-branch reviews caught things every per-task review
  missed — the seams are where the bugs live.
- Reviewers found real defects in the author's own plans repeatedly. Do not
  assume the plan is right because it is detailed.
- Verify claims against real data before stating them. Several "obvious"
  diagnoses this session were wrong (the mpstat locale reproduction, a
  `reduce_network` check that passed the wrong argument type).
