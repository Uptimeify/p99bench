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
#   --skip-cpu-steady do not run the 15 min CPU sustained test (result marked
#                     incomplete; playwright_node and worker_probe grade "?")
#   --submitter NAME  who vouches for this run. Results are not accepted
#                     anonymously - see CONTRIBUTING.md
#   --notes TEXT      free text recorded as run.notes. Providers submitting
#                     results from their own hardware: disclose it here.
set -uo pipefail
# Resolve our own path BEFORE cd, because --help reads this file's comment block
# back out of $0. After the cd below, a relative $0 ("bench/run-all.sh") no
# longer resolves and --help silently printed nothing while still exiting 0.
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$0")" || exit 1
source ./lib.sh

# Print the header comment block as usage: every line from line 2 up to the
# first line that is not a comment. Derived rather than a hard-coded line range,
# so adding an option cannot desync the help text from the options themselves.
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$SELF"; }

PROVIDER="" PRODUCT="" REGION="" PRICE="null" BILLING="null"
SUBMITTER="" NOTES="" TIER="" SKIP_STEADY=0 STEADY_ONLY=0
SKIP_NETWORK=0 SKIP_CPU_STEADY=0

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
    --skip-cpu-steady) SKIP_CPU_STEADY=1; shift;;
    -h|--help) usage; exit 0;;
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

# Filename is exactly what results/ expects, so submitting is a copy and never
# a rename. The layout lives in tools/validate.py (FILENAME_RE); if you change
# one, change the other.
#
#   results/<provider>/<region>/YYYY-MM-DDThhmm-<product-slug>.json
#
# The product slug is lowercased and stripped of anything outside [a-z0-9-],
# because it goes in a path: "Standard_D4s_v5" -> "standard-d4s-v5".
STAMP=$(date -u +%Y-%m-%dT%H%M)
PRODUCT_SLUG=$(printf '%s' "$PRODUCT" | tr '[:upper:]' '[:lower:]' \
               | sed 's/[^a-z0-9-]\+/-/g; s/^-\+//; s/-\+$//')
BASENAME="$STAMP-$PRODUCT_SLUG"
START=$(date +%s)
OUTDIR="./results-local"
mkdir -p "$OUTDIR"
LOGFILE="$OUTDIR/$BASENAME.log"

log "p99bench $P99BENCH_VERSION"
log "Target: $P99_TARGET | size ${P99_SIZE}/job | log: $LOGFILE"
log "Expect ~60 min: 30 disk steady + 15 CPU steady + ~15 for everything else."

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
  if (( SKIP_CPU_STEADY )); then
    warn "CPU steady state skipped - playwright_node and worker_probe will grade '?'"
  else
    run_stage 02b-cpu-steady.sh
  fi
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

OUT="$OUTDIR/$BASENAME.json"
echo "$RESULT" | jq . > "$OUT"

# --- grades ------------------------------------------------------------
# A result with no grades block still passes validate.py's schema layer if
# nobody is watching (see tools/validate.py's trust check), so any way this
# can fail must WARN LOUDLY rather than quietly skip. Silence here is how a
# submitter ends up shipping a result CI then rejects for a block it never
# had a chance to see.
if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not found - $OUT has NO grades block"
  warn "install python3 + pyyaml and run: python3 tools/grade.py $OUT --in-place"
elif [[ ! -f ../tools/grade.py ]]; then
  warn "../tools/grade.py not found - $OUT has NO grades block"
  warn "this checkout looks incomplete; do not submit this result"
elif python3 ../tools/grade.py "$OUT" --in-place 2>/dev/null; then
  log "grades computed"
else
  warn "grade computation failed (pip install pyyaml jsonschema) - $OUT has NO grades block"
  warn "do not submit this result until it has been graded successfully"
fi

echo
echo "=============================================="
echo " Result: $OUT"
echo " Log:    $LOGFILE"
echo "=============================================="
jq -r '
  if .grades then
    "storage_class: \(.grades.storage_class // "?")",
    "",
    "category grades (what binds them):",
    (.grades.categories | to_entries[] |
      "  \(.key): \(.value.grade)" +
      (if .value.bound_by then " <- \(.value.bound_by)" else "" end)),
    "",
    "profile grades (what binds them):",
    (.grades.profiles | to_entries[] |
      "  \(.key): \(.value.grade)" +
      (if .value.bound_by then " <- \(.value.bound_by)" else "" end) +
      (if .value.reason then " (\(.value.reason))" else "" end))
  else
    "NO GRADES COMPUTED. Do not submit this result - see the warnings above " +
    "and run tools/grade.py by hand to see the real error."
  end
' "$OUT"
echo
echo "host_id: $HOST_ID  (stable for this VM; links your runs together)"
echo
echo "To submit - the filename is already the one results/ expects, just copy it:"
echo "  scp <this-host>:$(pwd)/$OUT results/$PROVIDER/$REGION/"
echo
echo "A single run is a data point about this VM at this hour, not a statement"
echo "about $PROVIDER. Please run this at least 3 times at different hours -"
echo "same VM keeps the same host_id, so the spread over time stays visible."