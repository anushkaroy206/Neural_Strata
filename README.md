# neural-strata

Underground strata-collapse early warning: 4 sensor nodes, 2 level hubs and 1 main hub, an edge laptop that forecasts time-to-failure, and a dashboard an operator can actually read.

**This repository is built phase by phase.** The plan of record is
`docs/Neural_Strata_Directory_Structure.md` (the tree and the phase map),
`docs/Neural_Strata_Backend_Build_Phases.md` (what each phase contains) and
`docs/Neural_Strata_Hardware_Build_Phases.md` (H0–H9). Where any two documents
disagree, the directory structure wins.

---

## Cold start

```bash
pip install -r requirements.txt
cp .env.example .env                 # optional; every key has a default
./run.sh --init-db                   # apply migrations once, then serve
```

> Copied the folder rather than cloning it? A copy can lose the executable bit —
> if `./run.sh` says *Permission denied*, run `bash run.sh` or `chmod +x run.sh`.

`./run.sh` walks seven steps and stops at the first failure: interpreter →
packages → `validate_config()` → database → artifacts → **gates** → serve. The
gates are `check_contracts.py` and `check_numbers.py`; if either is red, the
server does not start.

Open <http://127.0.0.1:5000>. Other entry points:

```bash
./run.sh --no-serial                 # API + dashboard, no USB
./run.sh --check                     # run the gates and exit
python3 backend/tools/replay.py rec.csv --speed 10 --no-serial
```

## What Phase 0 delivered

| Deliverable | Why it exists |
|---|---|
| `backend/config.py` | Every constant, env-parsed, validated **before** Flask starts. Tuning a threshold is an `.env` edit, not a source edit. |
| `backend/contracts.py` | Schema version, node IDs, frame validators, the `session.csv` format. A malformed frame is rejected with a reason, not a `KeyError`. |
| `backend/persistence/{db.py,schema.sql,migrations/}` | Pooled connections and a real migration ledger. Additive-only, idempotent. |
| `backend/engine/{features,ttf}.py` | The 16-feature extractor and MODEL 2, moved out of `app.py` verbatim. |
| `backend/tools/{record,replay}.py` | Record a session once; develop every later phase offline at 10× speed. |
| `backend/tools/{check_numbers,check_contracts}.py` | The two gates. They exist because the repository once said **5.4 s MAE** in four places and **0.10 s** in the artifact that ships. |
| `backend/tests/` | 87 tests, including feature parity against the notebook that trained the model. |

Phase 0 changed **no route and no response shape**. That is enforced, not asserted:
`tests/fixtures/` holds the JSON captured from the running server *before* the
extraction, and `tests/test_api_contracts.py` diffs against it.

## The two gates

```bash
cd backend
python3 tools/check_contracts.py     # code == frozen interface
python3 tools/check_numbers.py       # documents == shipped artifact
```

`check_numbers.py` reads the canonical metrics **out of `models/model_meta.json`**
— it never types them. A document may quote an older number, but the line has to
say so (`historical`, `provenance`, `raw harvest`, …). Otherwise the build fails.

## Layout

```
backend/      the edge server (Flask), persistence, the AI engines
firmware/     PlatformIO: 3 environments — main_hub · level_hub · node
ai/           notebooks, datasets, export artifacts, reports
frontend/     the operator dashboard (PWA)
infra/        docker-compose, cloud schema, the runbook
docs/         the plan set, kept with the code
runtime/      regenerable state: the black-box truth store (gitignored)
recordings/   session.csv captures for replay (gitignored)
```

## Database

PostgreSQL on the edge laptop is the **source of truth**; the cloud replica is
derived and never on the critical path. Migrations are **additive only** — no
`DROP`, no rewriting rows, ever, because the demo laptop's database is a *living*
database with rehearsal data in it.

```bash
cd backend
python3 -m persistence.db --status          # ledger: applied / pending / tables
python3 -m persistence.db --init            # apply what is pending (idempotent)
python3 -m persistence.db --dump-schema     # regenerate schema.sql from migrations
```

`migrations/*.sql` are the single source of truth; `schema.sql` is **generated**
from them, because two hand-maintained copies of the same DDL is exactly how a
plan and a database drift apart.

| Migration | Phase | What |
|---|---|---|
| 001 | 0 | `sensor_telemetry` (+2 indexes), `rfid_evacuation_log`, `schema_migrations` |
| 002 | 1 | widen `sensor_telemetry`: vib, micro, macro, rssi, batt, level |
| 003 | 8 | `alerts_history`, `alert_deliveries`, `outbox` |
| 005 | 0 | `node_hourly` (+ partial index), the `synced_at` watermark |

Take `pg_dump` before a rehearsal; `./restore.sh <dump>` puts it back.

## Recording and replay

```bash
cd backend
python3 tools/record.py --seconds 120 --out /tmp/session.csv
python3 tools/replay.py /tmp/session.csv --speed 10 --no-serial
python3 tools/replay.py /tmp/session.csv --speed 0        # inspect only
```

The replay does not re-implement anything: it swaps the serial reader thread for
a replay reader and calls the same `ingest_line()`. If it had its own path, it
would stop predicting what the live system does, and then it would stop being
trusted.

## Firmware

Three PlatformIO environments — `main_hub`, `level_hub`, `node` — with the
instance chosen by a build flag (`-DLEVEL=1/2`, `-DNODE_ID=1..4`) so a board class
has one firmware, not four. Phase 0 creates the environments as stubs; Phase 1
fills `src/node/`.

## Status

**Phase 0 complete** — 87 tests, both gates green, migrations applied and
idempotent. Next: **Phase 1** (B1, the four-node state engine). See
`docs/Neural_Strata_Backend_Build_Phases.md` §PHASE 1.
