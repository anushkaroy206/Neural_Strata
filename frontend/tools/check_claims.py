#!/usr/bin/env python3
"""
Acceptance checker for the frontend change request (CR-01 … CR-14).

    cd neuralstrata-frontend
    python3 tools/check_claims.py

Prints PASS/FAIL per rule with line numbers, then a summary. Exit code 1 if any
rule fails, so it can gate a pull request.

Rules with a "dual acceptance" allow the panel to stay in the UI as long as it
is honestly labelled — a relabelled simulation is acceptable, an unlabelled one
is not. Everything else is a hard requirement.

Usage:
    python3 tools/check_claims.py
    python3 tools/check_claims.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "index.html"

# The 11 seeded multi-mine rows. A multi-site regulator view is legitimate; an
# UNLABELLED fabricated incident history is not (see CR-06).
SEEDED_MINES = ["DHANSAR", "KUSUNDA", "GOVINDPUR", "JEENAGORA", "SUDAMDIH",
                "KATRAS", "KORBA", "ARGADA", "SAYAL", "BISHRAMPUR", "GIDI"]


def lines_with(text: str, needle: str, regex: bool = False) -> list[int]:
    pat = re.compile(needle) if regex else None
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if (pat.search(line) if regex else needle in line):
            out.append(i)
    return out


def rule_absent(text: str, needle: str, regex: bool = False) -> tuple[bool, str]:
    hits = lines_with(text, needle, regex)
    if hits:
        shown = ", ".join(map(str, hits[:6])) + (" …" if len(hits) > 6 else "")
        return False, f"{len(hits)} occurrence(s) at line {shown}"
    return True, "absent"


def function_body(text: str, signature: str) -> str:
    """Extract a JS function body by brace matching. Used so a rule can look
    INSIDE one function instead of anywhere in a 13,000-line file (a loose
    global search made CR-07 pass on an unrelated 'PREVIEW' button)."""
    i = text.find(signature)
    if i < 0:
        return ""
    j = text.find("{", i)
    if j < 0:
        return ""
    depth, k = 0, j
    while k < len(text):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j:k + 1]
        k += 1
    return text[j:]


def rule_present(text: str, needle: str) -> tuple[bool, str]:
    hits = lines_with(text, needle)
    if hits:
        return True, f"present at line {', '.join(map(str, hits[:6]))}"
    return False, "NOT FOUND"


def build_rules(text: str) -> list[dict]:
    rules: list[dict] = []

    def add(cid, title, ok, detail, action):
        rules.append({"id": cid, "title": title, "ok": ok, "detail": detail, "action": action})

    # ---- CR-01 the two 12-node models -------------------------------------- #
    mesh_decl = lines_with(text, "const MESH_CELLS=")
    risk_decl = lines_with(text, "const RISK=[")
    nods = lines_with(text, "NODES.push({")
    labelled = ("ILLUSTRATIVE" in text) or ("NOT INSTRUMENTED" in text)
    present = bool(mesh_decl or risk_decl or nods)
    ok = (not present) or labelled
    add("CR-01", "12-node mesh models removed or relabelled",
        ok,
        ("removed" if not present else
         f"model 1 declared at {mesh_decl or '-'} (surface cells); model 2 at {risk_decl or '-'} "
         f"with {len(nods)} NODES.push() site(s); no ILLUSTRATIVE/NOT INSTRUMENTED label"),
        "delete both models, or add the label ILLUSTRATIVE LAYOUT · NOT INSTRUMENTED")

    # ---- CR-02 solar / battery / rssi -------------------------------------- #
    sol = lines_with(text, "solar", regex=True)
    add("CR-02", "solar references removed (no solar in the build)",
        not sol, "absent" if not sol else f"{len(sol)} line(s): {', '.join(map(str, sol[:8]))}",
        "remove the solar field, the Battery · solar row and the solar-panel mesh")

    # ---- CR-03 the 12/12 counts -------------------------------------------- #
    c1 = lines_with(text, "12 / 12")
    c2 = lines_with(text, "12/12")
    tot = c1 + c2
    add("CR-03", "hardcoded 12/12 counts replaced by API-derived values",
        not tot, "absent" if not tot else f"{len(tot)} line(s): {', '.join(map(str, sorted(tot)))}",
        "replace with the real node set (today 2 / 4) or derive from /api/telemetry")

    # ---- CR-04 STM32 ------------------------------------------------------- #
    stm = lines_with(text, "STM32")
    add("CR-04", "STM32 replaced by ESP32",
        not stm, "absent" if not stm else f"line {', '.join(map(str, stm))}",
        "the nodes are ESP32 boards")

    # ---- CR-05 LoRa radio facts -------------------------------------------- #
    tot = sorted(set(lines_with(text, "433 MHz") + lines_with(text, "SX1278")))
    add("CR-05", "433 MHz / SX1278 replaced by ESP-NOW 2.4 GHz",
        not tot, "absent" if not tot else f"{len(tot)} line(s): {', '.join(map(str, sorted(tot)))}",
        "the prototype uses ESP-NOW; LoRa/SX1278 is deferred road-map")

    # ---- CR-06 ledger claims ----------------------------------------------- #
    hc = lines_with(text, "hash-chain")
    imm = lines_with(text, "IMMUTABLE")
    te = lines_with(text, "tamper-evident")
    tot = sorted(set(hc + imm + te))
    pointed = "/api/audit/export.csv" in text
    add("CR-06", "hash-chained/immutable ledger claim removed or pointed at the real export",
        (not tot) or pointed,
        ("now backed by /api/audit/export.csv" if pointed else
         f"claimed at line {', '.join(map(str, tot))} but the RAM ledger is the only implementation"),
        "delete the claim, or point the panel at GET /api/audit/export.csv")

    # ---- CR-07 SMS panel --------------------------------------------------- #
    body = function_body(text, "function wireSms(")
    if not body:
        add("CR-07", "SMS panel wired to the backend, or labelled as a preview",
            False, "wireSms() not found — panel may have been renamed; verify by hand",
            "POST to /api/alerts/broadcast, or label the panel PREVIEW · NOT DISPATCHED")
    else:
        wired = ("apiUrl(" in body) or ("fetch(" in body) or ("post(" in body)
        labelled = any(m in body.upper() for m in ("NOT DISPATCHED", "PREVIEW · ", "SIMULATED"))
        add("CR-07", "SMS panel wired to the backend, or labelled as a preview",
            wired or labelled,
            ("wireSms() now calls the backend" if wired else
             ("wireSms() is labelled as a preview" if labelled else
              "wireSms() still mints 'SMS-xxxx' and prints 'delivered … gateway ACK received' "
              "with no network call anywhere in the function")),
            "POST to /api/alerts/broadcast, or label the panel PREVIEW · NOT DISPATCHED")

    # ---- CR-08 hardcoded PINs ---------------------------------------------- #
    pins = lines_with(text, "pin:'")
    add("CR-08", "hardcoded demo PINs removed or moved out of the shipped bundle",
        not pins, "absent" if not pins else f"line {', '.join(map(str, pins))}",
        "keep the role picker, drop the credentials; the server guard in roles.py is the real control")

    # ---- CR-09 node list literal ------------------------------------------- #
    lit = lines_with(text, "NODES=['node_1','node_2']")
    add("CR-09", "node list read from the API instead of the literal",
        not lit, "absent" if not lit else f"hardcoded at line {', '.join(map(str, lit))}",
        "Phase 1 delivers node_1..node_4; read the set from /api/telemetry")

    # ---- CR-10 topology ---------------------------------------------------- #
    bad_hubs = lines_with(text, "HUB 2") + lines_with(text, "HUB 3")
    bad_exits = lines_with(text, "SECONDARY EMERGENCY INCLINE")
    tot = bad_hubs + bad_exits
    add("CR-10", "graph matches the frozen layout (1 hub per level, 2 exits)",
        not tot, "absent" if not tot else f"{len(tot)} line(s): {', '.join(map(str, sorted(tot)))}",
        "frozen map: J1-J4, EXIT1/EXIT2, one hub per level, cross J2<->J3")

    # ---- CR-11 seeded multi-mine history ----------------------------------- #
    present = [m for m in SEEDED_MINES if m in text]
    labelled = "DEMO DATA" in text
    add("CR-11", "seeded multi-mine history removed or labelled DEMO DATA",
        (not present) or labelled,
        (f"{len(present)} mine name(s) present, DEMO DATA label {'found' if labelled else 'MISSING'}"
         if present else "absent"),
        "seed one site, or label the rows DEMO DATA")

    # ---- CR-12 version + survey claims ------------------------------------- #
    v = lines_with(text, "v4.2")
    dgps = lines_with(text, "DGPS")
    tot = v + dgps
    add("CR-12", "version v4.4 and the DGPS survey claim removed",
        not tot, "absent" if not tot else f"line {', '.join(map(str, sorted(tot)))}",
        "app.py is v4.4; there is no GPS module, so no DGPS elevation")

    # ---- CR-13 PWA linked -------------------------------------------------- #
    ok, detail = rule_present(text, "manifest.webmanifest")
    add("CR-13", "PWA linked (manifest + service worker)", ok, detail,
        "add the four lines to <head>; the manifest and SW already exist beside this file")

    # ---- CR-14 mode indicator present ------------------------------------- #
    ok, detail = rule_present(text, "tkMode")
    add("CR-14", "LIVE/SIM indicator still present (and promoted to the decks)", ok, detail,
        "keep tkMode and render it inside each deck, not only the ticker strip")

    return rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--file", default=str(TARGET))
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2

    text = path.read_text(encoding="utf-8")
    rules = build_rules(text)
    passed = sum(1 for r in rules if r["ok"])

    if args.json:
        print(json.dumps({"file": str(path), "passed": passed,
                          "total": len(rules), "rules": rules}, indent=2))
        return 0 if passed == len(rules) else 1

    width = 78
    print("=" * width)
    print("Neural Strata — frontend change-request acceptance check")
    print(f"file: {path}")
    print("=" * width)
    for r in rules:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['id']}  {r['title']}")
        print(f"         {r['detail']}")
        if not r["ok"]:
            print(f"         fix: {r['action']}")
    print("-" * width)
    print(f"{passed}/{len(rules)} rules pass")
    if passed < len(rules):
        print("\nNot shippable yet: the failures above are claims the system cannot back.")
    else:
        print("\nAll clear.")
    return 0 if passed == len(rules) else 1


if __name__ == "__main__":
    raise SystemExit(main())
