#!/usr/bin/env bash
# 01-disk.sh - disk IOPS, bandwidth and (crucially) fsync latency.
# Emits disk{} fragment.
#
# The headline number here is wal_fsync.p999_us. Everything else is context.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need fio
need jq

TARGET="$P99_TARGET"
mkdir -p "$TARGET" || die "cannot create $TARGET"

# --- preflight -------------------------------------------------------------
# numjobs=4 * P99_SIZE is the real footprint. Getting ENOSPC halfway through
# silently corrupts the numbers, so refuse up front.
SIZE_G=${P99_SIZE%G}
NEED_G=$(( SIZE_G * 4 + 4 ))
AVAIL_G=$(df -BG --output=avail "$TARGET" | tail -1 | tr -dc '0-9')
if (( AVAIL_G < NEED_G )); then
  die "need ${NEED_G}G free at $TARGET, have ${AVAIL_G}G. Lower P99_SIZE or free space."
fi

RAM_MB=$(awk '/^MemTotal:/ {printf "%d", $2/1024}' /proc/meminfo)
FOOTPRINT_MB=$(( SIZE_G * 4 * 1024 ))
if (( FOOTPRINT_MB < RAM_MB * 2 )); then
  warn "fio footprint ${FOOTPRINT_MB}MB < 2x RAM (${RAM_MB}MB). Page cache may flatter results despite --direct=1."
fi

FS=$(findmnt -no FSTYPE --target "$TARGET" 2>/dev/null || echo "unknown")
SRC=$(findmnt -no SOURCE --target "$TARGET" 2>/dev/null || echo "")
DEV=$(lsblk -no PKNAME "$SRC" 2>/dev/null | head -1 || echo "")
[[ -z "$DEV" ]] && DEV=$(basename "${SRC:-unknown}" | sed 's/[0-9]*$//')
MODEL=$(lsblk -dno MODEL "/dev/$DEV" 2>/dev/null | xargs || echo "")
SCHED=$(grep -o '\[.*\]' "/sys/block/$DEV/queue/scheduler" 2>/dev/null | tr -d '[]' || echo "")
ROTA=$(lsblk -dno ROTA "/dev/$DEV" 2>/dev/null | xargs || echo "")
BOOTDEV=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null | head -1 || echo "")
IS_BOOT=$([[ "$DEV" == "$BOOTDEV" ]] && echo true || echo false)

log "Target $TARGET on /dev/$DEV ($FS), ${AVAIL_G}G free, boot volume: $IS_BOOT"

# --- helpers ---------------------------------------------------------------

# fio_run <name> <extra fio args...>
# Writes /tmp/p99bench/fio-<name>.json, cleans up data files afterwards so the
# next phase starts from a known free-space state.
# FIO_IOENGINE overrides the default libaio for one call (the QD1 read job
# uses psync). Set per-call rather than passed through "$@" so there is never
# a duplicate --ioengine on the command line whose winner depends on fio's
# argument-parsing order.
fio_run() {
  local name="$1"; shift
  log "fio: $name"
  fio --name="$name" --directory="$TARGET" --size="$P99_SIZE" --direct=1 \
      --ioengine="${FIO_IOENGINE:-libaio}" --group_reporting --runtime="$P99_RUNTIME" --time_based \
      --output-format=json "$@" > "$P99_WORK/fio-$name.json" 2>"$P99_WORK/fio-$name.err" \
      || warn "fio $name exited non-zero, see $P99_WORK/fio-$name.err"
  rm -f "$TARGET"/*
}

# Extract one direction (read|write) of a fio job into our ioResult shape.
# Percentiles are absent for the unused direction, hence the // null guards.
extract() {
  local file="$1" dir="$2"
  jq --arg d "$dir" '
    (.jobs[0][$d]) as $x |
    if ($x.iops // 0) > 0 then {
      iops:   ($x.iops    | . * 100 | round / 100),
      bw_mbs: ($x.bw/1024 | . * 100 | round / 100),
      p50_us: (($x.clat_ns.percentile."50.000000" // null) | if . == null then null else ./1000 | .*100|round/100 end),
      p99_us: (($x.clat_ns.percentile."99.000000" // null) | if . == null then null else ./1000 | .*100|round/100 end),
      p999_us:(($x.clat_ns.percentile."99.900000" // null) | if . == null then null else ./1000 | .*100|round/100 end)
    } else null end
  ' "$file" 2>/dev/null || echo null
}

# --- phases ----------------------------------------------------------------

# Sequential: restore, pg_basebackup, Timescale chunk compression, seq scans.
fio_run seq-read  --rw=read  --bs=1M --iodepth=32 --numjobs=1
fio_run seq-write --rw=write --bs=1M --iodepth=32 --numjobs=1

# Random 8k = Postgres page size. QD32 across 4 jobs approximates a busy pool.
# NOTE what this job's p99 is and is not. At a fixed queue depth, Little's law
# ties latency to throughput: latency ~= QD/IOPS, here 128/IOPS. Measured
# across 13 runs, rand_read_8k.p99_us / (128/IOPS) had a median of 1.08 and sat
# within 7% of 1.0 on every OVH host -- i.e. this p99 mostly restates the IOPS
# number. It is a queuing delay under saturation, NOT what one index lookup
# costs, so it is emitted for context and no longer graded. The QD1 job below
# answers the latency question.
fio_run rand-read-8k  --rw=randread  --bs=8k --iodepth=32 --numjobs=4
fio_run rand-write-8k --rw=randwrite --bs=8k --iodepth=32 --numjobs=4
fio_run mixed-8k --rw=randrw --rwmixread=70 --bs=8k --iodepth=32 --numjobs=4

# QD1 random read: what ONE index lookup costs, with no queue to hide behind.
# The companion to wal_fsync and measured the same way -- psync because that is
# how Postgres reads (pread), iodepth=1/numjobs=1 because a query waiting on a
# buffer-pool miss has nothing else outstanding to amortise against. This is
# the metric disk.rand_read_8k_qd1.p99_us bands; the QD128 job above cannot
# answer that question at any threshold, because its latency is pinned to its
# own throughput.
FIO_IOENGINE=psync fio_run rand-read-8k-qd1 --rw=randread --bs=8k --iodepth=1 --numjobs=1

# THE test: QD1 durable write. This is what a COMMIT actually does.
# psync + fdatasync=1 + sync=1, single job, no queue depth to hide behind.
# Consumer SSDs and throttled network storage die here while looking fine above.
log "fio: wal-sync (the one that matters)"
fio --name=wal-sync --directory="$TARGET" --size=1G --direct=1 --sync=1 \
    --ioengine=psync --rw=write --bs=8k --iodepth=1 --numjobs=1 \
    --runtime="$P99_RUNTIME" --time_based --fdatasync=1 \
    --output-format=json > "$P99_WORK/fio-wal.json" 2>"$P99_WORK/fio-wal.err" \
    || warn "fio wal-sync exited non-zero"
rm -f "$TARGET"/*

WAL=$(jq '
  .jobs[0].write as $w |
  {
    iops:    ($w.iops | .*100|round/100),
    avg_us:  (($w.clat_ns.mean // null) | if . == null then null else ./1000|.*100|round/100 end),
    p50_us:  (($w.clat_ns.percentile."50.000000" // null) | if . == null then null else ./1000|.*100|round/100 end),
    p99_us:  (($w.clat_ns.percentile."99.000000" // null) | if . == null then null else ./1000|.*100|round/100 end),
    p999_us: (($w.clat_ns.percentile."99.900000" // null) | if . == null then null else ./1000|.*100|round/100 end),
    max_us:  (($w.clat_ns.max // null) | if . == null then null else ./1000|.*100|round/100 end)
  }' "$P99_WORK/fio-wal.json" 2>/dev/null || echo '{"iops":null,"p999_us":null}')

emit_json disk "$(jq -n \
  --arg model "$MODEL" --arg sched "$SCHED" --arg fs "$FS" \
  --argjson is_boot "$IS_BOOT" \
  --argjson rota "$([[ "$ROTA" == "1" ]] && echo true || echo false)" \
  --argjson seq_read  "$(extract "$P99_WORK/fio-seq-read.json" read)" \
  --argjson seq_write "$(extract "$P99_WORK/fio-seq-write.json" write)" \
  --argjson rr "$(extract "$P99_WORK/fio-rand-read-8k.json" read)" \
  --argjson rr1 "$(extract "$P99_WORK/fio-rand-read-8k-qd1.json" read)" \
  --argjson rw "$(extract "$P99_WORK/fio-rand-write-8k.json" write)" \
  --argjson mx "$(extract "$P99_WORK/fio-mixed-8k.json" read)" \
  --argjson wal "$WAL" \
  '{disk: {
      device_model: (if $model == "" then null else $model end),
      scheduler: (if $sched == "" then null else $sched end),
      rotational: $rota,
      target_fs: $fs,
      is_boot_volume: $is_boot,
      seq_read: (if $seq_read == null then null else {bw_mbs: $seq_read.bw_mbs, iops: $seq_read.iops, p99_us: $seq_read.p99_us} end),
      seq_write: (if $seq_write == null then null else {bw_mbs: $seq_write.bw_mbs, iops: $seq_write.iops, p99_us: $seq_write.p99_us} end),
      rand_read_8k: $rr,
      rand_read_8k_qd1: $rr1,
      rand_write_8k: $rw,
      mixed_8k: $mx,
      wal_fsync: $wal
  }}')"

rm -rf "$TARGET"

# --- human summary ---------------------------------------------------------
echo
echo "=== Disk summary ==="
jq -r '.disk |
  "seq-read:      \(.seq_read.bw_mbs // "n/a") MB/s",
  "seq-write:     \(.seq_write.bw_mbs // "n/a") MB/s",
  "rand-read-8k:  \(.rand_read_8k.iops // "n/a") IOPS  p99=\(.rand_read_8k.p99_us // "n/a")us  (QD128: a queuing delay, not graded)",
  "rand-read QD1: \(.rand_read_8k_qd1.iops // "n/a") IOPS  p99=\(.rand_read_8k_qd1.p99_us // "n/a")us  (one index lookup, graded)",
  "rand-write-8k: \(.rand_write_8k.iops // "n/a") IOPS  p99=\(.rand_write_8k.p99_us // "n/a")us",
  "mixed-8k:      \(.mixed_8k.iops // "n/a") IOPS  p99=\(.mixed_8k.p99_us // "n/a")us",
  "",
  "WAL fsync:     \(.wal_fsync.iops // "n/a") IOPS  avg=\(.wal_fsync.avg_us // "n/a")us  p99=\(.wal_fsync.p99_us // "n/a")us  p99.9=\(.wal_fsync.p999_us // "n/a")us"
' "$P99_WORK/frag-disk.json"
echo
echo "wal_fsync p99.9 is the number that decides whether Postgres and Redis-AOF"
echo "are viable here. Everything above it is context."
