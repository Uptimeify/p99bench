# Contributing

Two kinds of contribution matter here: **results** and **arguments about
thresholds**. The second is often more valuable than the first.

## Submitting a result

### Before you run

- [ ] **Nothing else is running on the machine.** `run-all.sh` refuses above load
      0.5. Do not override this unless you enjoy publishing noise.
- [ ] **~20 GB free** at the target path.
- [ ] **You know what you are testing.** Boot volume or dedicated data volume?
      They behave completely differently, and the result records which.
- [ ] **You know the price and billing model.** Required fields.
- [ ] **~400 MB of egress is acceptable.** The network stage downloads from fixed
      reference targets. On metered bandwidth, check the cost first. No egress at
      all is fine — it records `reachable: false` and the result stays valid.

### Run it

```bash
sudo ./bench/run-all.sh \
  --provider hetzner \
  --product CPX41 \
  --region fsn1 \
  --price 29.90 \
  --billing monthly \
  --submitter yourgithubhandle
```

Do **not** pass `--skip-steady`. Validation rejects results without the sustained
test — without it there is no way to tell a fast disk from a burst credit
balance, which is most of the point of this project.

### Three runs, different hours

**One run is not a result.** Noisy neighbours have schedules; a benchmark at
03:00 says nothing about Tuesday at 14:00. Run it at least three times at
meaningfully different hours and submit all three files.

Runs on the same VM share a `host_id` automatically, so the spread across the
day stays visible. `RESULTS.md` reports median **and** worst per machine and
never a mean -- a machine that is fine at 03:00 and unusable at 18:00 should
look like exactly that, not like a mediocre average.

Two things are worth measuring and they are different questions:

| What you vary | What it tells you | How |
|---|---|---|
| **The hour**, same VM | Whether neighbours are ruining this machine | Same VM, 3+ runs at different times |
| **The VM**, same product | Whether the provider's fleet is uniform | Fresh VM, same product and region |

Both are welcome. `render.py` reports them separately because averaging them
together answers neither question.

A single run still validates -- it is a real measurement -- but `validate.py`
warns, and no spread is computed from it.

### Open the PR

```bash
mkdir -p results/hetzner/fsn1
cp bench/results-local/hetzner-CPX41-fsn1-*.json results/hetzner/fsn1/

python3 tools/validate.py results/
python3 tools/render.py > RESULTS.md

git checkout -b result/hetzner-cpx41-fsn1
git add results/ RESULTS.md
git commit -m "result: hetzner CPX41 fsn1, 3 runs"
```

Layout: `results/<provider>/<region>/YYYY-MM-DDThhmm-<product-slug>.json`

Provider and region are **separate directory levels**, not a combined slug like
`ovh-zrh`. That keeps both "how is OVH Zurich?" and "how is OVH overall?"
answerable; folding them together permanently destroys the second question. CI
checks that the directories match `provider.name` and `provider.region` in the
file.

### What CI will reject

- Schema violations
- Missing `disk.steady_state.degradation_pct` (no sustained test)
- Missing `disk.wal_fsync.p999_us` (the primary metric)
- Missing `run.submitter` — **results are not accepted anonymously**
- Wrong directory for the provider/region in the file
- A `verdict` that does not match what `thresholds.yaml` computes
- `RESULTS.md` not regenerated

### Why no anonymous results

Someone has to vouch for the claim that the machine was idle and the run was
honest. That is not a bureaucratic requirement; it is the only thing standing
between this dataset and being useless.

## About the network stage

Every host measures the **same** targets from
[`schema/network-targets.yaml`](schema/network-targets.yaml). Please do not
substitute your own "closer" targets: the whole point is that distance is a
constant so peering differences become visible. A run against different targets
is not comparable with anything and will be asked to be re-run.

`--with-ookla` is optional context. It picks a nearby server, so it is not
comparable across hosts, and Ookla's CLI is licensed for personal,
non-commercial use — which is exactly why it can never be required in a repo
that invites provider submissions.

Proposing a target change: open an issue. Changing the list breaks comparability
with every result already submitted, so `list_version` gets bumped and old runs
are flagged as measured against a different list. This is why the list is short
and boring.

## About `host_id`

Each result carries a `host_id`: a salted SHA-256 of the machine's
`/etc/machine-id`, truncated to 12 hex characters. It is generated
automatically; you do not pass it in.

It exists so that runs on the same VM can be linked together, which is the only
way to tell "this machine is inconsistent" from "this provider's machines are
inconsistent". Those are different findings with different implications.

Your raw `machine-id` is a local secret and is never published — only the hash
is, and the salt is a public constant in `bench/lib.sh`, so the value identifies
a machine **within this dataset only** and correlates with nothing outside it.

If you rebuild a VM from scratch, it gets a new `machine-id` and therefore a new
`host_id`. That is correct: it is a different machine.

## Provider submissions

**Providers are explicitly welcome to submit results from their own hardware.**
Please disclose the affiliation in `run.notes`. That is all.

This is not reluctant tolerance. A dataset that a provider can improve by
measuring their hardware properly is a dataset with a working feedback loop. The
alternative — us measuring one VM and declaring a verdict on a company — is both
methodologically weak and hostile.

If you think a result misrepresents your product, the productive responses are:

1. Submit runs of your own.
2. Argue that the threshold is wrong. It is in a file. Open an issue.
3. Point out a methodological error. We will fix it and re-render everything.

## Arguing about thresholds

Genuinely the most valuable contribution.

Open an issue with:

- the threshold, the proposed value, and the reasoning
- ideally, data

Read [THRESHOLDS.md](THRESHOLDS.md) first — the confidence level of each number
is documented, and the ones marked Low are already flagged as weak. The
`redis_aof.single_thread_eps` proxy in particular deserves a better idea.

Accepted changes re-render every existing verdict. Results may flip. That is the
system working.

## Code contributions

Keep the shell scripts POSIX-ish bash, readable over clever, and comment the
*why* — a future reader needs to know why `psync` and not `libaio`, not what
`psync` is.

Any new metric needs:
1. a field in `schema/result.schema.json`
2. a rule in `schema/thresholds.yaml` (or an explicit note that it is
   informational only)
3. a row in `THRESHOLDS.md` with reasoning and a confidence level

A metric nobody can act on is noise. If you cannot write the reasoning row, the
metric is not ready.

## Conduct

Be decent. Attack numbers, not people. Provider employees are welcome and should
say so.