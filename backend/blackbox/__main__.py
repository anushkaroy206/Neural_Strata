"""python3 -m neuralstrata-backend-v5  — read the hub protocol on stdin."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest import Ingestor          # noqa: E402
from store import Store              # noqa: E402

store = Store(":memory:")
ing = Ingestor(store, log=lambda m: print(f"[ingest] {m}", file=sys.stderr))
stats = ing.run(sys.stdin)
h = store.hub()
print(f"[ingest] lines={stats.lines} rows={stats.rows} dupes={stats.duplicates} "
      f"unknown={stats.unknown} pending={store.pending_total()}", file=sys.stderr)
for row in store.chunks():
    print(f"[chunk] {row['chunk_id']:>4}  state={row['state']:<6} "
          f"received={row['received_rows']:<5} expected={row['expected_rows']} "
          f"dropped={row['dropped_rows']}", file=sys.stderr)
for cid, reason in ing.sweep_reasons().items():
    print(f"[gate ] chunk {cid}: {reason}", file=sys.stderr)
