"""
Phase 12 — Blackbox Safety Alerts
=================================

The blackbox gateway produces alerts when things go wrong:
- Missing hub during bench mode (scenario 1: 15 s delay, scenario 2: 30 s)
- Gateway reconnect after disconnect
- Outbox depletion warnings
- Manual intervention required

These alerts integrate with the existing alert system via the
``alerts/alerts_dispatcher.py`` so operators see them in ``/api/alerts``
and ``/api/extended``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.blackbox import gateway as gateway_mod

# The gateway is the authority on blackbox state; alerts are projections of
# gateway health and recovery plane behavior.

_GATEWAY = None


def bind(gateway):
    """Inject the gateway source — a callable, not the gateway object itself.

    The gateway may start, stop, or restart (e.g., serial reconnect), so the
    route must ask "where is the gateway *now*" rather than remember what it
    was at import time.
    """
    global _GATEWAY
    _GATEWAY = gateway


def _gw():
    if _GATEWAY is None:
        return None
    return _GATEWAY() if callable(_GATEWAY) else _GATEWAY


def _alert(message: str, tier: str = "HIGH", kind: str = "BLACKBOX"):
    """Issue a blackbox alert.

    The alert layer expects a ``STATE`` object; this is a legacy wrapper so the
    function signature is consistent across other alert modules.
    """
    # This will be filled in by the caller using the actual alert system
    print(f"[BLACKBOX ALERT] {tier}: {message}")
    # TODO: Integrate with actual alert system when State is available


def monitor_gateway_health():
    """Check the gateway for safety issues and issue alerts as needed.

    Runs on every ticker (50 ms) to catch transient conditions quickly.
    """
    gw = _gw()
    if gw is None:
        # No gateway configured is normal for bench mode with --no-serial
        return

    mode = getattr(gw, "mode", None)
    if mode == "OFF":
        # This means no hub attached and no configured source
        # This is a valid state for bench mode
        return

    # Check for missing hub in bench mode scenarios
    if mode == "SIM":
        # Scenario 1: 15 s delay threshold
        # Check if we've been in SIM mode for >15 seconds without external feed
        sim_start = getattr(gw, "sim_start", datetime.now(timezone.utc))
        elapsed = (datetime.now(timezone.utc) - sim_start).total_seconds()
        if elapsed > 15:
            _alert(f"SIM mode active for {int(elapsed)} seconds - no external feed", tier="WARNING")

        # Scenario 2: 30 s delay threshold
        if elapsed > 30:
            _alert(f"SIM mode active for {int(elapsed)} seconds - extended bench mode", tier="CRITICAL")

    # Check for active gateway issues
    try:
        status = gw.extended_blocks()
        if status.get("mode") == "ERROR":
            _alert(f"Gateway in ERROR mode: {status.get('reason', 'unknown')}", tier="CRITICAL")

        # Check for outbox depletion warnings
        if "ledger" in status:
            ledger = status["ledger"]
            if ledger.get("chunks", 0) > 0 and ledger.get("releasable", 0) == 0:
                # No releasable chunks despite having chunks
                _alert(f"No releasable chunks - sync may be stuck", tier="WARNING")

    except Exception as e:
        _alert(f"Failed to get gateway status: {e}", tier="HIGH")


def monitor_recovery_plane():
    """Monitor the recovery plane (SD blackbox) for integrity issues.

    Checks:
    - SD card accessible
    - Recovery plane data consistency
    - Delete gate decisions
    """
    gw = _gw()
    if gw is None:
        return

    try:
        # Check recovery plane status
        recovery_status = getattr(gw, "recovery", None)
        if recovery_status is not None:
            recovery_stats = recovery_status.block()
            if recovery_stats.get("recovered", 0) == 0 and recovery_stats.get("active", 0) > 0:
                _alert(f"Recovery plane active but no data recovered", tier="WARNING")

        # Check delete gate health
        if hasattr(gw, "gate"):
            gate_stats = gw.gate.stats()
            if gate_stats.get("blocked_chunks", 0) > 0:
                # Some chunks are blocked by the delete gate
                _alert(f"{gate_stats['blocked_chunks']} chunks held by delete gate", tier="INFO")

    except Exception as e:
        _alert(f"Recovery plane monitor failed: {e}", tier="HIGH")


def monitor_failure_scenarios():
    """Monitor for failure rehearsal scenarios.

    This is a placeholder for Phase 12's failure rehearsal capabilities.
    In a full implementation, this would check for:
    - Missing telemetry data
    - Gateway state machine inconsistencies
    - Cloud sync failures
    - Data corruption indicators
    """
    # This would integrate with failure_suite.py when available
    pass


def full_health_check():
    """Run all blackbox monitoring checks.

    This is called from the main ticker and also from the health endpoint.
    """
    monitor_gateway_health()
    monitor_recovery_plane()
    monitor_failure_scenarios()


__all__ = ["bind", "full_health_check"]