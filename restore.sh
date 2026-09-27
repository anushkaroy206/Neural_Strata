#!/usr/bin/env bash
# =============================================================================
# Neural Strata — restore the rehearsal database from a pg_dump
# =============================================================================
# The other half of the backup habit. Take the dump before every rehearsal:
#
#   pg_dump -Fc neural_strata > backups/$(date +%F_%H%M).dump
#   ./restore.sh backups/2026-09-22_0900.dump
#
# Restoring DROPS and recreates the database. It asks first, and it asks loudly.
# =============================================================================
set -euo pipefail

DUMP="${1:-}"
DB="${NS_DB_NAME:-neural_strata}"
HOST="${NS_DB_HOST:-localhost}"
USER="${NS_DB_USER:-postgres}"

[ -n "$DUMP" ] || { echo "usage: ./restore.sh <dumpfile>"; exit 2; }
[ -f "$DUMP" ] || { echo "no such dump: $DUMP"; exit 2; }

echo "This will DROP and recreate database '$DB' on $HOST from:"
echo "    $DUMP"
read -r -p "Type the database name to confirm: " answer
[ "$answer" = "$DB" ] || { echo "aborted."; exit 1; }

echo "[restore] dropping and recreating $DB"
psql -h "$HOST" -U "$USER" -d postgres -c "DROP DATABASE IF EXISTS $DB;"
psql -h "$HOST" -U "$USER" -d postgres -c "CREATE DATABASE $DB;"

echo "[restore] loading $DUMP"
pg_restore -h "$HOST" -U "$USER" -d "$DB" --no-owner --no-privileges "$DUMP"

echo "[restore] verifying ledger"
psql -h "$HOST" -U "$USER" -d "$DB" -c "SELECT version, name, applied_at FROM schema_migrations ORDER BY version;"
echo "[restore] done — run ./run.sh --check"
