"""
Phase 5 (B2) — AI cascade: `/api/ai` with Model 1 + Model 2 fusion
============================================================

The deck's **AI cascade** panel reads `/api/ai` for the entire decision stream:
Model 1's classification + Model 2's regression → cascade verdict → severity

The response shape is **additive** across models and across phases:
Model 1 classification (Phase 5), Model 2 forecast (Phase 4), severity (Phase 6),
confidence (Phase 6), kinematic provenance (Phase 6) — and nothing is removed.

Each model may be unavailable; when it is the response publishes the failure
state rather than raising an HTTP error, so the dashboard can still render its
own version of the panel.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from api import map_payload
from backend.state import SystemState

bp = Blueprint("ai", __name__)

#: The single live source of truth, injected at bootstrap.
_STATE = None


def bind(state: SystemState) -> None:
    """Hand the state to the AI routes.

    Phase 1 moved the node set out of module globals into SystemState, and every
    route that needs it now gets injected rather than imported. This is the third
    place in the repository where this distinction has had to be made (see
    `engine/health.py`'s third rule and the crack engine branch above), and the
    second place it was forgotten.
    """
    global _STATE
    _STATE = state


def _state():
    return _STATE


@bp.route("/api/ai", methods=["GET"])
def get_ai():
    """The AI cascade block: Model 1 + Model 2 → fused verdict.

    The contract (static, present since Phase 4) is:
    ```json
    {
      "model": "xgboost_v1",
      "status": "ACTIVE / WARMING UP / FALLBACK",
      "horizon": "TTF in seconds (integer)",
      "last_train": "ISO timestamp or null",
      "confidence": "MEDIUM",
      "note": "optional string (empty when not warming/falling back)",
      "forecasts": [ {"model": "xgboost_v1", "value": 123.4, "band": [80,120] } ]
    }
    ```

    Phase 5 adds two blocks:
    * ``model_1`` — the pattern discriminator (Phase 5's new classifier)
    * ``cascade`` — the fusion: mode, cadence M2 is actually running at, the
      disagreement flag, and a sentence why.

    Both are **additive**, and both are absent-safe: with no classifier on disk
    ``model_1`` reports ``UNAVAILABLE`` and ``cascade`` reports the M2-only
    path, rather than the endpoint 500-ing or the card going blank.

    Model 1 is **computed from live telemetry** (the window of the most recent
    complete 60 s of samples) while Model 2 is a regressor over the same data,
    so both live in the same buffer and are updated on the same 1 Hz tick. The
    fusion uses the last live verdict for both.

    The function is **side-effect free**: reading the cache is fine, but writing
    even a trace line is not.
    """
    if _STATE is None:
        return jsonify({"error": "state not bound"}), 503

    # Model 2 (TTF) block - from engine.ttf.model_status()
    model_2_block = ttf_model.model_status()

    # Model 1 (pattern) block
    from engine import pattern as pattern_engine
    model_1_block = pattern_engine.pattern_status()

    # Cascade fusion
    from engine import cascade as cascade_engine
    cascade_block = cascade_engine.cascade_status()

    # Additional blocks from other phases
    from engine import severity as severity_engine
    from engine import ttf as ttf_model

    # Severity block (Phase 6)
    severity_block = severity_engine.severity_snapshot(_STATE)

    # Kinematic block (Phase 6)
    kinematic_block = severity_engine.kinematic_block(_STATE)

    # Confidence band (Phase 6)
    conf_tier = "MEDIUM"
    try:
        worst = max(
            (v.get("confidence", "MEDIUM") for v in severity_block.values()),
            key=severity_engine.severity_rank,
            default="MEDIUM",
        )
        conf_tier = worst
    except Exception:
        pass

    confidence_block = {"HIGH": "95%", "MEDIUM": "80%", "LOW": "60%"}.get(
        conf_tier, model_2_block.get("confidence", "MEDIUM"),
    )

    # Site metadata for the AI panel header
    site_meta = map_payload.load_site_meta()

    response = {
        "model": model_2_block.get("model", "xgboost_v1"),
        "status": model_2_block.get("status", "WARMING UP"),
        "horizon": model_2_block.get("horizon", "N/A"),
        "last_train": model_2_block.get("last_train"),
        "confidence": confidence_block,
        "note": model_2_block.get("note", ""),
        "forecasts": model_2_block.get("forecasts", []),
        "model_1": model_1_block,
        "cascade": cascade_block,
        "severity": severity_block,
        "kinematic": kinematic_block,
        "site_meta": site_meta,
    }

    return jsonify(response)


__all__ = ["bp", "bind", "get_ai"]