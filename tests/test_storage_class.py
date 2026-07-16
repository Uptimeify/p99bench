import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from grade import storage_class  # noqa: E402


def _r(iops):
    return {"disk": {"wal_fsync": {"iops": iops}}}


def test_class_boundaries():
    # Boundaries in us per durable write: <300 local-nvme, <1500 net-fast,
    # <10000 net-slow, else degraded.
    assert storage_class(_r(1_000_000 / 200)) == "local-nvme"
    assert storage_class(_r(1_000_000 / 800)) == "net-fast"
    assert storage_class(_r(1_000_000 / 4000)) == "net-slow"
    assert storage_class(_r(1_000_000 / 14000)) == "degraded"


def test_class_declines_without_the_input():
    assert storage_class({}) is None
    assert storage_class(_r(None)) is None
    assert storage_class(_r(0)) is None


def test_real_corpus_splits_into_the_regimes_spec_24_describes():
    # Spec 2.4: converting fsync IOPS to latency-per-op splits the corpus into
    # three physics regimes. This pins that the derivation actually reproduces
    # them from the published files, rather than from a table in a document.
    seen = {}
    for p in sorted((ROOT / "results").rglob("*.json")):
        d = json.loads(p.read_text())
        loc = f"{d['provider']['name']}/{d['provider']['region']}"
        seen.setdefault(loc, set()).add(storage_class(d))

    assert seen["hetzner/hel-1"] == {"net-fast"}     # 721-839 us/fsync
    assert seen["ovh/waw"] == {"net-fast"}           # 630 us
    assert seen["ovh/prg"] == {"net-slow"}           # 3360-3390 us
    assert seen["ovh/zrh"] == {"net-slow"}           # 4316-5562 us
    assert seen["windcloud/enge-sande"] == {"degraded"}  # 13945 us


def test_no_published_host_is_local_nvme():
    # Not a bug -- a fact worth pinning. Every host in this corpus is on network
    # storage. If a local-NVMe host ever lands, this test fails and someone must
    # look, because it changes what the fsync bands are being read against.
    classes = {
        storage_class(json.loads(p.read_text()))
        for p in (ROOT / "results").rglob("*.json")
    }
    assert "local-nvme" not in classes
