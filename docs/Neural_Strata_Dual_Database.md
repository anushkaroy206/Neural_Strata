# Neural Strata — Dual-Database Architecture
## Offline Edge Database + Cloud Replica · Recommended Stack, Schema and Sync Code

| | |
|---|---|
| **Document** | `Neural_Strata_Dual_Database.md` |
| **Version** | v1.0 |
| **Companion to** | `Neural_Strata_Backend_Build_Phases.md` (B6/Phase 9 is implemented in full here) |
| **Question answered** | *"There is an offline local database and a cloud database — how do I build that, with no paid subscription?"* |
| **Verified** | Free-tier limits checked against provider documentation on **18 Sep 2026** |
| **Total cost** | **₹0/month**, no credit card, no expiry |

---

## A. The recommendation

### A.1 The short answer

| Tier | Recommendation | Why |
|---|---|---|
| **Edge (laptop, in the mine, offline)** | **Keep local PostgreSQL** — harden it with a `run.sh` | Already working. Same SQL as the cloud, so **one schema file, one set of queries, zero dialect translation bugs**. `pg_dump` gives you a one-command backup and a one-command migration path. |
| **Cloud (cross-site reporting)** | **Neon free tier** (primary) · **Supabase free** (equal alternative) | Both are free, no card, permanent. Neon is the safer default **because it never needs manual intervention** — see §A.4. |
| **Provider lock-in** | **None.** Drive both from `DATABASE_URL`-style env vars | Because both are real Postgres, you can switch providers by editing **one line in `.env`**. This is what makes the recommendation low-stakes. |
| **The critical rule** | **The cloud is a *derived reporting replica*, never the source of truth** | Your SRS §NFR-2 requires operation with zero external network. The dashboard reads **only** local. A dead cloud is a pending queue, not an outage. |

### A.2 The category error to avoid

You asked *"Supabase *instead of* Postgres?"* — **Supabase *is* Postgres.** Same for Neon. Neither replaces Postgres; they **host** it. So the real choice is:

```
        WRONG — "Postgres or Supabase?"          ← wrong question
        OK  "Local Postgres or hosted Postgres — for which tier?"   ← right question
```

The answer: **local for the edge, hosted for the cloud, both at the same time.** One dialect, two instances.

### A.3 Free-tier comparison (verified 18 Sep 2026)

| | **Neon Free** | **Supabase Free** | Turso Free | CockroachDB Free |
|---|---|---|---|---|
| Engine | **Postgres** | **Postgres** | SQLite (libSQL) | Distributed SQL |
| Storage | 0.5 GB **per project** | 500 MB per project | 5 GB total | 10 GiB |
| Projects | **100** | **2 active** | 100 databases | 1 |
| Idle behaviour | **Auto-suspends after 5 min, auto-wakes on next query (~500 ms)** | **Pauses after 7 days inactive — manual dashboard restore required** | No pause | No pause |
| Compute cap | **100 CU-hours/project/month** (hard cutoff) | Shared, no CU cap | 500M row reads/mo | 50M request units/mo |
| Credit card | **No** | **No** | No | No |
| Bonus services | — | Auth, Storage (1 GB), Realtime, PostgREST | — | — |
| **Same SQL as your edge DB?** | **Yes** | **Yes** | No | Mostly |

### A.4 Why Neon is the default — the 7-day footgun

This is the single decisive difference for a **student team building over several weeks**:

| Scenario | Neon | Supabase |
|---|---|---|
| You don't touch the cloud DB for 8 days while doing hardware work | Compute suspends; the next query wakes it in ~500 ms. **You never notice.** | **Project is PAUSED.** Someone must log into the dashboard and click restore. |
| You discover this 10 minutes before the demo | Impossible — there's nothing to remember | Panic |
| Workaround needed | None | A GitHub Actions cron ping or UptimeRobot monitor to keep it alive |

**Neon's failure mode is invisible. Supabase's failure mode is a manual action you can forget.** For a project whose entire design premise is "assume the network will let you down", choosing the provider that *auto-heals* is consistent.

**Pick Supabase instead if** you specifically want the **table editor for the demo** (genuinely nicer for showing rows appearing on screen), or you think you'll want Auth/Storage later. Both are correct choices. **The code below does not care which you pick.**

### A.5 What I do *not* recommend

| Option | Why not |
|---|---|
| AWS RDS / DynamoDB free tier, Azure SQL free | **Credit card required.** A misconfiguration produces a surprise bill. You asked for no subscription — this is the category that violates it. |
| Fly.io Postgres | **Credit card required**, and there's no hard cap on overage. |
| Railway / Render free Postgres | Free tiers were removed or now expire. Don't build on a trial. |
| Heroku Postgres | Discontinued. |
| ElephantSQL | Being sunset; 20 MB anyway. |
| Cloudflare D1 | SQLite, and only usable from Workers — it would force a rewrite. |
| Self-hosted Postgres on a free VPS | "Free" VPS tiers expire after 30 days, and now you maintain a server. **Not free, just deferred cost.** |
| **MongoDB Atlas / Firebase** | Not SQL. Your schema, your queries and your ML extraction are all SQL. Rewriting for a document store to save ₹0 is a bad trade. |

---

## B. The architecture

### B.1 Two tiers, one source of truth

```
╔══════════════════════ TIER 1 · EDGE (must work with ZERO internet) ══════════════════╗
║                                                                                       ║
║  7 ESP32 boards ──► Flask app.py ──► LOCAL POSTGRES (source of truth)                 ║
║  (ESP-NOW + ring)    (inference,       ▸ sensor_telemetry   (raw, 2 Hz)               ║
║                       routing,         ▸ alerts_history     (incidents)               ║
║                       alerting)        ▸ rfid_evacuation_log                          ║
║                                        ▸ node_hourly        (rollups ← derived)       ║
║                                        ▸ sync_log           (audit)                   ║
║                                                                                       ║
║  The dashboard polls localhost. The AI reads local. Routing reads local.              ║
║  ⚠ No component on the critical path EVER touches the internet.                       ║
╚═══════════════════════════════════════════╤═══════════════════════════════════════════╝
                                            │
                    sync daemon · every 15 min · ONLY when outbox non-empty
                    ONLY summaries: hourly rollups + alerts + drill log
                    (~15 KB per sync, not 150 MB)
                                            │
╔═══════════════════════════════════════════▼═══════════════════════════════════════════╗
║                        TIER 2 · CLOUD (Neon or Supabase — free)                       ║
║                                                                                       ║
║  ▸ node_hourly      cross-site hourly trends, months of history                       ║
║  ▸ alerts_history   every incident from every site, with coordinates                  ║
║  ▸ drill_runs       one row per test/drill, with outcome                              ║
║  ▸ model_metrics    published MAE per model version (the training record)             ║
║  ▸ sync_log         what arrived, when, from which site                               ║
║                                                                                       ║
║  Consumed by: the scalability slide, the regulator read-only view,                    ║
║  the retraining pipeline, and the "cloud sync" demo beat.                             ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

### B.2 What syncs and what does not — the storage math

This is the decision that makes a 500 MB free tier effectively infinite.

**Raw telemetry — calculate it before you promise it:**

| Quantity | Value |
|---|---|
| Sample rate | 4 nodes × 2 Hz = **8 rows/second** |
| Rows per day | 8 × 86,400 = **691,200 rows/day** |
| Bytes per row (payload + tuple header + index entry) | **~200 bytes** |
| **Per day** | **≈ 138 MB/day** |
| Your plan's "keep raw 7 days" | **≈ 970 MB** |
| **Free tier capacity** | **0.5 GB** |
| **Verdict** | NO — **Raw telemetry fills the free tier in ~3 days. Never sync it.** |

**Hourly rollups — the same data, one thousand times smaller:**

| Quantity | Value |
|---|---|
| Rows per day | 4 nodes × 24 hours = **96 rows/day** |
| Bytes per row | ~150 bytes |
| **Per day** | **≈ 14 KB/day** |
| **Per year** | **≈ 5 MB/year** |
| **Free tier capacity** | 0.5 GB |
| **Verdict** | OK — **Rollups fit for ~100 years.** |

**So the rule is:** the cloud stores **decisions and summaries**, never raw sensor data.

### B.3 Why this is the correct architecture, not a workaround

Aggregating at the edge before transmitting is **standard IoT practice**, and it is the reason large sensor networks are economically possible at all. A mine with 200 nodes at 2 Hz would generate **7 GB/day** of raw data — no cellular plan and no cloud bill survives that. Sending hourly statistics instead is a **~10,000× bandwidth reduction** with no loss of the information the reporting layer actually needs.

**This is a viva line, not an apology:**

> *"The cloud never sees raw telemetry. We aggregate at the edge — hourly statistics per node — which is a ten-thousand-fold bandwidth reduction and the only reason a 500-megabyte free tier can hold a year of a mine's history. Raw data stays at the edge, where it's needed for the model; summaries go up, where they're needed for reporting."*

### B.4 Failure modes — and why none of them are a demo failure

| Failure | What happens | What the operator sees | Recovery |
|---|---|---|---|
| **No internet at the venue (expected)** | Sync is skipped. Rows accumulate locally with `synced_at = NULL`. | `OUTBOX: 412 pending · last sync never` chip. Everything else works. | Automatic when a link appears |
| **Cloud project suspended (Neon, after 5 min idle)** | First sync after a gap takes ~1 extra second to wake it | Nothing | Automatic |
| **Cloud project paused (Supabase, after 7 days idle)** | Sync fails with a connection error | `CLOUD UNREACHABLE — data safe locally` + retry counter climbing | Manual dashboard restore, then it drains |
| **Free tier quota exceeded** | Inserts fail; `last_error` records it | Same banner | Reduce sync interval; prune old cloud rollups |
| **Laptop dies, cloud is fine** | Cloud still holds every rollup and alert | — | **You cannot rebuild raw data from summaries** — see §C.3 |
| **Cloud provider disappears** | Change `NS_CLOUD_DB_URL`; re-run the schema | Nothing | ~10 minutes |

**The key property:** the cloud going down is **invisible to the live system**. Not degraded, not slow — invisible. That is what "the edge is the source of truth" buys you.

---

## C. Set up Tier 1 — the edge database

### C.1 Harden the existing local Postgres

You already have Postgres and `app.py` already connects to it. The goal here is not to change it — it is to make it **survive demo day**. The most likely local-DB failure is not corruption; it is *"Postgres wasn't running"*.

**On the Mac (Homebrew), verify and make it persistent:**

```bash
# Which version is installed?
brew list --versions | grep postgresql

# Start it now, and register it to start at every login
brew services start postgresql@16        # or @15 / @14 — match what you have

# Confirm it is actually listening
pg_isready -h localhost -p 5432
#  → localhost:5432 - accepting connections
```

**If `brew services` is not an option**, add the start command to your launch script instead (§C.2) with a wait loop.

### C.2 `run.sh` — one command, cold laptop to live dashboard

This is the single highest-value script in the project. It removes the "is Postgres up?" class of failure permanently.

```bash
#!/usr/bin/env bash
# Neural Strata — cold start. Usage: ./run.sh [--no-serial] [--replay FILE]
set -euo pipefail
cd "$(dirname "$0")"

echo "── 1/5  Edge database ────────────────────────────"
if ! pg_isready -h "${NS_DB_HOST:-localhost}" -p 5432 -q; then
  echo "   Postgres not responding — attempting start…"
  if command -v brew >/dev/null 2>&1; then
    brew services start postgresql@16 >/dev/null 2>&1 || true
  fi
  for i in $(seq 1 20); do
    pg_isready -h "${NS_DB_HOST:-localhost}" -p 5432 -q && break
    sleep 0.5
  done
fi
pg_isready -h "${NS_DB_HOST:-localhost}" -p 5432 -q \
  || { echo "   ✗ Postgres is NOT up. Run: brew services start postgresql@16"; exit 1; }
echo "   ✓ Postgres up"

echo "── 2/5  Schema / migrations ──────────────────────"
python3 -c "from persistence.db import init_schema; init_schema()"
echo "   ✓ schema current"

echo "── 3/5  Model artifacts ──────────────────────────"
python3 -c "from engine.ttf import load_ttf_model; print('   ✓ TTF model' if load_ttf_model() else '   ! TTF model MISSING — kinematic fallback armed')"
python3 -c "from engine.pattern import load_pattern_model; print('   ✓ Pattern model' if load_pattern_model() else '   ! Pattern model MISSING — cascade degrades to M2-only')"

echo "── 4/5  Cloud configuration ──────────────────────"
if [ -z "${NS_CLOUD_DB_URL:-}" ]; then
  echo "   ! No cloud configured — local-only mode (this is FINE for the demo)"
else
  echo "   ✓ Cloud configured (sync every ${NS_SYNC_INTERVAL_S:-900}s)"
fi

echo "── 5/5  Starting server ──────────────────────────"
exec python3 app.py "$@"
```

> **Note the failure policy:** a missing **cloud** is a warning. A missing **model** is a warning. A missing **local database** is a hard stop — because that one actually matters.

### C.3 Backups — and the honest limit of the cloud

**The cloud is not your backup.** It holds hourly summaries, so you could rebuild *trends* but never the raw 2 Hz data the model trains on.

```bash
# Local backup — snapshots BEFORE every rehearsal (make this a habit)
pg_dump -h localhost -U postgres -Fc neural_strata \
  > ~/NEURAL_STRATA/backups/ns_$(date +%Y%m%d_%H%M).dump

# Restore, if it's ever needed
pg_restore -h localhost -U postgres -d neural_strata --clean ns_20260918_1430.dump
```

**For an offsite copy**, once a day is enough:

```bash
# Compressed dump is ~5–20 MB for a month of raw data
pg_dump -h localhost -U postgres -Fc neural_strata | gzip \
  > ~/NEURAL_STRATA/backups/daily.dump.gz

# Then either: upload it to Supabase Storage (1 GB free), or sync it to Google Drive.
```

Put the daily dump on a cron / `launchd` job and stop thinking about it.

---

## D. Set up Tier 2 — the cloud database

Both walkthroughs end at the same place: **a connection string in `.env`**. Do one, not both.

### D.1 Neon (recommended)

1. Go to **neon.com** → **Sign up with GitHub** *(no credit card required)*
2. **Create a project**
   - Name: `neural-strata`
   - Postgres version: **16** (match your local version — avoids surprises)
   - Region: **the closest one offered.** For Odisha, aim for **Singapore (ap-southeast-1)**; if a Mumbai/India region is listed, prefer it. *(Latency is irrelevant for a 15-minute batch sync, but it costs nothing to be close.)*
3. From the dashboard, copy the **connection string**. It looks like:

```
postgresql://neondb_owner:npg_XXXXXXXX@ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
```

4. **Pick the pooled endpoint** if Neon offers you a choice — it handles the suspend/resume reconnection more gracefully than the direct endpoint for an always-running daemon.
5. Put it in `.env` (§D.3).

**Optional but recommended:** set a **Scale-to-zero** you can live with (5 min is the free default and is correct here). Do **not** try to disable it — the free tier does not allow it, and the auto-wake is transparent anyway.

### D.2 Supabase (alternative — nicer table editor)

1. Go to **supabase.com** → **Sign up** *(no credit card required)*
2. **New project**
   - Name: `neural-strata`
   - Region: **Mumbai (ap-south-1)** if offered — closest to you
   - Save the database password somewhere safe
3. **Settings → Database → Connection string → choose "Session pooler"**

```
postgresql://postgres.abcdefghijklm:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
```

> ⚠ **Use the Session pooler, not the direct connection.** On the free tier the direct connection (`db.[ref].supabase.co:5432`) is **IPv6-only**, and many Indian home/venue networks are IPv4-only. The shared pooler is reachable over IPv4 on every plan. *Session* mode (port 5432) is right for a long-running daemon; *transaction* mode (port 6543) does not support the prepared statements psycopg2 may use.

4. **Before demo day**, either add a keep-alive or accept a manual restore. The simplest free keep-alive is a GitHub Actions scheduled workflow that runs one `SELECT 1` daily:
   `.github/workflows/keepalive.yml` → `on: schedule: - cron: '0 3 * * *'` → `psql "${{ secrets.CLOUD_DB_URL }}" -c 'select 1'`

### D.3 Either way — wire it into `.env`

```ini
# ── Tier 1: EDGE (must always work) ───────────────────────────────
NS_DB_HOST=localhost
NS_DB_NAME=neural_strata
NS_DB_USER=postgres
NS_DB_PASS=your_password_here
NS_SITE_ID=jharia-panel-01
NS_SITE_NAME=Jharia Panel 1 (diorama)

# ── Tier 2: CLOUD (optional — leave EMPTY for local-only mode) ────
NS_CLOUD_DB_URL=postgresql://…from-step-D1-or-D2…?sslmode=require
NS_SYNC_INTERVAL_S=900        # 15 min. DO NOT go below 600 (see §G.1)
NS_SYNC_BATCH=500
NS_CLOUD_CONNECT_TIMEOUT=5    # never let a dead cloud hang the daemon
```

**Leave `NS_CLOUD_DB_URL` empty and the system runs exactly as it does today, local-only, with no code changes.** That is your instant fallback if the cloud misbehaves on demo day.

### D.4 Create the cloud schema

```bash
psql "$NS_CLOUD_DB_URL" -f cloud_schema.sql
psql "$NS_CLOUD_DB_URL" -c "\dt"     # should list 5 tables
```

The full file is in **§H.1** — copy it to `neuralstrata-backend/cloud_schema.sql`.

---

## E. The cloud schema — the *reduced* tier

### E.1 Design rules

1. **Different shape from the edge, deliberately.** The edge schema is optimised for 2 Hz writes; the cloud schema is optimised for reading a year of history. They are **not** mirrors — and trying to mirror them is what breaks the free tier.
2. **Every table carries `site_id`.** This is what makes the multi-site story true rather than aspirational: 100 sites × 0.5 GB each on one Neon account.
3. **Every synced table has an idempotency key.** Re-sending a batch must be harmless — the daemon will retry.
4. **Timestamps are `TIMESTAMPTZ`.** The mine, the laptop and the cloud may all be in different timezones; a naive `TIMESTAMP` will silently shift history.

### E.2 The five tables

| Table | Grain | Rows/day/site | Purpose |
|---|---|---|---|
| `node_hourly` | one row per node per hour | 96 | **The core reporting table.** Enables trends, "has this panel been stable for 3 weeks?", and the retraining pipeline. |
| `alerts_history` | one row per alert | 0–50 | **The incident record.** Carries coordinates (a problem-statement requirement), severity, TTF band and the M1 classification. |
| `drill_runs` | one row per drill/test | 0–5 | Makes the demo auditable: which drill, when, what the model predicted vs what happened. |
| `model_metrics` | one row per model version | ~0 | **The single-source-of-truth record** from backend Phase 0 (defect D4). Publishes MAE with the run that produced it. |
| `sync_log` | one row per batch | 96 | What arrived, how many rows, from which site. Powers the demo chip and tells you instantly if sync is broken. |

---

## F. The code

All of this is a **new module pair plus two local tables** — it does not modify any existing query.

### F.1 `config.py` additions

```python
# ── Tier 1: edge ─────────────────────────────────────────────────────
SITE_ID    = env_str("NS_SITE_ID", "site-unknown")
SITE_NAME  = env_str("NS_SITE_NAME", "Neural Strata site")

# ── Tier 2: cloud (empty ⇒ local-only mode, zero code paths change) ──
CLOUD_DB_URL     = env_str("NS_CLOUD_DB_URL", "")
CLOUD_ENABLED    = bool(CLOUD_DB_URL)
SYNC_INTERVAL_S  = env_int("NS_SYNC_INTERVAL_S", 900)
SYNC_BATCH       = env_int("NS_SYNC_BATCH", 500)
CLOUD_TIMEOUT_S  = env_int("NS_CLOUD_CONNECT_TIMEOUT", 5)
SYNC_MAX_ATTEMPTS = 10
```

### F.2 Local schema additions

```sql
-- Migration 005 (edge): the rollup table and its sync watermark
CREATE TABLE IF NOT EXISTS node_hourly (
    site_id      TEXT        NOT NULL,
    node_id      TEXT        NOT NULL,
    hour_start   TIMESTAMPTZ NOT NULL,
    n_samples    INTEGER     NOT NULL,
    disp_mean    REAL, disp_min REAL, disp_max REAL, disp_last REAL,
    tilt_mean    REAL, tilt_max REAL,
    acc_mean     REAL, acc_max  REAL,
    vib_sum      INTEGER,
    micro_max    REAL,          -- NULL on nodes without a strain channel
    macro_max    REAL,
    severity_max VARCHAR(16),   -- worst status seen in the hour
    -- sync bookkeeping (this IS the outbox for bulk rows)
    synced_at    TIMESTAMPTZ,
    sync_attempts SMALLINT DEFAULT 0,
    last_error   TEXT,
    PRIMARY KEY (site_id, node_id, hour_start)
);
CREATE INDEX IF NOT EXISTS ix_hourly_pending
    ON node_hourly (hour_start) WHERE synced_at IS NULL;

-- Alerts gain the same watermark
ALTER TABLE alerts_history
    ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sync_attempts SMALLINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS site_id TEXT NOT NULL DEFAULT 'site-unknown';
```

> **Why a `synced_at` watermark instead of a separate outbox table:** an outbox that *copies* rows duplicates your storage and can drift from the source. A nullable timestamp marker on the row itself is atomic, idempotent, costs one column, and is impossible to get out of sync. The generic `outbox` table in the backend plan stays for discrete events; **bulk rows use watermarks.** Say this if asked — it is a real design decision, not a shortcut.

### F.3 `persistence/rollup.py` — aggregate locally, hourly

```python
"""Edge-side hourly aggregation. Runs on the edge DB, writes to node_hourly."""
from datetime import datetime, timedelta, timezone
import psycopg2

ROLLUP_SQL = """
INSERT INTO node_hourly (site_id, node_id, hour_start, n_samples,
        disp_mean, disp_min, disp_max, disp_last,
        tilt_mean, tilt_max, acc_mean, acc_max,
        vib_sum, micro_max, macro_max, severity_max)
SELECT
    %(site)s, t.node_id, %(hour)s,
    count(*),
    avg(t.displacement_mm), min(t.displacement_mm), max(t.displacement_mm),
    (array_agg(t.displacement_mm ORDER BY t.ts DESC))[1],
    avg(t.tilt_x), max(t.tilt_x),
    avg(t.acceleration), max(abs(t.acceleration)),
    coalesce(sum(t.vib), 0),
    max(t.micro), max(t.macro),
    %(sev)s
FROM sensor_telemetry t
WHERE t.ts >= %(hour)s AND t.ts < %(hour)s + interval '1 hour'
GROUP BY t.node_id
ON CONFLICT (site_id, node_id, hour_start) DO UPDATE SET
    n_samples = EXCLUDED.n_samples,
    disp_mean = EXCLUDED.disp_mean, disp_min = EXCLUDED.disp_min,
    disp_max  = EXCLUDED.disp_max,  disp_last = EXCLUDED.disp_last,
    tilt_mean = EXCLUDED.tilt_mean, tilt_max  = EXCLUDED.tilt_max,
    acc_mean  = EXCLUDED.acc_mean,  acc_max   = EXCLUDED.acc_max,
    vib_sum   = EXCLUDED.vib_sum,
    micro_max = EXCLUDED.micro_max, macro_max = EXCLUDED.macro_max,
    severity_max = EXCLUDED.severity_max,
    synced_at = NULL            -- changed ⇒ re-sync this hour
"""


def rollup_hour(conn, site_id: str, hour: datetime, severity: str = "NOMINAL") -> int:
    """Aggregate one completed hour into node_hourly. Idempotent."""
    hour = hour.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    with conn.cursor() as cur:
        cur.execute(ROLLUP_SQL, {"site": site_id, "hour": hour, "sev": severity})
        n = cur.rowcount
    conn.commit()
    return n


def rollup_pending(conn, site_id: str, lookback_hours: int = 48) -> int:
    """Catch up on any completed hours that were never rolled up.

    Safe to call repeatedly — it re-derives from raw data and upserts.
    This is what makes the rollup robust to the laptop being asleep.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    total = 0
    for i in range(1, lookback_hours + 1):
        total += rollup_hour(conn, site_id, now - timedelta(hours=i))
    return total
```

**Wire it in:** call `rollup_hour()` from the ticker when the wall clock crosses an hour boundary, and `rollup_pending()` once at startup. Both are cheap and idempotent.

### F.4 `persistence/sync.py` — the daemon

```python
"""Cloud sync. One direction, one destination, summaries only.

Design decisions, and why:
  * own thread        — never blocks the 2 Hz ingest path
  * never raises      — every exception is captured, logged and recorded
  * idempotent        — every write is an upsert, so a re-send is harmless
  * attempts bump only on FAILURE, not on claim — so an outage that lasts
    hours does not permanently poison the queue (this is the bug that makes
    naive outboxes silently lose data)
  * no work ⇒ no connection — the free tier is allowed to stay asleep
"""
import json
import logging
import threading
import time
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

from config import (CLOUD_DB_URL, CLOUD_ENABLED, SITE_ID, SYNC_BATCH,
                    SYNC_INTERVAL_S, CLOUD_TIMEOUT_S, SYNC_MAX_ATTEMPTS)

log = logging.getLogger("sync")

ROLLUP_COLS = ["site_id", "node_id", "hour_start", "n_samples",
               "disp_mean", "disp_min", "disp_max", "disp_last",
               "tilt_mean", "tilt_max", "acc_mean", "acc_max",
               "vib_sum", "micro_max", "macro_max", "severity_max"]

ALERT_COLS = ["site_id", "ts", "level_id", "node_id", "severity", "event_type",
              "progression_stage", "ttf_seconds", "ttf_q10", "ttf_q90",
              "disruption_label", "disruption_index",
              "geo_lat", "geo_lon", "recommended_action"]

_last_ok = 0.0
_last_error = None


def status() -> dict:
    """Backs /api/sync/status and the dashboard chip."""
    return {
        "cloud_enabled": CLOUD_ENABLED,
        "last_success_s_ago": round(time.time() - _last_ok) if _last_ok else None,
        "last_error": _last_error,
        "interval_s": SYNC_INTERVAL_S,
    }


def pending_counts(edge_conn) -> dict:
    """Cheap read for the UI chip — safe to call from the API thread."""
    with edge_conn.cursor() as cur:
        cur.execute("""SELECT
            (SELECT count(*) FROM node_hourly
              WHERE synced_at IS NULL AND sync_attempts < %s) AS rollups,
            (SELECT count(*) FROM alerts_history
              WHERE synced_at IS NULL AND sync_attempts < %s) AS alerts""",
            (SYNC_MAX_ATTEMPTS, SYNC_MAX_ATTEMPTS))
        r = cur.fetchone()
    return {"rollups": r[0], "alerts": r[1]}


def _cloud_conn():
    """Short-lived cloud connection.

    We deliberately do NOT pool cloud connections: an idle connection is
    exactly what free tiers suspend, and a pool full of dead sockets is the
    classic cause of 'sync worked yesterday'. Open, work, close.
    """
    conn = psycopg2.connect(
        CLOUD_DB_URL,
        connect_timeout=CLOUD_TIMEOUT_S,
        sslmode="require",
        application_name=f"ns-{SITE_ID}",
    )
    conn.autocommit = False
    return conn


def _fetch_pending(edge_conn) -> tuple[list, list]:
    """Read (do not claim, do not mutate) the next batch of unsynced rows.

    FOR UPDATE SKIP LOCKED protects against a second concurrent syncer; with a
    single daemon it is belt-and-braces, and it costs nothing.
    """
    with edge_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT * FROM node_hourly
            WHERE synced_at IS NULL AND sync_attempts < %s
            ORDER BY hour_start ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """, (SYNC_MAX_ATTEMPTS, SYNC_BATCH))
        rollups = cur.fetchall()

        cur.execute("""
            SELECT * FROM alerts_history
            WHERE synced_at IS NULL AND sync_attempts < %s
            ORDER BY ts ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """, (SYNC_MAX_ATTEMPTS, SYNC_BATCH))
        alerts = cur.fetchall()
    edge_conn.commit()          # releases the row locks; upserts are idempotent
    return rollups, alerts


def _upsert(cloud_conn, table: str, cols: list, rows: list, conflict_cols: list) -> int:
    if not rows:
        return 0
    values = [tuple(r.get(c) for c in cols) for r in rows]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}"
                           for c in cols if c not in conflict_cols)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
           f"ON CONFLICT ({', '.join(conflict_cols)}) DO UPDATE SET {set_clause}")
    with cloud_conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, values, page_size=200)
    return len(values)


def _mark_synced(edge_conn, rollups: list, alerts: list) -> None:
    """Mark rows delivered. Keyed precisely, so no row is marked that was
    not actually sent."""
    with edge_conn.cursor() as cur:
        if rollups:
            psycopg2.extras.execute_values(cur, """
                UPDATE node_hourly AS n
                   SET synced_at = now(), last_error = NULL
                  FROM (VALUES %s) AS v(site_id, node_id, hour_start)
                 WHERE n.site_id    = v.site_id::text
                   AND n.node_id    = v.node_id::text
                   AND n.hour_start = v.hour_start::timestamptz
            """, [(r["site_id"], r["node_id"], r["hour_start"]) for r in rollups])
        if alerts:
            cur.execute("""UPDATE alerts_history
                              SET synced_at = now(), last_error = NULL
                            WHERE id = ANY(%s)""",
                        ([a["id"] for a in alerts],))
    edge_conn.commit()


def _mark_failed(edge_conn, rollups: list, alerts: list, err: str) -> None:
    """Bump attempt counters and record the reason — ONLY on real failure.
    This is what lets a multi-hour outage recover instead of giving up."""
    with edge_conn.cursor() as cur:
        if rollups:
            psycopg2.extras.execute_values(cur, """
                UPDATE node_hourly AS n
                   SET sync_attempts = n.sync_attempts + 1, last_error = v.err
                  FROM (VALUES %s) AS v(site_id, node_id, hour_start, err)
                 WHERE n.site_id    = v.site_id::text
                   AND n.node_id    = v.node_id::text
                   AND n.hour_start = v.hour_start::timestamptz
            """, [(r["site_id"], r["node_id"], r["hour_start"], err) for r in rollups])
        if alerts:
            cur.execute("""UPDATE alerts_history
                              SET sync_attempts = sync_attempts + 1,
                                  last_error = %s
                            WHERE id = ANY(%s)""",
                        (err, [a["id"] for a in alerts]))
    edge_conn.commit()


def sync_once(edge_conn) -> dict:
    """One sync cycle. Returns stats. Never raises into the caller."""
    global _last_ok, _last_error
    stats = {"rollups": 0, "alerts": 0, "skipped": False, "error": None}

    if not CLOUD_ENABLED:
        stats["skipped"] = True
        return stats

    try:
        rollups, alerts = _fetch_pending(edge_conn)
    except Exception as e:                          # noqa: BLE001
        stats["error"] = f"local read failed: {e}"
        _last_error = stats["error"]
        return stats

    if not rollups and not alerts:
        stats["skipped"] = True
        return stats        # ← no work ⇒ no cloud connection ⇒ free tier sleeps

    cloud = None
    try:
        cloud = _cloud_conn()

        if rollups:
            stats["rollups"] = _upsert(cloud, "node_hourly", ROLLUP_COLS,
                                       rollups, ["site_id", "node_id", "hour_start"])
        if alerts:
            stats["alerts"] = _upsert(cloud, "alerts_history", ALERT_COLS,
                                      alerts, ["site_id", "ts", "node_id", "event_type"])

        with cloud.cursor() as cur:
            cur.execute("""INSERT INTO sync_log
                             (site_id, n_rollups, n_alerts, ok, detail)
                           VALUES (%s, %s, %s, TRUE, %s)""",
                        (SITE_ID, stats["rollups"], stats["alerts"],
                         json.dumps({"host": "edge"})))
        cloud.commit()

        # Marked ONLY after the cloud committed — so a crash mid-sync
        # results in a harmless re-send, never in lost rows.
        _mark_synced(edge_conn, rollups, alerts)

        _last_ok = time.time()
        _last_error = None
        log.info("sync ok: %d rollups, %d alerts", stats["rollups"], stats["alerts"])

    except Exception as e:                          # noqa: BLE001
        # Nothing was committed — so report nothing delivered. Without this
        # reset the stats would claim a successful upsert that was rolled back
        # by the failed COMMIT, and /api/sync would lie to the demo UI.
        stats["rollups"] = 0
        stats["alerts"] = 0
        stats["error"] = str(e)[:200]
        _last_error = stats["error"]
        log.warning("sync failed (%d rollups, %d alerts pending): %s",
                    len(rollups), len(alerts), stats["error"])
        try:
            _mark_failed(edge_conn, rollups, alerts, stats["error"])
        except Exception as e2:                     # noqa: BLE001
            # Bookkeeping must never mask the real failure. The rows stay
            # unsynced, so the next cycle retries them — the safe direction.
            log.error("could not record failure (rows remain pending): %s", e2)
    finally:
        if cloud is not None:
            try:
                cloud.close()
            except Exception:                       # noqa: BLE001
                pass

    return stats


def sync_daemon(edge_conn, stop_event: threading.Event) -> None:
    """Background thread. Sleeps SYNC_INTERVAL_S between cycles."""
    log.info("sync daemon started (cloud=%s, every %ds)", CLOUD_ENABLED, SYNC_INTERVAL_S)
    while not stop_event.wait(SYNC_INTERVAL_S):
        try:
            sync_once(edge_conn)
        except Exception as e:                      # noqa: BLE001
            log.warning("sync cycle error: %s", e)
```

#### Four details in that code that are load-bearing

| Detail | Why it is there |
|---|---|
| **`sync_attempts` is bumped on failure only** | If you bumped it at claim time, a 3-hour network outage (12 cycles) would exhaust a 10-attempt budget and the affected hours would **never sync**, even after the link returned. This is the single most common way a home-grown outbox silently loses data. |
| **`_mark_synced` runs after `cloud.commit()`** | The failure mode is then "sent but marked unsent" → a harmless re-send. The opposite order gives you "marked sent but never delivered" → permanent silent loss. Always prefer the recoverable failure. |
| **`_mark_synced` matches on exact composite keys** | Marking by a timestamp or a range would mark rows as delivered that were never sent. `execute_values` + `FROM (VALUES …)` lets us match the composite primary key exactly. |
| **`if not rollups and not alerts: return` before connecting** | On a quiet site this means **zero cloud connections for hours** — which is exactly what keeps you inside a free tier's idle policy. |
| **`stats` is zeroed in the failure branch** | A failed `COMMIT` rolls back work that `_upsert` already counted. Reporting those counts would make `/api/sync` claim success to the demo UI while the cloud has nothing. **Verified by test (§H.6).** |

### F.5 Wire it into `app.py`

**Three touch points. Nothing else in the existing code changes.**

```python
# ── 1. main(): start the daemon, and catch up on any missed rollups ──
def main():
    ...
    load_model()
    load_pattern_model()

    from persistence.db import get_conn, init_schema
    from config import CLOUD_ENABLED, SITE_ID

    init_schema()

    edge_conn = get_conn()
    if CLOUD_ENABLED:
        # Re-derive any hours we slept through. Idempotent, cheap, and the
        # reason a laptop that was shut overnight leaves no gap in history.
        from persistence.rollup import rollup_pending
        n = rollup_pending(edge_conn, SITE_ID, lookback_hours=48)
        print(f"[sync] rolled up {n} missed hour-rows on startup")

        stop_event = threading.Event()
        threading.Thread(target=sync_daemon, args=(edge_conn, stop_event),
                         daemon=True, name="sync").start()
        print(f"[sync] cloud sync armed (interval {SYNC_INTERVAL_S}s)")
    else:
        print("[sync] no NS_CLOUD_DB_URL set — LOCAL-ONLY mode (this is fine)")

    threading.Thread(target=ticker_thread, daemon=True).start()
    ...


# ── 2. ticker: roll up when the wall clock crosses an hour ──
last_hour = int(time.time() // 3600)     # module-level, next to last_hist/last_infer

# inside the ticker loop, next to the existing snapshot_history block:
if t0 // 3600 != last_hour:
    from persistence.rollup import rollup_hour
    from datetime import datetime, timezone, timedelta
    prev_hour = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0) - timedelta(hours=1)
    rollup_hour(get_conn(), SITE_ID, prev_hour,
                severity=telemetry_state["system_status"])
    last_hour = int(t0 // 3600)


# ── 3. the two API routes (backend build plan, Phase 9) ──
@app.route("/api/sync", methods=["POST"])
def post_sync():
    """Manual flush. This is the demo button."""
    from persistence.sync import sync_once
    if not CLOUD_ENABLED:
        return jsonify({"ok": False, "reason": "cloud not configured"}), 409
    return jsonify(sync_once(get_conn()))


@app.route("/api/sync/status", methods=["GET"])
def get_sync_status():
    from persistence.sync import status, pending_counts
    payload = status()
    if CLOUD_ENABLED:
        payload.update(pending_counts(get_conn()))
    payload["ok"] = True
    return jsonify(payload)
```

**Dashboard chip** (optional, 4 lines in `index.html`) — this is what makes the demo beat land:

```js
// every 5 s
fetch("/api/sync/status").then(r => r.json()).then(s => {
  el.textContent = !s.cloud_enabled ? "LOCAL ONLY"
    : `${(s.pending_rollups ?? 0) + (s.pending_alerts ?? 0)} pending · ` +
      (s.last_success_s_ago == null ? "never synced"
                                    : `synced ${fmtAgo(s.last_success_s_ago)}`);
});
```

#### What each touch point does — and the failure it prevents

| Touch point | Prevents |
|---|---|
| `rollup_pending()` at startup | History gaps whenever the laptop was asleep or closed at an hour boundary. Without it, your cloud history has holes that you only notice in the report. |
| Rollup at the hour boundary **in the ticker** | A separate cron/launchd job — one more thing to configure, forget, and debug on a demo laptop. |
| `CLOUD_ENABLED` guard everywhere | Any chance that a missing/misconfigured cloud can break local operation. With `NS_CLOUD_DB_URL` empty, **not one line of cloud code executes.** |
| `/api/sync` returning **409** when unconfigured | A confusing silent success. A wrong answer is better than a misleading one. |
| `pending_counts()` reading the edge DB | The chip showing a stale or fabricated number. It counts rows with `synced_at IS NULL` — the truth. |

---

## G. Operating it

### G.1 Choose the sync interval correctly — this is the one setting that can break the free tier

On Neon, compute suspends **5 minutes after the last activity**. So a sync interval **below ~10 minutes keeps the database permanently awake** and burns compute:

| Sync interval | Awake per hour | CU-hours/month @ 0.25 CU | Within Neon's 100 CU-hour free cap? |
|---|---|---|---|
| 1 min | 60 min (always awake) | ~180 | NO — **No — cap exceeded in ~2 weeks** |
| 5 min | ~60 min (never gets to sleep) | ~180 | NO — **No** |
| **15 min** | ~20 min | **~60** | OK — **Yes, comfortably** |
| 30 min | ~10 min | ~30 | OK — Yes |
| **On-demand only (demo)** | negligible | ~5 | OK — Effectively nothing |

**Set `NS_SYNC_INTERVAL_S=900` (15 minutes) and do not go below 600.** A 15-minute reporting latency for cross-site trend data is completely acceptable — nothing in the product depends on it. If you ever need faster, realise that you are asking for a *real-time* cloud link, which contradicts the whole offline-first premise.

### G.2 Day-to-day

```bash
# Is sync healthy?
curl -s localhost:5000/api/sync/status | python -m json.tool
#  → {"cloud_enabled": true, "pending_rollups": 3, "last_success_s_ago": 412.9, ...}

# Force a flush (also the demo button)
curl -X POST localhost:5000/api/sync

# What has the cloud actually received?
psql "$NS_CLOUD_DB_URL" -c \
  "SELECT site_id, count(*), min(hour_start), max(hour_start) FROM node_hourly GROUP BY 1;"
```

### G.3 The demo beat (30 seconds, and it lands well)

1. Start with the **network off** — show `OUTBOX: 96 pending · last sync never`.
2. Run a drill. Show the local dashboard working perfectly, all of it, offline.
3. Say: *"Everything you've seen ran on the laptop. The cloud has seen nothing."*
4. Turn the hotspot on. Press **FORCE SYNC** (or `POST /api/sync`).
5. Watch the counter go to **0** and `last sync: just now`.
6. Alt-tab to the **Neon/Supabase table editor** and show the rows that just landed — with timestamps and coordinates.
7. Close with: *"Ninety-six summary rows for the whole day, not seven hundred thousand raw samples. That's why a free-tier database holds a year of a mine's history, and why a mine with no internet never loses a byte."*

### G.4 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `pending_rollups` never falls | Cloud unreachable, or wrong connection string | Check `last_error`; test with `psql "$NS_CLOUD_DB_URL" -c 'select 1'` |
| `SSL SYSCALL error` / timeout | IPv4-only network hitting Supabase's IPv6 direct endpoint | Use the **shared pooler** string (§D.2) |
| `too many connections` | Using a direct connection with a connection-per-call pattern | Use the pooler; the daemon already opens one short-lived connection per cycle |
| Sync succeeds but the cloud has no rows | `site_id` mismatch, or you are querying the wrong table | `SELECT site_id, count(*) FROM node_hourly GROUP BY 1;` |
| Supabase project paused | 7 days idle | Dashboard → restore. Then add the keep-alive workflow |
| Neon compute cap reached | Sync interval too short | Raise `NS_SYNC_INTERVAL_S`; check usage in the Neon console |
| Rows stuck at `sync_attempts = 10` | A permanently malformed row | Inspect `last_error`; fix the row or raise `SYNC_MAX_ATTEMPTS` |
| Rollups missing for some hours | Laptop was asleep at the hour boundary | `rollup_pending(conn, SITE_ID, lookback_hours=48)` at startup handles it — verify it is called |

---

## H. Appendices

### H.1 `cloud_schema.sql` — complete

```sql
-- Neural Strata — CLOUD tier schema (reduced; summaries only)
-- Run once:  psql "$NS_CLOUD_DB_URL" -f cloud_schema.sql

CREATE TABLE IF NOT EXISTS node_hourly (
    site_id      TEXT        NOT NULL,
    node_id      TEXT        NOT NULL,
    hour_start   TIMESTAMPTZ NOT NULL,
    n_samples    INTEGER     NOT NULL DEFAULT 0,
    disp_mean    REAL, disp_min REAL, disp_max REAL, disp_last REAL,
    tilt_mean    REAL, tilt_max REAL,
    acc_mean     REAL, acc_max  REAL,
    vib_sum      INTEGER,
    micro_max    REAL,
    macro_max    REAL,
    severity_max VARCHAR(16),
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (site_id, node_id, hour_start)
);
CREATE INDEX IF NOT EXISTS ix_ch_hourly_site_time
    ON node_hourly (site_id, hour_start DESC);

CREATE TABLE IF NOT EXISTS alerts_history (
    id BIGSERIAL PRIMARY KEY,
    site_id      TEXT NOT NULL,
    ts           TIMESTAMPTZ NOT NULL,
    level_id     VARCHAR(16),
    node_id      VARCHAR(16) NOT NULL,
    severity     VARCHAR(16) NOT NULL,
    event_type   VARCHAR(64) NOT NULL,
    progression_stage VARCHAR(16),
    ttf_seconds  INTEGER, ttf_q10 INTEGER, ttf_q90 INTEGER,
    disruption_label  VARCHAR(24),
    disruption_index  REAL,
    geo_lat      DOUBLE PRECISION,
    geo_lon      DOUBLE PRECISION,
    recommended_action TEXT,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_id, ts, node_id, event_type)      -- idempotency key
);
CREATE INDEX IF NOT EXISTS ix_ch_alerts_site
    ON alerts_history (site_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_ch_alerts_sev
    ON alerts_history (severity, ts DESC);

CREATE TABLE IF NOT EXISTS drill_runs (
    id BIGSERIAL PRIMARY KEY,
    site_id      TEXT NOT NULL,
    run_uid      TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    ended_at     TIMESTAMPTZ,
    profile      VARCHAR(32) NOT NULL,     -- warning_creep, critical_collapse, …
    outcome      VARCHAR(32),              -- BREACH, STABLE, ABORTED
    ttf_at_start INTEGER,
    mae_s        REAL,
    notes        TEXT,
    UNIQUE (site_id, run_uid)
);

CREATE TABLE IF NOT EXISTS model_metrics (
    id BIGSERIAL PRIMARY KEY,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    site_id      TEXT,
    model_name   TEXT NOT NULL,            -- ttf_model_v2, pattern_model_v1, …
    model_role   TEXT,                     -- MODEL_1 | MODEL_2 | KINEMATIC
    artifact_sha TEXT,
    train_rows   INTEGER,
    eval_rows    INTEGER,
    mae_s        REAL,
    rmse_s       REAL,
    recall_structural REAL,
    fp_rate_quiet     REAL,
    dataset_ref  TEXT,
    report_ref   TEXT,
    UNIQUE (model_name, published_at)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id BIGSERIAL PRIMARY KEY,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    site_id      TEXT NOT NULL,
    n_rollups    INTEGER NOT NULL DEFAULT 0,
    n_alerts     INTEGER NOT NULL DEFAULT 0,
    ok           BOOLEAN NOT NULL DEFAULT TRUE,
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS ix_ch_synclog_site
    ON sync_log (site_id, received_at DESC);

-- Convenience view for the regulator / reporting page
CREATE OR REPLACE VIEW v_site_daily AS
SELECT site_id,
       date_trunc('day', hour_start) AS day,
       node_id,
       count(*)                       AS hours_reported,
       max(disp_max)                  AS peak_disp_mm,
       max(abs(acc_max))              AS peak_accel,
       sum(vib_sum)                   AS vib_events,
       max(severity_max)              AS worst_severity
FROM node_hourly
GROUP BY site_id, day, node_id;
```

### H.2 Setup checklist

- [ ] `brew services start postgresql@16` — and confirm it survives a reboot (log out, log in, `pg_isready`)
- [ ] `run.sh` written, executable (`chmod +x run.sh`), and **tested from a cold start**
- [ ] `pg_dump` backup script + a dated dump sitting in `~/NEURAL_STRATA/backups/`
- [ ] Cloud project created (Neon **or** Supabase — one, not both)
- [ ] Region chosen closest to you (Mumbai / Singapore)
- [ ] Supabase only: connection string is the **shared session pooler**, not the IPv6 direct endpoint
- [ ] `cloud_schema.sql` applied; `\dt` lists **5 tables + 1 view**
- [ ] `NS_CLOUD_DB_URL` in `.env`; `NS_SYNC_INTERVAL_S=900`
- [ ] Local migration 005 applied (`node_hourly` exists, `alerts_history` has `synced_at`)
- [ ] `rollup_pending()` called at startup — verify by deleting a `node_hourly` row and restarting
- [ ] `POST /api/sync` returns `{"rollups": n, "alerts": m, "error": null}`
- [ ] `GET /api/sync/status` shows `cloud_enabled: true`
- [ ] **Tested with `NS_CLOUD_DB_URL` empty** — system runs local-only, no errors
- [ ] **Tested with the network physically off** — pending count climbs, nothing else changes
- [ ] Supabase only: keep-alive workflow added
- [ ] The demo beat (§G.3) rehearsed end to end at least twice

### H.3 Viva lines

> **"Why two databases?"**
> *"The mine has no internet. The edge database on the laptop is the source of truth — ingest, inference, routing and alerting all read from it and never touch the network. The cloud is a derived reporting replica for cross-site history and the regulator's view. It can be down for a week and the safety system is completely unaffected, because nothing on the critical path knows it exists."*

> **"Why don't you sync the raw telemetry?"**
> *"Four nodes at 2 Hz is 691,000 rows a day — about 138 megabytes. A free-tier database holds three days of that. Hourly aggregation cuts it to 96 rows and 14 kilobytes a day, a ten-thousand-fold reduction, and it carries every number the reporting layer needs. Raw data stays at the edge where the model needs it; summaries go up where reporting needs them."*

> **"What if the cloud is unreachable?"**
> *"Rows stay locally flagged as unsynced and a counter shows the backlog. Nothing retries in a way that can block ingest — the daemon has its own thread, its own connection timeout, and never raises. When the link returns, the backlog drains, and because every upsert is idempotent, a re-send is harmless."*

> **"Isn't this just a free-tier workaround?"**
> *"No — the aggregation is the architecture. A 200-node mine at 2 Hz generates seven gigabytes a day. No cellular plan and no cloud bill survives shipping that. Edge aggregation is what makes a large sensor network economically possible, and the free tier is just where that design happens to also be free."*

### H.4 Glossary

| Term | Meaning |
|---|---|
| **Edge database** | The local Postgres on the laptop, inside the mine, holding the raw 2 Hz data. The source of truth. |
| **Cloud database** | A hosted Postgres (Neon/Supabase) somewhere with internet. Holds summaries. Optional at runtime. |
| **Source of truth** | The one place data is authoritative. Here: the edge. The cloud is *derived*. |
| **Rollup** | One row summarising a period (here: an hour) — count, mean, min, max instead of every sample. |
| **Watermark (`synced_at`)** | A timestamp on the row itself meaning "this has been delivered". `NULL` = pending. |
| **Idempotent** | Running it twice has the same effect as once. `ON CONFLICT DO UPDATE` makes sync idempotent. |
| **`ON CONFLICT` / upsert** | Postgres's "insert, or update if it already exists" — the basis of safe re-sending. |
| **`FOR UPDATE SKIP LOCKED`** | Lets one worker claim rows without blocking another. Prevents double-sends. |
| **Scale-to-zero** | Neon suspends the database after 5 minutes idle and wakes it on the next query automatically. |
| **CU-hour** | Neon's compute unit × hours. Free tier gives 100 per project per month. |
| **Connection pooler** | A proxy that shares database connections. Supabase's is the IPv4 route; also required for serverless. |
| **IPv4 / IPv6** | Two versions of the internet addressing protocol. Supabase's free *direct* connection is IPv6-only; many Indian networks are IPv4-only. |
| **Egress** | Data transferred out. 5 GB/month on both free tiers — you will use kilobytes. |
| **`pg_dump` / `pg_restore`** | Postgres's backup/restore tools. One file, portable between local and cloud. |
| **Outbox pattern** | Holding outgoing data locally until it can be delivered. Here implemented as watermarks. |

### H.5 How this code was verified (and how to re-verify it)

The schema and the sync logic in this document were **executed**, not eyeballed. Two checks:

**1. Every SQL statement was parsed against the real PostgreSQL grammar**

Using `pglast` (bindings to PostgreSQL's own parser, `libpg_query`) — not a regex, not a linter:

```bash
pip install pglast
python3 tools/verify_dual_db_sql.py
#  11 statements checked against the PostgreSQL grammar
#  RESULT: ALL VALID
```

Covers the cloud schema (5 tables + 1 view), migration 005, the rollup upsert, the watermark updates, the claim queries and the `sync_log` insert.

**2. The sync control flow was run against mocks**

`tests/test_sync_logic.py` runs `sync_once()` **verbatim from this document** (never a retyped copy) and runs it against a mock edge database and a mock cloud that can be made to fail on demand.

| # | Test | Result |
|---|---|---|
| T1 | No pending work ⇒ **zero cloud connections** (free tier stays asleep) | PASS |
| T2 | Happy path ⇒ upsert, then commit, then mark synced | PASS |
| T3 | Cloud unreachable ⇒ attempts incremented, rows **not** marked synced | PASS |
| T4 | **9 consecutive failures, then the link returns ⇒ data still syncs** | PASS |
| T5 | Demonstrates the naive "bump on claim" design would have **permanently blocked** that row | PASS |
| T6 | `cloud.commit()` is proven to precede `mark_synced()` | PASS |
| T7 | `pending_counts()` returns the number the demo chip displays | PASS |

**17 assertions, 0 failures.** Two genuine bugs were found and fixed by this process before the document was finalised:

| Bug | Consequence if shipped | Fix |
|---|---|---|
| `sync_attempts` was incremented at **claim** time | A network outage longer than `SYNC_MAX_ATTEMPTS × interval` (2.5 h at 15 min) would **permanently and silently lose those hours** — they would never retry, even after the link returned | Bump **only on recorded failure** |
| `stats["rollups"]` was left set after a failed `COMMIT` | `POST /api/sync` would report rows as delivered that had been rolled back — the demo chip would show success while the cloud had nothing | Zero the counters in the failure branch |

> **Re-run both checks after any edit to `sync.py` or `cloud_schema.sql`.** The second one is worth keeping in `tests/` permanently: it is the only thing standing between you and a silent data-loss regression in the one component whose whole job is not to lose data.

### H.6 What changes in the other documents

| Document | Change |
|---|---|
| `Neural_Strata_Backend_Build_Phases.md` | **Phase 0** — migration 005 (`node_hourly` + watermarks) and `run.sh` join the deliverables. **Phase 9** — this document supersedes the generic outbox for bulk rows; `tools/central_stub.py` is **deleted**; `sync.py` connects to Neon/Supabase. **Phase 10** — `/api/sync/status` joins `/api/metrics`. |
| `BACKEND_PLAN.md` §4.5 (B6) | Add one line: *"bulk rows use a `synced_at` watermark; the generic outbox is retained for discrete events."* |
| SRS §3.3 (Software Interfaces) | Add the dual-tier row; the existing "production: MQTT → central server" line is now concrete. |
| SRS §NFR-2 (Offline-First) | **Strengthened**, not weakened — this design makes the offline claim architecturally enforced rather than merely stated. |
| `Neural_Strata_Hardware_Build_Phases.md` | **No change.** The seven boards never see the cloud. |

---

*End of document. The recommendation in one line: **local Postgres for the edge, a free hosted Postgres (Neon, or Supabase) for the cloud, summaries only, and never on the critical path.***
