# p99bench — session handoff

Written 2026-07-17. Read this before touching anything; it will save you the
mistakes already made once.

## Where things are

- Branch `main`, everything pushed to `origin/main`. Working tree clean.
- 137 non-docker tests pass: `python3 -m pytest tests/ -q -m "not docker"`
- Docker-marked tests need a container: `docker build -t p99bench-test tests/`
- `python3 tools/validate.py results/` → 3/3 valid
- `python3 tools/render.py --check` → exit 0 (CI runs this; generated files are
  never hand-edited)
- **No `Co-Authored-By` trailers anywhere in history, by owner's explicit
  instruction.** Strip before committing, not after — subagents re-add it by
  default. Check with `git log --grep='Co-Authored-By'` before every push.
- `CLAUDE.md` is gitignored (owner's choice). It is edited on disk but never
  committed. Leave that alone.

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

**Every serious bug was invisible until real hardware or CI ran it.** Six so far,
all the same shape: something worked locally because the dev environment already
had what the target lacked, or because `results/` only ever held files from the
*previous* tool version.

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

**Guard:** `tests/test_fresh_run_validates.py` pins the `bench/` ↔ `tools/`
contract (schema_version, `grade.py` invocation, steady durations). When you
change one side, check the other. `bench/run-all.sh` now preflights every tool
before spending 60 minutes (`preflight_tools` in `lib.sh`).

## Open work, roughly in priority order

1. **Phase 5 — recalibrate the four provisional bands.** `cpu.stall_p999_us`,
   `cpu.steady_state.degradation_pct`, `cpu.tls_verify_s`, `ram.bw_read_mbs`
   ship with `provisional: true` and **no corpus behind them**. Spec §11. This
   is blocked on real 0.2.0 runs and is the last thing between the redesign and
   being calibrated rather than reasoned. First real data so far:
   `stall_p999_us = 251 µs` (hetzner), `tls_verify_s` 16.8k (Genoa) vs 9.6k
   (Xeon) — it discriminates.
2. **Spec §9.3 is narratively stale.** It describes v1 results grading `?` for
   five profiles; after the rollup fix they grade real letters with
   `incomplete: true`. Behaviour is better; the doc hasn't caught up.
3. **`network.dns_ms` is ungradeable as measured.** `06-network.sh` takes ONE
   uncached `curl time_namelookup` per target, n=1, reduced worst-of-four. One
   OVH run, one resolver: 1.86 / 81.07 / 109.60 / 149.45 ms — an 80x spread that
   is authoritative-NS distance, not the machine. Demoted to informational
   (§6.5). Grading it needs repeated lookups with warm/cold separated — a
   `bench/` change.
4. **The 100 GB/s warning in `03-ram.sh` may be miscalibrated.** It was written
   for dual-channel assumptions. A 12-channel DDR5 EPYC genuinely does ~100 GB/s
   on 4 threads (ovh/zrh measured 103/108/94 across 128M/512M/1G — no
   convergence, i.e. real). Hetzner's 32 MiB L3 box *did* converge (98 → 66 →
   66), which is what motivated the 512M floor.
5. **`results/README.md`, `CONTRIBUTING.md`** may need a pass once a full 0.2.0
   corpus lands.

## Recent fixes that require a re-run to take effect

The owner's three hosts (hetzner CPX32 hel-1, ovh vps-1-lz-2026 zrh, windcloud
VPS-L enge-sande) were measured *before* these. `git pull` on each, then re-run:

- **`export LC_ALL=C`** — windcloud's silent nulls
- **RAM floor 128M → 512M** — hetzner's published 104 GB/s is ~48% inflated
  (real ≈ 66); its grade does not change (still A) but the number is wrong
- **`llc_bytes` via `lscpu`** — sysfs cache info absent in guests
- **Cache guard units** — compared per-thread BLOCK against total LLC and nulled
  a good OVH measurement; now compares `WORKING_SET = BLOCK * CORES`
- **Tool preflight** — a missing `rt-tests`/`sysstat` used to cost a full hour

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
