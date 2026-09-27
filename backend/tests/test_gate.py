"""Gate tests — every way a chunk can be held, and the one way it is released."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

for _p in (Path(__file__).resolve().parent.parent,          # backend/
           Path(__file__).resolve().parent.parent / "blackbox"):  # the gateway modules
    sys.path.insert(0, str(_p))

from gate import ChunkView, evaluate

OLD = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="milliseconds")


def view(**kw):
    base = dict(chunk_id=42, state="CLOSED", expected_rows=1000, received_rows=1000,
                dropped_rows=0, closed_at=OLD, ack_sent_at=None, pending_outbox=0)
    base.update(kw)
    return ChunkView(**base)


def test_the_perfect_chunk_is_released():
    d = evaluate(view())
    assert d.allowed, d.reason
    assert "fully synced" in d.reason


def test_open_chunk_is_never_released():
    d = evaluate(view(state="OPEN", closed_at=None))
    assert not d.allowed and "still open" in d.reason


def test_missing_close_line_blocks():
    d = evaluate(view(expected_rows=None))
    assert not d.allowed and "row count unknown" in d.reason


def test_row_count_mismatch_blocks_and_says_how_many():
    """The failure the addendum's gate would have missed entirely:
    the laptop lost 12 rows, the outbox drained to zero, and it deleted anyway."""
    d = evaluate(view(received_rows=988, pending_outbox=0))
    assert not d.allowed
    assert "12 rows would be lost" in d.reason


def test_dropped_rows_pin_the_chunk_forever():
    d = evaluate(view(dropped_rows=3))
    assert not d.allowed and "only record of them" in d.reason


def test_unsynced_rows_block():
    d = evaluate(view(pending_outbox=350))
    assert not d.allowed and "not yet confirmed" in d.reason


def test_safety_margin_holds_a_fresh_close():
    fresh = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    d = evaluate(view(closed_at=fresh))
    assert not d.allowed and "safety margin" in d.reason
    d2 = evaluate(view(closed_at=fresh), safety_margin_s=0)
    assert d2.allowed


def test_already_acked_is_idempotent():
    d = evaluate(view(ack_sent_at=OLD))
    assert not d.allowed and "already acknowledged" in d.reason


def test_naive_timestamp_does_not_crash():
    d = evaluate(view(closed_at="2026-09-20T10:00:00"))
    assert isinstance(d.allowed, bool)


# ── §5.6's refusal table, word for word ──────────────────────────────────────
# The Plan prints the five refusal messages as a table, and the criterion is not
# "a refusal happens" — it is that the reason an operator reads on the panel is the
# sentence the reviewer can find in the Plan. So these are string assertions on
# purpose: if someone rewords the gate, this file fails and makes them decide
# whether the Plan moved too.

#: A fixed instant, a fixed twelve seconds later — see the boundary test below for
#: why nothing in this file reads the wall clock: `datetime.now()` at import time
#: is minutes stale in a full-suite run, and a test asserting the words "closed 12s
#: ago" then reads "closed 14s ago" and fails for a reason that has nothing to do
#: with the gate.
_CLOSED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_TWELVE_S_LATER = _CLOSED + timedelta(seconds=12)


def test_all_five_refusals_in_the_plans_own_words():
    assert "still open" in evaluate(view(state="OPEN")).reason
    assert "closed, complete, undropped, fully synced" in evaluate(view()).reason

    lost = evaluate(view(expected_rows=600, received_rows=588)).reason
    assert "12 rows would be lost" in lost, lost

    dropped = evaluate(view(dropped_rows=4)).reason
    assert "hub dropped 4 rows in this chunk" in dropped
    assert "the SD copy is the only record of them" in dropped

    assert "350 rows not yet confirmed by the cloud" in \
        evaluate(view(pending_outbox=350)).reason

    inconsistent = evaluate(view(db_rows=590)).reason
    assert "internal inconsistency" in inconsistent
    assert "ingest counted 1000 rows but the table holds 590" in inconsistent

    young = evaluate(view(closed_at=_CLOSED.isoformat(timespec="milliseconds")),
                     now=_TWELVE_S_LATER).reason
    assert "closed 12s ago, safety margin is 60s" in young, young


def test_the_twelve_row_case_is_the_one_the_addendum_would_have_deleted():
    """The reason the gate has five conditions and not one.

    The laptop received 148 of the hub's 160 rows. The other twelve never arrived,
    so they were never enqueued, so **the outbox is empty** — and a gate that asked
    only "is PENDING zero?" would unlink the file holding the only copy of them.
    Every other condition passes here: closed, complete on both counters, no drops,
    old enough. It is the row-count condition alone that saves the data.
    """
    d = evaluate(view(chunk_id=9, expected_rows=160, received_rows=148, db_rows=148))
    assert not d.allowed
    assert "12 rows would be lost" in d.reason


def test_the_margin_boundary_is_exactly_sixty_seconds():
    """At 60.0 s the gate releases; a hair earlier it does not — asserted against a
    **supplied** clock, not the wall clock.

    `evaluate(..., now=…)` exists for this: an assertion that straddles the
    boundary by 100 ms and reads `datetime.now()` inside is a test that passes on
    a fast machine and fails under load, which is how a green suite develops a
    flake nobody can reproduce. The gate is a pure function of (view, now), so the
    boundary can be probed exactly instead of approximately.
    """
    closed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat(timespec="milliseconds")
    at_60 = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)          # exactly +60.0 s
    just_short = datetime(2026, 1, 1, 12, 0, 59, 900_000, tzinfo=timezone.utc)

    assert evaluate(view(closed_at=closed), now=at_60).allowed
    assert not evaluate(view(closed_at=closed), now=just_short).allowed

    # …and the same two answers a minute later, so it is the *age* being tested
    # rather than one lucky timestamp.
    later = datetime(2026, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
    assert evaluate(view(closed_at=closed), now=later).allowed


def test_the_gate_is_a_pure_function():
    """No clock, no store, no hub: the same view must decide the same way twice,
    and deciding must not mutate the view it was given."""
    v = view(state="OPEN")
    before = repr(v)
    answers = {evaluate(v).reason for _ in range(5)}
    assert len(answers) == 1
    assert repr(v) == before
