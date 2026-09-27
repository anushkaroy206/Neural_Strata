#!/usr/bin/env python3
"""
verify_dual_db_sql.py — the two checks that must pass after **any** sync.py edit.
=================================================================================

    python3 tools/verify_dual_db_sql.py            # both checks
    python3 tools/verify_dual_db_sql.py --sql-only # skip the live database

Why a tool and not a test
-------------------------
The failure this exists for is not a behaviour, it is a *mismatch*: three lists
have to agree — the edge schema, the cloud schema, and the columns `sync.py`
actually sends — and they live in three files that are edited by three different
phases. A unit test can only check one of them at a time; this checks all three
against each other, in about a second, and it is the thing you run after touching
`ROLLUP_COLS`.

**Check 1 — the two schemas agree with the code.**
  Every column `sync.py` sends must exist in `infra/sql/cloud_schema.sql`, and the
  conflict target of every upsert must be a real key in that file. A column the
  cloud does not have is an INSERT that fails *in the field*, at 15-minute
  intervals, with the edge looking perfectly healthy — the worst possible failure
  shape.

**Check 2 — the watermarks are really there.**
  `synced_at`, `sync_attempts` and `site_id` must exist on both `node_hourly` and
  `alerts_history` **in the live edge database**, and the `outbox` table must have
  the columns `outbox.py` writes. This is the check that would have caught the
  Phase-9 defect: migration 005 added the watermark columns to `alerts_history`
  inside a guard, 003 created the table *after* it in time, and nothing queried
  those columns until the sync daemon tried — so for a whole phase the schema was
  "convergent" on paper and missing in practice. A guard that defers work to
  another migration needs a check that the other migration did it.

**Check 3 — the cloud files actually apply.**
  Both `infra/sql/*.sql` files are executed into a throwaway schema, inside a
  transaction that is rolled back, and the objects they create are counted. This is
  the check that was missing: `v_site_daily` was written with a correlated subquery
  over a grouped column, which Postgres **refuses to create at all** — so
  `cloud_schema.sql` had never successfully loaded onto a fresh central database,
  and nothing noticed, because check 1 parses the file as text and a text file that
  will not run still looks like a schema. *Parseable* is not *applicable*.

Exit code is the number of failed checks, so it drops straight into `run.sh`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
CLOUD_SQL = ROOT / "infra" / "sql" / "cloud_schema.sql"
INGEST_SQL = ROOT / "infra" / "sql" / "ingest_function.sql"

sys.path.insert(0, str(BACKEND))

#: What each synced table must be keyed by, and where the code declares it. The
#: conflict targets live in `sync.py`'s calls; these are the spec's (§A.3.7 / §E.2)
#: and the SQL files' — if a phase changes one, this table is the place it breaks.
CONFLICT_KEYS = {
    "node_hourly": ["site_id", "node_id", "hour_start"],
    "alerts_history": ["site_id", "ts", "node_id", "event_type"],
}

#: Columns `persistence/outbox.py` writes, and the ones `sync.py`'s watermark
#: queries read. Missing any of these is a runtime error on the first sync cycle.
OUTBOX_COLUMNS = ["id", "ts", "kind", "payload", "status", "priority", "attempts",
                  "queued_at", "next_try_at", "last_error", "sent_at", "site_id", "ref"]
WATERMARK_COLUMNS = ["synced_at", "sync_attempts"]


def _columns_from_sql(path: Path, table: str) -> list[str]:
    """Parse `CREATE TABLE [IF NOT EXISTS] <table> ( … )` out of a .sql file."""
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"CREATE TABLE (?:IF NOT EXISTS )?{re.escape(table)}\s*\((.*?)\n\);",
                  text, re.S | re.I)
    if not m:
        return []
    # Comments first, line-wise. The column list in this repo is heavily
    # commented, and a whole-line comment *between* two columns lands inside the
    # same comma-fragment as the column that follows it — so stripping comments
    # after the split deletes the column along with the note about it. (That is
    # exactly how the first version of this parser reported six real columns as
    # missing, twice, in two different ways. A checker earns trust by being
    # obviously right, so it is written the long way round on purpose.)
    body = "\n".join(raw.split("--")[0] for raw in m.group(1).splitlines())

    # Split on commas that are **not** inside parentheses, because a DDL line can
    # hold several columns (`disp_mean REAL, disp_min REAL, disp_max REAL, …`) and
    # a type can hold a comma (`NUMERIC(10, 2)`). The first version of this parser
    # took only the first token of each line, which reported six of the cloud's own
    # columns as missing — a tool that cries wolf about a schema that is fine is
    # worse than no tool, so the split is done properly rather than approximately.
    parts: list[str] = []
    depth = 0
    current = ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)

    cols: list[str] = []
    for part in parts:
        line = part.strip()
        if not line:
            continue
        if line.upper().startswith(("PRIMARY", "UNIQUE", "FOREIGN", "CHECK",
                                    "CONSTRAINT", ")")):
            continue
        name = line.split()[0].strip().strip('"')
        if name:
            cols.append(name)
    return cols


def check_schemas_agree() -> list[str]:
    """CHECK 1 — what sync.py sends exists in the cloud schema, keyed as declared."""
    from persistence import sync

    problems: list[str] = []
    if not CLOUD_SQL.exists():
        return [f"missing {CLOUD_SQL.relative_to(ROOT)} — the cloud schema is not in the repo"]

    for table, cols in (("node_hourly", sync.ROLLUP_COLS),
                        ("alerts_history", sync.ALERT_COLS)):
        cloud_cols = _columns_from_sql(CLOUD_SQL, table)
        if not cloud_cols:
            problems.append(f"{table}: not found in cloud_schema.sql")
            continue
        missing = [c for c in cols if c not in cloud_cols]
        if missing:
            problems.append(f"{table}: sync.py sends {missing} — the cloud table "
                            f"has no such column")
        keys = CONFLICT_KEYS[table]
        keyless = [k for k in keys if k not in cloud_cols]
        if keyless:
            problems.append(f"{table}: upsert conflict target {keyless} is not a "
                            f"column of the cloud table")
        not_null = [c for c in ("site_id",) if c not in cloud_cols]
        if not_null:
            problems.append(f"{table}: {not_null} missing — every cloud row must "
                            f"say which site it came from")

    # The ingest function's own key set must match the table constraints, or a
    # duplicate batch would insert twice.
    if INGEST_SQL.exists():
        ingest = INGEST_SQL.read_text(encoding="utf-8")
        for table, keys in CONFLICT_KEYS.items():
            if f"ON CONFLICT ({', '.join(keys)})" not in ingest:
                problems.append(f"ingest_function.sql: no ON CONFLICT "
                                f"({', '.join(keys)}) for {table} — a re-sent batch "
                                f"would duplicate rows")
    return problems


class Unreachable(RuntimeError):
    """The check could not run — which is not the same as the check failing."""


def check_watermarks_live() -> list[str]:
    """CHECK 2 — the live edge database really has the columns the sync path reads."""
    problems: list[str] = []
    try:
        from persistence import db
    except Exception as exc:                                    # noqa: BLE001
        return [f"cannot import persistence.db: {exc}"]

    conn = db.try_get_conn()
    if conn is None:
        # Not a mismatch — an unavailable check. `run.sh` promises to work from a
        # cold clone where postgres may not be running, and a tool that fails a
        # gate because it could not look would teach people to ignore it. It says
        # so loudly instead, and the operator runs it again once the DB is up.
        raise Unreachable("edge database unreachable")
    try:
        for table, required in (("node_hourly", WATERMARK_COLUMNS + ["last_error"]),
                                ("alerts_history", WATERMARK_COLUMNS + ["site_id"]),
                                ("outbox", OUTBOX_COLUMNS)):
            with conn.cursor() as cur:
                cur.execute("""SELECT column_name FROM information_schema.columns
                                WHERE table_name = %s""", (table,))
                have = {r[0] for r in cur.fetchall()}
            if not have:
                problems.append(f"{table}: table does not exist in the edge database")
                continue
            missing = [c for c in required if c not in have]
            if missing:
                problems.append(f"{table}: missing {missing} — the sync path will "
                                f"raise UndefinedColumn on its first cycle")

        # The migrations must really be in the ledger, not merely in the folder:
        # an unapplied file is the same schema as no file.
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            applied = [r[0] for r in cur.fetchall()]
        for needed, why in ((5, "the sync watermarks on alerts_history"),
                            (6, "the outbox table"),
                            (7, "the watermark columns the daemon updates"),
                            (8, "the UNIQUE index `enqueue_outbox`'s ON CONFLICT "
                                "infers — without it every alert enqueue fails with "
                                "InvalidColumnReference and the row is dropped")):
            if needed not in applied:
                problems.append(f"migration {needed:03d} is not applied ({why}) — run "
                                f"`python3 -m persistence.db --init`")
    finally:
        db.release(conn)
    return problems


def check_sql_applies() -> list[str]:
    """CHECK 3 — every SQL file loads, into a scratch schema, and is rolled back.

    Nothing here is meant to survive: the schema is created inside the transaction,
    the files are executed against it, the created objects are counted, and the whole
    thing is rolled back — so running this against a live central database changes
    nothing at all. Counting the objects matters as much as the absence of an error:
    a file that applies because it is empty would otherwise pass, and "the schema
    loaded" would mean "there was nothing in it".
    """
    problems: list[str] = []
    try:
        from persistence import db
    except Exception as exc:                                    # noqa: BLE001
        return [f"cannot import persistence.db: {exc}"]

    conn = db.try_get_conn()
    if conn is None:
        raise Unreachable("edge database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS ns_sqlcheck CASCADE")
            cur.execute("CREATE SCHEMA ns_sqlcheck")
            cur.execute("SET search_path TO ns_sqlcheck, public")
            for path, wants in ((CLOUD_SQL, {"BASE TABLE": 5, "VIEW": 1}),
                                (INGEST_SQL, {"FUNCTION": 1})):
                # One savepoint per file: a failure must not poison the rest of the
                # run, and it must not leave a half-built schema behind either. Each
                # file is executed **once** — a per-object loop would report the same
                # broken file twice, and a gate that repeats itself reads as two
                # problems when there is one.
                cur.execute("SAVEPOINT ns_file")
                try:
                    cur.execute(path.read_text(encoding="utf-8"))
                    cur.execute("RELEASE SAVEPOINT ns_file")
                except Exception as exc:                        # noqa: BLE001
                    cur.execute("ROLLBACK TO SAVEPOINT ns_file")
                    problems.append(f"{path.name} does not apply: "
                                    f"{type(exc).__name__}: {str(exc).strip().splitlines()[0]}")
                    continue
                with conn.cursor() as probe:                    # a second cursor, so
                    probe.execute("""SELECT table_type, count(*)
                                       FROM information_schema.tables
                                      WHERE table_schema = 'ns_sqlcheck'
                                   GROUP BY table_type""")
                    counts = dict(probe.fetchall())
                    probe.execute("""SELECT count(*) FROM information_schema.routines
                                      WHERE routine_schema = 'ns_sqlcheck'""")
                    counts["FUNCTION"] = probe.fetchone()[0]
                for kind, want in wants.items():
                    if counts.get(kind, 0) < want:
                        problems.append(f"{path.name} applied but produced "
                                        f"{counts.get(kind, 0)} {kind}(s) (expected "
                                        f"{want}) — an empty file is not a schema")
        conn.rollback()          # nothing this check created survives it
    except Unreachable:
        raise
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS ns_sqlcheck CASCADE")
            conn.commit()
        except Exception:                                       # noqa: BLE001
            conn.rollback()
        db.release(conn)
    return problems



def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--sql-only", action="store_true",
                    help="check the SQL files and the code only; skip the live database")
    args = ap.parse_args(argv)

    failures = 0
    print("── check 1 · sync.py ↔ infra/sql (cloud schema) ────────────────────")
    for p in check_schemas_agree():
        print(f"  ✗ {p}")
        failures += 1
    if not failures:
        print("  ✓ node_hourly and alerts_history match what sync.py sends, keyed as declared")

    skipped = False
    if args.sql_only:
        print("── check 2 · skipped (--sql-only) ─────────────────────────────────")
    else:
        print("── check 2 · the live edge database ───────────────────────────────")
        try:
            problems = check_watermarks_live()
        except Unreachable as why:
            print(f"  ! skipped — {why} (start postgres, or pass --sql-only)")
            problems = []
            skipped = True
        for p in problems:
            print(f"  ✗ {p}")
        failures += len(problems)
        if not problems and not skipped:
            print("  ✓ node_hourly, alerts_history and outbox carry every column the "
                  "sync path reads")

    if args.sql_only:
        print("── check 3 · skipped (--sql-only) ─────────────────────────────────")
    else:
        print("── check 3 · do the cloud SQL files actually apply? ───────────────")
        try:
            problems = check_sql_applies()
        except Unreachable as why:
            print(f"  ! skipped — {why} (start postgres, or pass --sql-only)")
            problems = []
            skipped = True
        for p in problems:
            print(f"  ✗ {p}")
        failures += len(problems)
        if not problems and skipped:
            pass
        elif not problems:
            print("  ✓ cloud_schema.sql → 5 tables + v_site_daily; "
                  "ingest_function.sql → ingest_batch() (applied in a rolled-back schema)")

    verdict = "PASS" if not failures else "FAIL"
    if not failures and skipped:
        verdict = "PASS (check 2 skipped)"
    print(f"\n{verdict} — {failures} problem(s)")
    return min(failures, 255)


if __name__ == "__main__":
    sys.exit(main())
