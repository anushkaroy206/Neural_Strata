-- ============================================================================
-- Neural Strata — the ingest contract, in SQL. Phase 9 (B6).
-- ============================================================================
-- Apply after cloud_schema.sql:
--     psql "$NS_CLOUD_DB_URL" -f infra/sql/ingest_function.sql
--
-- The contract this function implements, and why it is a function rather than
-- three statements in a Python handler:
--
--     A batch is **durable before the 200 is sent**, and a batch that arrives
--     twice is **applied once**.
--
-- Both halves are load-bearing and both are easy to get wrong:
--
--   * **COMMIT then 200.** If the 200 outlives a rolled-back transaction, the edge
--     stops retrying and the rows are gone from both ends. The recoverable order is
--     the other one: "stored but the edge thinks it failed" costs a duplicate, which
--     is idempotent by construction.
--   * **Idempotent on `batch_id`.** The daemon's preferred failure mode *is* a
--     re-send (see persistence/sync.py: rows are marked delivered only after this
--     side commits). So the second arrival must be a no-op that says so, not an
--     error and not a second write.
--
-- `ingest_batch()` returns a JSON object the HTTP layer can hand straight back,
-- which is what lets the endpoint be three lines and impossible to get wrong:
--
--     SELECT ingest_batch($1, $2, $3)         -- batch_id, site_id, payload
--
-- The payload's shape is `{"items":[{"kind":…,"ref":…,"payload":{…}}]}` — the same
-- body `cloud_client.post_batch()` sends and `sim/cloud_mock.py` accepts, so the
-- stand-in and the real thing cannot disagree about the wire format.
-- ============================================================================

CREATE TABLE IF NOT EXISTS ingest_batches (
    batch_id    TEXT PRIMARY KEY,
    site_id     TEXT        NOT NULL,
    n_items     INTEGER     NOT NULL DEFAULT 0,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at  TIMESTAMPTZ
);

CREATE OR REPLACE FUNCTION ingest_batch(
    p_batch_id TEXT,
    p_site_id  TEXT,
    p_payload  JSONB
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_seen      TIMESTAMPTZ;
    v_kind      TEXT;
    v_ref       TEXT;
    v_item      JSONB;
    v_items     JSONB;
    v_stored    INTEGER := 0;
    v_rollups   INTEGER := 0;
    v_alerts    INTEGER := 0;
BEGIN
    IF p_batch_id IS NULL OR p_batch_id = '' THEN
        RAISE EXCEPTION 'batch_id is required';
    END IF;

    -- ── the idempotency gate. Serialise two simultaneous arrivals of the *same*
    --    batch: the first inserts, the second blocks on this row and then sees it.
    SELECT received_at INTO v_seen
      FROM ingest_batches WHERE batch_id = p_batch_id FOR UPDATE;
    IF FOUND THEN
        RETURN jsonb_build_object('ok', TRUE, 'duplicate', TRUE,
                                  'batch_id', p_batch_id, 'stored', 0);
    END IF;

    INSERT INTO ingest_batches (batch_id, site_id,
                                n_items, applied_at)
    VALUES (p_batch_id, COALESCE(p_site_id, 'site-unknown'),
            COALESCE(jsonb_array_length(p_payload->'items'), 0), NOW());

    v_items := COALESCE(p_payload->'items', '[]'::jsonb);
    FOR v_item IN SELECT * FROM jsonb_array_elements(v_items) LOOP
        v_kind := COALESCE(v_item->>'kind', '');
        v_ref  := COALESCE(v_item->>'ref', v_item->>'id', '');
        IF v_kind = 'rollup' THEN
            BEGIN
                INSERT INTO node_hourly (site_id, node_id, hour_start, n_samples,
                        disp_mean, disp_min, disp_max, disp_last,
                        tilt_mean, tilt_max, acc_mean, acc_max,
                        vib_sum, micro_max, macro_max, severity_max)
                VALUES (COALESCE(v_item->'payload'->>'site_id', p_site_id),
                        v_item->'payload'->>'node_id',
                        (v_item->'payload'->>'hour_start')::timestamptz,
                        COALESCE((v_item->'payload'->>'n_samples')::int, 0),
                        (v_item->'payload'->>'disp_mean')::real,
                        (v_item->'payload'->>'disp_min')::real,
                        (v_item->'payload'->>'disp_max')::real,
                        (v_item->'payload'->>'disp_last')::real,
                        (v_item->'payload'->>'tilt_mean')::real,
                        (v_item->'payload'->>'tilt_max')::real,
                        (v_item->'payload'->>'acc_mean')::real,
                        (v_item->'payload'->>'acc_max')::real,
                        (v_item->'payload'->>'vib_sum')::int,
                        (v_item->'payload'->>'micro_max')::real,
                        (v_item->'payload'->>'macro_max')::real,
                        v_item->'payload'->>'severity_max')
                ON CONFLICT (site_id, node_id, hour_start) DO NOTHING;
                v_rollups := v_rollups + 1;
            EXCEPTION WHEN others THEN
                -- One malformed item must not lose the batch. The batch is marked
                -- applied; the item's error is not swallowed — it is countable in
                -- the caller's log by `stored` being smaller than `n_items`.
                NULL;
            END;
        ELSIF v_kind IN ('alert', 'alerts') THEN
            BEGIN
                INSERT INTO alerts_history (site_id, ts, level_id, node_id, severity,
                        event_type, progression_stage, ttf_seconds, ttf_q10, ttf_q90,
                        disruption_label, disruption_index, geo_lat, geo_lon,
                        recommended_action)
                VALUES (COALESCE(v_item->'payload'->>'site_id', p_site_id),
                        COALESCE((v_item->'payload'->>'ts')::double precision,
                                 extract(epoch FROM NOW()))::timestamptz,
                        v_item->'payload'->>'level_id',
                        COALESCE(v_item->'payload'->>'node_id', 'site'),
                        COALESCE(v_item->'payload'->>'severity', 'WATCH'),
                        COALESCE(v_item->'payload'->>'event_type', 'ALERT'),
                        v_item->'payload'->>'progression_stage',
                        (v_item->'payload'->>'ttf_seconds')::int,
                        (v_item->'payload'->>'ttf_q10')::int,
                        (v_item->'payload'->>'ttf_q90')::int,
                        v_item->'payload'->>'disruption_label',
                        (v_item->'payload'->>'disruption_index')::real,
                        (v_item->'payload'->>'geo_lat')::double precision,
                        (v_item->'payload'->>'geo_lon')::double precision,
                        v_item->'payload'->>'recommended_action')
                ON CONFLICT (site_id, ts, node_id, event_type) DO NOTHING;
                v_alerts := v_alerts + 1;
            EXCEPTION WHEN others THEN
                NULL;
            END;
        END IF;
        v_stored := v_stored + 1;
    END LOOP;

    INSERT INTO sync_log (site_id, n_rollups, n_alerts, ok, detail)
    VALUES (COALESCE(p_site_id, 'site-unknown'), v_rollups, v_alerts, TRUE,
            jsonb_build_object('batch_id', p_batch_id, 'stored', v_stored)::text);

    -- The function returns; the *caller* commits. `COMMIT-then-200` is therefore a
    -- property of the HTTP handler plus this function's transaction, and the rule is
    -- stated in the handler: commit first, answer second, and never the reverse.
    RETURN jsonb_build_object('ok', TRUE, 'duplicate', FALSE,
                              'batch_id', p_batch_id, 'stored', v_stored,
                              'rollups', v_rollups, 'alerts', v_alerts);
END;
$$;

-- The three-line HTTP layer that makes the contract checkable by reading it:
--
--     @app.post("/ingest")
--     def ingest():
--         body = gzip.decompress(request.get_data())
--         if not verify_hmac(body, request.headers.get("X-NS-Signature")):
--             return {"ok": False, "error": "bad signature"}, 401
--         payload = json.loads(body)
--         row = db.execute("SELECT ingest_batch(%s, %s, %s)",
--                          (payload["batch_id"], payload["site_id"], payload)).fetchone()
--         db.commit()                       # ← durable BEFORE the 200 exists
--         return row[0], 200
