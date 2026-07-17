#!/usr/bin/env bash
# 06-network.sh - uplink throughput and latency to FIXED reference targets.
# Emits network{} fragment.
#
# INFORMATIONAL ONLY: nothing here produces a pass/fail verdict. See
# THRESHOLDS.md for why -- in short, nobody can currently justify a number like
# "a database host needs 500 Mbit/s" from a workload requirement rather than
# from vibes. When enough data exists to draw a real line, it can become a rule.
#
# Every host measures the SAME targets (schema/network-targets.yaml) so the
# numbers are comparable. A nearest-server speedtest measures a different path
# per host and cannot be put in a shared table honestly.
#
# Egress-blocked hosts are fine: the fragment records reachable=false and the
# result stays valid. "This host cannot reach the internet" is information, not
# a failure to measure.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need jq
need curl

TARGETS_FILE="../schema/network-targets.yaml"
[[ -f "$TARGETS_FILE" ]] || die "missing $TARGETS_FILE"

WITH_OOKLA="${P99_WITH_OOKLA:-0}"

# Parse the YAML with python3 (already required by the toolchain) rather than
# adding a yq dependency for four fields.
read_targets() {
  python3 - "$TARGETS_FILE" <<'PY'
import sys, json
try:
    import yaml
except ImportError:
    sys.exit(3)
d = yaml.safe_load(open(sys.argv[1]))
print(json.dumps({
    "list_version": str(d.get("list_version", "?")),
    "size_mb": d.get("download_size_mb", 100),
    "targets": d.get("targets", []),
}))
PY
}

CFG=$(read_targets) || {
  warn "pyyaml missing; cannot read target list. Skipping network tests."
  emit_json network '{"network": {"reachable": false, "skip_reason": "pyyaml not installed"}}'
  exit 0
}

LIST_VERSION=$(echo "$CFG" | jq -r .list_version)
N_TARGETS=$(echo "$CFG" | jq '.targets | length')

log "Network: $N_TARGETS fixed targets (list v$LIST_VERSION)"

# --- reachability gate -----------------------------------------------------
# Decide up front whether there is any egress at all, so a firewalled host
# spends 5 seconds here instead of 4 minutes timing out per target.
if ! curl -s --max-time 5 -o /dev/null -w '%{http_code}' \
     "$(echo "$CFG" | jq -r '.targets[0].url')" -r 0-1024 >/dev/null 2>&1; then
  warn "no egress to reference targets - recording reachable=false"
  warn "this is a valid result; the host simply cannot reach the internet"
  emit_json network "$(jq -n --arg lv "$LIST_VERSION" \
    '{network: {reachable: false, target_list_version: $lv,
                skip_reason: "no egress (firewall or no route)"}}')"
  echo
  echo "=== Network ==="
  echo "unreachable - egress blocked or no route. Recorded as reachable=false."
  exit 0
fi

# --- per-target measurement ------------------------------------------------

# dns_lookup_ms <url> -- one DNS lookup, in ms. Prints nothing on failure.
#
# curl -sI rather than a GET: the name is resolved before any response body
# matters, so time_namelookup is valid even against an endpoint that rejects
# HEAD (a 405 still resolved the name). -o /dev/null so this never pulls the
# throughput file six times per target.
dns_lookup_ms() {
  local url="$1" t
  t=$(curl -sI --max-time 10 -o /dev/null -w '%{time_namelookup}' "$url" 2>/dev/null) || return 0
  [[ -z "$t" ]] && return 0
  echo "scale=2; $t * 1000" | bc 2>/dev/null || true
}

# measure_target <id> <url> <ping_host>
# Emits one JSON object. Every field independently nullable: a target that
# answers HTTP but drops ICMP still yields useful throughput.
measure_target() {
  local id="$1" url="$2" phost="$3"

  # DNS first, before the throughput curl resolves the name and warms every
  # cache between here and the authoritative server.
  #
  # The old measurement was one curl time_namelookup taken in passing during
  # the throughput request: n=1, no warming, no separation. Worst-of-four
  # across targets then made it worst-of-four-cold-first-lookups, which is
  # authoritative-NS distance, not the host -- ovh/waw measured 1.86 / 81.07 /
  # 109.60 / 149.45 ms in a single run against a single resolver. That is why
  # it was never gradeable (THRESHOLDS.md).
  #
  # Two separate questions, so two separate fields:
  #   dns_first_ms    - the first lookup this run makes for this name. Still
  #                     n=1 and still mostly NS distance and prior cache state.
  #                     Informational, and honest about it in the name.
  #   dns_warm_p50_ms - median of 5 lookups once the name is cached. THIS is a
  #                     host property: a monitoring probe hitting a target
  #                     every minute pays the cached lookup essentially always,
  #                     so what it actually waits on is this host's resolver
  #                     answering a name it already knows. Repeatable, and a
  #                     median rather than a max because there is no queue here
  #                     whose tail we are hunting -- the tail belongs to rtt.
  log "  $id: dns"
  local dns_first dns_warm_p50
  dns_first=$(dns_lookup_ms "$url")
  # median() (lib.sh) drops failed lookups rather than counting them as zero,
  # and is unit tested there -- this stage cannot be tested without egress.
  dns_warm_p50=$(for _ in 1 2 3 4 5; do dns_lookup_ms "$url"; done | median)
  [[ -z "$dns_warm_p50" ]] && warn "  $id: no DNS lookup succeeded - dns_warm_p50_ms null"

  log "  $id: throughput"
  # -w gives us curl's own measurements: no arithmetic on wall clock, no
  # writing 100MB to disk (-o /dev/null) polluting a disk benchmark.
  local out speed_bps ttfb
  out=$(curl -s --max-time 120 -o /dev/null \
        -w '%{speed_download} %{time_starttransfer} %{http_code}' \
        "$url" 2>/dev/null) || out=""

  if [[ -z "$out" ]]; then
    jq -n --arg id "$id" \
       --argjson dns_first "$(jnum "$dns_first")" \
       --argjson dns_warm "$(jnum "$dns_warm_p50")" \
       '{id: $id, reachable: false, mbps: null, ttfb_ms: null,
         dns_first_ms: $dns_first, dns_warm_p50_ms: $dns_warm,
         rtt_p50_ms: null, rtt_p99_ms: null, loss_pct: null}'
    return
  fi

  speed_bps=$(echo "$out" | awk '{print $1}')
  ttfb=$(echo "$out" | awk '{print $2}')
  local code
  code=$(echo "$out" | awk '{print $3}')

  # bytes/s -> Mbit/s
  local mbps="null"
  if [[ -n "$speed_bps" && "$speed_bps" != "0" ]]; then
    mbps=$(echo "scale=2; $speed_bps * 8 / 1000000" | bc 2>/dev/null || echo null)
  fi
  local ttfb_ms="null"
  [[ -n "$ttfb" ]] && ttfb_ms=$(echo "scale=2; $ttfb * 1000" | bc 2>/dev/null || echo null)

  # Latency: percentiles, not the average. Same reasoning as every other metric
  # in this suite -- the mean hides exactly the tail that hurts.
  log "  $id: latency"
  local p50="null" p99="null" loss="null" ping_out
  ping_out=$(ping -c 100 -i 0.2 -W 2 "$phost" 2>/dev/null) || ping_out=""
  if [[ -n "$ping_out" ]]; then
    loss=$(echo "$ping_out" | awk -F'[ %]' '/packet loss/ {for(i=1;i<=NF;i++) if($i=="packet") print $(i-2)}' | head -1)
    [[ -z "$loss" ]] && loss="null"
    local rtts
    rtts=$(echo "$ping_out" | grep -oE 'time=[0-9.]+' | cut -d= -f2 | sort -n)
    if [[ -n "$rtts" ]]; then
      local n
      n=$(echo "$rtts" | wc -l)
      p50=$(echo "$rtts" | awk -v n="$n" 'NR==int(n*0.50)+0 {print; exit}')
      p99=$(echo "$rtts" | awk -v n="$n" 'NR==int(n*0.99)+0 {print; exit}')
      [[ -z "$p50" ]] && p50="null"
      [[ -z "$p99" ]] && p99="null"
    fi
  fi

  jq -n --arg id "$id" \
     --argjson mbps "$(jnum "$mbps")" \
     --argjson ttfb "$(jnum "$ttfb_ms")" \
     --argjson dns_first "$(jnum "$dns_first")" \
     --argjson dns_warm "$(jnum "$dns_warm_p50")" \
     --argjson p50 "$(jnum "$p50")" \
     --argjson p99 "$(jnum "$p99")" \
     --argjson loss "$(jnum "$loss")" \
     --argjson ok "$([[ "$code" == "200" ]] && echo true || echo false)" \
     '{id: $id, reachable: $ok, mbps: $mbps, ttfb_ms: $ttfb,
       dns_first_ms: $dns_first, dns_warm_p50_ms: $dns_warm,
       rtt_p50_ms: $p50, rtt_p99_ms: $p99, loss_pct: $loss}'
}

RESULTS="[]"
while IFS=$'\t' read -r id url phost; do
  [[ -z "$id" ]] && continue
  R=$(measure_target "$id" "$url" "$phost")
  RESULTS=$(jq -n --argjson a "$RESULTS" --argjson b "$R" '$a + [$b]')
done < <(echo "$CFG" | jq -r '.targets[] | [.id, .url, .ping_host] | @tsv')

# --- Ookla (optional context) ----------------------------------------------
OOKLA="null"
if [[ "$WITH_OOKLA" == "1" ]]; then
  if command -v speedtest >/dev/null 2>&1; then
    log "Ookla speedtest (nearest server - context only, not comparable)"
    OUT=$(speedtest --format=json --accept-license --accept-gdpr 2>/dev/null) || OUT=""
    if [[ -n "$OUT" ]]; then
      OOKLA=$(echo "$OUT" | jq '{
        server: ((.server.name // "?") + " (" + (.server.location // "?") + ")"),
        down_mbps: ((.download.bandwidth // 0) * 8 / 1000000 | .*100|round/100),
        up_mbps:   ((.upload.bandwidth // 0) * 8 / 1000000 | .*100|round/100),
        idle_latency_ms: (.ping.latency // null),
        jitter_ms: (.ping.jitter // null),
        loss_pct: (.packetLoss // null)
      }' 2>/dev/null || echo null)
    else
      warn "speedtest failed or was not accepted"
    fi
  else
    warn "--with-ookla given but 'speedtest' not installed; see README"
  fi
fi

emit_json network "$(jq -n \
  --arg lv "$LIST_VERSION" \
  --argjson t "$RESULTS" \
  --argjson o "$OOKLA" \
  '{network: {
      reachable: true,
      target_list_version: $lv,
      targets: $t,
      ookla: $o
  }}')"

echo
echo "=== Network (informational, no verdict) ==="
echo "$RESULTS" | jq -r '.[] |
  "\(.id): \(if .reachable then "\(.mbps // "-") Mbit/s  rtt p50=\(.rtt_p50_ms // "-")ms p99=\(.rtt_p99_ms // "-")ms  loss=\(.loss_pct // "-")%" else "unreachable" end)"'
if [[ "$OOKLA" != "null" ]]; then
  echo
  echo "$OOKLA" | jq -r '"ookla (\(.server)): down \(.down_mbps) / up \(.up_mbps) Mbit/s, loss \(.loss_pct // "-")%"'
fi
echo
echo "Fixed targets are comparable across hosts. Ookla picks a nearby server and"
echo "is not comparable - it is context, not a measurement of this provider."