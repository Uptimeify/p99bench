#!/usr/bin/env bash
# 00-inventory.sh - identify the machine. Emits host{} fragment.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need jq
need lscpu

log "Collecting host inventory"

VIRT=$(systemd-detect-virt 2>/dev/null || echo "unknown")
VCPU=$(nproc)
RAM_MB=$(awk '/^MemTotal:/ {printf "%d", $2/1024}' /proc/meminfo)
CPU_MODEL=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//' || true)
[[ -z "$CPU_MODEL" ]] && CPU_MODEL=$(lscpu | awk -F: '/Model name/{gsub(/^ +/,"",$2); print $2; exit}')
GOV=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "")
NUMA=$(numactl --hardware 2>/dev/null | awk '/^available:/ {print $2}' || echo 1)
KERNEL=$(uname -r)
DISTRO=$(. /etc/os-release 2>/dev/null && echo "${ID}-${VERSION_ID}" || echo "")

# ECC: dmidecode reports what the firmware claims. On a VM that is the
# hypervisor's SMBIOS table, which need not reflect the physical host.
ECC_CLAIMED=$(dmidecode -t memory 2>/dev/null | awk -F: '/Error Correction Type/{gsub(/^ +/,"",$2); print $2; exit}' || echo "")

# EDAC is the only thing that proves ECC is real AND visible to you:
# it exposes live correctable/uncorrectable error counters.
ECC_VERIFIABLE=false
if [[ -d /sys/devices/system/edac/mc ]] && compgen -G "/sys/devices/system/edac/mc/mc*" >/dev/null; then
  ECC_VERIFIABLE=true
fi

emit_json host "$(jq -n \
  --arg virt "$VIRT" \
  --argjson vcpu "$VCPU" \
  --argjson ram_mb "$RAM_MB" \
  --arg cpu_model "$CPU_MODEL" \
  --arg gov "$GOV" \
  --argjson numa "${NUMA:-1}" \
  --arg ecc_claimed "$ECC_CLAIMED" \
  --argjson ecc_verifiable "$ECC_VERIFIABLE" \
  --arg kernel "$KERNEL" \
  --arg distro "$DISTRO" \
  '{host: {
      virt: $virt,
      vcpu: $vcpu,
      ram_mb: $ram_mb,
      cpu_model: (if $cpu_model == "" then null else $cpu_model end),
      cpu_governor: (if $gov == "" then null else $gov end),
      numa_nodes: $numa,
      ecc_claimed: (if $ecc_claimed == "" then null else $ecc_claimed end),
      ecc_verifiable: $ecc_verifiable,
      kernel: $kernel,
      distro: (if $distro == "" then null else $distro end)
  }}')"

# --- human-readable section, for the .log file ---
echo "=== CPU ==="
lscpu | grep -E "Model name|Socket|Core|Thread|MHz|Hypervisor|Flags" | head -20
echo "--- Governor ---"
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null | sort | uniq -c || echo "no cpufreq (typical on VMs)"

echo "=== RAM ==="
dmidecode -t memory 2>/dev/null | grep -E "Size|Speed|Type:|Configured|Rank|Manufacturer" | head -20
echo "--- ECC ---"
echo "claimed by firmware: ${ECC_CLAIMED:-<none>}"
if [[ "$ECC_VERIFIABLE" == "true" ]]; then
  grep -r . /sys/devices/system/edac/mc/mc*/{ce_count,ue_count} 2>/dev/null
else
  echo "EDAC absent -> correctable errors are NOT observable from inside this guest."
  echo "A dmidecode claim of ECC on a VM is not evidence. Ask the provider in writing."
fi

echo "=== NUMA ==="
numactl --hardware 2>/dev/null || echo "numactl unavailable"

echo "=== Disks ==="
lsblk -o NAME,MODEL,SIZE,ROTA,PHY-SEC,LOG-SEC,SCHED,TYPE,MOUNTPOINTS

echo "=== Virtualisation ==="
echo "systemd-detect-virt: $VIRT"
dmidecode -s system-manufacturer 2>/dev/null || true
dmidecode -s system-product-name 2>/dev/null || true

log "Inventory done"
