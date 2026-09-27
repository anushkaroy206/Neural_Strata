"""The contract the operator deck depends on.

Regression guard for the Option-A wiring: GET /api/extended must serve `blackbox`
and `cloud` with exactly the field names the deck's BBX.ingest() reads, projected
from the real ingest + gate store, and the ACK_DELETE the deck renders must only
go out once the cloud has confirmed every row.

If a field is renamed here the deck silently stops updating — which is the exact
failure this file exists to catch.

Each test stops the feed before asserting, so nothing races the reader thread.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (Path(__file__).resolve().parent.parent,
           Path(__file__).resolve().parent.parent / "blackbox"):
    sys.path.insert(0, str(_p))

import gate                                            # noqa: E402
import ingest                                          # noqa: E402
from sim.cloud_mock import PushResult                   # noqa: E402
from gateway import Gateway                            # noqa: E402

# Field names lifted verbatim from the deck's BBX.ingest() reads.
DECK_BLACKBOX = {"sd_ok", "usb_ok", "power_ok", "chunk_id", "row", "rows_per_chunk",
                 "free_mb", "card_mb", "ram_ring", "retained"}
DECK_CLOUD = {"online", "pending", "sent", "next_upload_s", "last_upload", "last_ack"}
# Optional by design: the deck guards these (isFinite / !== undefined) and keeps
# its own default, so we omit rather than invent them.
DECK_OPTIONAL = {"card_mb", "ram_ring", "free_mb", "last_upload", "last_ack"}


class DeadSink:
    """A cloud endpoint that is up enough to answer, and refuses everything."""
    name = "dead"

    def push(self, batch_id, chunk_ids, rows):
        return PushResult(ok=False, http_status=503, error="upstream down")


def _run_until(pred, *, sink=None, timeout=20.0):
    """Start a gateway on a throwaway DB, wait for `pred`, stop, hand it back.

    Stopping first is what makes the assertions deterministic: the reader thread
    is no longer adding rows underneath them.
    """
    td = tempfile.TemporaryDirectory()
    gw = Gateway(db_path=Path(td.name) / "bbx.sqlite", sim_rows_per_chunk=40,
                 sim_rows_per_sec=2000.0, sink=sink)
    gw.start()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(gw):
            break
        time.sleep(0.05)
    else:
        gw.stop()
        td.cleanup()
        raise AssertionError("timed out waiting for the feed")
    gw.stop()
    gw._td = td                     # keep the temp dir alive for the caller
    return gw


def _some_rows(gw) -> bool:
    return gw.store.count_telemetry() >= 40


def _some_closed(gw) -> bool:
    return len([c for c in gw.store.chunks() if c["state"] == "CLOSED"]) >= 2


def test_blocks_carry_the_field_names_the_deck_reads():
    gw = _run_until(_some_rows)
    blocks = gw.extended_blocks()

    assert set(blocks) >= {"mode", "blackbox", "cloud"}, sorted(blocks)
    bb, cl = blocks["blackbox"], blocks["cloud"]

    missing = (DECK_BLACKBOX - set(bb)) - DECK_OPTIONAL
    assert not missing, f"deck reads blackbox.{sorted(missing)} — not served"
    missing = (DECK_CLOUD - set(cl)) - DECK_OPTIONAL
    assert not missing, f"deck reads cloud.{sorted(missing)} — not served"

    # the ones the deck cannot render without
    assert bb["sd_ok"] is True and bb["usb_ok"] is True
    assert isinstance(bb["chunk_id"], int) and isinstance(bb["row"], int)
    assert isinstance(bb["retained"], list)
    # Phase 10 added two keys per retained file — `reason` and `releasable` — so the
    # panel can show *why* the gate is still holding it (§5.6: "the reason is
    # displayed") in the same payload as the row counts. They ride together because
    # a reason fetched separately can be seconds out of step with the numbers it is
    # meant to explain. Nothing was removed: `chunk_id` and `rows` are still what
    # the ledger renders.
    for c in bb["retained"]:
        assert {"chunk_id", "rows"} <= set(c)
        assert set(c) <= {"chunk_id", "rows", "reason", "releasable"}
        assert isinstance(c["reason"], str) and c["reason"], "every held file has a reason"
        assert isinstance(c["releasable"], bool)
        if not c["releasable"]:
            assert "confirmed" in c["reason"] or "open" in c["reason"]
    assert bb["retained_total"] == len(bb["retained"])
    assert bb["retained_releasable"] == sum(1 for c in bb["retained"] if c["releasable"])
    assert cl["pending"] == gw.store.pending_total()
    gw._td.cleanup()


def test_mode_is_sim_when_the_simulator_feeds_it():
    """The LIVE/SIM badge must never be claimable by the bench feed."""
    gw = _run_until(_some_rows)
    assert gw.extended_blocks()["mode"] == "SIM"
    assert gw.mode == "SIM"
    gw._td.cleanup()


def test_card_mb_is_omitted_until_configured():
    """SD capacity is still an open decision — the deck keeps its own default."""
    gw = _run_until(_some_rows)
    assert "card_mb" not in gw.extended_blocks()["blackbox"]
    gw._td.cleanup()

    gw = _run_until(_some_rows)          # and appears the moment it is known
    gw.card_mb = 4096
    assert gw.extended_blocks()["blackbox"]["card_mb"] == 4096
    gw._td.cleanup()


def test_cloud_drain_then_the_gate_releases_the_chunk():
    """rows in -> cloud confirms -> ACK_DELETE -> SD released.

    The 60 s safety margin is patched to 0 for the test only; everything else is
    the production path.
    """
    orig = ingest.evaluate
    ingest.evaluate = lambda view, **kw: gate.evaluate(view, safety_margin_s=0.0)
    try:
        gw = _run_until(_some_closed)
        before = gw.extended_blocks()
        assert before["blackbox"]["retained_total"] > 0, "nothing held"
        assert before["cloud"]["pending"] > 0

        res = gw.force_sync()
        assert res["batches_failed"] == 0, res
        assert gw.store.pending_total() == 0, "outbox not drained"

        gw.upkeep(0.0)                            # let the gate look again
        after = gw.extended_blocks()
        assert after["cloud"]["sent"] > 0
        assert after["cloud"]["online"] is True
        assert after["blackbox"]["retained_total"] < before["blackbox"]["retained_total"], \
            "gate released nothing after a clean sync"
        assert after["cloud"].get("last_ack", {}).get("chunk_id") is not None
        gw._td.cleanup()
    finally:
        ingest.evaluate = orig


def test_a_failed_cloud_never_releases_anything():
    """The SD copy is precious: a dead endpoint must hold every chunk."""
    orig = ingest.evaluate
    ingest.evaluate = lambda view, **kw: gate.evaluate(view, safety_margin_s=0.0)
    try:
        gw = _run_until(_some_closed, sink=DeadSink())
        res = gw.force_sync()
        assert res["batches_failed"] == 1 and res["rows_sent"] == 0, res
        gw.upkeep(0.0)

        blocks = gw.extended_blocks()
        assert blocks["cloud"]["online"] is False
        assert blocks["cloud"]["sent"] == 0

        # Nothing released: every closed chunk is still held, and the hub was
        # never told to unlink anything.
        closed = [c for c in gw.store.chunks() if c["state"] == "CLOSED"]
        assert blocks["blackbox"]["retained_total"] == len(closed), \
            "a chunk was released while the cloud was down"
        assert all(c["ack_sent_at"] is None for c in gw.store.chunks()), \
            "ACK_DELETE went out with the cloud down"
        assert "last_ack" not in blocks["cloud"], "ACK_DELETE sent with the cloud down"
        gw._td.cleanup()
    finally:
        ingest.evaluate = orig


# ════════════════════════════════════════════════════════════════════════════
# Phase 10 — `/api/blackbox/{status,chunks}` (api/routes_blackbox.py)
# ════════════════════════════════════════════════════════════════════════════
# The chunks endpoint is the operator's view of the gate, and a gate verdict is
# only useful if it is *current*: these tests drive the feed to the point where a
# chunk is held, then clear the reason, and assert the endpoint's answer moves.
# A cached verdict would pass the first assertion and fail the second.

from flask import Flask                                          # noqa: E402

from api import routes_blackbox                                  # noqa: E402
from sim.hub_sim import stream as sim_stream                     # noqa: E402


def _client(gw):
    """A Flask app with only this blueprint — no Postgres, no ticker, no threads."""
    mini = Flask("bbx-contract")
    mini.config["TESTING"] = True
    mini.register_blueprint(routes_blackbox.bp)
    routes_blackbox.bind(lambda: gw)
    return mini.test_client()


def _gw_with(**kw):
    """A gateway with no threads and no serial line — `feed()` is the only input."""
    td = tempfile.TemporaryDirectory()
    gw = Gateway(db_path=Path(td.name) / "bbx.sqlite", external_feed=True,
                 card_mb=8000, **kw)
    gw._td = td
    return gw


def _feed(gw, chunks=2, rows_per_chunk=10):
    for line in sim_stream(chunks=chunks, rows_per_chunk=rows_per_chunk):
        gw.feed(line)
    return gw


def test_status_serves_the_two_blocks_the_deck_reads():
    gw = _feed(_gw_with())
    try:
        r = _client(gw).get("/api/blackbox/status")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True and body["gateway"] is True
        assert set(body["blackbox"]) >= DECK_BLACKBOX - DECK_OPTIONAL
        assert set(body["cloud"]) >= DECK_CLOUD - {"last_upload", "last_ack"}
        led = body["ledger"]
        assert led["chunks"] == 2 and led["held"] == 2 and led["acked"] == 0
        assert all("not yet confirmed" in x["reason"] for x in led["reasons"])
    finally:
        gw._td.cleanup()


def test_chunks_lists_newest_first_with_a_live_verdict():
    gw = _feed(_gw_with())
    try:
        c = _client(gw)
        body = c.get("/api/blackbox/chunks").get_json()
        assert body["total"] == 2 and body["count"] == 2
        assert [e["chunk_id"] for e in body["chunks"]] == [2, 1], "newest first"
        for e in body["chunks"]:
            assert set(e) >= {"chunk_id", "state", "expected_rows", "received_rows",
                              "dropped_rows", "pending", "in_db", "gate"}
            assert e["gate"]["allowed"] is False
            assert "not yet confirmed" in e["gate"]["reason"]

        # Clear the reason and the verdict must move on the *next* request.
        gw.store.conn.execute("UPDATE cloud_outbox SET status='SENT'")
        gw.store.conn.execute("UPDATE blackbox_chunks SET closed_at=?",
                              ("2020-01-01T00:00:00.000+00:00",))
        body2 = c.get("/api/blackbox/chunks").get_json()
        assert all(e["gate"]["allowed"] for e in body2["chunks"]), \
            "the gate verdict is recomputed per request, not cached"
        assert all(e["gate"]["reason"] == "closed, complete, undropped, fully synced"
                   for e in body2["chunks"])
    finally:
        gw._td.cleanup()


def test_chunks_filters():
    gw = _feed(_gw_with(), chunks=3)
    try:
        c = _client(gw)
        # one chunk closed and released by hand
        gw.store.conn.execute("UPDATE cloud_outbox SET status='SENT' WHERE chunk_id=1")
        gw.store.conn.execute("UPDATE blackbox_chunks SET closed_at=?, state='CLOSED'"
                              " WHERE chunk_id=1", ("2020-01-01T00:00:00.000+00:00",))
        assert gw.ingestor.sweep_acks() == ["ACK_DELETE:0001\n"]

        # Chunk 1 is *released*, not held: `evaluate()` refuses it ("already
        # acknowledged") and always will, but a released chunk is on its way out.
        # Counting it as held would make "why is nothing deleting" climb every
        # time something deleted.
        held = c.get("/api/blackbox/chunks?held=1").get_json()
        assert [e["chunk_id"] for e in held["chunks"]] == [3, 2]
        assert all(not e["gate"]["allowed"] for e in held["chunks"])
        assert all(e["ack_sent_at"] is None for e in held["chunks"])

        led = c.get("/api/blackbox/status").get_json()["ledger"]
        assert (led["chunks"], led["held"], led["releasable"], led["acked"]) == (3, 2, 0, 1)

        one = c.get("/api/blackbox/chunks?limit=1").get_json()
        assert one["count"] == 1 and one["total"] == 3

        closed = c.get("/api/blackbox/chunks?state=closed").get_json()
        assert all(e["state"] == "CLOSED" for e in closed["chunks"])
    finally:
        gw._td.cleanup()


def test_chunks_rejects_a_bad_query_instead_of_guessing():
    gw = _feed(_gw_with(), chunks=1)
    try:
        c = _client(gw)
        for url in ("/api/blackbox/chunks?limit=abc", "/api/blackbox/chunks?state=WAT"):
            r = c.get(url)
            assert r.status_code == 400, url
            assert r.get_json()["ok"] is False
        # a limit of zero is clamped, not an error: the panel may ask for anything
        assert c.get("/api/blackbox/chunks?limit=0").get_json()["count"] == 1
    finally:
        gw._td.cleanup()


def test_no_gateway_is_a_state_not_an_error():
    """A laptop with no hub attached is the normal bench configuration. Both
    endpoints must answer 200 with `gateway: false` and an empty ledger — a 503
    here would be a panel that cannot render, and a 200 with invented numbers
    would be worse."""
    routes_blackbox.bind(lambda: None)
    mini = Flask("bbx-contract-none")
    mini.config["TESTING"] = True
    mini.register_blueprint(routes_blackbox.bp)
    c = mini.test_client()
    for url in ("/api/blackbox/status", "/api/blackbox/chunks"):
        r = c.get(url)
        assert r.status_code == 200, url
        body = r.get_json()
        assert body["gateway"] is False and body["chunks"] == []
        assert body["reason"]


def test_recover_exists_now_and_still_refuses_without_a_hub():
    """Phase 10 asserted this route was a 404, and it was: the honest answer while
    nothing was behind it. Phase 11 built it (B15), so the same assertion flips —
    and what stays asserted is the part that mattered: **with no gateway it must not
    report success.** 409, not 503: "there is nothing to ask" is a state, not a
    transient fault, and a command that could not be carried out must not answer 200.
    """
    routes_blackbox.bind(lambda: None)
    mini = Flask("bbx-contract-nogw")
    mini.config["TESTING"] = True
    mini.register_blueprint(routes_blackbox.bp)
    r = mini.test_client().post("/api/blackbox/recover")
    assert r.status_code == 409
    assert r.get_json() == {"ok": False, "gateway": False,
                            "reason": "no blackbox gateway — there is no hub to ask"}


def test_recover_asks_the_hub_and_reports_the_burst():
    gw = _gw_with()
    try:
        # The realistic gap: twenty rows of chunk 1 never arrive at the laptop (the
        # link was cut), while the hub wrote all forty. Built by *omitting lines from
        # the stream*, not by deleting rows afterwards — the ingest's own count is
        # what a gap is measured against, so a table edit would simulate a different
        # fault entirely.
        lines = list(sim_stream(chunks=2, rows_per_chunk=10))
        missing = {line for line in lines
                   if line.startswith("{") and '"chunk_id":1' in line}
        missing = set(sorted(missing)[:20])
        for line in lines:
            if line not in missing:
                gw.feed(line)
        assert gw.store.chunk(1)["received_rows"] == 20
        assert gw.store.chunk(1)["expected_rows"] == 40

        r = _client(gw).post("/api/blackbox/recover")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True and body["state_query_sent"] is True
        assert body["replay_lines"] == ["REPLAY:0001:0001\n"], body["replay_lines"]
        assert body["gaps"] == [1] and body["missing_rows"] == 20
        assert "1 chunk(s) short of 20 row(s)" in body["summary"]
    finally:
        gw._td.cleanup()
