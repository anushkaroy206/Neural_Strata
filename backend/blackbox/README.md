# neuralstrata-backend-v5

Fresh backend for Neural Strata SIH 2026, built around the
**Underground Blackbox & Cloud Sync** addendum.

Status: **Phase 0 + Phase 1 complete** (skeleton, frozen protocol, ingest, outbox, delete gate).

## Layout

| File | What it is |
|---|---|
| `protocol.py` | The frozen hub ↔ laptop serial protocol. Both directions. Never change without bumping `PROTOCOL_VERSION`. |
| `schema.sql` | Truth store, chunk bookkeeping, outbox, cloud batches, hub status. SQLite now, Postgres-portable. |
| `store.py` | DB access. `ingest_row()` is the only write path for telemetry — it inserts the row **and** enqueues it in one transaction. |
| `gate.py` | The delete gate. Pure function, five conditions, explains every refusal. |
| `ingest.py` | Line-at-a-time ingest loop + the periodic ACK sweep. |
| `../sim/hub_sim.py` | Fake hub that emits the real protocol, so nothing here needs hardware. (Lives in `backend/sim/` — see the directory structure §2.) |
| `tests/` | 24 assertions over protocol, gate, ingest, replay idempotency. |

## Run

```bash
cd neuralstrata-backend-v5
python3 -m pytest tests -q                 # or: python3 tests/run_all.py
python3 ../sim/hub_sim.py --chunks 3 --rows 40 | python3 -m blackbox.ingest
```

## The three invariants this code protects

1. **A row is enqueued for the cloud exactly once, ever.** `UNIQUE(chunk_id, seq)`
   on both tables, and the outbox insert only happens when the telemetry insert
   was genuinely new. Replaying chunk 42 a hundred times changes nothing.
2. **No chunk is deleted while any copy of it is still at risk.** The gate
   requires closed **and** count-matched **and** undropped **and** fully synced
   **and** past a 60 s safety margin.
3. **A bad line never stops the loop.** Malformed wire data becomes `Unknown`,
   gets counted, and ingest carries on. The hub does not get to crash us.
