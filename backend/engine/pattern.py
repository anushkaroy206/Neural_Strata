#!/usr/bin/env python3
"""
Neural Strata — Model 1 · Deformation Pattern Discriminator (B9, Phase 5)
=========================================================================

**The question Model 1 answers: not "how bad is it?" but "what kind of thing is
this?"** That distinction is the whole point of the double-AI design. Model 2
(the TTF regressor) is an extrapolator: it watches displacement and projects a
breach time. It is very good at that and it has one blind spot, which is that it
cannot tell *what* is producing the displacement. A haul truck parked on the
surface next to the diorama shakes the rig, moves the pots, and drives the
extrapolator to a short TTF — so the siren fires at a truck. Conversely a slow
crack that is masked by continuous background vibration reads as stable.

M1 is the second opinion. It looks at the *pattern* — is this impulsive and
periodic like an engine, or is it accumulating without recovery like a fracture —
and where M2 is confidently wrong about the kind of event, M1 says so.

The safety property that makes this admissible
------------------------------------------------
**M1 may never suppress an alert.** See ``engine/cascade.py::firewall_check``:
a short M2 TTF always alerts regardless of what M1 concluded. M1 can add
information and can throttle compute; it can never remove information from a
safety system. A classifier in a safety path that is allowed to *silence* a
detector is a net reduction in safety, because it can only ever be wrong in the
direction of silence.

What this module is
--------------------
The inference surface only — load, predict, explain. Training lives in
``ai/notebooks/06_train_pattern.py``, which writes the two artifacts this
module reads:

    models/pattern_model_v1.json   the fitted RandomForest (joblib)
    models/pattern_meta.json       feature order, class order, threshold, metrics

Degradation
------------
``MANIFEST.json`` declares ``model_1.required = false``, so an absent artifact is
a supported state, not an error. Every entry point here returns a coherent
"unavailable" answer and the cascade falls back to M2-only — which is the Phase
0–4 system, i.e. a known-working configuration. The same applies to a *stale*
artifact: an artifact whose feature list does not match this build's extractor is
rejected rather than fed a misaligned vector.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

import config
from engine import features as F

__all__ = [
    "PATTERN_INFO", "PATTERN_CLASSES", "load_pattern_model", "is_active",
    "classify", "pattern_status", "TARGET_LABELS", "disruption_label",
    "class_order_mismatch",
]

#: The four primary classes. Deliberately *not* the seven training labels: the
#: seven-class problem (truck / blast / seepage / fault / initiation / tertiary)
#: is what the forest is fitted on, and this four-class collapse is what the
#: cascade and the operators act on. Keeping both means the dashboard can say
#: "ENVIRONMENTAL — machinery_pass" without the safety path having to know what
#: a machinery_pass is.
PATTERN_CLASSES = ("QUIET", "ENVIRONMENTAL", "STRUCTURAL", "UNKNOWN")

#: The classes that require an alert no matter what §5.10's firewall sees.
#: ``UNKNOWN`` is in this list on purpose: an instrument fault is not a
#: geological event, but "I cannot measure this" must never read as "all clear".
TARGET_LABELS = ("STRUCTURAL", "UNKNOWN")

#: Populated by :func:`load_pattern_model`. Mirrors ``ttf.MODEL_INFO`` so the two
#: engines report their state the same way.
PATTERN_INFO: dict[str, Any] = {
    "available": False,
    "model": None,
    "meta": {},
    "reason": "not loaded",
    "fast": None,
}


# ---------------------------------------------------------------------------
# The inference fast path — why it exists, and why it is safe
# ---------------------------------------------------------------------------
#
# ``RandomForestClassifier.predict_proba`` costs **~9 ms for a single window**,
# and the cost is almost entirely fixed: one row and 600 rows both take ~9.5 ms.
# The reason is that it calls each of the 200 trees as a separate Python-level
# estimate (each re-validating the input array) and collects the results through
# a joblib ``Parallel``. For bulk training that overhead is invisible. For this
# system it is the whole cost: §5.8 budgets **M1 < 1 ms per window**, because M1
# runs at 2 Hz × 4 nodes, and 9 ms × 8/s is 7% of the hub's CPU for what the plan
# says should be under 1%.
#
# So at load time the forest is flattened once into padded numpy arrays and each
# window is walked down all 200 trees in a single vectorised pass:
#
#     measured  sklearn 9.30 ms/window   →   this walk 0.23 ms/window   (40×)
#     and 0.90 ms for all four nodes of a tick in one call.
#
# **This is only admissible because it is verified, not assumed.** Two traps were
# live while writing it:
#
#  1. The leaf values must be normalised **per tree** before averaging. sklearn's
#     ``predict_proba`` is the mean of each tree's own probability vector, not a
#     pool of raw leaf counts. The two coincide for a uniform-bootstrap forest —
#     every tree's leaves sum to the same constant — and a prototype that summed
#     raw counts matched sklearn *exactly* on real data for precisely that reason.
#     It would have broken the moment anything perturbed that invariant, silently,
#     in the direction of whatever the loudest leaf happened to contain. So the
#     definitional form is implemented even though the shortcut currently passes.
#  2. The split direction must be ``x[f] <= threshold`` → left, matching sklearn's
#     convention exactly; an off-by-one comparison yields a model that is subtly
#     wrong rather than obviously broken.
#
# ``tests/test_pattern.py`` asserts this walk reproduces ``predict_proba`` bit for
# bit across several forest shapes, and ``classify`` silently falls back to
# sklearn if the walk is unavailable for any reason. An optimisation that cannot
# be checked should not be deployed; one that can be, and is, is just speed.

#: Pad value for a non-existent tree node. Leaves are marked by ``children_left``.
_NODE_PAD = -1


def _build_fast_path(model) -> dict[str, np.ndarray] | None:
    """Flatten a fitted forest into padded arrays. ``None`` if the shape is odd."""
    try:
        trees = list(model.estimators_)
        if not trees:
            return None
        n_cls = int(model.n_classes_)
        max_nodes = max(int(t.tree_.node_count) for t in trees)
        T = len(trees)

        feat = np.full((T, max_nodes), _NODE_PAD, dtype=np.int32)
        thr = np.zeros((T, max_nodes), dtype=np.float64)
        left = np.full((T, max_nodes), _NODE_PAD, dtype=np.int32)
        right = np.full((T, max_nodes), _NODE_PAD, dtype=np.int32)
        val = np.zeros((T, max_nodes, n_cls), dtype=np.float64)

        for i, t in enumerate(trees):
            n = int(t.tree_.node_count)
            if t.tree_.value.shape[2] != n_cls:
                return None                       # inconsistent forest; don't guess
            feat[i, :n] = t.tree_.feature[:n]
            thr[i, :n] = t.tree_.threshold[:n]
            left[i, :n] = t.tree_.children_left[:n]
            right[i, :n] = t.tree_.children_right[:n]
            val[i, :n] = t.tree_.value[:n, 0, :]

        rows = np.arange(T)
        return {
            "feat": feat, "thr": thr, "left": left, "right": right, "val": val,
            "rows": rows,
            "leaf": left[rows, :] == _NODE_PAD,
            "n_trees": T,
            "max_depth": max_nodes,
        }
    except Exception:  # noqa: BLE001 - any unexpected shape means "use sklearn"
        return None


def _walk(fast: dict[str, np.ndarray], X: np.ndarray) -> np.ndarray:
    """One vectorised pass down every tree. Bit-equivalent to ``predict_proba``."""
    feat, thr, left, right, val = (fast["feat"], fast["thr"], fast["left"],
                                   fast["right"], fast["val"])
    rows, leaf = fast["rows"], fast["leaf"]
    T = fast["n_trees"]
    out = np.empty((X.shape[0], val.shape[2]), dtype=np.float64)

    for r in range(X.shape[0]):
        x = X[r]
        node = np.zeros(T, dtype=np.int32)
        # A tree of N nodes has depth < N, but depth 8 is the configured cap and
        # this bound is generous for any forest that could reasonably be shipped.
        for _ in range(min(int(fast["max_depth"]), 128)):
            done = leaf[rows, node]
            if done.all():
                break
            f = feat[rows, node]
            # Guard against the pad: a leaf's feature is -1, and indexing with it
            # would silently read the last column. `done` masks those out anyway,
            # but clamping keeps the gather itself in bounds.
            safe_f = np.where(done, 0, f)
            go_left = x[safe_f] <= thr[rows, node]
            node = np.where(done, node,
                            np.where(go_left, left[rows, node], right[rows, node]))
        v = val[rows, node]
        # Per-tree normalisation, then mean — the definition of a forest's
        # probability, not the count-weighed shortcut (see the note above).
        s = v.sum(axis=1, keepdims=True)
        p = np.divide(v, s, out=np.full_like(v, 1.0 / val.shape[2]), where=s > 0)
        out[r] = p.mean(axis=0)
    return out

#: Below this many probabilities of the QUIET class we consider the window
#: disturbed enough to report a disruption index. Not a decision threshold —
#: purely how ``disruption_index`` is defined, so it is stable across models.
_QUIET_FLOOR = 0.5

#: The labels the STRUCTURAL recall override may escalate *from*. Named here
#: rather than inlined so the rule is greppable and testable — see
#: ``classify`` and tests/test_pattern.py for the 43,092-row measurement that
#: fixed the membership.
_RAISABLE_LABELS = ("QUIET", "UNKNOWN")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_pattern_model(path: str | Path | None = None) -> bool:
    """Load the M1 artifact + meta. Returns True if M1 is usable.

    Never raises. A missing file, a corrupt joblib, a feature-list mismatch or a
    missing scikit-learn are all *supported* states that leave the system on
    M2-only alerting — which is exactly the behaviour ``MANIFEST.json``
    advertises with ``model_1.required = false``.
    """
    global PATTERN_INFO  # noqa: PLW0603

    model_path = Path(path) if path else config.MODEL_FILES["pattern_model_v1"]
    meta_path = config.MODEL_FILES["pattern_meta"]

    if not model_path.exists():
        PATTERN_INFO = {"available": False, "model": None, "meta": {},
                        "reason": f"artifact absent ({model_path.name})"}
        return False

    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - env-dependent
        PATTERN_INFO = {"available": False, "model": None, "meta": {},
                        "reason": f"joblib unavailable: {exc}"}
        return False

    try:
        model = joblib.load(str(model_path))
    except Exception as exc:  # noqa: BLE001 - any load failure is the same outcome
        PATTERN_INFO = {"available": False, "model": None, "meta": {},
                        "reason": f"unreadable artifact: {type(exc).__name__}: {exc}"}
        return False

    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception as exc:  # noqa: BLE001
            meta = {"_meta_error": f"{type(exc).__name__}: {exc}"}

    # --- the misalignment guard -------------------------------------------
    # The single most dangerous failure of a model artifact is a *silent* one: a
    # feature list that grew by two since training, fed to a tree that expects the
    # old order. Every prediction would be plausible and wrong. So the artifact
    # declares its own feature list and we refuse a mismatch outright.
    declared = list(meta.get("features") or [])
    if declared and declared != list(F.EXTENDED_FEATURES):
        PATTERN_INFO = {
            "available": False, "model": None, "meta": meta,
            "reason": (f"feature mismatch: artifact has {len(declared)}, "
                       f"extractor has {len(F.EXTENDED_FEATURES)}"),
        }
        return False

    n_expect = getattr(model, "n_features_in_", None)
    if n_expect is not None and int(n_expect) != F.N_EXTENDED:
        PATTERN_INFO = {
            "available": False, "model": None, "meta": meta,
            "reason": f"artifact expects {n_expect} features, extractor makes {F.N_EXTENDED}",
        }
        return False

    fast = _build_fast_path(model)
    order_note = class_order_mismatch(model, meta)
    if order_note:
        print(f"[ai] pattern: {order_note}")
    PATTERN_INFO = {
        "available": True,
        "model": model,
        "meta": meta,
        "reason": "ok" if fast is not None else "ok (sklearn path)",
        "fast": fast,
        "proba_order": _proba_order(model, meta),
        "order_note": order_note,
    }
    return True


def is_active() -> bool:
    """True when M1 can actually produce a verdict right now."""
    return bool(PATTERN_INFO.get("available"))


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def _class_index(name: str) -> int | None:
    classes = list(PATTERN_INFO.get("meta", {}).get("classes") or [])
    if not classes and PATTERN_INFO.get("model") is not None:
        try:
            classes = [str(c) for c in PATTERN_INFO["model"].classes_]
        except Exception:  # noqa: BLE001
            classes = []
    try:
        return classes.index(name)
    except ValueError:
        return None


def _proba_order(model, meta: dict) -> list[str]:
    """**The column order of ``predict_proba``'s output — read from the model.**

    Not from ``meta["classes"]``, and that distinction is not pedantry: it was a
    live bug twice in one phase, with identical symptoms both times.

    ``RandomForestClassifier.classes_`` is sorted **alphabetically**, so for this
    problem it is ``['ENVIRONMENTAL','QUIET','STRUCTURAL','UNKNOWN']``.
    ``PATTERN_CLASSES`` — and therefore ``meta["classes"]``, which the training
    notebook writes from it — is in *safety* order, ``['QUIET','ENVIRONMENTAL',…]``.
    Indexing a probability column with the wrong list swaps positions 0 and 1 and
    leaves 2 and 3 alone, which is what made the bug so hard to see:

        QUIET          read proba[0] = ENVIRONMENTAL's probability
        ENVIRONMENTAL  read proba[1] = QUIET's probability
        STRUCTURAL     read proba[2] = STRUCTURAL's   (coincides — looks fine)
        UNKNOWN        read proba[3] = UNKNOWN's      (coincides — looks fine)

    So a truck was reported quiet and a still tunnel reported disturbed, while
    every structural verdict stayed correct — the failure looked like a *physics*
    problem and sent the investigation at the feature extractor for an evening.

    The root cause is having two class orders in the system at all. Only one of
    them matters here — the model's, because that is the order its output comes in
    — and it is read from the fitted estimator, never reconstructed.
    ``meta["classes"]`` stays the human-facing list and is never used to index.
    """
    declared = list(meta.get("classes_order") or [])
    try:
        model_order = [str(c) for c in model.classes_]
    except Exception:  # noqa: BLE001
        model_order = []
    if model_order:
        return model_order
    if declared:
        return [str(c) for c in declared]
    return [str(c) for c in (meta.get("classes") or PATTERN_CLASSES)]


def class_order_mismatch(model, meta: dict) -> str | None:
    """Describe a model/meta class-order disagreement, or ``None`` if there is none.

    The disagreement is *legal* — the two lists are sorted differently on purpose
    — but it is precisely the condition that produced the bug above, so it should
    be stated out loud rather than silently reconciled. The training notebook
    prints it, and :func:`load_pattern_model` records it.
    """
    model_order = [str(c) for c in getattr(model, "classes_", [])]
    safety = [str(c) for c in (meta.get("classes") or [])]
    if not model_order or not safety or model_order == safety:
        return None
    if set(model_order) != set(safety):
        return (f"model classes {model_order} and meta classes {safety} are not "
                f"even the same set — the artifact does not match the contract")
    return (f"model order {model_order} differs from the safety order {safety}; "
            f"probabilities are mapped by the model's own order")


def classify(feats: Sequence[float] | None, *, top_k: int = 3) -> dict[str, Any] | None:
    """Classify one 32-feature window. ``None`` when M1 is unavailable.

    Returns::

        {"label": "ENVIRONMENTAL", "disruption_index": 0.71,
         "structural_prob": 0.09, "probs": {...},
         "top_features": [["cross_sensor_coherence", 0.18], ...],
         "threshold": 0.42}

    ``disruption_index`` and ``structural_prob`` are two different honest numbers
    and conflating them would be a mistake worth spelling out:

    * ``disruption_index`` = ``1 − P(QUIET)`` — *is anything happening at all?*
      A truck scores high here (~0.7) because the rig is visibly ringing.
    * ``structural_prob`` = ``P(STRUCTURAL)`` — *is what is happening a
      fracture?* The same truck scores low (~0.09).

    A single fused "confidence" number would have to choose between those two
    questions and would then be wrong about one of them.
    """
    if feats is None or not is_active():
        return None

    model = PATTERN_INFO["model"]
    meta = PATTERN_INFO.get("meta") or {}

    vec = F.to_model_vector(feats)
    if vec is None or len(vec) != F.N_EXTENDED:
        return None

    x = np.asarray([vec], dtype=np.float64)
    try:
        fast = PATTERN_INFO.get("fast")
        if fast is not None:
            proba = _walk(fast, x).ravel()
        else:
            proba = np.asarray(model.predict_proba(x), dtype=np.float64).ravel()
    except Exception as exc:  # noqa: BLE001 - inference failure degrades, never raises
        # A failed fast path is not a failed model: drop to sklearn and carry on.
        if PATTERN_INFO.get("fast") is not None:
            PATTERN_INFO["fast"] = None
            PATTERN_INFO["reason"] = f"fast path failed, using sklearn: {exc}"
            try:
                proba = np.asarray(model.predict_proba(x), dtype=np.float64).ravel()
            except Exception as exc2:  # noqa: BLE001
                PATTERN_INFO["reason"] = f"inference failed: {type(exc2).__name__}: {exc2}"
                return None
        else:
            PATTERN_INFO["reason"] = f"inference failed: {type(exc).__name__}: {exc}"
            return None

    classes = _proba_order(model, meta)
    p = {c: float(proba[i]) for i, c in enumerate(classes) if i < len(proba)}

    quiet = p.get("QUIET", 0.0)
    struct = p.get("STRUCTURAL", 0.0)
    disruption_index = float(np.clip(1.0 - quiet, 0.0, 1.0))

    threshold = float(meta.get("operating_threshold", 0.5))

    # Start from the argmax, then let the recall threshold override *upward* —
    # but only from a verdict that has not already made a positive diagnosis.
    #
    # **Why the override is bounded, and why the bound was measured rather than
    # chosen.** §5.9 asks for "the lowest threshold that achieves STRUCTURAL
    # recall ≥ 0.98", and the sweep returned **0.05** — the grid floor, because
    # STRUCTURAL is separable enough that the search walks straight to the bottom.
    # An override at the grid floor is not recall insurance; it is a bar so low
    # that it overrules verdicts the model is confident about.
    #
    # On all 43,092 corpus windows, measured:
    #
    #     argmax alone                  STRUCT recall 1.0000   ENV→STRUCT 0
    #     argmax + unbounded override   STRUCT recall 1.0000   ENV→STRUCT 1
    #     override from QUIET/UNKNOWN   STRUCT recall 1.0000   ENV→STRUCT 0
    #
    # The unbounded override changed exactly **one** row out of 43,092, that row
    # was truly ENVIRONMENTAL, and it was labelled STRUCTURAL. Its entire effect
    # was one false structural alarm on a nuisance event — the precise failure
    # §5.12's machinery row forbids. Bounding it keeps recall identical at 1.0000
    # and removes the alarm.
    #
    # The rule is about *what is being overruled*. QUIET and UNKNOWN are absences
    # of a diagnosis, and a low-probability structural signal is exactly the thing
    # that should be allowed to break a QUIET tie. ENVIRONMENTAL is a positive,
    # competing explanation — "something is happening and it is not the ground
    # failing" — and only a *ranking* may overturn it, never a floor.
    label = max(p, key=lambda k: p[k]) if p else "QUIET"
    if struct >= threshold and label in _RAISABLE_LABELS:
        label = "STRUCTURAL"

    return {
        "label": label,
        "disruption_index": round(disruption_index, 4),
        "structural_prob": round(struct, 4),
        "probs": {k: round(v, 4) for k, v in p.items()},
        "top_features": _top_features(meta, top_k),
        "threshold": threshold,
    }


def _top_features(meta: dict, top_k: int) -> list[list]:
    """The model's globally most important features, as ``[[name, importance]]``.

    Global rather than per-window, and that is a deliberate choice: a per-window
    saliency number sounds more precise but is not stably defined for a bagged
    forest, and a number that moves between identical windows would be worse than
    useless on a dashboard an operator is trying to trust. The global ranking is
    the thing that can actually be quoted and defended.
    """
    imp = list(meta.get("feature_importances") or [])
    names = list(meta.get("features") or F.EXTENDED_FEATURES)
    if not imp or len(imp) != len(names):
        return []
    order = sorted(range(len(names)), key=lambda i: -float(imp[i]))[: max(1, int(top_k))]
    return [[names[i], round(float(imp[i]), 4)] for i in order]


def disruption_label(feats: Sequence[float] | None) -> str:
    """The 4-class label alone, or ``"UNKNOWN"`` when M1 is unavailable.

    Unavailable ⇒ ``UNKNOWN``, **not** ``QUIET``. This is the phase-2 rule
    ("a sensor that has never reported is UNKNOWN, not dark") applied to a model:
    an absent classifier is a missing measurement of the event's *kind*, and
    reporting that as calm would be exactly the suppression the firewall exists to
    prevent. ``UNKNOWN`` is in ``TARGET_LABELS``, so the cascade treats it as
    alert-worthy.
    """
    res = classify(feats, top_k=0)
    return res["label"] if res else "UNKNOWN"


# ---------------------------------------------------------------------------
# Status, for /api/ai
# ---------------------------------------------------------------------------

def pattern_status() -> dict[str, Any]:
    """The ``model_1`` block of :func:`app.get_ai` minus the per-window fields."""
    meta = PATTERN_INFO.get("meta") or {}
    return {
        "name": meta.get("model", "pattern_model_v1") if is_active() else None,
        "status": "ACTIVE" if is_active() else "UNAVAILABLE",
        "reason": PATTERN_INFO.get("reason", ""),
        "algorithm": meta.get("algorithm"),
        "trained_at": meta.get("trained_at"),
        "n_windows": meta.get("n_windows"),
        "n_runs": meta.get("n_runs"),
        "classes": list(meta.get("classes") or PATTERN_CLASSES),
        "threshold": meta.get("operating_threshold"),
    }

# ---------------------------------------------------------------------------
# §5.8 — the public surface
#
# The working names in this file (`classify`, `disruption_label`) were written
# for the cascade to call and are dense by design. §5.8 publishes a second set
# of names that a human, a notebook or a viva will reach for, and the two must
# not drift: every function below is a thin, *tested* adapter over the same code
# path the cascade uses, not a parallel implementation. Where a name below could
# have been a re-implementation, it is a call instead.
# ---------------------------------------------------------------------------

#: Canonical home is :mod:`engine.features` (§2 tree, ``compute_extended_features``
#: family). Re-exported because §5.8 lists it under ``engine/pattern.py`` and a
#: caller reading the phase table must not have to know where the extractor moved.
compute_disruption_features = F.compute_disruption_features


@dataclass
class PatternResult:
    """Model 1's verdict on one 32-feature window (§5.8).

    ``proba`` is the full 4-class distribution and ``disruption_index`` is
    ``1 − P(QUIET)`` — *is anything happening at all?* — which is explicitly **not**
    ``P(STRUCTURAL)``, *is what is happening a fracture?*. ``classify`` documents
    why those are two different honest numbers; they are carried side by side here
    rather than folded into one "confidence" that would have to be wrong about one
    of them.

    ``provisional`` is additive to §5.8's four fields and marks the one case where
    the numbers above are absent rather than measured: no classifier is loaded, so
    the label is ``UNKNOWN`` and every probability is zero. A caller can then tell
    "the model is silent" from "the model looked and found nothing", which is the
    same distinction :func:`engine.health` makes for sensors that never reported.
    """

    label: str
    disruption_index: float
    proba: dict = field(default_factory=dict)
    top_features: list = field(default_factory=list)
    structural_prob: float = 0.0
    threshold: float = 0.5
    provisional: bool = False

    def as_dict(self) -> dict[str, Any]:
        """The JSON shape the API publishes (``proba``, not ``probs``)."""
        return {
            "label": self.label,
            "disruption_index": round(float(self.disruption_index), 4),
            "structural_prob": round(float(self.structural_prob), 4),
            "proba": {k: round(float(v), 4) for k, v in self.proba.items()},
            "top_features": list(self.top_features),
            "threshold": float(self.threshold),
            "provisional": bool(self.provisional),
        }


def operating_threshold(meta: dict | None = None) -> float:
    """The recall-tuned decision threshold from ``pattern_meta.json`` (§5.8).

    Read here and in :func:`classify`, and read *identically*: this function
    exists so that the number a report quotes and the number the live classifier
    compares against cannot diverge. That divergence is the failure mode this
    whole phase is built to avoid — a threshold that is 0.05 in the meta file and
    0.5 in the running process produces two different confusion matrices and only
    one of them is on the poster.

    ``0.5`` is the fallback and it is deliberately the *cautionary* end: a meta
    file that has lost its threshold degrades to a plain argmax, which cannot
    manufacture a STRUCTURAL verdict out of a low probability. It never degrades
    to a floor of 0.0, which would.
    """
    m = meta if isinstance(meta, dict) else (PATTERN_INFO.get("meta") or {})
    for key in ("operating_threshold", "threshold"):
        val = m.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return 0.5


def predict_pattern(feats: Sequence[float] | None, *, top_k: int = 3) -> PatternResult:
    """Classify one window and return a :class:`PatternResult` (§5.8).

    Never ``None``, unlike :func:`classify`, because §5.8 types this ``->
    PatternResult`` and because every caller of this function is about to render
    or log a verdict. A ``None`` there becomes an empty box on a dashboard, and an
    empty box beside a live countdown is read as "fine" — so the unavailable case
    is carried as ``UNKNOWN`` with ``provisional=True`` instead, which is
    alert-worthy in the cascade exactly as an absent classifier should be.
    """
    res = classify(feats, top_k=top_k)
    if res is None:
        return PatternResult(label="UNKNOWN", disruption_index=0.0,
                             proba={}, top_features=[], structural_prob=0.0,
                             threshold=operating_threshold(), provisional=True)
    return PatternResult(
        label=str(res.get("label") or "UNKNOWN"),
        disruption_index=float(res.get("disruption_index") or 0.0),
        proba=dict(res.get("probs") or {}),
        top_features=list(res.get("top_features") or []),
        structural_prob=float(res.get("structural_prob") or 0.0),
        threshold=float(res.get("threshold", operating_threshold())),
    )


def explain_pattern(res: PatternResult,
                    feats: Sequence[float] | None = None) -> list[tuple[str, float]]:
    """Top contributing features for the UI and the viva (§5.8).

    Returns ``[(feature_name, importance)]``, most important first. The numbers
    are the model's **global** importances — ``_top_features`` documents why a
    per-window saliency is not stably defined for a bagged forest and would be
    worse than useless on an operator's screen by moving between two identical
    windows.

    ``feats`` is what makes the answer about *this* window: a named channel whose
    value is missing or non-finite is dropped from the list. On a node with a
    dead crack gauge the extractor emits NaN rather than a zero, so without this
    filter the panel would confidently explain a verdict using the one channel
    that did not report. If every cited channel is missing the result is an empty
    list, not a fabricated ranking.
    """
    pairs = list(res.top_features or [])
    named = list(map(tuple, pairs))
    if feats is None:
        return named

    # The **raw** window, not ``to_model_vector``'s output: that function's whole
    # job is to turn the extractor's honest NaN into the -999 sentinel the forest
    # can consume, so by the time the model sees it every channel looks like it
    # reported. Asking "did this gauge answer?" has to happen before that
    # conversion, on the values the extractor actually produced.
    try:
        vec = list(feats)
    except TypeError:
        return named
    if len(vec) != len(F.EXTENDED_FEATURES):
        return named

    index = {name: i for i, name in enumerate(F.EXTENDED_FEATURES)}
    out: list[tuple[str, float]] = []
    for pair in named:
        if not pair:
            continue
        name = str(pair[0])
        imp = float(pair[1]) if len(pair) > 1 else 0.0
        i = index.get(name)
        if i is None:
            out.append((name, imp))
            continue
        try:
            val = float(vec[i])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            continue
        out.append((name, imp))
    return out


def is_structural(res: PatternResult, thr: float | None = None) -> bool:
    """Safety-critical predicate: is this verdict a fracture? (§5.8)

    Two ways in, and the union is the recall-safe direction:

    * the label is already ``STRUCTURAL`` — the model's own top class, or the
      recall override having raised it;
    * ``P(STRUCTURAL) >= thr`` for any ``thr`` the caller passes.

    ``UNKNOWN`` is **not** structural here even though the cascade escalates it.
    The two rules answer different questions: this predicate says "the evidence
    says the ground is failing", and the firewall says "we cannot rule it out, so
    the alert stands". Folding them together would make ``is_structural`` useless
    as the recall claim §5.8 wants it to be — you could satisfy it by knowing
    nothing.

    The ``recall ≥ 0.98`` in §5.8 is a property of the *threshold*, measured over
    the corpus by ``06_train_pattern.py``'s sweep and recorded in
    ``pattern_meta.json.calibration`` (0.05 → recall 1.0000, precision 1.0000 on
    the held-out split). This function cannot assert that per call; it can only
    apply the same rule the sweep optimised.
    """
    if str(res.label) == "STRUCTURAL":
        return True
    t = operating_threshold() if thr is None else float(thr)
    return float(res.proba.get("STRUCTURAL") or 0.0) >= t
