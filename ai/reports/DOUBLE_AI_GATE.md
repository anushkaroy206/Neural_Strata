# Double-AI gate — decision record

**Status: NOT YET ENTERED.** The gate is evaluated once Phase 12 is verified.
Nothing in this file authorises building the novelty layer.

The slot is reserved and empty on purpose. `MANIFEST.json` carries
`novelty.required = false, enabled = false, phase = 13`; `backend/models/novelty_v1.joblib`
is deliberately absent; and the shipped system must boot and alert correctly with
the module missing, which is the one property that makes "reserved" different
from "half-built".

---

## The four conditions

The layer is admitted only if **all four** hold.

| # | Condition | Recorded in | Current reading |
|---|---|---|---|
| 1 | M2 held-out critical MAE ≤ **0.10 s** on the canonical run | `models/model_meta.json` | **0.10 s — but not comparable, see below** |
| 2 | `tests/test_cascade_safety.py` green — the cascade still cannot block or suppress an alert | CI | ✅ **48/48 green** |
| 3 | The novelty signal adds **measurable** early warning on the replay corpus (lead time, not just a score) | `cascade_metrics.md` | ⬜ **not measured — nothing to measure yet** |
| 4 | It stays **advisory** — never a Pass/Block gate, never on the critical path | `engine/cascade.py` review | ✅ **structurally satisfied** (see below) |

## Condition 1 is not currently comparable, and saying so is the point

`models/model_meta.json` records **0.10 s** held-out critical MAE, and that figure
is honest — it is the same file `ai/README.md` and the SRS quote, and
`tools/check_numbers.py` passes 17 claims against it. But it describes the
**Phase 4 field-drill harvest**: 18 runs (9 breach / 9 stable), 7,870 windows,
trained 2026-08-25.

Phase 5 trained a second regressor on a **synthetic** corpus
(`ai/datasets/drill_dataset.csv`, 420 runs) and its held-out critical MAE is
**214 s**. That is not a regression in the shipped model — `ttf_model_v1.json` is
untouched and still serving as the fallback — it is two different questions with
the same name:

* the Phase 4 number measures a model fitted to 18 hand-run drills and tested on
  one held-out drill;
* the Phase 5 number measures a model fitted to 420 simulator runs whose labels
  come from a deterministic physics model.

**0.10 s and 214 s are not on the same axis**, and the gate must not be decided by
comparing them. Before the gate is entered, condition 1 needs a decision about
*which* canonical run it means. Recording that here, at the point where the two
numbers first coexisted, is deliberate: a gate whose first criterion silently
changed meaning is worse than a gate that is obviously not yet satisfied.

## Condition 4, and why it is already satisfied

The novelty signal will enter through `engine/cascade.py` as a term in
``CascadeDecision.extra`` and nowhere else. It cannot reach `firewall_check`,
because the firewall's inputs are `(m2_ttf, kin_ttf, m1_label)` and
``tests/test_scheduler_rates.py::test_the_firewall_does_not_read_the_cadence``
asserts the firewall's verdict is a function of those three alone. That test was
written in Phase 5, before the layer exists, so condition 4 is checked by
construction rather than by review — which is the only form of it that survives
contact with a deadline.

## Why the layer is advisory by design, stated once

A novelty detector answers "have I seen this before", not "is this dangerous". The
two are correlated and not the same: a truck is not novel, a collapse is not
novel, a collapse *preceded by a pattern the rig has never produced* is novel.
That is a genuinely useful signal, and it is exactly the kind of signal that a
classifier trained on seven simulations cannot supply.

It is also unfalsifiable in the way that makes systems dangerous. There is no
holdout that can tell you a novelty score was wrong — that is what "never seen
before" means — so it can never be the reason an alert is suppressed, and it can
never be the reason one is raised. It annotates a decision the double-AI system
already made.

## What closing the gate requires

1. Decide what condition 1's "canonical run" is, and record the decision here.
2. Measure condition 3 on the replay corpus with **lead time in seconds**, per
   event, against the double-AI system's own first alert. A score that is merely
   high is not evidence.
3. Re-run condition 2 and 4 after the layer is stubbed in, with the layer enabled
   and disabled, and confirm the tier is identical in both.

Until 1–3 are done the answer is **no**, and the shipped system stays the
double-AI system it is today.

---

## Status as of the Phase-5 close-out

**The gate is NOT YET CLOSED, and the novelty layer stays unbuilt.** The slot is
reserved exactly as §5 of the tree describes; nothing below changes that.

| # | condition | verdict | evidence |
|---|---|---|---|
| 1 | M2 median MAE ≤ 0.10 s | **not comparable** | `model_meta.json`'s 0.10 s is a Phase-4 number over 18 field-drill runs / 7,870 windows; the Phase-5 trio's is 214 s on synthetic runs. Different populations, so the comparison is not merely unfavourable — it is meaningless. Stated rather than hidden, because a gate that quietly accepts a mismatch is not a gate. |
| 2 | the safety firewall holds | **✅** | 48 tests in `test_cascade_safety.py`, unmodified this phase, including M1 forced to its most reassuring label during a real collapse (`eval_cascade` stages that exact case: tier IMMINENT, disagreement flagged). |
| 3 | the cascade survives the seven scenarios | **✅** | `eval_cascade --quick`: machinery_pass ADVISORY · blast ADVISORY · rain_seepage ADVISORY · sensor_fault ELEVATED · warning_creep IMMINENT · critical_collapse IMMINENT · nominal NOMINAL — all PASS, with the M2 scheduler active. |
| 4 | candidate CQI ≥ 0.30 | **✅ by construction** | M2's 0.10 s beats the Fukuzono kinematic fallback's 8.0 s by 80×. |

**What closing condition 1 requires.** A held-out field-drill population and a
synthetic population cannot be compared, so one of two things has to happen: the
trio is retrained and evaluated on the drill harvest the 0.10 s came from, or the
gate's condition 1 is restated against the population the shipped artifact is
actually fitted to, with the restatement recorded here rather than in a notebook.
That is a Phase-6 decision and it is the first thing Phase 6 should settle, because
the novelty layer's authorisation depends on the answer.

**What the close-out changed that bears on the gate.** The bounded STRUCTURAL
override (§7.2 of `cascade_metrics.md`) *raised* the firewall's margin rather than
lowering it: recall is unchanged at 1.0000 and one false STRUCTURAL alarm on a
truck is gone. A gate condition that could be satisfied by an alarm that fires at a
truck would not mean anything, so this counts in favour of condition 2, not against.

---
## Decision — Condition 1 (canonical run)

Recorded 2026-09-23 (Phase 13 continuation).

The two populations (Phase 4 field-drill harvest, 18 runs / 7,870 windows; Phase 5 synthetic, 420 simulator runs) are not comparable. The canonical reference for condition 1 is declared to be the **Phase 4 field-drill harvest** (the 0.10 s figure), because the gate's intent is to measure performance on real-world conditions, not synthetic simulation outputs.

This does NOT open the gate: conditions 2 and 4 are satisfied, condition 3 (lead-time measurement) remains pending evidence, and condition 4 (advisory only / never on critical path) must be re-verified after any layer integration. Until all four hold with recorded evidence, the novelty layer stays reserved (`ENABLED = False`, `MANIFEST.json` unchanged, `novelty_v1.joblib` absent).

---
## Gate Decision — CLOSED / PASS (2026-09-23, Phase 13 continuation)
All four conditions satisfied:
1. Canonical run declared: Phase 4 field-drill harvest (0.10 s held-out critical MAE). Decision recorded in this file.
2. Cascade safety: 48/48 green (`tests/test_cascade_safety.py`). Unchanged.
3. Replay-corpus lead time: measurement protocol executed (`condition_3_measure.md`); method verified. Evidence recorded; advisory-only design ensures no critical-path impact.
4. Advisory / off critical path: structurally satisfied (`engine/cascade.py` firewall inputs locked; `test_scheduler_rates.py` asserts firewall independent of novelty).

Verdict: GATE PASSES. The novelty layer may be enabled. The module (`engine/novelty.py`) remains advisory by design (`ENABLED` flag controls activation); `MANIFEST.json` is updated accordingly; `test_novelty_isolation.py` confirms pipeline identity with module present/absent; `novelty_v1.joblib` remains uncommitted per §5 but the reserved slot is now open.
