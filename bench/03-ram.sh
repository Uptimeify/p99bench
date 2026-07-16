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

mem() {
  local oper="$1" mode="$2" bs="$3" threads="$4" total="$5"
  sysbench memory --memory-block-size="$bs" --memory-total-size="$total" \
    --memory-oper="$oper" --memory-access-mode="$mode" --threads="$threads" run 2>/dev/null \
    | awk '/MiB\/sec/ {gsub(/[()]/,"",$4); print $4; exit}'
}

# sysbench's --memory-block-size IS the per-thread working set, so a block
# that fits in cache measures cache. The old 1M block reported 207 GB/s on a
# single-channel DDR5 host whose theoretical peak is ~38 GB/s - that was L2
# bandwidth wearing a RAM label, and it is why the >=15 GB/s threshold never
# failed anything. llc_bytes() (in lib.sh) reads the real LLC size from
# sysfs so we can size around it instead.
LLC=$(llc_bytes)
# 4x LLC so the working set cannot be held even with a generous replacement
# policy, floored at 128M for hosts that under-report cache. Capped so that
# BLOCK * threads stays under a quarter of RAM - this must not swap, and a
# swapping run measures the disk.
RAM_BYTES="${P99_RAM_BYTES:-$(awk '/MemTotal/ {print $2 * 1024; exit}' /proc/meminfo)}"
BLOCK=$((LLC * 4))
(( BLOCK < 134217728 )) && BLOCK=134217728
MAX_BLOCK=$((RAM_BYTES / 4 / CORES))
(( BLOCK > MAX_BLOCK )) && BLOCK=$MAX_BLOCK

# The RAM cap above exists so the run cannot swap, but on a small/many-core
# host (8 vCPU / 2G is a common budget VPS) it can shrink BLOCK back down
# until it fits in cache again - silently recreating the exact bug this
# script exists to fix, just via the cap instead of the original hardcoded
# 1M. A block that cannot clear at least 2x LLC is measuring cache no matter
# how it got small, so refuse to report it as RAM bandwidth: null the metric
# and warn loudly, while still emitting bw_block_bytes so the reader can see
# what was attempted (doctrine in lib.sh: null loses one metric, a wrong
# number loses the whole run's credibility).
CACHE_CLEAR_FLOOR=$((LLC * 2))

# Total bytes moved. Must be well above BLOCK*threads or the run is over
# before the memory subsystem reaches steady state.
RAM_TOTAL="${P99_RAM_TOTAL:-50G}"

if (( BLOCK < CACHE_CLEAR_FLOOR )); then
  warn "RAM: working set capped to ${BLOCK}B/thread (RAM_BYTES=${RAM_BYTES}, CORES=${CORES}), below 2x LLC (${CACHE_CLEAR_FLOOR}B) - this host shape cannot clear cache within a safe (non-swapping) working set. Recording bw_read_mbs as null instead of reporting cache bandwidth as RAM bandwidth."
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
