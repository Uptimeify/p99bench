#!/usr/bin/env bash
# lib.sh - shared helpers for p99bench
# Sourced by every 0x-*.sh script. Not executable on its own.

# shellcheck disable=SC2034  # consumed by run-all.sh after sourcing
P99BENCH_VERSION="0.2.1"

# Force C locale for every stage. Not cosmetic -- two real failures on a
# German-locale Debian host (LANG=de_DE.UTF-8), both silent:
#
#   1. mpstat prints "Durchschn.:" instead of "Average:", so the awk that finds
#      the summary row never matched and cpu.steal_pct_under_load came back
#      null. steal is `required: true` in four profiles.
#   2. Worse, it prints DECIMAL COMMAS: "0,13" not "0.13". jnum rejects that as
#      non-numeric (correctly -- it cannot know it is not a thousands
#      separator), so the value would be nulled even if the row were found.
#
# Every stage parses the English, dot-decimal output of fio/sysbench/mpstat/
# openssl. One LANG away from silently nulling any metric, on a host that looks
# completely healthy. Set here because every stage sources this file.
export LC_ALL=C
export LANG=C

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

# preflight_tools - verify every external tool a full run touches, before any
# stage starts. Why this exists: three separate ~60-minute runs were lost to
# a missing package discovered only once a stage got to it, sometimes 30+
# minutes in. Some stages `need` a tool and die outright (see need() above);
# others only `command -v` check it, warn, and skip the metric. The second
# group is the dangerous one: the metrics behind cyclictest, mpstat and
# openssl are `required: true` in MORE grading profiles (5, 4 and 1 -
# worker_probe - respectively) than the tools that die loud. A run missing
# mpstat still finishes in an hour, looks healthy, and grades a floor or "?"
# on four profiles with nothing louder than a warn in the log.
#
# stress-ng is listed but never blocks alone: 02-cpu.sh falls back to
# sysbench for CPU load generation when stress-ng is absent.
preflight_tools() {
  # tool|apt package|what silently doing without it costs
  local -a rows=(
    "fio|fio|disk stage; run dies without it"
    "sysbench|sysbench|cpu + ram stages; run dies without it"
    "jq|jq|every stage's JSON; run dies without it"
    "bc|bc|arithmetic in several stages"
    "cyclictest|rt-tests|cpu.stall_p999_us -- required by 5 profiles"
    "mpstat|sysstat|cpu.steal_pct_under_load -- required by 4 profiles"
    "openssl|openssl|cpu.tls_verify_s -- required by worker_probe"
    "numactl|numactl|NUMA locality; ram stage"
    "dmidecode|dmidecode|host inventory, ECC observability"
    "curl|curl|network stage"
    "ping|iputils-ping|network stage RTT/loss"
    "lscpu|util-linux|LLC detection for the RAM working set"
    "stress-ng|stress-ng|CPU load generator; OPTIONAL -- 02-cpu.sh falls back to sysbench"
  )

  local -a missing_rows=() blocking_pkgs=() blocking_tools=()
  local row tool pkg cost
  for row in "${rows[@]}"; do
    IFS='|' read -r tool pkg cost <<< "$row"
    command -v "$tool" >/dev/null 2>&1 && continue
    missing_rows+=("$row")
    if [[ "$tool" != "stress-ng" ]]; then
      blocking_pkgs+=("$pkg")
      blocking_tools+=("$tool")
    fi
  done

  (( ${#missing_rows[@]} == 0 )) && return 0

  warn "missing tools -- each row below is a metric a real run needs:"
  printf '%-12s %-14s %s\n' "TOOL" "APT PACKAGE" "COST IF MISSING" >&2
  for row in "${missing_rows[@]}"; do
    IFS='|' read -r tool pkg cost <<< "$row"
    printf '%-12s %-14s %s\n' "$tool" "$pkg" "$cost" >&2
  done

  if (( ${#blocking_pkgs[@]} == 0 )); then
    warn "only stress-ng is missing, and it is optional (02-cpu.sh falls back to sysbench) -- continuing"
    return 0
  fi

  if [[ ! -t 0 ]]; then
    # A y/N prompt here would hang a scripted/CI/nohup 60-minute run forever --
    # worse than exiting now with the fix already on screen.
    warn "stdin is not a TTY, so not prompting. Install and rerun:"
    warn "  apt-get install -y ${blocking_pkgs[*]}"
    return 1
  fi

  local a
  read -rp "install missing packages now (apt-get install -y ${blocking_pkgs[*]})? [y/N] " a
  if [[ "$a" != "y" ]]; then
    warn "declined. Install and rerun:"
    warn "  apt-get install -y ${blocking_pkgs[*]}"
    return 1
  fi

  apt-get install -y "${blocking_pkgs[@]}"

  local -a still_missing=()
  for tool in "${blocking_tools[@]}"; do
    command -v "$tool" >/dev/null 2>&1 || still_missing+=("$tool")
  done
  if (( ${#still_missing[@]} > 0 )); then
    warn "still missing after install: ${still_missing[*]}"
    return 1
  fi
  return 0
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
  # sysfs cache info is ABSENT in real cloud guests -- confirmed on Hetzner,
  # OVH and windcloud VMs, where this loop matched nothing and the 32M default
  # fired silently on every one. lscpu reads CPUID instead, so it still answers
  # in a guest. Kept second so the fixture-driven tests (P99_CACHE_ROOT) still
  # exercise the sysfs parser.
  if (( biggest == 0 )) && command -v lscpu >/dev/null 2>&1; then
    local v
    v=$(lscpu -B 2>/dev/null | awk '/^L3 cache:/ {print $3; exit}')
    [[ "$v" =~ ^[0-9]+$ ]] && (( v > 0 )) && biggest=$v
  fi
  (( biggest == 0 )) && biggest=$((32 * 1024 * 1024))
  printf '%s' "$biggest"
}

# numa_node_cpus <node> - how many CPUs that NUMA node has, or 0 if absent.
#
# Lives here so CI tests it: this dev box and the test container have one NUMA
# node, so the multi-node path never executes anywhere but on real hardware.
# P99_NUMA_HW points at a file holding `numactl --hardware` output; real runs
# leave it unset and read numactl directly.
#
# It exists because --cpunodebind=0 confines the locality test to node 0's
# CPUs, so the thread count must come from THAT node, not from nproc. Asking
# for nproc threads oversubscribes: windcloud (4 vCPU, 2 nodes) ran 4 threads
# on node 0's 2 cores and both the local and the remote number came back
# CPU-bound at ~17,400 MiB/s -- half the unpinned 30,763 -- which flattened
# the local/remote difference the test exists to find.
numa_node_cpus() {
  local node="$1" line
  line=$( { [[ -n "${P99_NUMA_HW:-}" ]] && cat "$P99_NUMA_HW" || numactl --hardware 2>/dev/null; } |
    awk -v n="$node" '$0 ~ "^node " n " cpus:" {sub(/^node [0-9]+ cpus:[ ]*/, ""); print; exit}')
  [[ -z "$line" ]] && { printf '0'; return; }
  printf '%s' "$(printf '%s\n' "$line" | wc -w | tr -d ' ')"
}

# median - median of the numbers on stdin. Prints nothing when none are valid.
#
# Lives here so CI can test it: its only caller (06-network.sh, for
# dns_warm_p50_ms) needs real egress to run at all, which neither the test
# container nor a dev sandbox has. The arithmetic must not be the untested
# part -- a non-power-of-two block and a 1K NUMA default both reached real
# hosts by being arithmetic no gate ever executed.
#
# Non-numeric lines are DROPPED, not coerced: a failed lookup contributes
# nothing rather than a zero that would drag the median down. Same doctrine as
# jnum -- never let a word become a number.
#
# Even counts take the lower middle rather than interpolating, so the reported
# value is always one that was actually measured.
median() {
  local vals n
  vals=$(grep -E '^-?([0-9]+\.?[0-9]*|\.[0-9]+)$' | sort -n)
  [[ -z "$vals" ]] && return 0
  n=$(printf '%s\n' "$vals" | wc -l | tr -d ' ')
  # printf '%s' (no trailing newline), as llc_bytes and ram_block_bytes do --
  # every caller is a command substitution, and the helpers agree on shape.
  printf '%s' "$(printf '%s\n' "$vals" | awk -v n="$n" 'NR == int((n + 1) / 2) {print; exit}')"
}

# ram_block_bytes <llc_bytes> <cores> <ram_bytes>
#
# The per-thread sysbench block for the RAM bandwidth run, or 0 if no legal
# block exists for this host shape. Lives here rather than in 03-ram.sh (its
# only caller) so it is unit tested by CI: every 03-ram.sh container test is
# @pytest.mark.docker and CI runs -m "not docker", so this arithmetic had no
# gate CI ran. It shipped a non-power-of-two block to three real hosts.
#
# THE BLOCK MUST BE A POWER OF TWO. sysbench is not tolerant here --
# sb_memory.c, memory_init():
#   if (memory_block_size < SIZEOF_SIZE_T ||
#       (memory_block_size & (memory_block_size - 1)) != 0)
#     log_text(LOG_FATAL, "Invalid value for memory-block-size: %s", ...);
# A FATAL emits no "MiB/sec" line, so the awk parse returns empty and the
# metric nulls. The old cap (RAM/4/CORES) produced whatever byte count the
# arithmetic landed on -- 508355840 on a 4 vCPU / 7756 MB Hetzner CPX32 --
# so every host small enough to hit the cap silently lost bw_read_mbs.
#
# Sizing policy, in order:
#   1. 4x LLC, so the working set cannot be held even by a generous
#      replacement policy, rounded UP to a power of two (an odd LLC slice
#      like 96 MiB must never round DOWN back into the cache).
#   2. Floored at 512M/thread. Measured: a CPX32 (EPYC Genoa, 32 MiB L3)
#      reports 98 GB/s at 128M and 66 GB/s at both 512M and 1G -- 128M, though
#      4x its LLC, over-reports by ~48%. Prefetchers stream happily past a
#      buffer that "should not fit"; only a big working set makes TLB and DRAM
#      behaviour dominate.
#   3. Capped so BLOCK * CORES stays under half of RAM -- this must not swap,
#      and a swapping run measures the disk. Rounded DOWN to a power of two.
#      Half, not a quarter: a quarter cannot fit 4 x 512M on a 4 vCPU / 8 GB
#      host, which is the single most common shape this suite is pointed at.
#      2 GiB of 7.57 GiB is 26% -- no swap risk on any shape measured so far.
#
# The result can land below the 512M floor on a small/many-core host. That is
# the caller's cache guard to judge, not this function's: the contract here is
# "largest safe legal block", not "a block worth reporting".
ram_block_bytes() {
  local llc="$1" cores="$2" ram="$3"
  local block=$((llc * 4)) max p

  (( block < 536870912 )) && block=536870912
  # Round up to a power of two.
  p=8
  while (( p < block )); do p=$((p * 2)); done
  block=$p

  max=$((ram / 2 / cores))
  if (( block > max )); then
    # Round down. Below sysbench's own minimum (sizeof(size_t)) there is no
    # legal block at all, so report 0 rather than a value that would FATAL.
    if (( max < 8 )); then
      printf '0'
      return
    fi
    p=8
    while (( p * 2 <= max )); do p=$((p * 2)); done
    block=$p
  fi
  printf '%s' "$block"
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