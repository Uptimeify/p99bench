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

## Why old results show `?`

Every result carries a `grades` block, computed by `tools/grade.py` from
`schema/thresholds.yaml`: A–F for each of the four categories (`disk`, `cpu`,
`ram`, `network`) and for each of the seven profiles
(`postgres_oltp`, `timescale_ingest`, `patroni_member`, `redis_sentinel`,
`worker_probe`, `playwright_node`, `nuxt_ssr`). `?` means "not measured," not
"failing" — it is the rollup's answer when a required metric is absent
(spec 4.2). CI recomputes every stored `grades` block and rejects a result
whose block doesn't match; grades are never hand-written.

Results measured with `tool_version` < 0.2.0 predate Phase 1's
metric-integrity stages and carry no `cpu.stall_*`, `cpu.steady_state`,
`cpu.tls_verify_s`, or `ram.bw_read_mbs`. Five of the seven profiles
(`patroni_member`, `redis_sentinel`, `worker_probe`, `playwright_node`,
`nuxt_ssr`) require at least one of those fields, so every v1 result grades
`?` on those profiles, and the `cpu`/`ram` categories grade `?` too. That is
correct, not a bug: the host was genuinely never put through the newer
stages. `postgres_oltp` and `timescale_ingest` still grade fully on v1
results, because neither profile's rules touch the newer fields.

The fix is not a rebanding — it is re-running the host with today's
`bench/run-all.sh`, which measures every field above. Submit that result and
`tools/grade.py` produces a real grade instead of `?`.

## host_id

Each result carries a `host_id` — a salted hash of the machine's
`/etc/machine-id`, generated automatically by `bench/lib.sh`. It links runs on
the same VM together, which is what lets `render.py` distinguish:

- **same host_id, different hours** → time variance → noisy neighbours
- **different host_id, same product** → host variance → fleet is not uniform

The raw `machine-id` is never published. The salt is a public constant, so the
value identifies a machine within this dataset only.