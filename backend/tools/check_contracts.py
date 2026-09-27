#!/usr/bin/env python3
"""
check_contracts.py — the code must match the frozen contracts.
==============================================================

``check_numbers.py`` guards the *published numbers*. This guards the *interfaces*.
It is the pre-demo gate: run it before every rehearsal, and in CI if there ever
is CI.

    python3 tools/check_contracts.py            # exit 0 clean, 1 on drift
    python3 tools/check_contracts.py --verbose

What is asserted
----------------
1. **Schema version** — ``SCHEMA_VERSION == 2``, and v1 is still accepted (the
   firmware in the field emits bare frames).
2. **Feature order** — ``engine.features.FEATURES`` equals both ``scaler.json``
   and ``models/MANIFEST.json``. This is the assertion that catches a new feature
   list running against an old scaler.
3. **Topology** — ``NODE_IDS``, ``LEVELS`` and ``ACTIVE_NODES`` agree, and
   ``validate_config()`` returns nothing.
4. **Routes** — the live Flask route table equals ``tests/fixtures/routes.json``,
   captured from the running v4.4 server. No route may be added, removed or
   renamed without this file being updated on purpose.
5. **Telemetry paths** — every path in ``REQUIRED_TELEMETRY_PATHS`` is present in
   a real ``/api/telemetry`` response.
6. **Session format** — ``SESSION_CSV_FIELDS`` is unchanged, so old recordings
   still replay.
7. **No orphaned references** — ``app.py`` contains no reference to the code that
   Phase 0 moved out (a stale reference fails at *request* time, not boot time).
8. **Database** — if PostgreSQL is reachable, the tables the schema file promises
   exist. Skipped, not failed, when the database is down: a rehearsal without a
   database is legal.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import config                                                  # noqa: E402
import contracts                                               # noqa: E402
from engine import features                                    # noqa: E402

OK, BAD, SKIP = "ok  ", "DRIFT", "skip"


class Report:
    def __init__(self, verbose: bool):
        self.verbose = verbose
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = ""):
        self.rows.append((status, name, detail))
        if self.verbose or status != OK:
            print(f"  {status} {name:34} {detail}")

    def check(self, name: str, condition: bool, detail: str = ""):
        self.add(OK if condition else BAD, name, detail if not condition else "")
        return condition

    @property
    def failures(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == BAD)


def check_schema(r: Report) -> None:
    r.check("schema version == 2", contracts.SCHEMA_VERSION == 2,
            f"SCHEMA_VERSION is {contracts.SCHEMA_VERSION}")
    r.check("v1 still accepted", 1 in contracts.SUPPORTED_VERSIONS,
            "v1 must stay supported — the field firmware emits bare frames")
    r.check("4 nodes / 2 levels / 1 active set",
            list(config.NODE_IDS) == ["node_1", "node_2", "node_3", "node_4"],
            f"NODE_IDS = {config.NODE_IDS}")
    covered = sorted(n for lvl in config.LEVELS.values() for n in lvl)
    r.check("every node on exactly one level", covered == sorted(config.NODE_IDS),
            f"LEVELS cover {covered}")
    r.check("ACTIVE_NODES ⊂ NODE_IDS", set(config.ACTIVE_NODES) <= set(config.NODE_IDS),
            f"ACTIVE_NODES = {config.ACTIVE_NODES}")
    problems = config.validate_config()
    r.check("validate_config() clean", not problems, "; ".join(problems))


def _read_artifact(path, label: str) -> dict | None:
    """Parse a model artifact, or report which one is unreadable.

    Returns ``None`` rather than raising, so the caller decides whether the file
    is worth one failed check or several. A bare ``json.loads`` here produced a
    stack trace with no filename — the operator could see *that* something was
    wrong but not *what*, which is the difference between a 3-second fix and a
    30-minute hunt.
    """
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        head = path.read_text(errors="replace")[:48].replace("\n", "\\n")
        hint = (" It starts with 'version https://git-lfs', so it is a Git LFS pointer "
                "rather than the artifact — run `git lfs pull`.") if head.startswith("version https") \
            else (" The file is empty — the artifact did not survive the copy into this "
                  "tree; restore it from the source tree.") if not head \
            else ""
        print(f"  DRIFT {label:34} {path.name} is not JSON ({e.__class__.__name__}): "
              f"starts {head!r} ({path.stat().st_size} B).{hint}")
        return None


def check_features(r: Report) -> None:
    r.check("16 features in canonical order", features.N_FEATURES == 16,
            f"{features.N_FEATURES} features")
    parsed: dict[str, dict | None] = {}
    for label, key in (("scaler", "scaler"), ("manifest", "manifest")):
        path = config.MODEL_FILES.get(key)
        if not path or not path.exists():
            r.add(SKIP, f"{label} feature order", f"{path} absent")
            continue
        data = _read_artifact(path, label)
        parsed[key] = data
        if data is None:
            r.add(BAD, f"{label} feature order", f"{path.name} is not readable JSON")
            continue
        got = list(data.get("features") or [])
        r.check(f"{label} feature order matches", got == list(features.FEATURES),
                f"{label} has {len(got)} features, extractor has {features.N_FEATURES}")
    manifest_path = config.MODEL_FILES["manifest"]
    if manifest_path.exists():
        man = parsed.get("manifest")          # read once, above — never twice
        if man is None:
            # One unreadable manifest is one problem, not three. Without this the
            # first check fires, the next two raise on the same file, and the
            # operator gets a traceback instead of the count.
            r.add(BAD, "manifest is readable", f"{manifest_path.name} could not be parsed")
        else:
            r.check("manifest schema == 2", man.get("schema") == 2, f"schema={man.get('schema')}")
            r.check("manifest boot policy stated",
                    "NEVER CRASH" in str(man.get("boot_policy", "")), "boot_policy missing")
            r.check("manifest names model 2",
                    bool((man.get("model_2") or {}).get("file")), "model_2.file missing")


def check_routes(r: Report) -> None:
    frozen_path = BACKEND / "tests" / "fixtures" / "routes.json"
    if not frozen_path.exists():
        r.add(SKIP, "route table", "no frozen routes.json")
        return
    import app as edge
    frozen_doc = _read_artifact(frozen_path, "routes fixture")
    if frozen_doc is None:
        r.add(BAD, "route table", f"{frozen_path.name} is not readable JSON")
        return
    frozen = frozen_doc["endpoints"]
    live = sorted(f"{rule.rule} [{','.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))}]"
                  for rule in edge.app.url_map.iter_rules())
    added = sorted(set(live) - set(frozen))
    removed = sorted(set(frozen) - set(live))
    r.check("route table unchanged", not added and not removed,
            f"added {added} removed {removed}")

    with edge.app.test_client() as client:
        resp = client.get("/api/telemetry")
        if resp.status_code != 200:
            r.check("/api/telemetry responds", False, f"HTTP {resp.status_code}")
        else:
            from tests.shape import key_paths
            paths = key_paths(resp.get_json())
            missing = [p for p in contracts.REQUIRED_TELEMETRY_PATHS if p not in paths]
            r.check("required telemetry paths present", not missing, f"missing {missing}")
        ai = client.get("/api/ai")
        body = ai.get_json() or {}
        r.check("/api/ai status string valid",
                body.get("status") in ("ACTIVE", "WARMING UP", "FALLBACK"),
                f"status={body.get('status')!r}")
        r.check("/api/ai forecast keys match ACTIVE_NODES",
                set(body.get("forecasts") or {}) == set(config.ACTIVE_NODES),
                f"forecasts={sorted(body.get('forecasts') or {})}")


def check_session_format(r: Report) -> None:
    frozen = ("t_ms", "node", "disp", "tilt", "acc", "vib", "micro", "macro", "uid", "source")
    r.check("session.csv fields unchanged", contracts.SESSION_CSV_FIELDS == frozen,
            f"{contracts.SESSION_CSV_FIELDS}")


def check_no_orphans(r: Report) -> None:
    """A reference to code that moved fails at request time, not boot time."""
    src = (BACKEND / "app.py").read_text()
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    orphans = [tok for tok in ("model_info", "model_last_ok", "_live_val_at",
                               "compute_live_features", "_load_dotenv", "psycopg2")
               if re.search(r"\b" + re.escape(tok) + r"\b", body)]
    # `model_ttf_current` is a deliberate kept shim; `\bmodel_ttf\b` would not match it.
    orphans += [tok for tok in ("model_ttf",) if re.search(r"\bmodel_ttf\b", body)]
    r.check("no reference to moved code", not orphans, f"still referencing {orphans}")
    # Phase 5 (§5.9) deleted `run_model_inference`: the ticker now calls
    # `cascade_engine.cascade_tick`, and `ttf_model.run_inference` is reached
    # from inside the cascade, not from app.py. The guard's intent — "the app
    # delegates inference to an engine, it does not own a model" — is unchanged,
    # so the delegate it looks for moves with the code. Keeping the old token
    # would have forced app.py to keep a dead call site purely to satisfy a test.
    for needed in ("ttf_model.load_ttf_model", "cascade_engine.cascade_tick",
                   "ttf_model.model_status", "db.insert_telemetry", "validate_config"):
        if needed not in src:
            r.check(f"delegate present: {needed}", False, "missing from app.py")


def check_database(r: Report, strict: bool) -> None:
    from persistence import db
    st = db.status()
    if not st["connected"]:
        r.add(SKIP, "database", f"{st['host']}/{st['database']} unreachable — "
                                "the pipeline degrades without it")
        if strict:
            r.add(BAD, "database (--strict)", "required but unreachable")
        return
    expected = {"sensor_telemetry", "rfid_evacuation_log", "schema_migrations", "node_hourly"}
    missing = expected - set(st["tables"])
    r.check("schema applied", not missing, f"missing tables {sorted(missing)}")
    r.check("no pending migrations", not st["pending"], f"pending {st['pending']}")
    r.check("ledger records 001 and 005", {1, 5} <= set(st["applied"]),
            f"applied {st['applied']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assert code == frozen contract")
    ap.add_argument("--verbose", action="store_true", help="print passing checks too")
    ap.add_argument("--strict", action="store_true", help="fail when the database is down")
    args = ap.parse_args(argv)

    r = Report(args.verbose)
    print("Neural Strata — contract check")
    print(f"  backend {BACKEND}")
    print()
    check_schema(r)
    check_features(r)
    check_routes(r)
    check_session_format(r)
    check_no_orphans(r)
    check_database(r, args.strict)

    print()
    if r.failures:
        print(f"FAIL — {r.failures} contract violation(s).")
        return 1
    skipped = sum(1 for s, _, _ in r.rows if s == SKIP)
    print(f"PASS — {len(r.rows) - skipped} checks passed"
          + (f", {skipped} skipped" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
