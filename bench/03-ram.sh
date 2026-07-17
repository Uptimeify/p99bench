#!/usr/bin/env bash
# 03-ram.sh - memory bandwidth, random access, NUMA locality.
# Emits ram{} fragment.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need sysbench
need jq

# P99_CORES/P99_RAM_BYTES are injection seams for tests: they let a test
# simulate a host shape (e.g. 8 vCPU / 2 GiB) without needing that actual
# hardware. Real runs never set them, so they read the real host.
CORES="${P99_CORES:-$(nproc)}"

SPEED=$(dmidecode -t memory 2>/dev/null | awk -F: '/Configured Memory Speed/{gsub(/^ +/,"",$2); print $2; exit}' || echo "")
MTYPE=$(dmidecode -t memory 2>/dev/null | awk -F: '/^\tType:/{gsub(/^ +/,"",$2); print $2; exit}' || echo "")
SLOTS=$(dmidecode -t memory 2>/dev/null | grep -c "Size: [0-9]" || echo "")

# 2>/dev/null here cost three full re-runs across three hosts: sysbench
# FATALs on an illegal --memory-block-size, prints no "MiB/sec" line, and the
# metric nulled with no trace of why. A null is the right OUTCOME (doctrine:
# null loses one metric, garbage loses the run) but it must never be a silent
# one -- the run log is the only evidence a remote host leaves behind.
mem() {
  local oper="$1" mode="$2" bs="$3" threads="$4" total="$5" out val
  if ! out=$(sysbench memory --memory-block-size="$bs" --memory-total-size="$total" \
      --memory-oper="$oper" --memory-access-mode="$mode" --threads="$threads" run 2>&1); then
    warn "sysbench memory failed (oper=$oper mode=$mode block=$bs threads=$threads total=$total): $(printf '%s' "$out" | grep -iE 'fatal|error' | head -2 | tr '\n' ' ')"
    return 0
  fi
  val=$(printf '%s\n' "$out" | awk '/MiB\/sec/ {gsub(/[()]/,"",$4); print $4; exit}')
  if [[ -z "$val" ]]; then
    warn "sysbench memory (oper=$oper block=$bs) exited 0 but printed no MiB/sec line - output format changed?: $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
  fi
  printf '%s' "$val"
}

# sysbench's --memory-block-size IS the per-thread working set, so a block
# that fits in cache measures cache. The old 1M block reported 207 GB/s on a
# single-channel DDR5 host whose theoretical peak is ~38 GB/s - that was L2
# bandwidth wearing a RAM label, and it is why the >=15 GB/s threshold never
# failed anything. llc_bytes() (in lib.sh) reads the real LLC size from
# sysfs so we can size around it instead.
LLC=$(llc_bytes)
RAM_BYTES="${P99_RAM_BYTES:-$(awk '/MemTotal/ {print $2 * 1024; exit}' /proc/meminfo)}"
# Sizing policy (4x LLC, 512M floor, power-of-two, capped at RAM/2) lives in
# ram_block_bytes() in lib.sh, with the reasoning behind each step. It is
# there rather than here because every test of this script needs a container
# and CI runs -m "not docker" -- so this arithmetic, inline, was never gated
# by CI, and shipped a block sysbench rejects outright to three real hosts.
BLOCK=$(ram_block_bytes "$LLC" "$CORES" "$RAM_BYTES")

# The RAM cap inside ram_block_bytes() exists so the run cannot swap, but on
# a small/many-core host (8 vCPU / 2G is a common budget VPS) it shrinks BLOCK
# until it fits in cache again - silently recreating the exact bug this
# script exists to fix, just via the cap instead of the original hardcoded
# 1M. A block that cannot clear at least 2x LLC is measuring cache no matter
# how it got small, so refuse to report it as RAM bandwidth: null the metric
# and warn loudly, while still emitting bw_block_bytes so the reader can see
# what was attempted (doctrine in lib.sh: null loses one metric, a wrong
# number loses the whole run's credibility).
# Compare the TOTAL working set against the LLC, not one thread's block.
# sysbench allocates BLOCK per thread and every thread streams its own buffer,
# so what the cache sees is BLOCK * threads. Comparing BLOCK alone against a
# TOTAL L3 mixes units, and it nulled a perfectly good measurement: an OVH VPS
# (256 MB L3 across 4 instances, 8 GB RAM -> cap 496 MB/thread) reported n/a
# because 496 MB < 2 * 256 MB -- while its actual working set was 4 x 496 MB =
# 1,987 MB, nearly 8x the L3 and comfortably clear of it.
CACHE_CLEAR_FLOOR=$((LLC * 2))
WORKING_SET=$((BLOCK * CORES))

# Total bytes moved. Must be well above BLOCK*threads or the run is over
# before the memory subsystem reaches steady state.
RAM_TOTAL="${P99_RAM_TOTAL:-50G}"

if (( BLOCK == 0 )); then
  warn "RAM: no legal sysbench block exists for this host shape (LLC ${LLC}B, ${CORES} threads, RAM_BYTES=${RAM_BYTES}) - recording bw_read_mbs as null."
  BW_R=""
elif (( WORKING_SET < CACHE_CLEAR_FLOOR )); then
  warn "RAM: total working set ${WORKING_SET}B (${BLOCK}B/thread x ${CORES} threads, capped by RAM_BYTES=${RAM_BYTES}) is below 2x LLC (${CACHE_CLEAR_FLOOR}B) - this host shape cannot clear cache within a safe (non-swapping) working set. Recording bw_read_mbs as null instead of reporting cache bandwidth as RAM bandwidth."
  BW_R=""
else
  log "RAM: bandwidth (working set ${BLOCK}B/thread, LLC ${LLC}B)"
  BW_R=$(mem read seq "$BLOCK" "$CORES" "$RAM_TOTAL")
fi

log "RAM: sequential read"
SEQ_R=$(mem read seq 1M "$CORES" 100G)
log "RAM: sequential write"
SEQ_W=$(mem write seq 1M "$CORES" 100G)
# 8k random is far closer to how a database actually touches its buffer pool
# than a 1M sequential sweep, which mostly measures prefetchers.
log "RAM: random read 8k"
RND_R=$(mem read rnd 8k "$CORES" 50G)
log "RAM: random write 8k"
RND_W=$(mem write rnd 8k "$CORES" 50G)

NUMA_L=null
NUMA_R=null
NODES=$(numactl --hardware 2>/dev/null | awk '/^available:/ {print $2}' || echo 1)
if [[ "${NODES:-1}" -gt 1 ]]; then
  log "RAM: NUMA local vs remote"
  NUMA_L=$(numactl --cpunodebind=0 --membind=0 \
    sysbench memory --memory-total-size=20G --threads=4 run 2>/dev/null \
    | awk '/MiB\/sec/ {gsub(/[()]/,"",$4); print $4; exit}')
  NUMA_R=$(numactl --cpunodebind=0 --membind=1 \
    sysbench memory --memory-total-size=20G --threads=4 run 2>/dev/null \
    | awk '/MiB\/sec/ {gsub(/[()]/,"",$4); print $4; exit}')
else
  log "Single NUMA node, skipping locality test"
fi

emit_json ram "$(jq -n \
  --arg speed "$SPEED" --arg mtype "$MTYPE" \
  --argjson slots "$(jnum "$SLOTS")" \
  --argjson sr "$(jnum "$SEQ_R")" --argjson sw "$(jnum "$SEQ_W")" \
  --argjson rr "$(jnum "$RND_R")" --argjson rw "$(jnum "$RND_W")" \
  --argjson nl "$(jnum "$NUMA_L")" --argjson nr "$(jnum "$NUMA_R")" \
  --argjson bwr "$(jnum "$BW_R")" \
  --argjson block "$(jnum "$BLOCK")" \
  '{ram: {
      configured_speed: (if $speed == "" then null else $speed end),
      type: (if $mtype == "" then null else $mtype end),
      populated_slots: $slots,
      bw_read_mbs: $bwr,
      bw_block_bytes: $block,
      seq_read_mbs: $sr,
      seq_write_mbs: $sw,
      rnd_read_mbs: $rr,
      rnd_write_mbs: $rw,
      numa_local_mbs: $nl,
      numa_remote_mbs: $nr
  }}')"

echo
echo "=== RAM summary ==="
jq -r '.ram |
  "reported:     \(.type // "?") @ \(.configured_speed // "?"), \(.populated_slots // "?") slots",
  "bandwidth:    \(.bw_read_mbs // "n/a") MiB/s   (working set \(.bw_block_bytes // "?")B/thread)",
  "seq read:     \(.seq_read_mbs // "n/a") MiB/s   (legacy: 1M block, cache-resident, not banded)",
  "seq write:    \(.seq_write_mbs // "n/a") MiB/s   (legacy)",
  "rnd read 8k:  \(.rnd_read_mbs // "n/a") MiB/s   (legacy: 8k block, L1-resident, not banded)",
  "rnd write 8k: \(.rnd_write_mbs // "n/a") MiB/s   (legacy)",
  "numa local:   \(.numa_local_mbs // "n/a") MiB/s",
  "numa remote:  \(.numa_remote_mbs // "n/a") MiB/s"
' "$P99_WORK/frag-ram.json"
echo
echo "Reference: DDR4-3200 dual channel ~35-45 GB/s. DDR5-4800 dual ~60-70 GB/s."
echo "A DDR4-3200 host delivering 12 GB/s is running single channel."
echo "bw_read_mbs above ~100 GB/s means the working set is still in cache - file a bug."
