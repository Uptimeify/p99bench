#!/usr/bin/env bash
# 04-app-optional.sh - application-level verification. Emits app{} fragment.
#
# THIS IS OPTIONAL AND IS NOT REQUIRED FOR A VALID SUBMISSION.
#
# The synthetic tests (01, 02, 03, 05) answer "should I deploy here?" without
# needing anything deployed. This script answers "was the synthetic prediction
# right?" and only runs if a service happens to be reachable already. It is
# useful for validating the thresholds themselves, not for procurement.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./lib.sh

need jq

PG=null
REDIS=null

# --- PostgreSQL ------------------------------------------------------------
if command -v pg_isready >/dev/null 2>&1 && pg_isready -q 2>/dev/null; then
  if command -v pgbench >/dev/null 2>&1 && pgbench --version >/dev/null 2>&1; then
    log "PostgreSQL reachable, running pgbench"
    export PGHOST="${PGHOST:-localhost}" PGUSER="${PGUSER:-postgres}"
    RAM_GB=$(awk '/^MemTotal:/ {printf "%d", $2/1048576}' /proc/meminfo)
    SCALE=$(( RAM_GB * 15 ))
    (( SCALE < 100 )) && SCALE=100

    DB=p99bench_$$
    if createdb "$DB" 2>/dev/null; then
      pgbench -i -q -s "$SCALE" "$DB" >/dev/null 2>&1 || warn "pgbench init failed"

      TPS_RO=$(pgbench -c 64 -j "$(nproc)" -T 60 -S "$DB" 2>/dev/null | awk '/^tps/ {print $3; exit}')
      TPS_RW=$(pgbench -c 32 -j "$(nproc)" -T 60 -N "$DB" 2>/dev/null | awk '/^tps/ {print $3; exit}')
      P95=$(pgbench -c 32 -j "$(nproc)" -T 30 -N "$DB" 2>/dev/null | awk '/latency average/ {print $4; exit}')

      psql -q -c "ALTER SYSTEM SET synchronous_commit = on;" -c "SELECT pg_reload_conf();" >/dev/null 2>&1
      TPS_SYNC=$(pgbench -c 32 -j "$(nproc)" -T 60 -N "$DB" 2>/dev/null | awk '/^tps/ {print $3; exit}')
      psql -q -c "ALTER SYSTEM RESET synchronous_commit;" -c "SELECT pg_reload_conf();" >/dev/null 2>&1

      VER=$(psql -tAc "show server_version" 2>/dev/null | xargs)
      dropdb "$DB" 2>/dev/null || true

      PG=$(jq -n --arg v "$VER" --argjson s "$SCALE" \
        --argjson ro "$(jnum "$TPS_RO")" --argjson rw "$(jnum "$TPS_RW")" \
        --argjson sy "$(jnum "$TPS_SYNC")" --argjson p95 "$(jnum "$P95")" \
        '{version: $v, scale: $s, tps_ro_64c: $ro, tps_rw_32c: $rw,
          tps_rw_sync_commit_32c: $sy, latency_p95_ms: $p95}')
    else
      warn "could not create test database, skipping pgbench"
    fi
  else
    warn "PostgreSQL running but pgbench unusable (install postgresql-client-<version>)"
  fi
else
  log "No PostgreSQL reachable, skipping (this is fine)"
fi

# --- Redis -----------------------------------------------------------------
if command -v redis-cli >/dev/null 2>&1 && redis-cli -t 2 ping >/dev/null 2>&1; then
  log "Redis reachable, running redis-benchmark"
  VER=$(redis-cli info server 2>/dev/null | awk -F: '/redis_version/ {print $2}' | tr -d '\r')
  ORIG=$(redis-cli config get appendfsync 2>/dev/null | tail -1)

  redis-cli config set appendonly yes >/dev/null 2>&1
  redis-cli config set appendfsync everysec >/dev/null 2>&1
  Q_EVERY=$(redis-benchmark -t set -n 100000 -c 50 -q 2>/dev/null | awk '/^SET/ {print $2; exit}')

  redis-cli config set appendfsync always >/dev/null 2>&1
  Q_ALWAYS=$(redis-benchmark -t set -n 50000 -c 50 -q 2>/dev/null | awk '/^SET/ {print $2; exit}')
  P99=$(redis-benchmark -t set -n 50000 -c 50 2>/dev/null | awk '/p99/ {print $2; exit}')

  [[ -n "$ORIG" ]] && redis-cli config set appendfsync "$ORIG" >/dev/null 2>&1

  REDIS=$(jq -n --arg v "$VER" \
    --argjson e "$(jnum "$Q_EVERY")" --argjson a "$(jnum "$Q_ALWAYS")" \
    --argjson p "$(jnum "$P99")" \
    '{version: $v, set_qps_appendfsync_everysec: $e,
      set_qps_appendfsync_always: $a, set_p99_ms: $p}')
else
  log "No Redis reachable, skipping (this is fine - see 05-latency.sh)"
fi

if [[ "$PG" == "null" && "$REDIS" == "null" ]]; then
  emit_json app '{"app": null}'
else
  emit_json app "$(jq -n --argjson pg "$PG" --argjson r "$REDIS" \
    '{app: {pgbench: $pg, redis: $r}}')"
fi
