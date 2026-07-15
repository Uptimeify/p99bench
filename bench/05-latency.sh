#!/usr/bin/env bash
# 05-latency.sh - scheduler stall measurement. Needs no running service.
# Emits cpu.intrinsic_latency_* fragment.
#
# WHY THIS REPLACED THE OLD redis-benchmark SCRIPT
# ------------------------------------------------
# A benchmark that requires Redis to already be installed cannot answer the
# question "should I deploy Redis here?". It answers "how is the Redis I already
# deployed doing?" - a monitoring question, not a procurement one.
#
# Redis performance decomposes into three things, none of which need a server:
#
#   1. Scheduler stalls    -> redis-cli --intrinsic-latency (this file)
#   2. Single-core speed   -> sysbench cpu 1 thread          (02-cpu.sh)
#   3. AOF fsync latency   -> fio psync fdatasync QD1        (01-disk.sh)
#
# --intrinsic-latency runs a tight loop measuring how long the kernel takes to
# give the CPU back. It ships in redis-tools, opens no socket, and is the single
# best cheap detector of an oversubscribed hypervisor. On decent bare metal the
# worst case sits under 30us. Anything past a few hundred microseconds means the
# host is stealing time in chunks large enough to stall a single-threaded
# process - which is exactly what Redis, and every Node event loop, is.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need jq

DURATION="${P99_LATENCY_DURATION:-60}"

if ! command -v redis-cli >/dev/null 2>&1; then
  warn "redis-cli not found (apt install redis-tools). Skipping intrinsic latency."
  emit_json latency '{"cpu": {"intrinsic_latency_max_us": null, "intrinsic_latency_avg_us": null}}'
  exit 0
fi

log "Scheduler stalls: ${DURATION}s tight loop (no Redis server required)"

OUT=$(redis-cli --intrinsic-latency "$DURATION" 2>&1 || true)
echo "$OUT"

# Final line: "N total runs (avg latency: X.XXX microseconds / ...)"
AVG=$(echo "$OUT" | awk -F'avg latency: ' '/total runs/ {split($2,a," "); print a[1]}' | tail -1)
# Highest "Max latency so far: N microseconds" line seen.
MAX=$(echo "$OUT" | awk '/Max latency so far/ {print $(NF-1)}' | sort -n | tail -1)

emit_json latency "$(jq -n \
  --argjson max "$(jnum "$MAX")" \
  --argjson avg "$(jnum "$AVG")" \
  '{cpu: {intrinsic_latency_max_us: $max, intrinsic_latency_avg_us: $avg}}')"

echo
echo "=== Scheduler latency ==="
echo "avg: ${AVG:-n/a} us"
echo "max: ${MAX:-n/a} us"
echo
echo "Reference: good bare metal <30us worst case. >200us fails redis_aof."
echo "A single-threaded process is frozen for the whole duration of a stall."
