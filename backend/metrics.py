"""
Phase 12 (B16) — counters, timers, and the numbers the presentation stands on.
==============================================================================

The rule this module exists to enforce, from Phase 12's acceptance criterion:

    *every number in the presentation was produced by the suite.*

That sentence rules out almost everything a dashboard normally does. A "100 %
synced" badge is a claim; `bytes_gzip / bytes_raw` on the last batch is a
measurement. A "healthy" indicator is a claim; `count(gate_refused{reason=…})`
is a measurement — and the difference matters most on the day the thing is
broken, which is the only day anyone reads these numbers.

So: no derived adjectives here, no scores, no health. **Counters, gauges and
timers only**, each one incremented at the point the thing actually happened,
each one readable through `/api/metrics` as JSON (for the deck and the failure
suite) and as Prometheus text (for anything that speaks that, which includes
every ops tool an examiner will have heard of).

Why hand-rolled and not `prometheus_client`
-------------------------------------------
Two reasons, both structural. This project runs on a laptop with no internet at
demo time, and a metrics library that is *missing* at boot must not be able to
stop the safety system starting — an observability dependency that can take down
the thing it observes is a bad trade at any size. And the exposition format is
thirty lines of text: writing those thirty lines means the numbers are readable
by a human with `curl`, with no library version to match.

The cardinality rule
--------------------
Label values are **fixed sets**, never user input, never chunk ids, never
timestamps: `gate_refused{reason="rows missing"}` is a metric, `gate_refused
{chunk_id=4711}` is a memory leak wearing a hat. There is a test for this.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, Dict, Iterable, Optional

#: Label values that are allowed to exist. Anything not in a set is folded into
#: `other` rather than creating a new series — because a bounded set of labels is
#: the difference between a metrics endpoint and a slow memory leak.
LABEL_SETS: Dict[str, tuple] = {
    "gate_refused": ("still open", "row count unknown", "rows missing", "db short",
                     "dropped rows", "not yet confirmed", "safety margin",
                     "already acknowledged", "hub", "other"),
    "sync_result": ("ok", "http_error", "timeout", "refused", "busy", "other"),
    "recovery_event": ("state_query", "replay_sent", "rows_backfilled", "exhausted",
                       "other"),
    "alert": ("NO_SD", "DROPPED", "SYNC_STALLED", "CARD_FULL", "HUB_SILENT",
              "GATE_BLOCKED", "other"),
    "api": ("telemetry", "extended", "history", "ai", "site", "map", "health",
            "metrics", "sync", "blackbox", "cloud", "sim", "other"),
}


def _fold(name: str, label: Optional[str]) -> Optional[str]:
    if label is None:
        return None
    allowed = LABEL_SETS.get(name)
    if not allowed:
        return None                      # this metric takes no labels at all
    return label if label in allowed else "other"


class Registry:
    """Thread-safe counters, gauges and timers. One instance is the default."""

    def __init__(self, *, clock: Callable[[], float] = time.perf_counter):
        self._lock = threading.RLock()
        self._clock = clock
        self._counters: Dict[tuple, float] = {}
        self._gauges: Dict[str, float] = {}
        self._timers: Dict[str, dict] = {}
        self._started = time.time()

    # ------------------------------------------------------------------ writing
    def counter(self, name: str, n: float = 1.0, *, label: Optional[str] = None) -> float:
        """Add to a counter. Returns the new total, so a caller can assert on it."""
        key = (name, _fold(name, label))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + float(n)
            return self._counters[key]

    def gauge(self, name: str, value: float) -> float:
        """Set a value that goes up and down (rows pending, files retained, MB free).

        Gauges are **set**, never accumulated: a pending count that only went up
        would be a counter wearing the wrong hat, and somebody would eventually
        read it as a queue depth and panic.
        """
        with self._lock:
            self._gauges[name] = float(value)
            return self._gauges[name]

    def timing(self, name: str, seconds: float) -> float:
        """Record one observation. Keeps count, total, max and the last value.

        Deliberately not a histogram: the four numbers here answer every question
        this project actually asks ("how slow was the last upload", "what is the
        worst", "how many"), and a full histogram would be four hundred lines of
        bucket edges nobody chose on purpose.
        """
        with self._lock:
            t = self._timers.setdefault(name, {"count": 0, "total": 0.0, "max": 0.0,
                                               "last": 0.0})
            t["count"] += 1
            t["total"] += float(seconds)
            t["max"] = max(t["max"], float(seconds))
            t["last"] = float(seconds)
            return float(seconds)

    @contextmanager
    def timeit(self, name: str):
        """`with METRICS.timeit("sync.push"): …` — records even when it raises.

        The exception path is the interesting one: an upload that timed out after
        30 s is exactly the measurement somebody needs, and the naive version
        (`start = now(); …; METRICS.timing(name, now() - start)`) skips it.
        """
        start = self._clock()
        try:
            yield
        finally:
            self.timing(name, self._clock() - start)

    def timed(self, name: str):
        """Decorator form, for Flask handlers and the suite's steps."""
        def wrap(fn):
            def inner(*a, **kw):
                with self.timeit(name):
                    return fn(*a, **kw)
            inner.__name__ = getattr(fn, "__name__", "wrapped")
            inner.__doc__ = getattr(fn, "__doc__", None)
            return inner
        return wrap

    # ------------------------------------------------------------------ reading
    def snapshot(self) -> dict:
        """The whole registry, in a shape both a test and a JSON response accept."""
        with self._lock:
            out = {
                "uptime_s": round(time.time() - self._started, 1),
                "counters": {}, "gauges": {}, "timers": {},
            }
            # A counter written without a label shares its name with a labelled one
            # of the same metric; merging them under the name keeps a reader from
            # having to know which form the writer used.
            for (name, label), value in sorted(self._counters.items()):
                slot = out["counters"].setdefault(name, {})
                slot["total" if label is None else label] = value
            out["gauges"] = {k: v for k, v in sorted(self._gauges.items())}
            for name, t in sorted(self._timers.items()):
                out["timers"][name] = {
                    "count": t["count"],
                    "total_s": round(t["total"], 6),
                    "max_s": round(t["max"], 6),
                    "last_s": round(t["last"], 6),
                    "mean_s": round(t["total"] / t["count"], 6) if t["count"] else 0.0,
                }
            return out

    def prometheus(self) -> str:
        """Text exposition. Names get the project prefix so they are greppable."""
        lines: list[str] = []
        with self._lock:
            for (name, label), value in sorted(self._counters.items()):
                metric = f"ns_{name}_total"
                if label is None:
                    lines.append(f"{metric} {value:g}")
                else:
                    lines.append(f'{metric}{{kind="{label}"}} {value:g}')
            for name, value in sorted(self._gauges.items()):
                lines.append(f"ns_{name} {value:g}")
            for name, t in sorted(self._timers.items()):
                base = f"ns_{name}_seconds"
                lines.append(f"{base}_count {t['count']:g}")
                lines.append(f"{base}_sum {t['total']:g}")
                lines.append(f"{base}_max {t['max']:g}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """For tests, and for the failure suite between scenarios.

        Between scenarios matters: `test a drop is counted` asserting on
        `drops_total >= 1` after five other scenarios have also dropped rows is
        the kind of test that passes for the wrong reason.
        """
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._timers.clear()
            self._started = time.time()


#: The process-wide registry. Modules import this, never a copy of it: two
#: registries would produce two answers to "how many drops", which is precisely
#: the ambiguity this project keeps designing out.
METRICS = Registry()


# ── Flask wiring ─────────────────────────────────────────────────────────────
#: Path -> the label the API metric uses. Anything unlisted is `other`, so adding
#: a route never adds a series.
_API_LABELS = (
    ("/api/telemetry", "telemetry"), ("/api/extended", "extended"),
    ("/api/history", "history"), ("/api/ai", "ai"), ("/api/site", "site"),
    ("/api/map", "map"), ("/api/health", "health"), ("/api/metrics", "metrics"),
    ("/api/sync", "sync"), ("/api/blackbox", "blackbox"), ("/api/cloud", "cloud"),
    ("/api/sim", "sim"),
)


def api_label(path: str) -> str:
    for prefix, label in _API_LABELS:
        if path.startswith(prefix):
            return label
    return "other"


def install(app) -> None:
    """Count every request and time it. Call once, from `app.py`.

    Times **every** request including the 404s — a route that suddenly answers
    404 is a number worth having, and a middleware that only counted successful
    requests would be silent on exactly the day it was needed.
    """
    from flask import request

    @app.before_request
    def _ns_metrics_before():                             # pragma: no cover - trivial
        request.environ["ns_t0"] = time.perf_counter()

    @app.after_request
    def _ns_metrics_after(resp):
        t0 = request.environ.get("ns_t0")
        label = api_label(request.path)
        METRICS.counter("api_requests", label=label)
        METRICS.counter("api_status", label="other" if resp.status_code >= 500 else None)
        if resp.status_code >= 400:
            METRICS.counter("api_errors", label=label)
        if t0 is not None:
            METRICS.timing(f"api_latency_{label}", time.perf_counter() - t0)
        return resp


def live_gauges(gateway, sync_state: Optional[dict] = None) -> dict:
    """The gauges that come from live objects rather than from a counter site.

    Called by `/api/metrics` on each read so the *current* values (rows pending,
    files retained, missing rows) are never a stale copy of themselves. It reads;
    it never writes anything to the gateway — a metrics endpoint that mutated the
    system it is measuring would be a genuinely excellent bug.
    """
    out = {}
    if gateway is None:
        return out
    try:
        health = gateway.health()
        out["bbx_chunks_retained"] = float(len(health.get("chunks", [])))
        out["bbx_rows"] = float(health.get("rows", 0))
        out["bbx_pending_rows"] = float(health.get("pending", 0))
        out["bbx_acks_sent"] = float(health.get("acks_sent", 0))
        out["bbx_files_held"] = float(sum(1 for c in health.get("chunks", [])
                                          if not c.get("ack_sent_at")))
        out["bbx_rows_missing"] = float(gateway.reconciler.total_missing())
        out["bbx_hub_alive"] = 1.0 if gateway.store.hub()["last_seen"] else 0.0
    except Exception:                        # noqa: BLE001 — the endpoint never fails
        return out
    if sync_state:
        for key in ("pending", "sent", "failed"):
            if key in sync_state:
                out[f"cloud_{key}"] = float(sync_state[key])
    return out
