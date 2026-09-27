"""
Novelty stays off the critical path.

The score may be present. It must not be an input to the tier, and turning the
layer off must yield no score rather than a default all-clear.
"""
import importlib.util
import inspect

from engine import cascade as C
from engine import novelty as N


def test_novelty_module_loads_and_explains_itself():
    spec = importlib.util.find_spec("engine.novelty")
    assert spec is not None
    assert callable(N.unavailable_reason)
    assert callable(N.score)


def test_a_disabled_layer_returns_no_score(monkeypatch):
    monkeypatch.setattr(N, "ENABLED", False)
    assert N.score([0.0] * 16) is None
    assert N.unavailable_reason()


def test_an_enabled_score_is_advisory_and_bounded(monkeypatch):
    monkeypatch.setattr(N, "ENABLED", True)
    out = N.score([0.0] * 16)
    assert out is not None
    assert out["advisory"] is True
    assert 0.0 <= out["index"] <= 1.0
    assert "tier" not in out


def test_decide_does_not_take_a_novelty_argument():
    import inspect
    names = inspect.signature(C.decide).parameters
    assert "novelty" not in names
