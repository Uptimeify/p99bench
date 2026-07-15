#!/usr/bin/env bash
# run-all.sh - orchestrate a full p99bench run and emit one result JSON.
#
# Usage:
#   ./run-all.sh --provider hetzner --product CPX41 --region fsn1 \
#                --price 29.90 --billing monthly [--submitter yourhandle]
#
# Options:
#   --target PATH     directory to benchmark (default /var/lib/p99bench)
#   --size 4G         fio file size PER JOB, 4 jobs -> 4x this on disk
#   --skip-steady     skip the 30 min sustained test (result marked incomplete)
#   --steady-only     run only the 30 min sustained test
#   --storage-tier S  block storage tier name, if not the boot volume
#   --with-ookla      also run Ookla speedtest as context (needs `speedtest`;
#                     Ookla's CLI is licensed for personal, non-commercial use)
#   --skip-network    do not measure the network at all (recorded as skipped)
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

PROVIDER="" PRODUCT="" REGION="" PRICE="null" BILLING="null"
SUBMITTER="" NOTES="" TIER="" SKIP_STEADY=0 STEADY_ONLY=0
SKIP_NETWORK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider) PROVIDER="$2"; shift 2;;
    --product)  PRODUCT="$2"; shift 2;;
    --region)   REGION="$2"; shift 2;;
    --price)    PRICE="$2"; shift 2;;
    --billing)  BILLING="\"$2\""; shift 2;;
    --submitter) SUBMITTER="$2"; shift 2;;
    --notes)    NOTES="$2"; shift 2;;
    --storage-tier) TIER="$2"; shift 2;;
    --target)   export P99_TARGET="$2"; shift 2;;
    --size)     export P99_SIZE="$2"; shift 2;;
    --skip-steady) SKIP_STEADY=1; shift;;
    --steady-only) STEADY_ONLY=1; shift;;
    --with-ookla) export P99_WITH_OOKLA=1; shift;;
    --skip-network) SKIP_NETWORK=1; shift;;
    -h|--help) sed -n '2,21p' "$0"; exit 0;;
    *) die "unknown option: $1";;
  esac
done

[[ -z "$PROVIDER" ]] && die "--provider is required (lowercase slug, e.g. hetzner)"
[[ -z "$PRODUCT"  ]] && die "--product is required (exact instance type as billed)"
[[ -z "$REGION"   ]] && die "--region is required (provider DC code, e.g. fsn1)"
[[ $EUID -ne 0 ]] && die "run as root (dmidecode, cpufreq and fio need it)"

# --- preflight -------------------------------------------------------------
LOAD=$(awk '{print $1}' /proc/loadavg)
if (( $(echo "$LOAD > 0.5" | bc -l) )); then
  warn "load average is $LOAD - something else is running. Results will be noise."
  read -rp "continue anyway? [y/N] " a; [[ "$a" == "y" ]] || exit 1
fi

rm -rf "$P99_WORK"; mkdir -p "$P99_WORK"
rm -rf "$P99_TARGET"

STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
START=$(date +%s)
OUTDIR="./results-local"
mkdir -p "$OUTDIR"
LOGFILE="$OUTDIR/$PROVIDER-$PRODUCT-$REGION-$STAMP.log"

log "p99bench $P99BENCH_VERSION"
log "Target: $P99_TARGET | size ${P99_SIZE}/job | log: $LOGFILE"

run_stage() {
  local s="$1"
  log "--- $s ---"
  bash "./$s" 2>&1 | tee -a "$LOGFILE"
}

if (( STEADY_ONLY )); then
  run_stage 01b-steady.sh
else
  run_stage 00-inventory.sh
  run_stage 01-disk.sh
  run_stage 02-cpu.sh
  run_stage 03-ram.sh
  run_stage 05-latency.sh
  if (( SKIP_NETWORK )); then
    warn "network skipped by request"
    printf '%s' '{"network": {"reachable": false, "skip_reason": "--skip-network"}}' \
      > "$P99_WORK/frag-network.json"
  else
    run_stage 06-network.sh
  fi
  run_stage 04-app-optional.sh
  if (( SKIP_STEADY )); then
    warn "steady state skipped - submission will be flagged incomplete"
  else
    run_stage 01b-steady.sh
  fi
fi

END=$(date +%s)

# --- merge fragments -------------------------------------------------------
LOCAL_HOUR=$(date +%-H)
HOST_ID=$(host_id)
META=$(jq -n \
  --arg ts "$(date -u -d "@$START" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg hid "$HOST_ID" \
  --argjson hour "$LOCAL_HOUR" \
  --argjson dur "$((END - START))" \
  --arg ver "$P99BENCH_VERSION" \
  --arg sub "$SUBMITTER" --arg notes "$NOTES" \
  --arg name "$PROVIDER" --arg prod "$PRODUCT" --arg reg "$REGION" \
  --argjson price "$PRICE" --argjson billing "$BILLING" \
  --arg tier "$TIER" \
  '{
    schema_version: "1.0",
    run: {
      timestamp_utc: $ts, host_id: $hid, local_hour: $hour, duration_s: $dur,
      tool_version: $ver,
      submitter: (if $sub == "" then null else $sub end),
      notes: (if $notes == "" then null else $notes end)
    },
    provider: {
      name: $name, product: $prod, region: $reg,
      price_eur_month: $price, billing: $billing,
      storage_tier: (if $tier == "" then null else $tier end)
    }
  }')

RESULT="$META"
for f in "$P99_WORK"/frag-*.json; do
  [[ -e "$f" ]] || continue
  # emit_json should have caught this already, but a fragment left over from an
  # older tool version or a killed stage would otherwise take down the whole
  # merge with jq's unhelpful "invalid JSON text passed to --argjson", which
  # names neither the file nor the stage.
  if ! jq empty "$f" 2>/dev/null; then
    warn "fragment $(basename "$f") is empty or invalid - that stage failed"
    warn "the result will be missing a section; do not submit it without checking"
    continue
  fi
  RESULT=$(jq -n --argjson a "$RESULT" --argjson b "$(cat "$f")" '$a * $b')
done

OUT="$OUTDIR/$PROVIDER-$PRODUCT-$REGION-$STAMP.json"
echo "$RESULT" | jq . > "$OUT"

# --- verdict ---------------------------------------------------------------
if command -v python3 >/dev/null 2>&1 && [[ -f ../tools/verdict.py ]]; then
  if python3 ../tools/verdict.py "$OUT" --in-place 2>/dev/null; then
    log "verdict computed"
  else
    warn "verdict computation failed (pip install pyyaml)"
  fi
fi

echo
echo "=============================================="
echo " Result: $OUT"
echo " Log:    $LOGFILE"
echo "=============================================="
jq -r '
  if .verdict then
    .verdict |
    "postgres_oltp:    \(.postgres_oltp)",
    "timescale_ingest: \(.timescale_ingest)",
    "redis_aof:        \(.redis_aof)",
    "nuxt_ssr:         \(.nuxt_ssr)",
    "",
    (if (.reasons | length) > 0 then "reasons:" else "no failing rules" end),
    (.reasons[]? | "  - \(.)")
  else "no verdict computed" end
' "$OUT"
echo
echo "host_id: $HOST_ID  (stable for this VM; links your runs together)"
echo
echo "To submit:"
echo "  mkdir -p results/$PROVIDER/$REGION"
echo "  cp $OUT results/$PROVIDER/$REGION/"
echo
echo "A single run is a data point about this VM at this hour, not a statement"
echo "about $PROVIDER. Please run this at least 3 times at different hours -"
echo "same VM keeps the same host_id, so the spread over time stays visible."