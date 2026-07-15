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

## The files currently here are EXAMPLES

They exist to demonstrate the two variance types the tooling reports and to give
CI something to chew on. **Delete them before the first real submission:**

```bash
rm -rf results/ovh results/hetzner
python3 tools/render.py > RESULTS.md
```

They are shaped from real measurements but contain filled-in values for metrics
that did not complete, so they must not be read as findings about either
provider.

## host_id

Each result carries a `host_id` — a salted hash of the machine's
`/etc/machine-id`, generated automatically by `bench/lib.sh`. It links runs on
the same VM together, which is what lets `render.py` distinguish:

- **same host_id, different hours** → time variance → noisy neighbours
- **different host_id, same product** → host variance → fleet is not uniform

The raw `machine-id` is never published. The salt is a public constant, so the
value identifies a machine within this dataset only.