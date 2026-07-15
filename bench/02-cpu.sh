#!/usr/bin/env bash
# 02-cpu.sh - single/multi core throughput, clock under load, steal time.
# Emits cpu{} fragment (partial; intrinsic latency comes from 05-latency.sh).
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need sysbench
need jq

CORES=$(nproc)

log "CPU: single thread"
ST=$(sysbench cpu --cpu-max-prime=20000 --threads=1 --time=30 run 2>/dev/null \
     | awk '/events per second/ {print $4}')

log "CPU: $CORES threads"
MT=$(sysbench cpu --cpu-max-prime=20000 --threads="$CORES" --time=30 run 2>/dev/null \
     | awk '/events per second/ {print $4}')

# Perfect scaling would be single * cores. Real silicon gives ~0.9 on physical
# cores, ~0.6 with SMT siblings, and much less when the host is oversubscribed.
SCALE=null
if [[ -n "$ST" && -n "$MT" ]]; then
  SCALE=$(echo "scale=3; $MT / ($ST * $CORES)" | bc 2>/dev/null || echo null)
fi

CLOCK_IDLE=$(awk '/cpu MHz/ {s+=$4; n++} END {if(n) printf "%.0f", s/n}' /proc/cpuinfo 2>/dev/null)

log "CPU: clock + steal under full load (60s)"
if command -v stress-ng >/dev/null 2>&1; then
  stress-ng --cpu "$CORES" --timeout 65s >/dev/null 2>&1 &
  STRESS_PID=$!
else
  warn "stress-ng missing, using sysbench to generate load"
  sysbench cpu --cpu-max-prime=20000 --threads="$CORES" --time=65 run >/dev/null 2>&1 &
  STRESS_PID=$!
fi
sleep 5

CLOCK_LOAD=$(awk '/cpu MHz/ {s+=$4; n++} END {if(n) printf "%.0f", s/n}' /proc/cpuinfo 2>/dev/null)

# %steal = time the vCPU was runnable but the hypervisor scheduled someone else.
# It is the single clearest signal of an oversubscribed host.
STEAL=null
if command -v mpstat >/dev/null 2>&1; then
  STEAL=$(mpstat 1 50 2>/dev/null | awk '/Average.*all/ {print $(NF-1)}')
fi
wait "$STRESS_PID" 2>/dev/null || true

AES=null
if command -v openssl >/dev/null 2>&1; then
  log "CPU: AES-256-GCM (TLS termination)"
  # Last line of openssl speed is the 8192-byte block column, in 1000s of bytes/s.
  AESK=$(openssl speed -evp aes-256-gcm -multi "$CORES" -seconds 3 2>/dev/null \
         | awk '/^aes-256-gcm/ {print $NF}' | tail -1 | tr -d 'k')
  [[ -n "$AESK" ]] && AES=$(echo "scale=2; $AESK / 1000" | bc 2>/dev/null || echo null)
fi

SHA=null
if command -v stress-ng >/dev/null 2>&1; then
  log "CPU: sha256 (proof-of-work, e.g. Anubis)"
  SHA=$(stress-ng --cpu "$CORES" --cpu-method sha256 --metrics-brief --timeout 20s 2>&1 \
        | awk '/cpu/ && /[0-9]/ {print $5}' | tail -1)
fi

emit_json cpu "$(jq -n \
  --argjson st "$(jnum "$ST")" \
  --argjson mt "$(jnum "$MT")" \
  --argjson scale "$(jnum "$SCALE")" \
  --argjson ci "$(jnum "$CLOCK_IDLE")" \
  --argjson cl "$(jnum "$CLOCK_LOAD")" \
  --argjson steal "$(jnum "$STEAL")" \
  --argjson aes "$(jnum "$AES")" \
  --argjson sha "$(jnum "$SHA")" \
  '{cpu: {
      single_thread_eps: $st,
      multi_thread_eps: $mt,
      scaling_efficiency: $scale,
      clock_idle_mhz: $ci,
      clock_under_load_mhz: $cl,
      steal_pct_under_load: $steal,
      aes_256_gcm_mbs: $aes,
      sha256_bogo_ops: $sha
  }}')"

echo
echo "=== CPU summary ==="
jq -r '.cpu |
  "single thread:   \(.single_thread_eps // "n/a") eps",
  "\(env.CORES // "all") threads:     \(.multi_thread_eps // "n/a") eps",
  "scaling eff:     \(.scaling_efficiency // "n/a")   (<0.6 = SMT siblings or oversubscribed host)",
  "clock idle:      \(.clock_idle_mhz // "n/a") MHz",
  "clock at load:   \(.clock_under_load_mhz // "n/a") MHz",
  "steal at load:   \(.steal_pct_under_load // "n/a") %   (>5 = host is oversold)",
  "aes-256-gcm:     \(.aes_256_gcm_mbs // "n/a") MB/s",
  "sha256:          \(.sha256_bogo_ops // "n/a") bogo-ops/s"
' "$P99_WORK/frag-cpu.json"
