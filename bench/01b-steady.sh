#!/usr/bin/env bash
# 01b-steady.sh - 30 minute sustained mixed load.
# Emits disk.steady_state fragment.
#
# Why this exists: most providers grant a burst credit budget that covers the
# first 30-60 seconds of IO at full speed, then throttle to a baseline that can
# be an order of magnitude lower. Every short benchmark passes. Your database
# does not run for 60 seconds.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need fio
need jq

DURATION="${P99_STEADY_DURATION:-1800}"
TARGET="$P99_TARGET"
mkdir -p "$TARGET" || die "cannot create $TARGET"

SIZE_G=${P99_SIZE%G}
NEED_G=$(( SIZE_G * 4 + 4 ))
AVAIL_G=$(df -BG --output=avail "$TARGET" | tail -1 | tr -dc '0-9')
(( AVAIL_G < NEED_G )) && die "need ${NEED_G}G free at $TARGET, have ${AVAIL_G}G"

log "Steady state: ${DURATION}s of 70/30 random 8k. Go and do something else."

fio --name=steady --directory="$TARGET" --size="$P99_SIZE" --direct=1 \
    --ioengine=libaio --rw=randrw --rwmixread=70 --bs=8k \
    --iodepth=32 --numjobs=4 --runtime="$DURATION" --time_based \
    --group_reporting \
    --write_iops_log="$P99_WORK/steady" --write_lat_log="$P99_WORK/steady" \
    --log_avg_msec=1000 \
    --output-format=json > "$P99_WORK/fio-steady.json" 2>"$P99_WORK/fio-steady.err" \
    || warn "fio steady exited non-zero"

# fio writes steady_iops.*.log as: msec, value, direction, blocksize
# Sum across jobs and directions per second, then compare first vs last minute.
compute() {
  python3 - "$P99_WORK" "$DURATION" <<'PY'
import glob, json, sys, statistics
work, duration = sys.argv[1], int(sys.argv[2])

def load(pattern):
    per_sec = {}
    for f in glob.glob(f"{work}/{pattern}"):
        with open(f) as fh:
            for line in fh:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    continue
                try:
                    ms, val = int(parts[0]), float(parts[1])
                except ValueError:
                    continue
                per_sec.setdefault(ms // 1000, []).append(val)
    return per_sec

iops = load("steady_iops.*.log")
lat  = load("steady_lat.*.log")

def window(d, lo, hi, agg):
    vals = [v for s, lst in d.items() if lo <= s < hi for v in lst]
    if not vals:
        return None
    return round(agg(vals), 2)

out = {"duration_s": duration}
if iops:
    # iops log is per-job samples; sum them to get cluster-wide iops per second
    def sum_per_sec(d, lo, hi):
        secs = [sum(lst) for s, lst in sorted(d.items()) if lo <= s < hi]
        return round(statistics.mean(secs), 2) if secs else None
    out["first_min_iops"] = sum_per_sec(iops, 0, 60)
    out["last_min_iops"]  = sum_per_sec(iops, duration - 60, duration)
    if out["first_min_iops"] and out["last_min_iops"]:
        drop = (out["first_min_iops"] - out["last_min_iops"]) / out["first_min_iops"] * 100
        out["degradation_pct"] = round(max(drop, 0), 2)
if lat:
    def p99(vals):
        vals = sorted(vals)
        return vals[int(len(vals) * 0.99)] / 1000.0  # ns -> us
    out["p99_us_first_min"] = window(lat, 0, 60, p99)
    out["p99_us_last_min"]  = window(lat, duration - 60, duration, p99)

print(json.dumps(out))
PY
}

STEADY=$(compute 2>/dev/null || echo '{"duration_s":null}')
emit_json steady "$(jq -n --argjson s "$STEADY" '{disk: {steady_state: $s}}')"

rm -rf "$TARGET"

echo
echo "=== Steady state ==="
echo "$STEADY" | jq -r '
  "duration:    \(.duration_s // "n/a")s",
  "first min:   \(.first_min_iops // "n/a") IOPS, p99=\(.p99_us_first_min // "n/a")us",
  "last min:    \(.last_min_iops // "n/a") IOPS, p99=\(.p99_us_last_min // "n/a")us",
  "degradation: \(.degradation_pct // "n/a")%"
'
echo
echo "Degradation over ~50% means burst credits ran out. The 60s numbers in"
echo "01-disk.sh describe a machine you will not be running."
