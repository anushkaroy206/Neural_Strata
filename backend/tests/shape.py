"""
Shared test helpers.
====================

`key_paths()` is what makes the API-contract test meaningful: comparing raw JSON
bodies would fail on every volatile value, while comparing *key paths and types*
is exactly the promise the contract makes ("new keys are additive only; the
existing paths never move or change type").
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def key_paths(obj, prefix: str = "") -> dict[str, str]:
    """Flatten a JSON document into ``{dotted.path: type_name}``.

    Lists collapse to one entry per element type (``nodes[]``), because a list's
    length is data, not contract.
    """
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            out[p] = type(v).__name__
            out.update(key_paths(v, p))
    elif isinstance(obj, list):
        out[f"{prefix}[]"] = "list"
        if obj:
            out.update(key_paths(obj[0], f"{prefix}[]"))
    return out


def fixture(name: str) -> dict:
    """Load a captured baseline payload."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def api_self_check(client, name: str) -> tuple[dict, dict, set[str]]:
    """Fetch ``/api/<name>`` and return ``(payload, fixture, missing_paths)``."""
    resp = client.get(f"/api/{name}")
    assert resp.status_code == 200, f"/api/{name} returned {resp.status_code}"
    payload = resp.get_json()
    base = fixture(name)
    p_live = key_paths(payload)
    p_base = key_paths(base)
    missing = {k for k in p_base if k not in p_live}
    return payload, base, missing
