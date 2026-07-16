# Results directory

Layout:

```
results/<provider>/<region>/YYYY-MM-DDThhmm-<product-slug>.json
```

Provider and region are separate levels on purpose. A combined slug like
`ovh-zrh` would make "how is OVH Zurich?" answerable and "how is OVH overall?"
permanently unanswerable, because the dimension is gone. Keeping them apart
costs nothing and preserves both questions.

CI checks that the directory names match `provider.name` and `provider.region`
inside each file.

## These are real submissions

Every file here is a real measurement of a real machine. They are not examples
and must not be deleted. Results are immutable: grades are recomputed from
`schema/thresholds.yaml` on every render, but the measured numbers never change.

Results measured with tool_version < 0.2.0 predate the metric-integrity fixes
and carry no `cpu.stall_*`, `cpu.steady_state`, `cpu.tls_verify_s` or
`ram.bw_read_mbs`. No profile reads those newer metrics yet — rebanding
`schema/thresholds.yaml` to use them is separate follow-up work, not yet
done. Today, every result (old and new tool_version alike) still grades
`postgres_oltp` and `timescale_ingest` fully, because those profiles' rules
don't touch the new fields.

`redis_aof` and `nuxt_ssr` are the exception, and it currently bites *new*
runs, not just old ones: `thresholds.yaml` still keys them off
`cpu.intrinsic_latency_max_us`, which `05-latency.sh` now always emits as
`null` (the tool that produced it, `redis-cli --intrinsic-latency`, was
removed as a metric no machine could pass). A run made with today's code
therefore grades `redis_aof` and `nuxt_ssr` as `unknown` — `verdict.py`'s
actual output word for "the input the rule needs is missing," not `?`. This
is expected until thresholds.yaml is rebanded onto `cpu.stall_p999_us`; it is
not a bug and not something a re-run will fix on its own.

## host_id

Each result carries a `host_id` — a salted hash of the machine's
`/etc/machine-id`, generated automatically by `bench/lib.sh`. It links runs on
the same VM together, which is what lets `render.py` distinguish:

- **same host_id, different hours** → time variance → noisy neighbours
- **different host_id, same product** → host variance → fleet is not uniform

The raw `machine-id` is never published. The salt is a public constant, so the
value identifies a machine within this dataset only.