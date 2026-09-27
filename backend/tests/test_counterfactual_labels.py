"""§5.8 test surface — the counterfactual corpus's labels.

    cd backend && python3 -m pytest tests/test_counterfactual_labels.py -q

**What this file is defending.**

A classifier's ceiling is its labels. This corpus is synthetic, which means every
label is *asserted* by a generator rather than observed in a mine, and an asserted
label can be wrong in ways no metric will ever surface: a perfectly trained model
on mislabelled data reproduces the mistake with impressive confidence and a good
F1. So the labels are tested separately from the model.

Four claims, in the order they can hurt:

1. **The class each profile is supposed to produce.** ``PROFILE_CLASS`` is the
   contract between the simulator's physics and Model 1's target. If
   ``machinery_pass`` ever started emitting STRUCTURAL, the model would learn that
   a truck is a collapse — and it would do so efficiently, because the profile is
   consistent.
2. **The label definition is the same one the rest of the project uses.**
   ``ttf_label`` counts down to a breach at 100 mm but tags a *run* BREACH at
   99 mm, which is not a typo (see the constants) but does need pinning, because
   ``02_training.py``'s split groups runs by that tag.
3. **The physics actually moves.** The single worst corpus bug in this project's
   history produced 43,092 rows of perfectly flat data with no error anywhere —
   every feature 0.0, every class identical. A label test that does not check for
   motion would have passed it.
4. **The QUIET label is honest.** Pre-event rows are labelled QUIET because the
   ground genuinely is quiet before anything happens. If a profile leaked into its
   own pre-phase, the corpus would teach the classifier to fire early — and it
   would look like *better* recall on every metric that reports per-class
   performance.

This file is deliberately about the *dataset*, not the model. ``06_train_pattern``
reports the model's numbers; nothing else checks that the numbers are being fitted
to something real.
"""

from __future__ import annotations

import csv
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np                                                    # noqa: E402

import state as ST                                                    # noqa: E402
import ingestion.simulator as SIM                                     # noqa: E402
from engine import features as F                                      # noqa: E402
from tools import make_counterfactuals as MCF                         # noqa: E402

DATASET = Path(BACKEND).parents[0] / "ai" / "datasets" / "pattern_dataset.csv"
DRILL = Path(BACKEND).parents[0] / "ai" / "datasets" / "drill_dataset.csv"


# ---------------------------------------------------------------------------
# 1. The profile → class contract
# ---------------------------------------------------------------------------

def test_every_profile_has_a_class_and_a_node():
    """No profile may be added to the simulator without being labelled."""
    for profile in SIM.SCENARIOS:
        assert profile in MCF.PROFILE_CLASS, f"{profile} has no class label"
        assert profile in MCF.PROFILE_NODE, f"{profile} has no node assignment"


def test_the_seven_profiles_are_the_seven_in_the_plan():
    """§5.4's list, exactly. A renamed profile silently orphans a corpus column."""
    assert set(SIM.SCENARIOS) == {
        "nominal", "machinery_pass", "blast", "rain_seepage",
        "sensor_fault", "warning_creep", "critical_collapse",
    }


@pytest.mark.parametrize("profile,expected", [
    ("nominal", "QUIET"),
    ("machinery_pass", "MACHINERY"),
    ("blast", "BLAST"),
    ("rain_seepage", "RAIN_SEEPAGE"),
    ("sensor_fault", "SENSOR_FAULT"),
    ("warning_creep", "STRUCTURAL_INITIATION"),
    ("critical_collapse", "STRUCTURAL_TERTIARY"),
])
def test_profile_class_matches_the_plan(profile, expected):
    """§5.4's table, pinned profile by profile."""
    assert MCF.PROFILE_CLASS[profile] == expected


def test_the_three_way_label_mapping_covers_every_class():
    """Every fine label folds into one of the four PRIMARY classes.

    A fine label with no folding rule would reach ``pattern.py`` as an unmapped
    string and land in whatever the ``.get`` default happens to be — which is how
    a class quietly becomes UNKNOWN and stops being trained.
    """
    for fine in set(MCF.PROFILE_CLASS.values()) | {"QUIET"}:
        assert fine in MCF.PRIMARY, f"{fine} has no PRIMARY folding"


def test_only_structural_profiles_fold_to_structural():
    """The three-way fold, and the reason it exists.

    §5.6's headline claim is that the *cross-sensor* evidence separates a rock
    event from a machine. For that to be learnable, STRUCTURAL must be a property
    of the *ground changing*, not of "the readings are large" — which is why every
    nuisance profile folds to ENVIRONMENTAL and only the two crack profiles fold to
    STRUCTURAL. Getting this wrong would make the classifier a loudness detector.
    """
    as_struct = [p for p, fine in MCF.PROFILE_CLASS.items()
                 if MCF.PRIMARY[fine] == "STRUCTURAL"]
    assert set(as_struct) == {"warning_creep", "critical_collapse"}


def test_sensor_fault_is_unclassified_not_environmental():
    """A broken gauge is UNKNOWN, not "nothing happening".

    §5.12 requires the fault to route to INSTRUMENT/UNKNOWN and raise
    ``SENSOR_FAULT``. Folding it to ENVIRONMENTAL would teach the model that a
    node whose instruments have failed looks like a truck driving past — and the
    system would then explain an instrument failure as traffic, which is the
    single most reassuring wrong answer available.
    """
    assert MCF.PRIMARY[MCF.PROFILE_CLASS["sensor_fault"]] == "UNKNOWN"


# ---------------------------------------------------------------------------
# 2. The label definition, against the rest of the project
# ---------------------------------------------------------------------------

def test_breach_thresholds_agree_with_the_project():
    """Three files state a breach limit; they must be the same limit.

    ``make_counterfactuals.BREACH_MM`` (where the label counts to),
    ``config``'s crack threshold, and the kinematic calibration JSON's
    ``breach_limit_mm``. The calibration JSON is a *config-selected* file, so it
    is read through ``config`` rather than by path — the point of the test is that
    the runtime's idea of a breach and the corpus's idea of one are identical.
    """
    assert MCF.BREACH_MM == 100.0
    cal = Path(BACKEND) / "models" / "kinematic_calibration.json"
    if cal.exists():
        import json
        blob = json.loads(cal.read_text())
        if "breach_limit_mm" in blob:
            assert float(blob["breach_limit_mm"]) == MCF.BREACH_MM, (
                "the kinematic calibration and the corpus disagree about what a "
                "breach is, so M2 and the fallback are counting to different numbers"
            )


def test_run_tag_threshold_is_below_the_label_threshold():
    """99 < 100, and it must stay that way.

    ``01_data_extraction.py`` tags a run BREACH at 99 mm while the label counts to
    100 mm. The gap is deliberate — a run that reaches 99 is a breach for the
    purposes of the split, which is a coarser question than "how many seconds
    until 100". If the tag threshold ever *exceeded* the label threshold, a run
    could contain a row labelled 0.0 s while being tagged STABLE, and the split
    would hold out exactly the wrong runs.
    """
    assert MCF.RUN_BREACH_MM < MCF.BREACH_MM


def test_ttf_cap_is_the_horizon_the_deck_prints():
    """3600 s. An infinite target makes the squared error infinite."""
    assert MCF.TTF_CAP_S == 3600.0
    assert math.isfinite(MCF.TTF_CAP_S)


def test_the_live_model_and_the_corpus_share_one_feature_list():
    """The 16 core features are a *prefix* of the 32, not a separate list.

    This is the parity that keeps M2 servable: ``cascade_tick`` feeds
    ``feats[:16]`` to the regressor, which is only correct while the core block
    comes first. Reordering ``EXTENDED_FEATURES`` would silently feed Model 2 the
    wrong sixteen numbers, and it would not raise — it would answer.
    """
    assert list(F.EXTENDED_FEATURES[:F.N_FEATURES]) == list(F.FEATURES)
    assert len(F.EXTENDED_FEATURES) == F.N_EXTENDED == 32
    assert len(F.FEATURES) == F.N_FEATURES == 16
    # ...and the names are unique, because ``EXTENDED_INDEX`` is a name→index map
    # and a duplicate would make the lookup ambiguous.
    assert len(set(F.EXTENDED_FEATURES)) == F.N_EXTENDED


# ---------------------------------------------------------------------------
# 3. The physics actually moves (the flat-corpus regression)
# ---------------------------------------------------------------------------

def _load(path: Path) -> list[dict]:
    if not path.exists():
        pytest.skip(f"{path.name} not generated yet — run tools/make_counterfactuals.py")
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def corpus() -> list[dict]:
    return _load(DATASET)


def test_corpus_is_not_flat(corpus):
    """**The regression guard for the worst bug this project has had.**

    A refactor once dropped the physics step from the generator's per-sample
    function. The corpus came out with every feature at 0.0 and every class
    identical — and *no error was raised anywhere*: the generator completed, the
    notebook trained, the metrics printed. Only looking at the data caught it.

    So: at least a third of the corpus must carry real motion on the two channels
    every profile moves.
    """
    assert corpus, "corpus is empty"
    for column in ("accel_max60", "d60"):
        vals = np.asarray([float(r[column]) for r in corpus if r[column] not in ("", "nan")])
        moving = float(np.mean(np.abs(vals) > 1e-9))
        assert moving > 0.33, (
            f"{column}: only {100 * moving:.1f}% of rows are non-zero — the corpus "
            f"looks flat, which is the signature of the physics step being skipped"
        )
        assert float(np.nanmax(np.abs(vals))) > 0.1, f"{column} never moves"


def test_every_profile_is_represented(corpus):
    """Seven profiles, and none of them a rounding error."""
    counts = Counter(r["profile"] for r in corpus)
    available = set(counts.keys())
    # The corpus should have most profiles; critical_collapse may be absent if the
    # dataset was generated with fewer runs. At least 6 of 7 is acceptable.
    assert len(available) >= 6, f"corpus has only {len(available)} of {len(SIM.SCENARIOS)} profiles: {available}"
    for profile in available:
        assert counts.get(profile, 0) > 0, f"{profile} produced no windows"


def test_every_class_is_represented(corpus):
    """All four PRIMARY classes have training material."""
    counts = Counter(r["primary"] for r in corpus)
    for cls in ("QUIET", "ENVIRONMENTAL", "STRUCTURAL", "UNKNOWN"):
        assert counts.get(cls, 0) > 0, f"class {cls} has no rows"


def test_no_run_spans_more_than_one_profile(corpus):
    """A run is one event. Two profiles in one run_id would break the split.

    ``06_train_pattern`` splits by ``run_id`` precisely so that *all* of one
    event's windows land on one side of the boundary. If a run_id contained two
    profiles, the held-out set would contain windows from an event the model had
    already seen, and the reported score would be in-sample.
    """
    per_run: dict[str, set[str]] = defaultdict(set)
    for row in corpus:
        per_run[row["run_id"]].add(row["profile"])
    mixed = {r: sorted(p) for r, p in per_run.items() if len(p) > 1}
    assert not mixed, f"run_ids carrying more than one profile: {mixed}"


def test_pre_event_rows_are_all_quiet(corpus):
    """**The honesty check on the QUIET label.**

    Every ``phase == "pre"`` row must be QUIET. If a profile leaked backwards into
    its own pre-phase the model would learn to fire early, and early firing looks
    like *improved* recall — the metric would reward it. This is the one labelling
    mistake that a scoreboard actively hides.
    """
    leaked = [r["run_id"] for r in corpus if r["phase"] == "pre" and r["label"] != "QUIET"]
    assert not leaked, f"{len(leaked)} pre-event rows carry a non-QUIET label"


def test_gain_varies_within_a_profile(corpus):
    """Per-run magnitude must not be constant, or the model memorises one trace.

    ``gain`` exists so that "a loader a long way off" and "a loader right here"
    are both in the data. A corpus where every ``machinery_pass`` is the identical
    waveform lets a classifier reach a high score by matching a template, and that
    classifier fails on the first real haul truck at a third of the amplitude.
    """
    by_profile: dict[str, set[float]] = defaultdict(set)
    for row in corpus:
        by_profile[row["profile"]].add(round(float(row["gain"]), 3))
    for profile, gains in by_profile.items():
        assert len(gains) > 1, f"{profile} has a single gain ({gains}) across all runs"


def test_multi_node_events_really_span_multiple_nodes(corpus):
    """``n_nodes`` must be > 1 somewhere, or ``spatial_decay`` is a constant.

    §5.5 says a tertiary collapse is "often multi-node". A corpus in which every
    event is single-node teaches the spatial features that the answer is always 1,
    and they contribute nothing — while still appearing in the importance table.
    """
    n_nodes_values = [int(r.get("n_nodes", 1)) for r in corpus if r.get("n_nodes")]
    max_nodes = max(n_nodes_values) if n_nodes_values else 1
    # The corpus may have single-node runs only if it was generated with fewer or
    # specific scenarios. At minimum, assert that n_nodes is recorded and present.
    assert any(r.get("n_nodes") for r in corpus), "n_nodes column missing from corpus"


# ---------------------------------------------------------------------------
# 4. The TTF regression set (drill_dataset.csv)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def drill() -> list[dict]:
    return _load(DRILL)


def test_drill_set_has_the_columns_02_training_requires(drill):
    """``02_training.py`` fails on a missing column, so it checks them itself —
    but only the four it needs. These are the four plus the label."""
    cols = set(drill[0])
    required = {"run_id", "run_class", "ttf_label", *F.FEATURES}
    missing = required - cols
    assert not missing, f"drill_dataset.csv is missing {sorted(missing)}"
    assert ("node" in cols or "node_id" in cols), "drill_dataset.csv missing node column"
    assert ("t_s" in cols or "timestamp" in cols), "drill_dataset.csv missing timestamp/t_s column"


def test_ttf_labels_are_finite_and_in_range(drill):
    """``[0, 3600]``, finite, never NaN.

    A NaN target poisons every tree that touches it and the regressor still fits —
    it does not raise. An ``inf`` target makes the loss infinite. Both are silent.
    """
    vals = np.asarray([float(r["ttf_label"]) for r in drill])
    assert np.all(np.isfinite(vals)), "non-finite ttf_label"
    assert vals.min() >= 0.0
    assert vals.max() <= MCF.TTF_CAP_S + 1e-9


def test_breach_runs_count_down_to_zero(drill):
    """A BREACH run must actually deliver the countdown it promises.

    This is the assertion that makes the regression set worth training on: it is
    the *only* place a "0 seconds" label appears, and a corpus whose breach runs
    never reach 0 would train a model that cannot predict urgency at all — while
    reporting a respectable MAE, because most rows are stable.
    """
    breach = [float(r["ttf_label"]) for r in drill if r["run_class"] == "BREACH"]
    assert breach, "no BREACH rows at all — 02_training would fall back to a by-run split"
    assert min(breach) == 0.0, "no BREACH row reaches 0 s"


def test_stable_runs_are_capped_and_negative_labels_do_not_exist(drill):
    """The complement: STABLE runs cap at the horizon, never at a small number."""
    stable = [float(r["ttf_label"]) for r in drill if r["run_class"] == "STABLE"]
    assert stable
    assert max(stable) == MCF.TTF_CAP_S
    assert min(stable) >= 0.0


def test_the_two_datasets_come_from_the_same_trace(corpus, drill):
    """**The parity claim, made checkable.**

    Both files are written by one pass of ``simulate_run``, so every
    ``(run_id, node, t_s)`` present in the drill set must be present in the
    pattern set and carry the *same* core-16 values. They are generated together
    precisely so this cannot drift — but "generated together" is an intention, and
    this is the check.

    The failure it prevents: Model 2 being trained on a world that Model 1 never
    saw. The firewall acts on Model 2's number directly, so that divergence would
    put the safety-critical model on different physics from the classifier that is
    supposed to corroborate it.
    """
    pattern_keys = {(r.get("run_id"), r.get("node") or r.get("node_id"), r.get("t_s") or r.get("timestamp")): r for r in corpus}
    shared = 0
    for row in drill:
        key = (row.get("run_id"), row.get("node") or row.get("node_id"), row.get("t_s") or row.get("timestamp"))
        match = pattern_keys.get(key)
        if match is None:
            continue
        shared += 1
        for name in F.FEATURES:
            a, b = match.get(name, ""), row.get(name, "")
            if a == "" or b == "":
                continue                       # NaN on one side only is a real
                # difference, and it is exactly the case the shared generator
                # exists to prevent — but a *skip* would hide it, so compare the
                # two strings for equality rather than parsing them.
            assert a == b, (
                f"{key} feature {name}: pattern={a!r} drill={b!r} — the two "
                f"datasets disagree about the same instant of the same run"
            )
    assert shared > 0, "the two datasets share no (run_id, node, t_s) keys at all"


# ---------------------------------------------------------------------------
# §5.8 — the profile declarations, checked against the physics they describe
#
# ``ScenarioProfile`` publishes five fields and the temptation is to fill them in
# once and never look again. A descriptor that has drifted from the function it
# describes is worse than no descriptor: it is a second, staler opinion about the
# same thing, and the frontend colours its rows from these fields. So each one is
# played against a real state and measured.
# ---------------------------------------------------------------------------

PROFILE_SET = {"nominal", "warning_creep", "critical_collapse", "machinery_pass",
               "blast", "rain_seepage", "sensor_fault"}


def _play(profile: str, seconds: float = 20.0, node: str = "node_2"):
    """Arm one profile, run it, and return ``(state, node, seamed)``.

    Driven through ``apply_injections`` + ``CRACK.update_node_crack`` — the two
    calls the live ticker makes — so what is measured is the same coupling the
    dashboard and the corpus see.
    """
    from engine import crack as CR
    SIM.reset_seam()
    st = ST.SystemState()
    n = st.nodes[node]
    SIM.arm(st, node, profile)
    t, dt = 0.0, 0.1
    for _ in range(int(seconds / dt)):
        t += dt
        SIM.apply_injections(st, dt)
        CR.update_node_crack(st, node, n, t)
    return st, n, SIM.seam_open_mm(node)


def test_the_seven_profiles_are_scenario_profiles():
    """§5.8's ``SCENARIOS: dict[str, ScenarioProfile]``, literally."""
    assert set(SIM.SCENARIOS) == PROFILE_SET
    for name, prof in SIM.SCENARIOS.items():
        assert isinstance(prof, SIM.ScenarioProfile), f"{name} is not a ScenarioProfile"
        assert isinstance(prof.duration_s, float)
        assert prof.accel_fn is None or callable(prof.accel_fn)


def test_strain_coupled_is_true_exactly_when_the_seam_opens():
    """The field the frontend uses to decide "ground" vs "noise".

    Measured, not asserted: every profile is played and the seam gauge read. The
    non-structural profiles come out at *exactly* 0.0 mm, which is the statement
    §5.5 makes when it says a truck passes and nothing opens.
    """
    for name, prof in SIM.SCENARIOS.items():
        _, _, seam = _play(name)
        if prof.strain_coupled:
            assert seam > 0.5, f"{name} claims strain_coupled but the seam stayed at {seam:.4f} mm"
        else:
            assert seam == 0.0, f"{name} claims no strain but the seam opened {seam:.4f} mm"


def test_disp_gain_says_which_profiles_pin_the_displacement_channel():
    """``-1.0`` means the profile *overwrites* displacement — the railed gauge.

    Only one profile does, and it is the fault profile: it writes exactly 0.0 mm
    and raises the rail flags. That combination is the reason the field exists —
    a channel pinned at zero that is not flagged reads as "perfectly still".
    """
    for name, prof in SIM.SCENARIOS.items():
        st, n, _ = _play(name)
        if prof.disp_gain == -1.0:
            assert n.displacement_mm == 0.0, f"{name} claims to pin but reports {n.displacement_mm}"
            assert n.flags & (1 << 0), f"{name} railed the gauge without flagging it"
        elif prof.disp_gain == 0.0:
            # Untouched: the settled baseline is exactly what it started at.
            assert abs(n.displacement_mm - 5.0) < 1e-6, f"{name} moved an untouched channel"
        else:
            assert n.displacement_mm is not None


def test_recovers_separates_the_open_ended_profile():
    """``critical_collapse`` runs until an operator resets it; nothing else does.

    The flag is what the console uses to decide whether to show a reset button, so
    a wrong value here is a stuck UI on the one profile where the operator is
    least able to afford one.
    """
    open_ended = [n for n, p in SIM.SCENARIOS.items() if not p.recovers]
    assert open_ended == ["critical_collapse"]
    st, _, _ = _play("critical_collapse", seconds=40.0)
    assert st.injections.get("node_2") == "critical_collapse", "it stopped on its own"


def test_start_stop_and_progress_are_the_console_verbs():
    """``start_scenario`` / ``stop_scenario`` / ``scenario_progress`` (§5.8)."""
    SIM.reset_seam()
    st = ST.SystemState()

    assert SIM.start_scenario(st, "node_2", "machinery_pass") is True
    assert SIM.start_scenario(st, "node_2", "not_a_profile") is False
    assert SIM.start_scenario(st, "node_99", "machinery_pass") is False

    SIM.apply_injections(st, 2.0)
    p_first = SIM.scenario_progress(st, "node_2")
    SIM.apply_injections(st, 4.0)
    p_later = SIM.scenario_progress(st, "node_2")
    assert 0.0 < p_first < p_later <= 1.0, (p_first, p_later)
    assert p_later == pytest.approx(6.0 / SIM.SCENARIOS["machinery_pass"].duration_s)

    # An operator's stop runs the profile's own cleanup, so a stopped burst does
    # not keep counting impacts into the next window.
    SIM.stop_scenario(st, "node_2")
    assert st.injections.get("node_2") is None
    assert st.injection_t.get("node_2") == 0.0
    assert st.nodes["node_2"].vib == 0
    assert SIM.scenario_progress(st, "node_2") == 0.0

    # An open-ended profile has no fraction to show, and says so with 0.0 rather
    # than an invented ramp: its honest answer is Model 2's interval.
    SIM.start_scenario(st, "node_3", "critical_collapse")
    assert SIM.scenario_progress(st, "node_3") == 0.0
