#!/usr/bin/env bash
# 02b-cpu-steady.sh - sustained CPU load. Detects burst-credit throttling.
# Emits cpu.steady_state fragment.
#
# WHY THIS EXISTS
# ---------------
# Cloud block storage grants burst credits, so 01b-steady.sh runs 30 minutes to
# find the disk a customer actually gets rather than the credit balance. CPU
# credits work the same way on budget VPS, and until now nothing here measured
# them: every CPU number in this suite came from a 30-second sysbench run, which
# is exactly the window a credit budget covers.
#
# The failure mode is real and was diagnosed in production before this stage
# existed. A Playwright probe node failed 52% of its checks against 2% on two
# peer nodes running the same checks. Its plain HTTP checks were fine, so egress
# was healthy. It failed constantly - 2-21 fails/hr around the clock - not in
# spikes, and even its PASSING runs took 46s against 12.8s on peers. That is a
# node pinned at a throttled baseline, and steal time did not see it: every box
# in that fleet reported 0.1-0.2% steal.
#
# WHY 15 MINUTES AND NOT 30
# -------------------------
# CPU credit budgets are granted in seconds-to-minutes, not hours, so 15 minutes
# exhausts typical schemes at half the wall-clock cost of the disk stage.
#
# WHY THIS CANNOT SHARE THE DISK STEADY WINDOW
# --------------------------------------------
# fio saturating the disk makes these numbers measure I/O wait. The two would be
# indistinguishable, which is worse than not measuring either.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need sysbench
need jq

CORES=$(nproc)
MINUTES="${P99_CPU_STEADY_MIN:-15}"
DURATION=$((MINUTES * 60))

log "CPU sustained load: ${MINUTES} min at $CORES threads"
log "This finds the CPU you actually get, not the credit balance."

# One sysbench per minute, back to back. Reporting per-interval throughput this
# way (rather than parsing sysbench's own interim reports) keeps the parser
# independent of sysbench's --report-interval output format, which has moved
# between versions and has already broken parsers in this repo once.
EPS_SERIES=()
for ((m = 0; m < MINUTES; m++)); do
  e=$(sysbench cpu --cpu-max-prime=20000 --threads="$CORES" --time=60 run 2>/dev/null \
      | awk '/events per second/ {print $4}')
  EPS_SERIES+=("$e")
  log "  minute $((m + 1))/$MINUTES: ${e:-parse-failed} eps"
done

# Steal during the final minute, when credits are gone and the hypervisor's
# real allocation is visible. An idle VM steals nothing; a throttled one may
# steal a great deal precisely here.
STEAL=""
if command -v mpstat >/dev/null 2>&1; then
  sysbench cpu --cpu-max-prime=20000 --threads="$CORES" --time=15 run >/dev/null 2>&1 &
  LOAD_PID=$!
  STEAL=$(mpstat 1 10 2>/dev/null | awk '
    # Locate %steal by its OFFSET FROM THE END, not from the start.
    #
    # The header carries a leading timestamp whose field count varies by locale:
    # "18:56:02 CPU %usr ..." in a 24-hour locale, but "06:56:02 PM CPU %usr ..."
    # in a 12-hour one. The Average: line always has exactly one leading field.
    # So an index counted from the left is off by one in some locales and right in
    # others -- the previous version used `col = i - 1` and, on a 24-hour host,
    # silently printed %soft as if it were %steal. A plausible small number,
    # labelled as the metric that decides whether Patroni fails over.
    #
    # The trailing columns (%steal %guest %gnice %idle) do not move, so an offset
    # from NF is stable across locales and sysstat versions.
    /%steal/ && !found { for (i = 1; i <= NF; i++) if ($i == "%steal") { off = NF - i; found = 1 } }
    /^Average/ && found { print $(NF - off); exit }
  ')
  wait "$LOAD_PID" 2>/dev/null || true
else
  warn "mpstat missing (apt install sysstat) - steal not measured"
fi

FIRST="${EPS_SERIES[0]}"
LAST="${EPS_SERIES[${#EPS_SERIES[@]} - 1]}"

# Positive = throughput fell. Same sign convention as
# disk.steady_state.degradation_pct, which the bands read with op "lte". A sign
# flip here would grade a throttled host as excellent.
DEG=""
if [[ -n "$FIRST" && -n "$LAST" ]] && (( $(echo "$FIRST > 0" | bc -l) )); then
  DEG=$(echo "scale=2; ($FIRST - $LAST) / $FIRST * 100" | bc 2>/dev/null || echo "")
fi

emit_json cpu-steady "$(jq -n \
  --argjson deg "$(jnum "$DEG")" \
  --argjson first "$(jnum "$FIRST")" \
  --argjson last "$(jnum "$LAST")" \
  --argjson dur "$(jnum "$DURATION")" \
  --argjson steal "$(jnum "$STEAL")" \
  '{cpu: {steady_state: {
      degradation_pct: $deg,
      first_min_eps: $first,
      last_min_eps: $last,
      duration_s: $dur,
      steal_pct: $steal
  }}}')"

echo
echo "=== CPU steady state (${MINUTES} min) ==="
echo "first minute: ${FIRST:-n/a} eps"
echo "last minute:  ${LAST:-n/a} eps"
echo "degradation:  ${DEG:-n/a} %   (positive = throughput fell)"
echo "steal at end: ${STEAL:-n/a} %"
echo
echo "A large drop means the short CPU tests describe a machine you do not have."
echo "This is the signal steal time misses: a host can throttle you to baseline"
echo "without ever reporting steal."
