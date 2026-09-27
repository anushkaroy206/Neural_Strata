"""
Neural Strata — ingestion: everything that turns bytes into state.
===============================================================

Three modules, one direction of flow::

    serial_reader.py   bytes  → validated frames → SystemState
    ring.py            relay envelopes + heartbeats + link state (the UART ring)
    simulator.py       the scenario console and its 7 injection profiles

**The rule this package exists to protect:** there is exactly one path from a
frame to a state change. ``serial_reader.handle_node_frame`` is that path, and it
is called by the USB reader, by the replay reader, by the ring relay and by the
simulator. Phase 0 established this for record/replay; Phase 1 keeps it while the
node count goes from two to four.

Nothing here owns state. ``SystemState`` (``backend/state.py``) does, and these
modules are handed a reference to it — which is why a replay, the bench and the
mine all exercise identical code.
"""

__all__ = ["serial_reader", "ring", "simulator"]
