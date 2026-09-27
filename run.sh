#!/usr/bin/env bash
# =============================================================================
# Neural Strata — cold start
# =============================================================================
# One command from a cold laptop to a live dashboard.
#
#   ./run.sh                      # serial hub if present, simulator if not
#   ./run.sh --no-serial          # API + dashboard only
#   ./run.sh --replay rec.csv --speed 10
#   ./run.sh --init-db            # apply migrations, then start
#   ./run.sh --check              # run the gates and exit, do not serve
#
# If a copied/cloned tree has lost the executable bit and `./run.sh` answers
# "Permission denied", use `bash run.sh` (identical) or `chmod +x run.sh`.
#
# Every step prints one line. If any step fails the script stops and says which
# one, so a 1 a.m. failure is a three-second diagnosis instead of an archaeology
# project.
# =============================================================================
set -uo pipefail
export PYTHONPATH="$(pwd)"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$HERE/backend"
PY="${NS_PYTHON:-python3}"
INIT_DB=0
CHECK_ONLY=0
ARGS=()

for arg in "$@"; do
  case "$arg" in
    --init-db) INIT_DB=1 ;;
    --check)   CHECK_ONLY=1 ;;
    *)         ARGS+=("$arg") ;;
  esac
done

step() { printf '\n\033[1m[%s]\033[0m %s\n' "$1" "$2"; }
ok()   { printf '  \033[32mok\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; exit 1; }
warn() { printf '  \033[33mwarn\033[0m %s\n' "$1"; }

# --- 1. interpreter ----------------------------------------------------------
step 1/7 "Python"
command -v "$PY" >/dev/null 2>&1 || bad "no $PY on PATH"
PYV="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || bad "Python $PYV is too old (need >= 3.10)"
ok "Python $PYV"

# --- 2. packages -------------------------------------------------------------
step 2/7 "Dependencies"
MISSING="$("$PY" - <<'PYEOF'
import importlib
need = {"flask": "flask", "flask_cors": "flask-cors", "psycopg2": "psycopg2-binary",
        "serial": "pyserial", "numpy": "numpy"}
missing = []
for mod, pkg in need.items():
    try:
        importlib.import_module(mod)
    except Exception:
        missing.append(pkg)
print(" ".join(missing))
PYEOF
)"
[ -n "$MISSING" ] && bad "missing packages: $MISSING  →  pip install -r requirements.txt"
ok "core packages present"

"$PY" -c 'import xgboost' 2>/dev/null \
  && ok "xgboost present — /api/ai can reach ACTIVE" \
  || warn "xgboost absent — /api/ai stays on the kinematic FALLBACK (legal, degraded)"

# --- 3. configuration --------------------------------------------------------
step 3/7 "Configuration"
cd "$BACKEND" || bad "cannot enter $BACKEND"
"$PY" -c 'import config, sys; p = config.validate_config();
[print("  \033[31mFAIL\033[0m " + x) for x in p]; sys.exit(1 if p else 0)' \
  || bad "configuration is not bootable (see above; fix .env)"
ok "validate_config() clean"

# --- 4. database -------------------------------------------------------------
step 4/7 "Database"
if [ "$INIT_DB" = "1" ]; then
  "$PY" -m persistence.db --init || bad "migrations failed"
else
  "$PY" -m persistence.db --status | head -1
  PENDING="$("$PY" -c 'import sys; sys.path.insert(0,"."); from persistence import db;
st = db.status(); print(",".join(str(v) for v in st["pending"]))' 2>/dev/null)"
  if [ -n "$PENDING" ]; then
    warn "pending migrations: $PENDING  →  ./run.sh --init-db"
  else
    ok "schema up to date"
  fi
fi

# --- 5. artifacts ------------------------------------------------------------
step 5/7 "Model artifacts"
if [ -f "$BACKEND/models/ttf_model_v1.json" ] && [ -f "$BACKEND/models/scaler.json" ]; then
  ok "ttf_model_v1.json + scaler.json"
else
  warn "model artifacts absent — the server boots on the kinematic fallback"
fi
[ -f "$BACKEND/assets/tunnel_map.json" ] && ok "tunnel_map.json" || warn "tunnel_map.json absent"

# --- 6. gates ----------------------------------------------------------------
step 6/7 "Contracts, numbers and the dual-database schema"
"$PY" tools/check_contracts.py >/tmp/ns_contracts.log 2>&1 \
  && ok "check_contracts.py" \
  || { cat /tmp/ns_contracts.log; bad "check_contracts.py failed"; }
"$PY" tools/check_numbers.py >/tmp/ns_numbers.log 2>&1 \
  && ok "check_numbers.py" \
  || { cat /tmp/ns_numbers.log; bad "check_numbers.py failed"; }
# Phase 9 — the two sync checks: the cloud schema agrees with what sync.py sends,
# and the edge database really carries every column the sync path reads. Run it
# again by hand after any edit to `persistence/sync.py`; it is here because a
# mismatch is invisible until 15 minutes into a demo.
"$PY" tools/verify_dual_db_sql.py >/tmp/ns_dualdb.log 2>&1 \
  && ok "verify_dual_db_sql.py  (schema ↔ sync.py, live watermarks)" \
  || { cat /tmp/ns_dualdb.log; bad "verify_dual_db_sql.py failed"; }

if [ "$CHECK_ONLY" = "1" ]; then
  step 7/7 "Done"
  ok "all gates green — not serving (--check)"
  exit 0
fi

# --- 7. serve ----------------------------------------------------------------
# --- 7. serve ----------------------------------------------------------------
step 7/7 "Edge server"
echo "  starting: python3 app.py ${ARGS[*]:-}"
# `${ARGS[@]:-}` expands to ONE EMPTY ARGUMENT when ARGS is empty, and argparse
# rejects that with "unrecognized arguments:" before the server ever starts — so
# a plain `bash run.sh` cold start did not boot. Branch instead of using a
# default that is not empty but *looks* empty.

# FIX: Disable strict mode temporarily because macOS Bash 3.2 crashes 
# when checking the length of an empty array under set -u
set +u 

if [ "${#ARGS[@]}" -gt 0 ]; then
  exec "$PY" app.py "${ARGS[@]}"
else
  exec "$PY" app.py
fi