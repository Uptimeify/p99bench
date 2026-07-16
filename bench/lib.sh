#!/usr/bin/env bash
# lib.sh - shared helpers for p99bench
# Sourced by every 0x-*.sh script. Not executable on its own.

# shellcheck disable=SC2034  # consumed by run-all.sh after sourcing
P99BENCH_VERSION="0.2.0"

# Where partial JSON fragments accumulate before run-all.sh merges them.
: "${P99_WORK:=/tmp/p99bench}"
mkdir -p "$P99_WORK" 2>/dev/null || true
if [[ ! -w "$P99_WORK" ]]; then
  printf '\033[1;31m[x]\033[0m %s\n' "$P99_WORK is not writable by $(id -un)." >&2
  printf '\033[1;31m[x]\033[0m %s\n' "Usually left over from an earlier run as root. Try: rm -rf $P99_WORK" >&2
  exit 1
fi

# Where fio writes. Override with P99_TARGET to test a dedicated data volume.
: "${P99_TARGET:=/var/lib/p99bench}"

# Per-job file size for fio. Total footprint = SIZE * numjobs.
: "${P99_SIZE:=4G}"

# Short runtime for the individual fio phases.
: "${P99_RUNTIME:=60}"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# emit_json <fragment-name> <json-string>
# Stores a fragment that run-all.sh will deep-merge into the final result.
#
# All three failure modes are fatal on purpose:
#
#   empty    - the jq that built this payload failed, usually because a parser
#              upstream fed it a word where a number was expected. Writing a
#              0-byte file here makes run-all.sh die 40 minutes later with
#              "invalid JSON text passed to --argjson", naming neither the file
#              nor the stage. Dying here names the stage.
#   invalid  - same reasoning, caught one step earlier.
#   unwritable - a missing fragment silently removes a whole section from the
#              result and the verdict comes out "unknown" with no way to
#              reconstruct why. Worse than no result at all.
emit_json() {
  local name="$1"; shift
  local target="$P99_WORK/frag-$name.json"
  local payload="$*"

  if [[ -z "$payload" ]]; then
    die "stage '$name' produced no JSON - a jq call upstream failed. Rerun just this stage to see its error."
  fi
  if ! printf '%s' "$payload" | jq empty 2>/dev/null; then
    die "stage '$name' produced invalid JSON: $(printf '%s' "$payload" | head -c 200)"
  fi
  if ! printf '%s' "$payload" > "$target" 2>/dev/null; then
    die "cannot write $target - a missing fragment would leave holes in the result. Check: ls -ld $P99_WORK"
  fi
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "missing tool: $1 (see README install step)"
}

# jq helper: emit a JSON number, or JSON null when the value is missing or is
# not actually a number.
#
# The non-numeric guard matters: these values come from awk-parsing the output
# of tools whose format changes between versions. When a parse misses, the
# variable holds a word ("bytes", "not", an error message), and passing that to
# jq --argjson kills the whole fragment. Recording null loses one metric;
# passing garbage loses the entire run.
#
# The leading-dot branch is not cosmetic. bc prints values between -1 and 1
# without a leading zero (".977", not "0.977"), so a regex demanding a digit
# before the point silently nulls every ratio this suite computes -
# scaling_efficiency is always 0-1 and was null in every published result
# because of exactly this. JSON itself rejects ".977", but jq --argjson
# accepts it and normalises, so emitting it is safe.
jnum() {
  local v="$1"
  if [[ -z "$v" || "$v" == "null" ]]; then
    printf 'null'
  elif [[ "$v" =~ ^-?([0-9]+|[0-9]*[.][0-9]+)([eE][-+]?[0-9]+)?$ ]]; then
    printf '%s' "$v"
  else
    warn "expected a number, got '${v:0:40}' - recording null"
    printf 'null'
  fi
}

# jstr: JSON-escape a string, or null if empty.
jstr() {
  local v="$1"
  if [[ -z "$v" ]]; then printf 'null'; else printf '%s' "$v" | jq -Rs .; fi
}

# host_id - a stable, anonymous identifier for THIS machine.
#
# Purpose: distinguish "three runs on one VM at different hours" (time variance,
# i.e. noisy neighbours) from "three runs on three VMs of the same type" (host
# variance, i.e. provider consistency). Those are different claims and RESULTS.md
# reports them separately, which is impossible without knowing which is which.
#
# Privacy: /etc/machine-id is a local secret and must never be published as-is
# (systemd's own docs say to hash it with an application-specific key first).
# We hash it with a fixed public salt and truncate to 12 hex chars. The result is
# stable across reboots and runs on the same VM, and useless to anyone trying to
# correlate it with anything else - the salt is in this file, so it identifies a
# machine only within this dataset.
#
# Falls back to a hash of DMI identifiers where machine-id is absent, and to a
# random value as a last resort (a random id is honest: it says "we could not
# prove this is the same machine" rather than falsely claiming it is).
# llc_bytes - largest last-level-cache size in bytes, read from sysfs.
#
# Lives here (not in 03-ram.sh, the only current caller) so it can be unit
# tested without a container: sysbench's --memory-block-size IS the
# per-thread working set, so a benchmark block that fits in cache measures
# cache, not RAM. Getting this parse right is the difference between a real
# bandwidth number and a cache number wearing a RAM label.
#
# P99_CACHE_ROOT is an injection seam for tests - point it at a fixture
# directory laid out like a real .../cache dir (index0/size, index1/size,
# ...) to drive this deterministically. Real runs never set it, so they read
# the actual sysfs path.
#
# Falls back to a pessimistic 32M when sysfs has nothing readable (e.g. this
# repo's own test container), which is larger than most LLCs and therefore
# still safely out of cache.
llc_bytes() {
  : "${P99_CACHE_ROOT:=/sys/devices/system/cpu/cpu0/cache}"
  local biggest=0 size unit v
  for f in "$P99_CACHE_ROOT"/index*/size; do
    [[ -r "$f" ]] || continue
    size=$(cat "$f")                 # e.g. "32768K", "32M", "1G"
    [[ -z "$size" ]] && continue
    unit="${size: -1}"
    v="${size%?}"
    case "$unit" in
      K) [[ "$v" =~ ^[0-9]+$ ]] || continue; v=$((v * 1024));;
      M) [[ "$v" =~ ^[0-9]+$ ]] || continue; v=$((v * 1024 * 1024));;
      G) [[ "$v" =~ ^[0-9]+$ ]] || continue; v=$((v * 1024 * 1024 * 1024));;
      *)
        # An unrecognized unit used to fall through to stripping non-digits,
        # e.g. "32KB" -> 32 (should be 32768) - a silently too-small value.
        # Ignore the entry instead of guessing; stock Linux always emits
        # "%zuK" so this branch is unreachable on a real host.
        warn "llc_bytes: unrecognized cache size unit in '$size' ($f) - ignoring this entry rather than under-computing"
        continue
        ;;
    esac
    (( v > biggest )) && biggest=$v
  done
  (( biggest == 0 )) && biggest=$((32 * 1024 * 1024))
  printf '%s' "$biggest"
}

host_id() {
  local seed=""
  if [[ -r /etc/machine-id ]]; then
    seed=$(cat /etc/machine-id)
  elif [[ -r /var/lib/dbus/machine-id ]]; then
    seed=$(cat /var/lib/dbus/machine-id)
  elif [[ -r /sys/class/dmi/id/product_uuid ]]; then
    seed=$(cat /sys/class/dmi/id/product_uuid 2>/dev/null)
  fi
  if [[ -z "$seed" ]]; then
    warn "no stable machine identifier found; host_id will be random"
    warn "runs on this machine cannot be linked together"
    seed="random-$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  printf 'p99bench-hostid-v1:%s' "$seed" | sha256sum | cut -c1-12
}