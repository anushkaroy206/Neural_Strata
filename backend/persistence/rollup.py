"""B6 — hourly rollup: aggregate at the edge, ship the summary, keep the raw.

Why this file is the *bulk* half of cloud sync
----------------------------------------------
Two tiers, two shapes (§E.1 rule 1). The edge stores 2 Hz raw telemetry because it
has to: the dashboard graphs it, the models window it, and a post-incident question
is answered from it. The cloud stores **one row per node per hour** (96/day/site),
because the only questions asked of cloud history are trends — *"has this panel been
stable for three weeks?"*, *"what did last month look like?"* — and those do not need
seven million rows to answer.

The `synced_at` watermark on `node_hourly` **is** the outbox for this path. A copy
would duplicate the storage and could drift from the source; a nullable timestamp on
the row itself is atomic, idempotent, costs one column and cannot get out of sync.

Idempotence is the property that makes everything else safe
-----------------------------------------------------------
Both functions are upserts keyed on `(site_id, node_id, hour_start)`, and both are
safe to call any number of times. That is what lets `rollup_pending()` re-derive the
last 48 hours at every boot: a laptop that was closed at an hour boundary leaves no
gap, and re-running does not double anything. `synced_at` is reset to NULL on every
re-derivation, so a changed hour is re-sent — the row's own content is the trigger.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: One hour, aggregated. `disp_last` is the newest sample's displacement rather than
#: the mean, because "where is the panel now" and "where was it on average" are
#: different questions and the hour row should answer the first one.
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
    n_samples    = EXCLUDED.n_samples,
    disp_mean    = EXCLUDED.disp_mean, disp_min = EXCLUDED.disp_min,
    disp_max     = EXCLUDED.disp_max,  disp_last = EXCLUDED.disp_last,
    tilt_mean    = EXCLUDED.tilt_mean, tilt_max = EXCLUDED.tilt_max,
    acc_mean     = EXCLUDED.acc_mean,  acc_max  = EXCLUDED.acc_max,
    vib_sum      = EXCLUDED.vib_sum,
    micro_max    = EXCLUDED.micro_max, macro_max = EXCLUDED.macro_max,
    severity_max = EXCLUDED.severity_max,
    -- changed ⇒ re-sync this hour. NULL is the queue for this path: the row's own
    -- content is the trigger, so nothing has to be told that the hour moved.
    synced_at    = NULL
"""


def hour_start(when: datetime) -> datetime:
    """The hour this instant belongs to, in UTC. One definition, used everywhere."""
    return when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


#: The same aggregate as ROLLUP_SQL, over an arbitrary window, returning rows
#: instead of writing them. Phase 9's telemetry outbox item is *this* aggregate —
#: one summary per node per minute — which is why it is here rather than written a
#: second time in `app.py`: two copies of an aggregate are two definitions of
#: "mean", and they drift.
#:
#: Note what is **not** here: no `severity_max`. The severity is what the *system*
#: believed over the window, and that lives in the live state, not in the rows — so
#: the caller supplies it, exactly as `rollup_hour` takes it as an argument.
MINUTE_AGGREGATE_SQL = """
SELECT t.node_id,
       count(*)                                        AS n_samples,
       avg(t.displacement_mm)                          AS disp_mean,
       min(t.displacement_mm)                          AS disp_min,
       max(t.displacement_mm)                          AS disp_max,
       (array_agg(t.displacement_mm ORDER BY t.ts DESC))[1] AS disp_last,
       avg(t.tilt_x)                                   AS tilt_mean,
       max(t.tilt_x)                                   AS tilt_max,
       avg(t.acceleration)                             AS acc_mean,
       max(abs(t.acceleration))                        AS acc_max,
       coalesce(sum(t.vib), 0)                         AS vib_sum,
       max(t.micro)                                    AS micro_max,
       max(t.macro)                                    AS macro_max,
       (array_agg(t.rssi ORDER BY t.ts DESC))[1]       AS rssi,
       (array_agg(t.batt ORDER BY t.ts DESC))[1]       AS batt
  FROM sensor_telemetry t
 WHERE t.ts >= %(start)s AND t.ts < %(end)s
 GROUP BY t.node_id
 ORDER BY t.node_id
"""


def minute_aggregates(conn, end, window_s: int = 60) -> list[dict]:
    """Every node's summary over `[end - window_s, end)`. Reads only.

    Half-open on purpose: a sample stamped exactly on the boundary belongs to
    exactly one window, so consecutive windows partition the stream instead of
    double-counting the second they meet on.
    """
    end = end if isinstance(end, datetime) else datetime.fromtimestamp(
        float(end), tz=timezone.utc)
    end = end.replace(second=0, microsecond=0) if window_s >= 60 else end
    start = end - timedelta(seconds=int(window_s))
    # A plain cursor and `cur.description`, rather than `RealDictCursor`: this
    # module imports no psycopg2 at all (it is driven through the connection the
    # caller owns), and one dict conversion here is cheaper than a dependency that
    # exists only to name the columns of one query.
    with conn.cursor() as cur:
        cur.execute(MINUTE_AGGREGATE_SQL, {"start": start, "end": end})
        names = [d[0] for d in (cur.description or [])]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def rollup_hour(conn, site_id: str, hour: datetime, severity: str = "NOMINAL") -> int:
    """Aggregate one completed hour into `node_hourly`. Idempotent. Returns rows.

    `severity` is the worst status seen in the hour, and the caller is expected to
    pass it from the live state at the moment the hour closes — the row records what
    the *system* believed, which is the thing a post-mortem compares against the
    telemetry it believed it from.
    """
    hour = hour_start(hour)
    with conn.cursor() as cur:
        cur.execute(ROLLUP_SQL, {"site": site_id, "hour": hour, "sev": severity})
        n = cur.rowcount
    conn.commit()
    return n


def rollup_pending(conn, site_id: str, lookback_hours: int = 48) -> int:
    """Catch up on every completed hour that was never rolled up (or changed).

    Called once at boot. Safe to call repeatedly: it re-derives from raw data and
    upserts, so the worst case is wasted work, never wrong rows. This is the reason
    a laptop that slept through an hour boundary leaves no hole in the cloud — the
    hole would otherwise be discovered in the report, which is far too late.
    """
    now = hour_start(datetime.now(timezone.utc))
    total = 0
    for i in range(1, int(lookback_hours) + 1):
        total += rollup_hour(conn, site_id, now - timedelta(hours=i))
    return total


def pending_rollups(conn, max_attempts: int | None = None) -> int:
    """How many hour-rows are waiting to go up. The chip's other half."""
    import config
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*) FROM node_hourly
                        WHERE synced_at IS NULL AND sync_attempts < %s""",
                    (int(max_attempts or config.SYNC_MAX_ATTEMPTS),))
        return int(cur.fetchone()[0])
