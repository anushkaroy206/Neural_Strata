"""
Neural Strata — novelty layer. Advisory only.
==============================================

Scores how unlike the serving time-to-failure model's training distribution the
current 16-feature window is. The score is the root-mean-square of the model's
own scaled features: on the training set that number sits near 1, and it grows
as the window leaves that cloud.

It never raises a tier, never blocks a route, and never enters ``decide()``.
The cascade copies the finished verdict first and then annotates ``extra``.
``NS_NOVELTY_ENABLED=0`` turns the annotation off; the verdict is unchanged
either way.
"""
from __future__ import annotations

import math
import os

import numpy as np

from config import env_bool


def _meta_enabled() -> bool:
    try:
        from config import MODEL_FILES
        import json
        meta = json.loads(MODEL_FILES["novelty_meta"].read_text())
        return bool(meta.get("enabled"))
    except Exception:
        return False


ENABLED = env_bool("NS_NOVELTY_ENABLED", _meta_enabled())


def unavailable_reason() -> str:
    """Why this layer is not running, when it is not."""
    if ENABLED:
        return ""
    return "Novelty layer off — NS_NOVELTY_ENABLED is false and novelty_meta.json is disabled."


def _scaler():
    """Mean and scale of the model that is actually answering time-to-failure."""
    from engine import ttf as T
    if T.band_available():
        return T.BAND_INFO.get("mean"), T.BAND_INFO.get("scale")
    sc = (T.MODEL_INFO or {}).get("scaler") or {}
    return sc.get("mean"), sc.get("scale")


def score(feats) -> dict | None:
    """Advisory distance of one 16-feature window. ``None`` when the layer is off.

    ``index`` is 0 at the training cloud and rises toward 1 as the window gets
    stranger. It is not a failure probability.
    """
    if not ENABLED or feats is None:
        return None
    mean, scale = _scaler()
    if mean is None or scale is None:
        return None
    x = np.asarray(list(feats)[:16], dtype=float)
    mu = np.asarray(mean, dtype=float)
    sd = np.asarray(scale, dtype=float)
    n = min(x.size, mu.size, sd.size)
    if n < 8:
        return None
    x, mu, sd = x[:n], mu[:n], sd[:n]
    ok = np.isfinite(x) & np.isfinite(mu) & np.isfinite(sd) & (np.abs(sd) > 1e-12)
    if int(ok.sum()) < 8:
        return None
    z = (x[ok] - mu[ok]) / sd[ok]
    rms = float(math.sqrt(float(np.mean(z * z))))
    if not math.isfinite(rms):
        return None
    # Training windows have rms near 1. Anything past that is the departure.
    index = max(0.0, min(1.0, (rms - 1.0) / 4.0))
    return {
        "index": round(index, 3),
        "rms_z": round(rms, 3),
        "advisory": True,
        "seen": index < 0.35,
        "source": "ttf_scaler_z",
    }
