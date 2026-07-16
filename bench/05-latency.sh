#!/usr/bin/env bash
# 05-latency.sh - scheduler stall measurement. Needs no running service.
# Emits cpu.stall_* fragment.
#
# WHY NOT redis-cli --intrinsic-latency
# -------------------------------------
# It reports only a running max and an average, and those two numbers cannot
# support a threshold. A real result read avg=0.0565us, max=1642us: a ~30,000x
# skew. The average describes the loop, not the stalls. The max is
# worst-of-60-million-samples - an extreme-value statistic that grows with
# sample count, so every VM fails it eventually and the metric ends up
# measuring "is this a VM?" rather than "is this VM good?". Every one of the
# first 10 published results failed the old <=200us bar; best measured was
# 1642us. A metric no machine can pass is not a metric.
#
# cyclictest emits a latency histogram, which gives real percentiles. p99.9 is
# what a single-threaded process actually feels, and unlike max it converges
# rather than drifting upward with runtime.
#
# WHY --policy=other AND NOT RT PRIORITY (AND NOT -p 0!)
# --------------------------------------------------------
# cyclictest is usually run at -p 99 to characterise RT kernels. That would
# measure the hypervisor's best case. Redis and Node run at normal priority,
# so normal priority is what we measure - same scheduling class the old
# redis-cli loop ran in.
#
# -p 0 does NOT get you SCHED_OTHER. cyclictest is a real-time testing tool:
# it defaults to SCHED_FIFO and clamps whatever -p value you give it upward,
# so -p 0 still starts the measuring thread at SCHED_FIFO priority 2 (verified
# live with `chrt -p` against the measuring thread's tid, not the main
# thread). A SCHED_FIFO thread preempts exactly the contention this stage
# exists to detect, so it silently understates stalls - the hypervisor's best
# case again, just reached by a different door. --policy=other is the only
# flag that actually puts the measuring thread in SCHED_OTHER, confirmed by
# the same chrt check. Do not "simplify" this back to -p 0.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need jq

DURATION="${P99_LATENCY_DURATION:-60}"

# Histogram ceiling in us. Published VMs stall 1.6-6.5ms, so 30ms leaves
# headroom. Anything above lands in "Histogram Overflows" and is handled below.
HIST_MAX="${P99_STALL_HIST_MAX:-30000}"

# 250us between samples -> ~240k samples in 60s, so p99.9 rests on ~240
# observations rather than a handful. Default 1000us would leave 60.
INTERVAL="${P99_STALL_INTERVAL_US:-250}"

if ! command -v cyclictest >/dev/null 2>&1; then
  warn "cyclictest not found (apt install rt-tests). Skipping stall measurement."
  emit_json latency "$(jq -n '{cpu: {
      stall_p99_us: null, stall_p999_us: null, stall_max_us: null,
      stall_samples: null,
      intrinsic_latency_max_us: null, intrinsic_latency_avg_us: null
  }}')"
  exit 0
fi

log "Scheduler stalls: ${DURATION}s cyclictest histogram (no Redis server required)"

# -q quiet, -m mlockall (keep our pages resident so we measure the scheduler
# and not a page fault), --policy=other normal priority (see comment above -
# -p 0 is NOT the same thing), -t 1 one thread (a single-threaded process is
# the thing under study), -h histogram ceiling.
OUT=$(cyclictest -q -m --policy=other -t 1 -i "$INTERVAL" -D "$DURATION" -h "$HIST_MAX" 2>&1 || true)

# Histogram lines are "<bucket_us> <count>"; trailing summary lines start '#'.
# Percentiles come from the cumulative distribution. Overflows are counted
# separately by cyclictest and are, by definition, above HIST_MAX - they must
# be added to the total or every percentile is computed against a short
# denominator and reads optimistically low.
read -r P99 P999 MAXV SAMPLES <<<"$(printf '%s' "$OUT" | awk '
  /^[0-9]+[ \t]+[0-9]+/ { b[n] = $1 + 0; c[n] = $2 + 0; total += $2; n++ }
  /^# Histogram Overflows:/ { for (i = 4; i <= NF; i++) overflow += $i + 0 }
  /^# Max Latencies:/       { for (i = 4; i <= NF; i++) { v = $i + 0; if (!gotmax || v > maxv) { maxv = v; gotmax = 1 } } }
  END {
    grand = total + overflow
    if (grand == 0) { print "  "; exit }
    t99 = grand * 0.99; t999 = grand * 0.999
    cum = 0
    for (i = 0; i < n; i++) {
      cum += c[i]
      if (!got99  && cum >= t99)  { p99  = b[i]; got99  = 1 }
      if (!got999 && cum >= t999) { p999 = b[i]; got999 = 1 }
    }
    # A percentile not reached inside the histogram lives in the overflow
    # bucket. We know only that it exceeds hist_max, so report null rather
    # than pinning it to the ceiling and understating a bad host.
    if (!got99)  p99  = "null"
    if (!got999) p999 = "null"
    if (!gotmax) maxv = "null"
    print p99, p999, maxv, grand
  }
')"

emit_json latency "$(jq -n \
  --argjson p99 "$(jnum "$P99")" \
  --argjson p999 "$(jnum "$P999")" \
  --argjson max "$(jnum "$MAXV")" \
  --argjson n "$(jnum "$SAMPLES")" \
  '{cpu: {
      stall_p99_us: $p99,
      stall_p999_us: $p999,
      stall_max_us: $max,
      stall_samples: $n,
      intrinsic_latency_max_us: null,
      intrinsic_latency_avg_us: null
  }}')"

echo
echo "=== Scheduler latency ==="
echo "p99:     ${P99:-n/a} us"
echo "p99.9:   ${P999:-n/a} us"
echo "max:     ${MAXV:-n/a} us   (context only - grows with runtime, not banded)"
echo "samples: ${SAMPLES:-n/a}"
echo
echo "A single-threaded process is frozen for the whole duration of a stall."
echo "p99.9 is what Redis and each Node event loop actually feel."
