#!/usr/bin/env bash
# 02-cpu.sh - single/multi core throughput, clock under load, steal time.
# Emits cpu{} fragment (intrinsic latency is added by 05-latency.sh).
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
  SCALE=$(echo "scale=3; $MT / ($ST * $CORES)" | bc 2>/dev/null || echo "")
fi

CLOCK_IDLE=$(awk '/cpu MHz/ {s+=$4; n++} END {if(n) printf "%.0f", s/n}' /proc/cpuinfo 2>/dev/null)

log "CPU: clock + steal under full load (60s)"
if command -v stress-ng >/dev/null 2>&1; then
  stress-ng --cpu "$CORES" --timeout 65s >/dev/null 2>&1 &
else
  warn "stress-ng missing, using sysbench to generate load"
  sysbench cpu --cpu-max-prime=20000 --threads="$CORES" --time=65 run >/dev/null 2>&1 &
fi
STRESS_PID=$!
sleep 5

CLOCK_LOAD=$(awk '/cpu MHz/ {s+=$4; n++} END {if(n) printf "%.0f", s/n}' /proc/cpuinfo 2>/dev/null)

# %steal = time the vCPU was runnable but the hypervisor scheduled someone else.
# It is the clearest single signal of an oversubscribed host, and it only shows
# up under load - an idle VM steals nothing.
#
# mpstat's column layout shifts between sysstat versions, so find %steal by its
# header name rather than by position.
STEAL=""
if command -v mpstat >/dev/null 2>&1; then
  STEAL=$(mpstat 1 50 2>/dev/null | awk '
    /%steal/ && !col { for (i = 1; i <= NF; i++) if ($i == "%steal") col = i - 1 }
    /^Average/ && col { print $col; exit }
  ')
else
  warn "mpstat missing (apt install sysstat) - steal time not measured"
fi
wait "$STRESS_PID" 2>/dev/null || true

# TLS termination throughput. The row is "AES-256-GCM" in upper case and the
# last column is the largest block size, which is the number that matters for
# bulk transfer. Match case-insensitively: this label has changed case across
# OpenSSL releases and a missed match used to poison the whole fragment.
AES=""
if command -v openssl >/dev/null 2>&1; then
  log "CPU: AES-256-GCM (TLS termination)"
  AESK=$(openssl speed -evp aes-256-gcm -multi "$CORES" -seconds 3 2>/dev/null \
         | awk 'toupper($1) ~ /^AES-256-GCM/ {v=$NF} END {if (v) {gsub(/k$/,"",v); print v}}')
  [[ -n "$AESK" ]] && AES=$(echo "scale=2; $AESK / 1000" | bc 2>/dev/null || echo "")
fi

# SHA-256 throughput: proof-of-work workloads such as Anubis.
#
# This used to call `stress-ng --cpu-method sha256`, which does not exist -
# stress-ng has no SHA-256 method at all. openssl does, and is already required
# above, so use it.
SHA=""
if command -v openssl >/dev/null 2>&1; then
  log "CPU: SHA-256 (proof-of-work, e.g. Anubis)"
  SHAK=$(openssl speed -multi "$CORES" -seconds 3 sha256 2>/dev/null \
         | awk 'toupper($1) ~ /^SHA256/ {v=$NF} END {if (v) {gsub(/k$/,"",v); print v}}')
  [[ -n "$SHAK" ]] && SHA=$(echo "scale=2; $SHAK / 1000" | bc 2>/dev/null || echo "")
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
      sha256_mbs: $sha
  }}')"

echo
echo "=== CPU summary ==="
jq -r --arg cores "$CORES" '.cpu |
  "single thread:   \(.single_thread_eps // "n/a") eps",
  "\($cores) threads:       \(.multi_thread_eps // "n/a") eps",
  "scaling eff:     \(.scaling_efficiency // "n/a")   (<0.6 = SMT siblings or oversubscribed host)",
  "clock idle:      \(.clock_idle_mhz // "n/a") MHz",
  "clock at load:   \(.clock_under_load_mhz // "n/a") MHz",
  "steal at load:   \(.steal_pct_under_load // "n/a") %   (>5 = host is oversold)",
  "aes-256-gcm:     \(.aes_256_gcm_mbs // "n/a") MB/s",
  "sha-256:         \(.sha256_mbs // "n/a") MB/s"
' "$P99_WORK/frag-cpu.json"