#!/usr/bin/env bash
# 02-cpu.sh - single/multi core throughput, clock under load, steal time.
# Emits cpu{} fragment (stall percentiles are added by 05-latency.sh).
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need sysbench
need jq

CORES=$(nproc)

# Container/CI runs only need to prove the parsers work. A real run must not
# use this - 5s of sysbench measures noise.
SB_TIME=30
STRESS_TIME=65
# mpstat sample count (1s per sample). Also gated by P99_CPU_QUICK below --
# it is not scaled by SB_TIME/STRESS_TIME, so it needs its own cut or a
# "quick" run still takes ~50s on this line alone.
STEAL_SAMPLES=50
if [[ -n "${P99_CPU_QUICK:-}" ]]; then
  warn "P99_CPU_QUICK set - runtimes cut to seconds. NOT a measurement."
  SB_TIME=2
  STRESS_TIME=6
  STEAL_SAMPLES=3
fi

log "CPU: single thread"
ST=$(sysbench cpu --cpu-max-prime=20000 --threads=1 --time="$SB_TIME" run 2>/dev/null \
     | awk '/events per second/ {print $4}')

log "CPU: $CORES threads"
MT=$(sysbench cpu --cpu-max-prime=20000 --threads="$CORES" --time="$SB_TIME" run 2>/dev/null \
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
  stress-ng --cpu "$CORES" --timeout "${STRESS_TIME}s" >/dev/null 2>&1 &
else
  warn "stress-ng missing, using sysbench to generate load"
  sysbench cpu --cpu-max-prime=20000 --threads="$CORES" --time="$STRESS_TIME" run >/dev/null 2>&1 &
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
  STEAL=$(mpstat 1 "$STEAL_SAMPLES" 2>/dev/null | awk '
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

# TLS sign/verify rate. SSL/HTTPS checks are bound by the asymmetric
# handshake, not by bulk cipher throughput - a probe node opens a new
# connection per check and almost never transfers enough bytes for
# aes_256_gcm_mbs to matter. P-256 because it is what essentially every
# modern certificate uses.
#
# Single-threaded on purpose: this feeds worker_probe, where the question is
# how fast one check can complete, not how many cores can be thrown at it.
#
# A worker_probe is the TLS *client*. In a standard (non-mTLS) handshake the
# SERVER signs and the CLIENT verifies -- and the client verifies more than
# once: the server's CertificateVerify, plus each signature in the presented
# chain. So verification, not signing, is the work a probe actually pays
# for. It is also the SLOWER half, which reverses the RSA intuition: ECDSA
# sign is one scalar multiplication, verify is two, so verify runs at
# roughly a third of sign's rate (measured: sign 104980/s vs verify 34887/s
# on P-256). A future reader must NOT "simplify" this back to sign/s -- that
# is precisely the bug this rewrite fixes; see the invariant test in
# tests/test_stages.py (tls_verify_s < tls_sign_s on every platform).
#
# openssl prints one line for both, sign before verify, regardless of which
# is faster:
#   sign    verify    sign/s verify/s
#   0.0000s 0.0000s   98131.3  34834.0
# On the verified layout (NF=8): $(NF-1) is sign/s, $NF is verify/s.
TLS_SIGN=""
TLS_VERIFY=""
if command -v openssl >/dev/null 2>&1; then
  log "CPU: ECDSA P-256 sign/verify (SSL check rate)"
  TLS_OUT=$(openssl speed -seconds 3 ecdsap256 2>/dev/null)
  TLS_SIGN=$(awk '/^ *256 bits ecdsa \(nistp256\)/ {print $(NF-1); exit}' <<<"$TLS_OUT")
  TLS_VERIFY=$(awk '/^ *256 bits ecdsa \(nistp256\)/ {print $NF; exit}' <<<"$TLS_OUT")
  # Label and column layout have both moved across OpenSSL 1.1/3.x. Fall back
  # to the generic ecdsa row rather than silently recording null.
  if [[ -z "$TLS_SIGN" || -z "$TLS_VERIFY" ]]; then
    TLS_SIGN=$(awk '/ecdsa/ && /nistp256/ {print $(NF-1); exit}' <<<"$TLS_OUT")
    TLS_VERIFY=$(awk '/ecdsa/ && /nistp256/ {print $NF; exit}' <<<"$TLS_OUT")
  fi
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
  --argjson tlsv "$(jnum "$TLS_VERIFY")" \
  --argjson tlss "$(jnum "$TLS_SIGN")" \
  '{cpu: {
      single_thread_eps: $st,
      multi_thread_eps: $mt,
      scaling_efficiency: $scale,
      clock_idle_mhz: $ci,
      clock_under_load_mhz: $cl,
      steal_pct_under_load: $steal,
      aes_256_gcm_mbs: $aes,
      sha256_mbs: $sha,
      tls_verify_s: $tlsv,
      tls_sign_s: $tlss
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
  "aes-256-gcm:     \(.aes_256_gcm_mbs // "n/a") MB/s   (context: absent AES-NI would be remarkable)",
  "tls verify:      \(.tls_verify_s // "n/a") /s   (ECDSA P-256, 1 thread -- what a probe pays)",
  "tls sign:        \(.tls_sign_s // "n/a") /s   (ECDSA P-256, 1 thread -- context only)",
  "sha-256:         \(.sha256_mbs // "n/a") MB/s"
' "$P99_WORK/frag-cpu.json"