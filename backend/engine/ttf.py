"""
Neural Strata — MODEL 2: the TTF regressor.
===========================================

The time-to-failure forecaster (XGBoost) plus the two guards that make it safe to
put on a screen: a **freshness** rule and a **physics sanity gate**.

Moved out of ``app.py`` (``load_model`` · ``model_ttf_current`` ·
``run_model_inference`` · the inner halves of ``get_ai``) in Phase 0. The model
maths, the printed startup lines and the three published status strings
(``ACTIVE`` / ``WARMING UP`` / ``FALLBACK``) are unchanged — the dashboard keys
off them.

Boot policy (§A.3.8)
--------------------
``ANY_MISSING_ARTIFACT => LOG + DEGRADE, NEVER CRASH.`` A missing ``xgboost``, a
missing file or a scaler whose feature order disagrees with ``FEATURES`` all end
in the same place: the kinematic fallback drives the pill and the server boots.

Two guards, and why both exist
------------------------------
*Freshness* — a prediction older than ``MODEL_FRESH_S`` is not evidence. The pill
drops back to the kinematic value rather than showing a frozen number.
*Sanity* — "imminent failure with no motion is not credible". A TTF under 30 s
must be accompanied by real velocity (``v10 > 0.5 mm/s``) or real displacement
(``disp > 30 mm``), otherwise the prediction is discarded. This is the guard that
stops a stale buffer from triggering an evacuation.
"""

from __future__ import annotations

import json
import math
import time

import numpy as np

from config import ACTIVE_NODES, MODEL_BUF, MODEL_FRESH_S, MODEL_FILES, MODELS_DIR
from engine.features import FEATURES, compute_live_features, scaler_matches

try:                                        # xgboost is optional at runtime
    import xgboost as xgb
except Exception:                           # pragma: no cover - depends on host
    xgb = None                              # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Runtime state — one process, one model. Exposed for the health endpoint.
# ---------------------------------------------------------------------------

MODEL_INFO: dict = {"available": False, "model": None, "scaler": None, "meta": {}}
#: Latest accepted forecast per node. Keys follow ``ACTIVE_NODES`` — two nodes
#: today, four once Phase 1 wires N3/N4 in.
MODEL_TTF: dict[str, float | None] = {n: None for n in ACTIVE_NODES}
STATE: dict[str, float] = {"last_ok": 0.0, "last_error_s": 0.0, "predictions": 0.0}

#: Fallback TTF used when nothing better exists (kinematic drives the pill).
KINEMATIC_FALLBACK_MAE = 8.0


def load_ttf_model() -> bool:
    """Load the TTF model + scaler + meta at startup.

    Missing or broken ⇒ return False and keep the kinematic fallback, printing
    the same lines ``app.py`` printed so the runbook still matches.
    """
    if xgb is None:
        print("[ai] xgboost not installed — kinematic fallback only")
        return False

    model_path = MODEL_FILES["ttf_model_v1"]
    scaler_path = MODEL_FILES["scaler"]
    meta_path = MODEL_FILES["model_meta"]

    if not (model_path.exists() and scaler_path.exists()):
        print(f"[ai] model files not found ({MODELS_DIR.name}/) — kinematic fallback only")
        return False

    try:
        m = xgb.XGBRegressor()
        m.load_model(str(model_path))
        sc = json.loads(scaler_path.read_text())
        if not scaler_matches(sc):
            raise ValueError(
                "scaler feature order does not match FEATURES "
                f"({len(sc.get('features', []))} vs {len(FEATURES)})"
            )
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        MODEL_INFO.update(available=True, model=m, scaler=sc, meta=meta)
        print(f"[ai] TTF model loaded: {meta.get('model', 'ttf_model_v1')} "
              f"(trained {meta.get('trained_at', '?')})")
        # Phase 5: the quantile trio rides along. It is *self-contained* — the
        # artifact carries its own feature list and its own scaler — so it is
        # loaded independently of ``scaler.json`` on purpose. See
        # ``predict_ttf`` for why that matters.
        load_ttf_band()
        return True
    except Exception as e:
        MODEL_INFO["available"] = False
        STATE["last_error_s"] = time.time()
        print(f"[ai] failed to load model ({e}) — kinematic fallback only")
        return False


def predict_ttf(feats: list[float] | np.ndarray) -> float | None:
    """Point forecast for one window, clamped to ``[0, 3600]`` s.

    **Prefers the quantile trio's median when the trio is on disk**, and that is a
    deliberate ordering rather than a preference.

    ``MODEL_INFO``'s path standardises with the shared ``scaler.json`` and then
    feeds ``ttf_model_v1.json``. That is two artifacts that must agree, and they
    are written by two different steps of one notebook — so a run that retrains
    without re-exporting the scaler, or exports the scaler for a model whose
    split changed, produces a v1 that is scaled by numbers it was never trained
    with. Nothing raises: the predictions stay plausible and quietly wrong.

    ``ttf_model_v2.json`` cannot drift that way. The feature list, the scaler and
    all three boosters are inside the one file, written in the same statement, so
    "the scaler that ships with this model" is not a promise anyone has to keep.
    When the trio is present the median is therefore the better-sourced number,
    and the v1 path remains for the configuration where Phase 5's artifact has not
    been generated yet — which is the state ``MANIFEST.json`` still describes.

    Returns ``None`` when no model is available, the arithmetic fails, or the
    result is NaN — the caller keeps whatever it had.
    """
    if band_available():
        band = predict_ttf_band(feats)
        if band is not None:
            return band[1]

    if not MODEL_INFO["available"]:
        return None
    sc = MODEL_INFO["scaler"]
    try:
        mean = np.asarray(sc["mean"], dtype=float)
        scale = np.asarray(sc["scale"], dtype=float)
        x = ((np.asarray(feats, dtype=float) - mean) / scale).reshape(1, -1)
        ttf = float(MODEL_INFO["model"].predict(x)[0])
    except Exception as e:
        STATE["last_error_s"] = time.time()
        print(f"[ai] inference error: {e}")
        return None
    if math.isnan(ttf):
        return None
    return max(0.0, min(3600.0, ttf))


# ---------------------------------------------------------------------------
# Phase 5 — the quantile band (§5.9)
# ---------------------------------------------------------------------------

#: Populated by ``load_ttf_band``. ``None`` means the trio is not on disk, which
#: is a supported state: the deck then prints the point forecast with no shading.
BAND_INFO: dict = {"available": False, "reason": "not loaded", "model": None}


def load_ttf_band(path=None) -> bool:
    """Load ``ttf_model_v2.json`` — the quantile trio — if it is present.

    Never raises, and never *required*: a missing band degrades the deck from a
    shaded interval to a bare number, which is a cosmetic difference. Making it
    required would mean a Phase-5 experiment could stop a Phase-2 system booting.

    The artifact inlines three boosters as base64 of their raw JSON. That is one
    file rather than four because ``MANIFEST.json`` names a single file per model,
    and a band whose members can go missing independently will eventually ship
    half-loaded. ``Booster.load_model`` accepts a bytearray, so the round-trip is
    exact — ``tests/test_pattern.py`` pins it, and the notebook prints the count of
    quantile crossings it had to sort out.
    """
    import base64

    global BAND_INFO  # noqa: PLW0603
    path = path or MODEL_FILES["ttf_model_v2"]

    if xgb is None:
        BAND_INFO = {"available": False, "reason": "xgboost unavailable", "model": None}
        return False
    if not path.exists():
        BAND_INFO = {"available": False, "reason": f"artifact absent ({path.name})",
                     "model": None}
        return False

    try:
        blob = json.loads(path.read_text())
        quantiles = [float(q) for q in blob["quantiles"]]
        mean = np.asarray(blob["scaler"]["mean"], dtype=float)
        scale = np.asarray(blob["scaler"]["scale"], dtype=float)
        boosters = {}
        for q in quantiles:
            raw = base64.b64decode(blob["boosters"][f"q{int(q * 100):02d}"])
            b = xgb.Booster()
            b.load_model(bytearray(raw))
            boosters[q] = b
    except Exception as exc:                                  # noqa: BLE001
        BAND_INFO = {"available": False, "reason": f"unreadable: {type(exc).__name__}: {exc}",
                     "model": None}
        return False

    BAND_INFO = {
        "available": True, "reason": "ok", "model": boosters,
        "quantiles": quantiles, "mean": mean, "scale": scale,
        "meta": blob.get("metrics") or {},
        "features": list(blob.get("features") or []),
        "created": blob.get("created"),
    }
    print(f"[ai] TTF band loaded: q{[f'{q:.2f}' for q in quantiles]}  "
          f"80% coverage {100 * float((blob.get('metrics') or {}).get('coverage_80', 0)):.1f}%")
    return True


def band_available() -> bool:
    return bool(BAND_INFO.get("available"))


def predict_ttf_band(feats: list[float] | np.ndarray) -> tuple[float, float, float] | None:
    """``(q10, q50, q90)`` seconds for one 16-feature window, or ``None``.

    **The returned triple is always sorted.** Nothing enforces ``q10 <= q50 <=
    q90`` across three independently fitted boosters, and a crossing pair renders
    as a *negative* interval behind the countdown — a shaded band that reads as a
    shorter time to failure than the number it is shading. The sort is applied here
    as well as in training so a stale artifact cannot display an inverted band.

    ``None`` rather than a degenerate triple when the band is absent: a caller that
    wants the point forecast has ``predict_ttf``, and a fake zero-width band would
    claim certainty the project does not have.
    """
    if not BAND_INFO.get("available") or feats is None:
        return None
    try:
        mean = BAND_INFO["mean"]
        scale = BAND_INFO["scale"]
        x = ((np.asarray(feats, dtype=float) - mean) / scale).reshape(1, -1)
        # **The feature names are not decoration.** The trio was fitted from a
        # pandas DataFrame, so its boosters carry the training column names and
        # xgboost *refuses* a DMatrix built from a bare array without them
        # ("data did not contain feature names, but the following fields are
        # expected: ..."). It refuses by raising, and this function catches and
        # returns ``None`` — so the band was silently absent while every log line
        # looked healthy and ``predict_ttf`` quietly served the v1 point model.
        # Passing the names is what makes the trio load-bearing.
        names = BAND_INFO.get("features") or list(FEATURES)
        dm = xgb.DMatrix(x, feature_names=list(names))
        band = [float(BAND_INFO["model"][q].predict(dm)[0])
                for q in BAND_INFO["quantiles"]]
    except Exception as exc:                                  # noqa: BLE001
        STATE["last_error_s"] = time.time()
        print(f"[ai] band inference error: {exc}")
        return None
    band = [max(0.0, min(3600.0, v)) for v in band]
    lo, mid, hi = sorted(band)
    return (lo, mid, hi)


def model_2_source() -> str:
    """Which artifact is actually answering ``predict_ttf``. For ``/api/ai``."""
    if band_available():
        return "ttf_model_v2 (quantile median)"
    if MODEL_INFO.get("available"):
        return "ttf_model_v1 (point)"
    return "none"


def sanity_gate(feats: list[float] | np.ndarray, ttf: float) -> bool:
    """Is an imminent-failure prediction physically credible?

    ``False`` = reject. A TTF under 30 s with no velocity and no displacement is
    a stale buffer artefact, not a collapse.
    """
    if ttf >= 30.0:
        return True
    v10 = feats[4]
    disp_now = feats[0]
    return not (v10 < 0.5 and disp_now < 30.0)


def run_inference(node_buffers: dict) -> dict[str, float]:
    """1 Hz: predict per-node TTF from the rolling buffers. Returns fresh values.

    ``node_buffers`` is the caller's node registry (``{node_id: deque}``); this
    module never owns buffers, so the replay clock and the live clock can both
    drive the same code path.
    """
    if not MODEL_INFO["available"]:
        return {}
    got_any = False
    out: dict[str, float] = {}
    for n_id, buf in node_buffers.items():
        feats = compute_live_features(buf)
        if feats is None:
            continue
        ttf = predict_ttf(feats)
        if ttf is None:
            continue
        if not sanity_gate(feats, ttf):
            continue
        MODEL_TTF[n_id] = ttf
        out[n_id] = ttf
        got_any = True
        STATE["predictions"] += 1
    if got_any:
        STATE["last_ok"] = time.time()
    return out


def model_fresh() -> bool:
    """True when the model has a recent, valid prediction worth trusting."""
    if not MODEL_INFO["available"]:
        return False
    if (time.time() - STATE["last_ok"]) > MODEL_FRESH_S:
        return False
    return any(v is not None for v in MODEL_TTF.values())


def model_status() -> dict:
    """The ``/api/ai`` body — identical keys and strings to the v4.4 endpoint.

    Moved from ``get_ai``. The three ``status`` strings are a contract with the
    dashboard: ``ACTIVE`` · ``WARMING UP`` · ``FALLBACK``.
    """
    avail = MODEL_INFO["available"]
    fresh = model_fresh()
    live = {n: MODEL_TTF.get(n) for n in ACTIVE_NODES}
    status = "ACTIVE" if (fresh and any(v is not None for v in live.values())) else (
        "WARMING UP" if avail else "FALLBACK")
    meta = MODEL_INFO["meta"] if avail else {}
    v1_mae = (meta.get("metrics") or {}).get("held_out_critical_mae_s")
    serving_v2 = band_available()
    if not avail and not serving_v2:
        model_name = "Kinematic Extrapolation Fallback"
    elif serving_v2:
        model_name = "ttf_model_v2"
    else:
        model_name = meta.get("model", "ttf_model_v1")
    mae = (BAND_INFO.get("meta") or {}).get("median_mae_s") if serving_v2 else v1_mae
    confidence = (f"{max(50, 100 - int(round((v1_mae or KINEMATIC_FALLBACK_MAE) * 3)))}%"
                  if avail else "85%")
    if not avail and not serving_v2:
        note = "Using 1/v derived TTF calculations."
    elif not fresh:
        note = "Warming up: building the 60 s telemetry buffer."
    elif serving_v2:
        cov = (BAND_INFO.get("meta") or {}).get("coverage_80")
        note = ("Quantile TTF · median answers, band is q10–q90 · "
                + (f"median MAE {mae:.0f} s · " if mae is not None else "")
                + (f"80% coverage {100 * float(cov):.0f}% · " if cov is not None else "")
                + "kinematic fallback armed. v1 stays loaded as the point-model rollback.")
    else:
        note = (f"XGBoost TTF forecast · held-out MAE {mae:.1f} s · "
                "kinematic fallback armed." if mae is not None
                else "XGBoost TTF forecast · kinematic fallback armed.")

    def forecast(t, node):
        if fresh and t is not None:
            return t <= 300.0
        return node["status"] != "NOMINAL"

    return {
        "model": model_name,
        "status": status,
        "horizon": "1 Hour",
        "last_train": (BAND_INFO.get("created") or meta.get("trained_at", "Static"))
                      if band_available() else meta.get("trained_at", "Static"),
        "confidence": confidence,
        "note": note,
        "forecasts": {n: forecast(live[n], _node_view(n)) for n in ACTIVE_NODES},
    }


#: Set by ``app.py`` at import time so ``model_status`` can read node status
#: without importing app (which would be a cycle). ``{node_id: {"status": …}}``.
NODE_PROVIDER: callable = lambda n_id: {"status": "NOMINAL"}      # noqa: E731


def _node_view(node_id: str) -> dict:
    try:
        return NODE_PROVIDER(node_id)
    except Exception:
        return {"status": "NOMINAL"}


def set_node_provider(fn) -> None:
    """Wire the app's node registry in (called once from ``app.py``)."""
    global NODE_PROVIDER
    NODE_PROVIDER = fn


def model_meta() -> dict:
    """The canonical metrics block — the single source the docs are checked against."""
    return MODEL_INFO.get("meta") or {}


__all__ = [
    "MODEL_INFO", "MODEL_TTF", "STATE", "KINEMATIC_FALLBACK_MAE",
    "load_ttf_model", "predict_ttf", "sanity_gate", "run_inference",
    "model_fresh", "model_status", "model_meta", "set_node_provider",
    "MODEL_BUF",
]
