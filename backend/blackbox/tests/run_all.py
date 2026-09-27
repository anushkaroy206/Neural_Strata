"""Plain test runner — no pytest required.

usage:  python3 tests/run_all.py
"""
import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MODULES = ("test_protocol", "test_gate", "test_ingest", "test_gateway_contract")

passed = failed = 0
failures = []
for name in MODULES:
    mod = importlib.import_module(name)
    tests = [(n, f) for n, f in vars(mod).items()
             if n.startswith("test_") and callable(f)]
    print(f"\n{name}  ({len(tests)} tests)")
    for n, f in tests:
        try:
            f()
            passed += 1
            print(f"  PASS  {n}")
        except Exception as e:
            failed += 1
            failures.append((name, n, traceback.format_exc()))
            print(f"  FAIL  {n}: {type(e).__name__}: {e}")

print(f"\n{passed} passed, {failed} failed")
for name, n, tb in failures:
    print(f"\n--- {name}::{n} ---\n{tb}")
sys.exit(1 if failed else 0)
