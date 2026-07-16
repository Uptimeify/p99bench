"""Container smoke tests for bench stages.

These prove a stage runs and emits a well-formed fragment. They prove
NOTHING about hardware -- numbers from a container on a laptop describe
the laptop's hypervisor, not a provider. Same rule as the CI smoke job.
"""
import json
import subprocess

import pytest

IMAGE = "p99bench-test"


def run_stage(repo_root, script: str, env: dict, frag: str) -> dict:
    """Run one stage in the container and return its parsed fragment."""
    env_args = []
    for k, v in {"P99_WORK": "/tmp/p99work", **env}.items():
        env_args += ["-e", f"{k}={v}"]
    proc = subprocess.run(
        # --cap-add=SYS_NICE: cyclictest calls sched_setscheduler even at
        # -p 0 (it "runs all threads at the same priority" under -h), which
        # needs CAP_SYS_NICE or a nonzero RLIMIT_RTPRIO. Docker's default
        # capability set has neither, so cyclictest exits 1 with "Unable to
        # change scheduling policy!" before it ever gets to mlockall. This
        # is a container-only concession for the test rig; the real script
        # runs on bare metal and needs no such grant.
        ["docker", "run", "--rm", "--cap-add=SYS_NICE",
         "-v", f"{repo_root}:/p99bench", *env_args, IMAGE,
         "bash", "-c", f"bash /p99bench/bench/{script} >/dev/null 2>&1; "
                       f"cat /tmp/p99work/frag-{frag}.json"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"stage failed: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


@pytest.mark.docker
def test_latency_stage_emits_stall_percentiles(repo_root):
    frag = run_stage(repo_root, "05-latency.sh",
                     {"P99_LATENCY_DURATION": "5"}, "latency")
    cpu = frag["cpu"]
    for key in ("stall_p99_us", "stall_p999_us", "stall_max_us", "stall_samples"):
        assert key in cpu, f"missing {key}"
        assert cpu[key] is not None, f"{key} is null -- parser missed"
        assert isinstance(cpu[key], (int, float))


@pytest.mark.docker
def test_latency_percentiles_are_ordered(repo_root):
    # p99 <= p99.9 <= max is a property of any correct percentile parser,
    # and holds regardless of what the host's latency actually is.
    cpu = run_stage(repo_root, "05-latency.sh",
                    {"P99_LATENCY_DURATION": "5"}, "latency")["cpu"]
    assert cpu["stall_p99_us"] <= cpu["stall_p999_us"] <= cpu["stall_max_us"]


@pytest.mark.docker
def test_latency_stage_retains_legacy_fields_as_null(repo_root):
    # Spec 9.2: legacy fields are retained so old and new results share a
    # schema, but the tool that produced them is gone, so they are null.
    cpu = run_stage(repo_root, "05-latency.sh",
                    {"P99_LATENCY_DURATION": "5"}, "latency")["cpu"]
    assert cpu["intrinsic_latency_max_us"] is None
    assert cpu["intrinsic_latency_avg_us"] is None
