#!/usr/bin/env bash
# lib.sh - shared helpers for p99bench
# Sourced by every 0x-*.sh script. Not executable on its own.

# shellcheck disable=SC2034  # consumed by run-all.sh after sourcing
P99BENCH_VERSION="0.1.0"

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
# A failed write is fatal on purpose. Swallowing it would produce a result file
# with a section silently missing, which is worse than no result at all: the
# verdict would come out "unknown" for reasons nobody can reconstruct later.
emit_json() {
  local name="$1"; shift
  local target="$P99_WORK/frag-$name.json"
  if ! printf '%s' "$*" > "$target" 2>/dev/null; then
    die "cannot write $target - a missing fragment would leave holes in the result. Check: ls -ld $P99_WORK"
  fi
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "missing tool: $1 (see README install step)"
}

# jq helper: null-safe number, rounds to 2dp, returns JSON null on missing.
# usage: jnum "$(some command | grep ...)"
jnum() {
  local v="$1"
  if [[ -z "$v" || "$v" == "null" ]]; then printf 'null'; else printf '%s' "$v"; fi
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