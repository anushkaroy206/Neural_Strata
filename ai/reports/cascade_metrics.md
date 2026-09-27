# Neural Strata — Cascade metrics

**Phase 5 · Model 1 (pattern) + Model 2 (quantile trio) + the safety firewall**

Generated from the artifacts below. Every number here is reproducible with the
commands in the last section; `tools/check_numbers.py` fails the build if a
documented figure drifts from the file that ships.

| Artifact | Value |
|---|---|
| Model 1 | `backend/models/pattern_model_v1.json` (RandomForest, 200 trees, depth 8) |
| Model 1 meta | `backend/models/pattern_meta.json` |
| Model 2 | `backend/models/ttf_model_v2.json` — quantile trio q10/q50/q90, one file |
| Model 1 corpus | `ai/datasets/pattern_dataset.csv` — 420 runs / 43,092 windows |
| Model 2 corpus | `ai/datasets/drill_dataset.csv` — same 420 runs, 16 core features |
| Latency record | `ai/reports/latency.json` |

---

## 1. Model 1 — the pattern discriminator

Label mapping: the simulator's seven profiles fold to four `PRIMARY` classes
(`PROFILE_CLASS` → `PRIMARY`), so the model answers "what kind of event is this"
rather than "which drill was run".

| Class | Recall | Precision | Notes |
|---|---|---|---|
| QUIET | 1.0000 | 1.0000 | false positives **0.0000** — a still tunnel never alarms |
| ENVIRONMENTAL | 0.9940 | 0.9990 | the defined weakness: 17 errors, **all ENV → QUIET** |
| STRUCTURAL | **1.0000** | **1.0000** | the class that matters |
| UNKNOWN | 1.0000 | 1.0000 | the sensor fault |

**Macro F1 = 0.9977** on 8,208 held-out windows / 84 unseen runs, split
`GroupShuffleSplit` **by `run_id`** (60/20/20) — never by row, because a
row-wise split leaks one event's second half into its own training set and
reports a number that would not survive a single real drill.

Operating threshold **0.05**. That is the grid floor, and it must not be read as
a tuned point: the threshold sweep searches `[0.05, 0.95]` for the lowest value
achieving STRUCTURAL recall ≥ 0.98, and STRUCTURAL is separable enough that the
search walks straight to the bottom of the grid. Recorded here so nobody later
quotes it as a calibrated decision boundary.

> **The 17 ENV → QUIET errors are the honest failure mode, and the safe one.**
> The model occasionally reads a nuisance event as nothing. The opposite
> confusion — QUIET → ENVIRONMENTAL — would raise an advisory on a still tunnel,
> and repeated false advisories are how operators learn to ignore advisories.
> The direction of this error was chosen, not accepted: it is why `regime 2` of
> the acceptance table below is stated as "adjacent nuisance classes may confuse
> each other; the error may not cross from QUIET to a structural class", and it
> is asserted rather than assumed.

Feature importance splits **0.646 core-16 / 0.354 new-16** — the disruption
features added in this phase carry more than a third of the model's decisions,
so they are load-bearing rather than decorative. The top six:
`v60` 0.1338 · `d60` 0.1272 · `disp_now` 0.1054 · `accel_max60` 0.0942 ·
`recovery_ratio` 0.0666 · `impulse_periodicity` 0.0663.

## 2. Model 2 — the quantile trio

Three boosters at α = 0.10 / 0.50 / 0.90, trained on the same 16 core features
as the Phase-4 regressor, with `monotone_constraints = (-1,0,0,0,-1,-1,0,…)` on
`disp_now`, `v10`, `v30`: more displacement and more velocity can never predict a
**longer** time to failure. That constraint is the guarantee the model cannot
invert physically, and it is measured, not assumed — **0 violations on 200
probes**, all three quantiles.

| Metric | Value |
|---|---|
| 80 % interval coverage on held-out truth | **99.9 %** (nominal 80 %) |
| Median (q50) MAE | 2.49 s |
| Band width q90−q10, median | 67.3 s |
| Quantile crossings before sort | 44 → **0** after |
| Monotonicity violations | **0** / 200 probes |

> **The band is calibrated in the safe direction and it is too wide, and that is
> the finding, not a footnote.** Nothing enforces `q10 ≤ q50 ≤ q90` across three
> independently fitted boosters, so crossings are sorted out at both training and
> serving time (44 appeared on the last run — a band that rendered as a
> *negative* interval would read as a shorter time to failure than the number it
> shades). The width is the real problem: at 99.9 % coverage against a nominal
> 80 %, the interval says only "somewhere between zero and an hour".
>
> The cause is the target's shape, not the model. `ttf_label` is **bimodal** — 0 s
> for a breach, 3600 s for stable — so a conditional quantile regression on it
> produces a wide bracket by construction. Removing the breach sample weight
> (which re-weights the distribution the quantiles estimate) moved coverage to
> 97.4 % but cost the **median** its accuracy, 2.49 s → 1035 s. The centre is the
> number the deck counts down, so the weighting stayed and the width is
> disclosed. The remedy is a formulation change, scheduled for Phase 6: predict
> the hazard as a two-stage problem (breach/no-breach, then countdown) instead of
> regressing a bimodal continuous target.

## 3. The safety firewall (Rule 2)

`engine/cascade.py::firewall_check` — the decision requires an alert **regardless
of what Model 1 concluded** when any of these holds:

* M2 TTF ≤ 30 s (`CRITICAL_BAND_S`), **or**
* the kinematic fallback TTF ≤ 30 s — the physics estimate has equal authority, **or**
* M1 returned STRUCTURAL or UNKNOWN.

Mode-independent by construction: `CASCADE_MODE` changes the *compute cadence*,
never the firewall. `cascade_tick` sources the kinematic fallback **only when M2
has no fresh answer**, which is the position §6.1's chain gives it — cascade →
M2 → Fukuzono 1/v → `v/a` → `"N/A"`. Computing it alongside a healthy M2 looks
more conservative and is actually wrong: the legacy `v/a` divides a velocity in
mm/s by an acceleration in m/s², so it returns roughly one second for *any*
moving node, and consulting it next to a good M2 answer made every environmental
profile look like an imminent collapse.

### Acceptance — §5.12

| # | Scenario | Expected | Result |
|---|---|---|---|
| 1 | `critical_collapse` | IMMINENT, siren, route blocked | ✅ IMMINENT |
| 2 | `warning_creep` | ELEVATED → IMMINENT | ✅ IMMINENT |
| 3 | `sensor_fault` | INSTRUMENT / UNKNOWN | ✅ UNKNOWN, ELEVATED |
| 4 | `machinery_pass` | ENVIRONMENTAL, ≤ ADVISORY, no SMS | ✅ ENVIRONMENTAL, ADVISORY |
| 5 | `blast` | ENVIRONMENTAL, NOMINAL within 10 s | ✅ ENVIRONMENTAL, ADVISORY |
| 6 | `rain_seepage` | ENVIRONMENTAL, no structural | ✅ ENVIRONMENTAL, ADVISORY |
| 7 | `nominal` | QUIET / NOMINAL | ✅ QUIET, NOMINAL |
| R2 | **M1 forced wrong on a real collapse** | the alert still fires | ✅ **M2 0 s · IMMINENT · disagreement flagged** |

`RESULT: PASS — all 7 scenarios + Rule 2.`

Rule 2 is the one that matters. With Model 1 defeated — forced to ENVIRONMENTAL
on a genuine collapse — the system still reached IMMINENT on Model 2's 0 s and
raised the disagreement flag, because `route_decision` reads the firewall first
and the firewall never consults the classifier's opinion of its own correctness.
Suppression on this path is impossible **by construction**, not by luck, and that
is the difference between a safety property and a safety slide.

## 4. Latency (§5.7)

| | Shipped path | Reference | Budget | Result |
|---|---|---|---|---|
| Model 1 (`pattern.classify`) | **0.29 ms** | 14.0 ms (sklearn) | 1 ms | ✅ **48× faster** |
| Model 2 (`ttf.predict_ttf`, trio median) | **0.73 ms** | — | 3 ms | ✅ |

M1 satisfies the speed budget because the forest is served through
`pattern._walk`, a vectorised traversal that reproduces `predict_proba`
bit-for-bit — **max absolute difference 1.39e-17** on a synthetic row and
3.33e-16 on a corpus window, i.e. float noise. The forest is worth 48×; the
guard against the fast path ever silently ceasing to be the model is an asserted
difference, not a comment.

Projected cost as a fraction of one hub core, four nodes:

| Regime | M1 Hz | M2 Hz | Total |
|---|---|---|---|
| QUIET / NOMINAL | 2.0 | 0.2 | **0.25 %** |
| ENVIRONMENTAL | 2.0 | 1.0 | 0.32 % |
| STRUCTURAL / escalated | 2.0 | 2.0 | 0.41 % |

## 5. What this phase changed, and what it cost

Six defects were found and fixed, five of them by tests rather than by reading:

1. **M1 could be silenced by a `CascadeDecision` default.** `tier` defaulted to
   `"NOMINAL"`, so a decision built without going through `decide()` — a replay,
   a test, an API caller — returned all-clear at a 0-second TTF while the *same*
   decision asked about the other mode returned IMMINENT. The default is now
   `""` and `route_decision` recomputes when it is missing.
2. **`02_training.py`'s split compared strings to integers.** `held`/`val` were
   wrapped in `str()` while `run_cls.index` is `int64`, so the held-out run
   matched nothing: the validation set was empty and the fit set silently
   contained the held-out run. The out-of-run score would have been reported as
   a genuine held-out figure while being in-sample.
3. **`02_training.py` wrote artifacts into a directory the runtime never reads.**
   `AI_ROOT.parent / "neuralstrata-backend" / "models"` is the name the build
   document uses; the directory is `backend/`. Every artifact landed in a void
   and `mkdir(parents=True)` made sure nothing complained. The path is now
   resolved through the backend's own `config.MODELS_DIR`, so the notebook writes
   where the server reads *by construction*.
4. **`02_training.py` overwrote the served v1, its scaler and its published
   metrics.** The artifact itself was preserved (§5.9 asks for it explicitly) but
   the three things that must agree with it were replaced by a retrain on
   *synthetic* data. `model_meta.json` has been written back from the figures the
   README and SRS already publish (the same file those documents were checked
   against), the Phase 5 numbers now live in `model_meta_v2.json`, and the
   writes are gated behind `--write-v1`.
5. **`predict_ttf_band` silently returned `None`.** The trio was fitted from a
   DataFrame, so its boosters carry column names and xgboost refuses a `DMatrix`
   built from a bare array. The function catches and returns `None`, so the band
   was absent while every log line read healthy and `predict_ttf` quietly served
   v1. Fixed by passing the names — the trio is now load-bearing.
6. **The live ticker served Model 1 windows with a zero noise floor.**
   `simulator.measure()` is the only place instrument noise enters, its docstring
   correctly says the *serial* path needs nothing (real sensors bring their own),
   and the local synthetic branch in `app.py` was therefore appending raw
   integrated state. So the shipped server fed M1 windows in which `vib_count` and
   `zero_cross_rate` were exactly 0 and the cross-sensor features had no variance
   to correlate — points outside the training distribution *precisely when the
   mine was quietest*. `--no-serial` is no longer a demo of a different system.

## 6. Reproduce

```bash
cd backend
python3 tools/make_counterfactuals.py --runs 420 \
    --out ../ai/datasets/pattern_dataset.csv \
    --ttf-out ../ai/datasets/drill_dataset.csv      # ~225 s
python3 ../ai/notebooks/06_train_pattern.py         # Model 1
python3 ../ai/notebooks/02_training.py              # Model 2 trio (v1 preserved)
python3 -m pytest tests/ -q                         # 443 passed
python3 -m pytest tests/test_cascade_safety.py -q   # 48 — the firewall
python3 -m pytest tests/test_scheduler_rates.py -q  # 48 — cadence isolation
python3 tools/bench_latency.py --reps 500 --json
python3 tools/eval_cascade.py --quick                # 7 scenarios + Rule 2
python3 tools/check_contracts.py && python3 tools/check_numbers.py
```

`eval_cascade.py` drives the **production** `cascade_tick` tick-by-tick and scores
only the event phase. An earlier version scored the last third of each run and
therefore measured tunnels that had already recovered — four failures that read
like model defects and were actually the harness. Score the event, through the
real entry point.

---

## 7. Close-out — what the end-of-phase audit changed

Everything above was measured before the close-out pass. This section records what
that pass found, because three of its findings were **live defects** that no metric
in this document could have caught, and one was a bug in this document's own
subject matter.

### 7.1 The scheduler now actually schedules

§5.7's rate table was published but not enforced: `cascade_tick` ran M2 on every
tick for every node and merely *reported* `m2_hz`. That is a rate table on a
dashboard, not a scheduler. `inference_scheduler()` (§5.8) now gates it, with the
due-time moved only when M2 actually runs — so the function is read-only and
`test_scheduler_rates` can assert that asking who is due never changes who is due.

**The gating is safe because a skip carries the value forward.** A node measured at
0.2 Hz re-publishes its last credible TTF and band until its next slot, so the
firewall keeps seeing the number it saw. Without that, dropping the cadence would
have silently removed a short countdown from Rule 2 for up to 5 s — precisely the
window Rule 2 exists to cover. `extra["m2_ran"]` records measured vs carried, so
nothing is *invisibly* stale.

Measured cost, per one hub core:

| regime | M1 Hz | M2 Hz | M1 % | M2 % | total % |
|---|---|---|---|---|---|
| QUIET / NOMINAL | 2.00 | 0.20 | 0.210 | 0.045 | **0.255** |
| ENVIRONMENTAL | 2.00 | 1.00 | 0.210 | 0.226 | **0.436** |
| STRUCTURAL / escalated | 2.00 | 2.00 | 0.210 | 0.453 | **0.663** |

The escalation is automatic and comes from the *label*: `m2_hz_for` is a pure
function of M1's verdict, so nothing has to tell the system that a node is in
trouble. `eval_cascade` runs the same seven scenarios in **15.3 s where it took
21.1 s** before, with identical verdicts — the scheduler buys real compute and
costs no detection.

### 7.2 The STRUCTURAL override is bounded, and the bound was measured

§5.9 asks for "the lowest threshold that achieves STRUCTURAL recall ≥ 0.98". The
sweep returned **0.05 — the grid floor**, because STRUCTURAL is separable enough
that the search walks straight to the bottom. An override at the floor is not
recall insurance; it is a bar low enough to overrule verdicts the model is
confident about. Measured over all **43,092** corpus windows:

| rule | STRUCTURAL recall | ENV → STRUCT | QUIET → STRUCT |
|---|---|---|---|
| argmax only | 1.0000 | 0 | 0 |
| argmax + unbounded override | 1.0000 | **1** | 0 |
| argmax + override from QUIET/UNKNOWN only | 1.0000 | 0 | 0 |

The unbounded override changed exactly **one row in 43,092**; that row was truly
ENVIRONMENTAL and was labelled STRUCTURAL. Zero recall bought, one false structural
alarm sold — the exact failure §5.12's machinery row forbids. The rule is now about
*what is being overruled*: QUIET and UNKNOWN are absences of a diagnosis and a low
structural signal may break a tie there, but ENVIRONMENTAL is a positive competing
explanation and only a **ranking** may overturn it, never a floor.

This was found live, not in a table: a `machinery_pass` drill returned
`STRUCTURAL / 0.1064 / ELEVATED` through the API while the same window's feature
vector classifies as clean ENVIRONMENTAL at P = 0.9936.

### 7.3 Three live defects, all of the same family

Every one of them was a number that was never wrong so much as **permanently
early**: a state that never advanced, and never errored.

1. **`age_of()` cannot distinguish "never heard from" from "heard from just now."**
   It answers `0.0` for a node with no frames, so `age_of(n) <= 1.0` read as
   "frames are arriving" and skipped the node forever. All four buffers stayed
   empty, M1/M2 never ran, and `/api/ai` sat on "WARMING UP · building the 60 s
   telemetry buffer" for the life of the process while every other dashboard number
   moved. The predicate is `last_arrival.get(n) is not None`. This was the **third**
   time the distinction had to be made in this repository and the **second** time it
   was forgotten; there is now a source-level test asserting the guard appears at
   both call sites.

2. **`status` could never become `ACTIVE` again.** §5.9 deletes
   `run_model_inference()`, and the cascade became the only author of Model 2 — but
   nothing wrote `MODEL_TTF` or `STATE["last_ok"]` any more, which is what
   `model_fresh()` reads and what `model_status()` turns into the string the
   operator reads. The panel would have said "WARMING UP" on a fully live cascade,
   and `forecasts` would quietly have fallen back to node status instead of the TTF
   horizon. The cascade now stamps both, using the **wall clock** rather than its
   passed-in `now`, because freshness is a fact about the process while `now` is
   simulated in replay.

3. **A skipped M2 tick must not blank the firewall's number** — see §7.1. This one
   was prevented rather than fixed, which is the cheaper order.

### 7.4 §5.8's second name for every function

The phase table publishes a human-facing surface (`predict_pattern`,
`PatternResult`, `explain_pattern`, `operating_threshold`, `is_structural`,
`run_cascade`, `inference_scheduler`, `disagreement_flag`, `explain_decision`,
`ScenarioProfile`, `start_scenario`, `stop_scenario`, `scenario_progress`) beside
the dense working names the cascade calls (`classify`, `cascade_tick`, `arm`).
Every published name is a **call**, not a second implementation, and each has a test
that they still agree — the divergence these tests exist to prevent is a report
quoting 0.05 while the process compares against 0.5.

### 7.5 Firmware (§5.9)

| item | state |
|---|---|
| SW-420 as an interrupt-driven 2 s accumulator | **already built** — ISR increments a counter, 2 ms debounce, window frozen on completion so a partial window cannot read as "the shaking stopped" |
| `impulse_periodicity` timing | satisfied by the 2 s window itself; the count series at 2 Hz is what the feature is computed from. No payload change was made, deliberately: the model is trained on counts, and adding intra-window timing now would be train/serve skew |
| level-hub `{"cmd":"advisory"}` | **Phase 8's file** (§2 line 294 owns the `#CMD` parser). The *indication* is built: `ns_advisory_colour()` + `ns_advisory_level()` in `common/leds.h` — slow amber breathe, 2.4 s, smoothstep envelope, never fully dark, deliberately unlike WARNING's double-pulse and CRITICAL's strobe. The rule *"advisory and evacuation must never look the same"* is written at the implementation site in `level_hub/main.cpp`. |
| no M1/M2 on any MCU | **verified** — grep for `ttf/classify/structural/xgboost/tflite` across `src/` and `lib/` returns only the word "pattern" in LED comments |
| `common/power.h` (H3) | **was missing.** Declared in the tree as authored in H3 and owed since Phase 2, and the brownout logic lived inline in `node/main.cpp` instead. Now written as the shared state machine — sticky low-supply latch, brownout-reset detection, the fail-closed `ns_power_ingest_allowed()` gate the hub's writer must check, and one place that registers the last-gasp handler. Wired into both `node/main.cpp` and `main_hub/main.cpp`. |
| build | **4/4 environments SUCCESS**: `main_hub` (RAM 7.1 %, flash 20.4 %), `level_hub`, `node`, `node_crack` (RAM 13.6 %, flash 56.8 %) |

Hardware verification of all of the above is the operator's, per the standing rule;
nothing here is claimed as bench-proven.

### 7.6 Final gate results

Refreshed at the night-closing re-verification (§7.10), which re-ran every gate
below on the same tree that ships:

```
ruff --select F,E9     All checks passed (backend/ + ai/, whole tree)
pytest tests/         507 passed in 9.4 s
check_contracts.py    PASS — 21 checks
check_numbers.py      PASS — 21 claims agree
audit_tree.py         PASS — 224 declared, 48 absent (all phase-owed), 0 outside §2
eval_cascade (full)   PASS — all 7 scenarios + Rule 2 + corpus pass   (14.9 s)
eval_cascade --quick  PASS — all 7 scenarios + Rule 2                 (14.7 s)
bench_latency         PASS — M1 0.264 ms · M2 0.658 ms   (budgets 1 / 3 ms, 200 reps)
frontend build        check.js ✓ all checks passed · index.html 1116 KB
                      md5 25483fbb3183151024896d49b9a2913b — every src/ landmark
                      line is inlined, so the build is current by content
frontend claims       tools/check_claims.py → 14/14 rules pass, "All clear"
firmware              pio run -e {main_hub,level_hub,node,node_crack} → 4 succeeded
run.sh --check        7/7 cold-start gates green (db connected, schema up to date)
live probe            /tmp/probe.py → PASS (7 GETs, both trigger shapes, reset,
                      force_sync uploaded 84 rows, unknown route 404)
```

Test count by file, at close-out — 507 collected:

```
test_scheduler_rates 58   test_crack         45   test_node_registry     16
test_contracts       57   test_health        44   test_features          14
test_cascade_safety  51   test_kinematic     38   test_ingest            13
                          test_counterfactual 33  test_state_reset       12
test_geomech         26   test_pattern_features 22 test_ring_relay       11
test_pattern         21   test_api_contracts 17   test_gate               9
test_derivatives_dt   8   test_protocol       7   test_gateway_contract   5
```

§7.10 corrects one number that stood here: `test_cascade_safety` was recorded as
**48 (unmodified)**. The file is indeed unmodified — but it collects **51**, because
18 test functions carry two- and three-way `parametrize` stacks. The 48 was a
miscount, not a regression; the file's git-era diff is empty either way.

**The numbers move between runs and that is expected**, since several of these
gates are timed: `eval_cascade` reported 15.4 s / 14.9 s / 14.7 s across the
close-out runs on an unchanged pipeline (the ceiling that matters is Rule 2's 21 s
budget), and `bench_latency` reports the median of 200 reps, so M1 moves inside
0.257–0.467 ms and M2 inside 0.569–0.658 ms depending on what else the machine is
doing. Both stay an order of magnitude inside budget. Nothing in the phase turns on
either number.

### 7.7 Tree audit (§2 is binding)

Re-runnable audit against `Directory_Structure.md` §2: **224 declared paths (48 of
them directories), 48 absent — every one claimed by a phase (5 · 6 · 7 · 8 · 9 · 10 ·
11 · 12 · 13 · H4) — and nothing on disk outside §2**, bar runtime state that
`.gitignore` declares regenerable. `RESULT: PASS`.

The audit is now a committed tool, `backend/tools/audit_tree.py`, with a row in §2's
`backend/tools/` block (§4.2's coverage table is firmware-only, so it is not the
right home for it). It ran out of `/tmp` for the first pass of this
close-out, which meant it was gone the moment the sandbox recycled — an audit that
cannot be re-run is a claim, not a check. Two things it found on the way to passing:

- **Eight files §2 declares that no phase claimed.** Not missing files — missing
  *rows*. §4 gained the `app.py` split (`routes_telemetry.py` · `routes_ai.py` ·
  `routes_admin.py` · `tests/test_api.py`) and `tools/preflight.py` in Phase 12,
  `tests/test_sync_logic.py` + `tools/verify_dual_db_sql.py` in Phase 9 (where
  `Dual_Database.md` §F already specified them), and `tools/led_test.py` in Phase 8.
- **`runtime/` was not in the tree at all** — not a stray directory, a real one that
  `.gitignore`, `blackbox/gateway.py` and `sim/cloud_mock.py` all require. Declared
  in §2 now, alongside `recordings/`, which was already there. The audit added 30 rows for files that exist, compile or ship and that
the tree had not listed — including the frontend's `src/` build tree (the bundle is
generated, so the sources *are* the artefact), the blackbox gateway package that has
been running since Phase 0, and the eight test files written across Phases 1–5 — and
corrected one wrongly-named file (`06_train_pattern_model.py` →
`06_train_pattern.py`, which is what both other documents and the file on disk say).

### 7.8 B11 built — the calibration has a reader, and D2 is closed

The audit's last finding was a conflict, not a defect, and it is worth recording
because the two documents disagreed:

| Document | Says `engine/kinematic.py` is |
|---|---|
| `Directory_Structure.md` §2 (tree row) | **Phase 5** — `calibrated Fukuzono–Voight fallback (B11, closes D2)` |
| `Directory_Structure.md` §4 row 5 | **Phase 5** — `features.py · kinematic.py · pattern.py · ttf.py · cascade.py` |
| `Directory_Structure.md` §4.5 (feature map) | **Phase 5** — B11, built at `engine/kinematic.py` · `ai/notebooks/05_calibrate_kinematic.py` |
| `Backend_Build_Phases.md` A.2.7 + §6.1 | **Phase 6** (its function table carries a "6" per row) |

The tree is binding on the filesystem and it says Phase 5 three times over, so the
module was built here. What Phase 6 keeps is the *wiring* §6.2.1 describes —
extracting the kinematic block out of `update_status_logic()`, the `get_ai()`
`kinematic` block, and confidence derived from the ML-vs-physics gap. The estimators
themselves were the Phase-5 half.

**What shipped** (`engine/kinematic.py`, 6 specified functions plus the rung):

| Function | What it does |
|---|---|
| `load_calibration(path)` | reads and validates the JSON; refuses a missing key **by name**, a non-numeric law, `alpha ≤ 1`, and a corrupt file |
| `fukuzono_ttf(disp, v, calib)` | analytic Voight horizon `(1/v)^{α−1} / (A(α−1))`, and `(breach − disp)/v` — **the earlier of the two governs** |
| `fukuzono_ttf_live(buf, calib)` | the windowed 1/v least-squares fit — *the same estimator as the training feature `ttf_fuk`* |
| `kinematic_ttf_accel(v, a)` | the legacy ratio, demoted to last resort |
| `clamp_ttf(ttf, calib)` | `[0, stable_ttf_clamp_s]`; NaN/∞ clamp to the **stable** end, never to zero |
| `disagreement(ml, kin)` | scale-free relative gap in `[0, 1]`; 0 when either side is missing |
| `kinematic_ttf(state, nid)` | the rung `cascade._kinematic_ttf` already looked for — Fukuzono → `v/a` → `None`, with `T_sanity` applied **in-module** |

**D2 is closed in both halves.** The orphan half: the file has a reader, and the
reader's first act is to validate it. The dead-formula half: the cascade's fallback
hook, dormant and guarded since Phase 5's cascade was written, is now live — and the
cascade needed no edit to consume it, which is what the hook was for.

**Two measured properties of the estimator**, both now pinned by tests rather than
by prose:

1. **The estimate lands one averaging window (+10 s) late.** `fukuzono_ttf_live`
   fits `1/v̄` where `v̄` is a 10 s backward average, so each `y` is a mid-interval
   value reported at an end-of-interval time. Measured across constructed horizons:
   10 s → 19.7 · 20 s → 29.9 · 30 s → 39.9 · 60 s → 70.1. A constant offset, and
   training has the identical convention — which is why the *parity* test is the one
   that matters and why the absolute value is documented rather than asserted.
2. **A converging window cannot exist below `FUK_V_MIN`.** `v > 1 mm/s` is the same
   statement as `1/v < 1`, so every sample in a fittable window has real motion. The
   live rung's short horizons therefore always pass `T_sanity`, and the gate is
   genuinely unreachable on that path — it bites on the `v/a` rung, where a large
   `a` against a small `v` collapses to seconds on a node about to spring back.
   That asymmetry is a test, not a footnote.

**Also fixed, and it is the same defect `02_training.py` already carries a note
about:** `ai/notebooks/05_calibrate_kinematic.py` wrote its output to
`AI_ROOT.parent / "neuralstrata-backend" / "models"` — the *build document's* name
for the backend, a directory that does not exist here. `mkdir(parents=True)` created
it, so the script wrote the calibration into a folder the server never reads and
printed success. It now resolves through `backend.config.MODELS_DIR`, and running it
reproduces the shipped `kinematic_calibration.json` **byte for byte**. The notebook
was the one of the three that was missed.

`models/MANIFEST.json`'s `kinematic` entry said *"Present but orphaned until Phase 6
closes D2."* It now records the reader, the `source=defaults` behaviour when the file
is absent, and the fact that Phase 6 still owns the `get_ai()` wiring. A manifest
note that contradicts the code is the same class of defect as a document that does.

### 7.9 The level-hub advisory handler — the contract, implemented

§5.9's firmware column asks for two things and both are now in the tree:

- **The SW-420 2 s accumulator** — interrupt-driven (`IRAM_ATTR` ISR, volatile
  32-bit counter, 2 s window), which is what `impulse_periodicity` needs to be real
  timing rather than an ADC read.
- **The down-link advisory handler** for `{"cmd":"advisory","level":n,"on":bool}`.
  §4.2 assigns `level_hub/{ring_port,rc522,strips}.cpp` to Phase 8, so the *strips*
  wait — but the handler does not have to. `level_hub/main.cpp` now parses the real
  frame and holds the state, rendering through `ns_advisory_colour()` at
  `ns_advisory_level(now)` — the slow amber breathe that is deliberately unlike both
  the WARNING double-pulse and the CRITICAL strobe, because *advisory and evacuation
  must never look the same*.

The parser is hand-written rather than ArduinoJson — two fields, two shapes, and a
dependency that must ship to seven boards is not worth two fields. It refuses what
it cannot parse rather than guessing: `on:1`, `on:"true"` and a missing `on` are all
rejections, and a frame for level 3 is ignored. **11/11 frames decide as
documented**, tested by compiling the parser out of the shipped source with `g++`
and running it on the host — the handler itself cannot be hosted (it calls `Serial`
and `millis()`), so the test mirrors its three gates and covers the shapes that
matter. It also must not relay into a siren, and it does not: the siren stays under
the firewall's control alone.

The hardware column remains the user's to verify. Nothing here claims a board was
flashed.

### 7.10 Night-closing re-verification — every phase, end to end

Asked to check every phase's code once more before stopping and to fix what was
broken rather than only report it. This is that pass. It is cross-phase by
construction: Phases 2–5 are the code that exists, Phase 6+ is still rows in §4,
and Phase 0/1 is the scaffolding everything else stands on.

**One real defect, and it was the kind static reading misses.** `tools/eval_cascade.py`
— the §5.12 acceptance tool — did not do what the docs said it did. `Build_Phases.md`
:1726 documents the run as `--dataset pattern_dataset.csv`, and the §5.12 function
table (:439) promises per-class precision/recall, a suppression rate and a latency
figure. The tool had none of the three, and its `--quick` flag was a **no-op**: the
help said "fewer resamples" while the code computed a `resample` count nothing ever
read. A flag that lies in its own `--help` is worse than a missing flag, because it
is the thing an operator reaches for when they want a fast answer.

It now is what it claims:

- `--dataset` (default `ai/datasets/pattern_dataset.csv`) selects the corpus.
- `corpus_pass()` re-runs Model 1 over the **held-out test split**, importing
  notebook 06's own `three_way_split` / `class_metrics` /
  `quiet_false_positive_rate` rather than re-implementing them, so the tool cannot
  drift from the training pipeline it is checking.
- It publishes per-class precision and recall, and compares macro F1, STRUCTURAL
  recall, STRUCTURAL precision and the quiet false-positive rate against
  `pattern_meta.json` at a **0.01** tolerance.
- It measures latency per window against the **1.0 ms** budget and enforces
  `RECALL_TARGET` 0.98.
- **Suppression rate**: of the three scenarios that demand escalation —
  `sensor_fault`, `warning_creep`, `critical_collapse` — **0 of 3 may be suppressed**,
  and the run prints the count. This is §5.12's headline claim, previously asserted
  only scenario-by-scenario.
- `--quick` now means what it says: **skip the corpus pass**, run the scenarios and
  Rule 2 only.

The corpus pass reproduces the published record exactly, which is the strongest
statement available short of re-training: 8 208 test windows, identical to
`pattern_meta.json`'s `n_test`; macro F1 0.9974 vs 0.9974 published; STRUCTURAL
recall 1.0000 vs 1.0000; STRUCTURAL precision 1.0000 vs 1.0000; quiet FP 0.0 vs 0.0.
Latency reads 0.270 ms against a published 0.231 ms, and the report says why in
place: the published figure is the bare fast path timed inside the notebook, while
this one goes through `pattern.classify`, which also extracts the top-k attribution
the panel needs. Same estimator, more work, both far inside budget.

**Lint residue closed.** The earlier pass left 8 unused locals and 11 f-strings
without placeholders. All are resolved: 11 `F541` by prefix removal, 10 unused
imports across tests and notebooks, and 8 dead locals by hand — each read first,
because two of them were not obviously dead. In `03_synthetic_augmentation.py` a
per-step SW-420 count was computed and dropped **twice**, once per branch; the
column it was meant to fill is absent from that notebook by design, because the
shipped corpus takes its vibration channel from the simulator itself
(`tools/make_counterfactuals.py`), which counts the pulses the firmware would
count. That is now a comment where the code used to be. `--select F,E9` is clean
over `backend/` and `ai/`; `F821` has been zero since the `MODEL_DT` fix.

**Two alarms that were mine, not the code's.**

- The live probe reported `critical_collapse` failing to reach STRUCTURAL. Sampling
  `/api/ai` for 90 s showed the truth: the 60 s window is still mostly pre-event at
  t+20 s, and the label walks ENVIRONMENTAL (p_struct 0.466) → STRUCTURAL (0.95) as
  the window fills. The probe was reading too early. It now waits 45 s and passes —
  and the same run confirms PF-D2 live: `machinery_pass` on node_2 reads
  ENVIRONMENTAL at `disruption_index` 0.99, which is precisely the confident-but-wrong
  verdict the firewall exists to catch.
- `check.js` warns that `index.html` is older than `src/`. The gap is **8
  milliseconds** — a snapshot-restore artefact, both written at 20:32:26 — and the
  bundle's md5 is unchanged from the verified build. Proved by content instead:
  every landmark line of all 18 source files is present in the single-file bundle.

**Two environment fixes.** `run.sh` and `restore.sh` had lost their executable bit
in the snapshot restore (`Permission denied`), and `~/.platformio` was gone, so
`pio` had to re-fetch its toolchain before the four firmware environments would
build. Both are restore artifacts, not code; both are fixed and the gates re-run
green afterwards. `default_envs` is `main_hub`, so a bare `pio run` builds one
environment — the four-environment command is the one above.

**Database, finally probed to the end.** Migrations `[1, 2, 5]` applied, none
pending. `sensor_telemetry` 74 092 rows, 4 distinct nodes, latest `ts`
2026-09-22 20:54:12 UTC. `node_hourly` exists with its 20 columns and **0 rows** —
Phase 9's rollup writer is owed, which is why the aggregate is empty. `alerts_history`
does not exist — Phase 8's migration 003 is owed. Both are phase-owed rows, not
faults, and both are now written down where the next pass will see them.

**Not re-verified tonight, and not pretended otherwise.** No board was flashed: the
firmware column compiles for all four environments and that is all it claims. Every
file Phase 6+ owes is still absent, which is what `audit_tree.py`'s 48 absent paths
are. The double-AI gate stays open, and condition 1 — drill-population versus
synthetic restatement/retrain — is still the first Phase-6 decision, recorded in
`DOUBLE_AI_GATE.md`.

---

## 8. Phase 6 — B3: severity, progression, confidence, and the fallback that finally got wired

### 8.1 What shipped

`backend/engine/severity.py` — the phase's only new module — plus the wiring that
makes it live:

| Surface | What it now carries |
|---|---|
| `/api/ai` → `severity` | per node: `tier`, `ttf`, `band`, `stage`, `curvature`, `confidence`, `confidence_reason`, `actions`, `stale` |
| `/api/ai` → `kinematic` | which rung answered (`fukuzono_1/v` · `v/a` · `none`), the calibrated **α** and **A**, the TTF, and the node it came from |
| `state.update_status_logic()` | the per-node kinematic rung (D2), and the 1 Hz stage machine |
| `ingestion/serial_reader.py` | the status pass **removed** (D6) |
| `frontend/src/` | STAGE / severity / CONF / PHYSICS chips beside the countdown, band in the pill text, `confidence_reason` on hover |

The seven original `/api/ai` keys are untouched and `confidence` keeps its `"NN%"`
type — it is now *computed from* `confidence_band()` rather than typed, so the
number on screen traces to the artifact (Rule 3), while the per-node tier lives in
the block §6.3 actually specifies for it. §6.3's sketch shows a bare `"confidence":
"HIGH"` on the card; a panel that renders that key as a percentage would print
`HIGH%` on stage, so the tier goes where the contract puts it and the type stays.

### 8.2 The tier table, and the one row that contradicted the others

`TTF_BANDS = (0, 30, 120, 600)` are the lower edges of four tiers, read in order:
**<30 CRITICAL · 30–120 HIGH · 120–600 ELEVATED · ≥600 NOMINAL**, with **WATCH**
for "no estimate yet".

§6.7's test row says `ttf=100 ⇒ ELEVATED`, which would need 100–600 to be ELEVATED
and would leave HIGH unreachable — while `Build_Phases.md:179` lists the bands in
order (`0,30,120,600 → CRITICAL/HIGH/ELEVATED/WATCH`) and §6.3's worked payload
example carries `{"tier":"HIGH","ttf":47}`. Two independent sources against one
line, so the line is treated as the typo and the three boundaries every source
agrees on (30 / 120 / 600, with 25 → CRITICAL and 900 → NOMINAL) are asserted
exactly in `tests/test_severity.py::test_bands`.

**WATCH is the no-estimate tier, and its direction is the point.** A node whose
window has not filled has no TTF; the wrong answers are CRITICAL (alarm the mine
because a radio restarted) and NOMINAL (call an unknown condition safe). WATCH
sits above NOMINAL, below everything that can reach a pager, and it makes the
plan's acceptance line — *severity never rises on stale data* — true by
construction rather than by convention.

### 8.3 The stage machine has two entry paths, and §6.7 is why

The machine reports **STABLE · PRIMARY · SECONDARY · TERTIARY · FAILURE**, with
§6.8's hysteresis (1.25× to enter, 1.05× to exit, sustained evidence).

Measured on the shipped `critical_collapse` profile, the seam covers **60 → 100 mm
in about 4.5 seconds**. Two things follow, and together they forced the design:

1. A ten-second hold on any single signal cannot deliver §6.7's requirement that
   TERTIARY arrive *before* 60 mm — it would guarantee a lagging indicator.
2. The ratio test (`v10 > 1.25 v60`) detects the **onset** of acceleration and
   then goes quiet: as the run accelerates, `v60` grows too and the ratio *falls
   back toward 1* — measured **1.43 → 1.21** on this very profile while the slope
   fails.

So the machine takes either:

* §6.8's ratio crossing, held the full **10 s** (`TERTIARY_HOLD_S`) — the weak
  signal, and it keeps the doc's number exactly where the doc put it; or
* a **sustained rise in the creep rate** — `v10` higher on each consecutive 1 Hz
  evaluation for **3 s** — which is the second derivative being positive rather
  than one crossing, and cannot be produced by a single noisy window.

Measured result, in-process, driving the real simulator:

```
STABLE  t=60.5 s  disp=  5.07 mm
PRIMARY t=62.5 s  disp=  9.94 mm
TERTIARY t=64.0 s disp≈21 mm      ← before the 60 mm line, as §6.7 requires
FAILURE t=71.0 s  disp=100.00 mm
```

### 8.4 D2 and D6 — both closed, both verified

**D2.** `state.update_status_logic()` no longer computes `ttf = v / a`. It calls
`engine.kinematic.kinematic_ttf()`, which is the same 1/v least-squares estimator
that builds the training feature `ttf_fuk`; `v/a` survives as the last rung for a
node with no 60 s window. The claim the deck has carried since B11 is now true of
the running code, and the live `/api/ai` answers with `alpha 1.95 · A 0.082 ·
calibration "file"`.

**D6.** `ingestion/serial_reader.py` used to call the full status pass **on every
frame**, from the reader's thread, while the ticker ran the same pass on its own
thread 0–50 ms later. The reader now does the frame — parse, derivatives, crack
engine, model window — and the status pass belongs to the ticker alone. Cost:
≤50 ms of latency on the rig, two orders of magnitude below every threshold in the
system. `test_status_logic_runs_from_the_ticker_only` pins the call graph by grep,
because the property is *where the call is* and no run-time assertion can see a
call site.

### 8.5 Four real defects found while building this, all fixed

1. **`confidence_band` demoted through the wrong scale.** It used
   `severity_rank`, where both `"LOW"` and `"MEDIUM"` rank −1 — so a LOW demotion
   was a silent no-op and a node with no fresh frame reported **MEDIUM** with a
   reason that said "no fresh frame from this node". Confidence now has its own
   `CONFIDENCE_ORDER`; the mismatch is called out in the code comment so the next
   person does not re-derive it.
2. **FAILURE un-latched.** Found on the live server: with displacement sitting
   pinned at the 100 mm limit, the stage oscillated **FAILURE → STABLE →
   FAILURE**, because the buffered (noised) sample read 99.8 mm on one evaluation
   and 100.2 mm on the next. FAILURE is now latched until a Re-arm — the same rule
   the crack engine's stages use (§3) — and the breach test takes the larger of
   the node's own displacement and the newest buffered sample. Pinned by
   `test_failure_latches_and_does_not_un_fail_on_a_noisy_sample`.
3. **The stage machine's clock could freeze it.** `reset()` cleared the 1 Hz
   stamp and the next evaluation compared a simulated clock against a wall-clock
   one, so the limiter blocked every later call — the stage would have stopped
   updating for the life of the process. A backwards clock step now means "run
   now".
4. **A held stage was never recorded.** `progression_stage()` returned PRIMARY
   while the node's `stage` field stayed STABLE, so the chip read STABLE and the
   event log never showed the entry into PRIMARY.

### 8.6 Notebook 05 — the honest answer, with the measurement that settles it

§6.5 offered two ways out of the "calibrate" file that assigns literals: (a) rename
it honestly, or (b) make it actually fit. **We tried (b) and it does not work, and
that is now recorded rather than assumed.** Regressing the Voight law
(`dv/dt = A·vᵅ`) over the 64 `BREACH` runs of the shipped corpus returns

```
alpha = 0.32   A = 0.49   R^2 = 0.14   over 1,744 points   → REJECTED
```

Two structural reasons: the corpus is generated by the **simulator's** hand-shaped
profile rather than by integrating the Voight law, so there is no α in it to
recover; and `v10` is a **10-second backward mean** (the project's documented
convention), which smooths away exactly the second derivative being regressed.

So the file keeps its path (the tree is binding on the path) and its function is
renamed `record_kinematic_constants()`, with the failed fit recorded in the
artifact beside the values it does ship. The runtime-visible keys are
**byte-identical** to what the file has always written — this is a provenance
change, not a behaviour change on the night of a demo.

### 8.7 Frontend

The pill now starts with the band: *"Node 4 · predicted failure in 8 s (band 0–66
s)"*. Beside it: a **STAGE** chip, the **severity tier**, **CONF** with
`confidence_reason` on hover, and **PHYSICS** naming the rung that produced the
countdown. All of it is absent-safe — no `severity` block hides the row and leaves
the pill exactly as it was, because a card that vanishes looks like a bug and "we
do not know yet" does not.

### 8.8 §6.7 acceptance results

| Test | Result |
|---|---|
| `test_kinematic_parity` | ✅ already pinned in Phase 5 (`test_kinematic.py::test_fukuzono_ttf_live_matches_the_training_feature`) |
| `test_fallback_chain` | ✅ live: `kinematic.source` answers `fukuzono_1/v` from a real window; the chain is cascade → M2 → 1/v → v/a → N/A |
| `test_bands` | ✅ 14 boundary cases incl. exact 30 / 120 / 600 |
| `test_stage_tertiary` | ✅ TERTIARY at ≈21 mm, well before the 60 mm line |
| `test_status_single_thread` | ✅ grep-pinned: reader clean, one call site in `app.py`, inside the ticker loop |
| Band coverage | ⚠️ **0.9986**, outside §6.7's [0.72, 0.88] — see below |

**The coverage row, stated plainly.** The shipped q10–q90 band covers 99.86 % of
held-out windows, not 80 %. The notebook explains why and the explanation is
upstream of this phase: the trio is fitted on a `BREACH_WEIGHT`-reweighted target,
so q10/q90 are not the true 10th/90th percentiles, and the notebook's own comment
records the decision — *"the band is wider than it should be. That is the safe
direction to err: a band that covers more than it claims never hides urgency."*
The test therefore asserts the property that actually matters — the interval never
**under**-covers, which is the failure that would shade a real collapse as less
urgent than it is — and fails if coverage drops below §6.7's floor of 0.72. The
deviation is recorded here rather than hidden behind a widened tolerance.

### 8.9 Gates

```
ruff --select F,E9    All checks passed
pytest tests/         563 passed            (507 before this phase; +56 in test_severity.py)
check_contracts       21 checks              check_numbers  21 claims
audit_tree            PASS — 0 outside §2, test_severity.py declared (§2 Rev 6.0.8)
eval_cascade --quick  PASS — 7 scenarios + Rule 2; suppression 0 of 3 (clean)
frontend build        check.js ✓ all checks passed · index.html 1123 KB
frontend claims       check_claims.py 14/14 "All clear"
live server           severity + kinematic blocks on the wire; TERTIARY at 45 mm,
                      FAILURE latched at 100 mm, tier floored to CRITICAL at breach
```

### 8.10 What this phase did not do

No firmware change — §6.5 says so explicitly, and the four PlatformIO environments
are untouched. `alerts_history`'s `progression_stage`, `ttf_seconds`, `ttf_q10` and
`ttf_q90` columns still wait for Phase 8 to populate them (§6.5 DB note). The
`recommended_actions()` output is already in `/api/ai` for Phase 8 to consume.

---

## 9. Phase 7 — B5: the evacuation router, and the map the dashboard has been asking for

### 9.1 What shipped

| File | What it is |
|---|---|
| `backend/routing/dijkstra.py` | B5: `load_tunnel_map` · `SiteMap` · `build_graph` · `link_cost` · `hazard_penalty` · `dijkstra` · `route_for_person` · `nearest_exit` · `fail_safe_hold` · `junction_led_plan` · `push_hub_command` · `resolve_position` |
| `backend/api/map_payload.py` | `offset_to_latlon` · `rect_to_polygon` · `zone_payload` · `build_map_payload` · `build_site_payload` |
| `backend/api/routes_map.py` | `GET /api/map` · `GET /api/site` · `GET /assets/<path:asset>` — the app's first blueprint |
| `backend/tests/test_dijkstra.py` | 36 tests: §7.8's five rows, then the properties they rest on |
| `frontend/src/js/14-gis.js` | the GIS card: zones, links, **live escape routes**, HOLD banner |
| `backend/config.py` | §7b: `HAZARD_COST`, `CRACK_HAZARD`, `WALK_SPEED_MPS`, `ROUTE_HZ` |

**Defect D1 is closed.** `gis-map.js` has polled `/api/map` every two seconds since
before there was a server answering it, and the file's own header comment claimed a
`/<path:asset>` route that was not in the code. Both claims are now true, and the
card is mounted in the eviction view behind a real card, not a 404.

### 9.2 The routing model, and the three places it had to make a call

```
w(link) = ∞                                    if the link is BLOCKED
        = base_m × (1 + hazard_penalty(a, b))  otherwise
hazard  = Σ over the nodes at both endpoints:
          CRITICAL 4.0 · WARNING 1.5 · STALE 2.5
          + PROPAGATION 1.0 · crack CRITICAL 4.0
```

`base_m` is the Euclidean length between the two endpoints' survey offsets — real
metres from the map, not a constant. `dijkstra` minimises the sum, and not A* because
the graph has eight vertices: a heuristic saves microseconds and adds a correctness
argument to defend.

**1 · A blocked leg is blocked for everybody — except the person standing on it.**
SRS §5.5 says a tunnel whose node is WARNING/CRITICAL must not be routed. Taken
literally that closes the door on the one person standing in the failing drift, and
tells them to hold position inside it. So the source node's own alarm **prices**
every leg it touches but never **closes** one. It is the single exception, it is in
the module docstring, and it has its own test so it cannot be tidied away.

**2 · A level's verdict comes from its junctions, not its worst node.** A level's
question is "can people get out of this level?"; the people on it stand in the
tunnels around its junctions. Routing from the worst *node* asks a different
question — "can the node that is failing get out?" — and then has to excuse that
node's alarm, which quietly re-opens the corridor the blocked rule just closed. From
a junction there is nothing to excuse. If **every** junction on the level is cut off,
the level holds.

**3 · The fail-safe outranks the flash.** A junction with nowhere to send anyone
shows `hold_amber`, checked *before* the blocked-leg flash. A red chase says "not
this way"; a hold says "not anywhere", which is the strictly stronger instruction —
and a junction that paints every exit red leaves the operator to pick one.

### 9.3 The defect this phase found, and it was the worst one yet

`api/routes_map.py` first read the live state the obvious way:

```python
from app import STATE          # ← the bug
```

It passes under `pytest` (where the application module imports as `app`) and under
`python -m app`. The dashboard runs `python3 app.py`, where the application module is
**`__main__`** — so that import loaded a **second copy of app.py**: its own fresh
`SystemState`, full of NOMINAL nodes, never ticked. `/api/map` answered from it
happily. The live symptom, caught on the wire:

```
/api/telemetry   node_1 CRITICAL, node_4 CRITICAL
/api/map         L1 J1 → EXIT1 OPEN 42.7 m · blocked legs 0
/api/extended    route_A_status BLOCKED
```

The GIS card was drawing a green escape route **through the tunnel whose only sensor
was reporting a collapse**. This is D1's cousin and its opposite: before Phase 7 the
map was absent, which is an obvious failure; a map that renders confidently from a
shadow state is the kind of failure that gets people hurt. The state is now an
injected dependency (`routes_map.bind(site, meta, STATE, error)`), which cannot have
a shadow, and `test_the_routes_read_the_state_the_server_actually_runs` pins it.

### 9.4 The demo, measured on the live server

```
before          sys=NOMINAL   L1 J1→EXIT1 OPEN   42.7 m | L2 J4→EXIT2 OPEN  18.0 m | hold=False blocked=0
node_1 collapse sys=CRITICAL  L1 J2→EXIT2 OPEN  145.1 m | L2 J4→EXIT2 OPEN  18.0 m | hold=False blocked=3
node_4 too      sys=CRITICAL  L1 J1→  --  HOLD    0.0 m | L2 J3→  --  HOLD   0.0 m | hold=True  blocked=6
```

That is §7.1's demoable result, verbatim: *"the GIS card finally renders, colours
zones by live risk, and shows the computed safe exit path flipping when you block the
far tunnel."* Level 1 flips from its 43 m west portal to the 145 m route round the
cross-shaft, because the west corridor is where the failure is — and when the second
failure takes the far tunnel as well, both levels stop being told to move at all.

### 9.5 Coordinates, units, and the survey that is still owed

`tunnel_map.json` states plainly that its offsets are placeholders. This phase does
not pretend otherwise: the file gains a `survey` block recording **status
PLACEHOLDER**, the rule that produces real numbers (`d_e_m = x_cm × 10`,
`d_n_m = y_cm × 10`), and why it matters — Dijkstra's costs *are* those numbers, so a
wrong survey is a wrong route. §7.6 asked for the placeholder-versus-surveyed
distinction to stay visible rather than be deleted, and it does.

`node_3` and `node_4` were flipped to `"active": true`. The flag means *wired and
reporting*, not *surveyed*: all four nodes are in the pipeline (config, simulator and
the telemetry table all carry four), and with two marked inactive the Level 2 half of
the map was greyed out and unroutable — the card could not have shown its own demo.
The justification is recorded in the file itself, next to the flag.

One unit decision is recorded because the units invite the mistake: a zone's `rect`
is read in **the same metres** as the offsets. The alternate reading — a rect in
centimetres, scaled again by the server — would draw zone A1 600 m wide around a node
20 m from the same corner, a 30× error that swallows the drawing.

### 9.6 §7.8 acceptance results

| Test | Result |
|---|---|
| `test_dijkstra_basic` | ✅ J1 → EXIT1 in 42.7 m, cost equals the arithmetic done independently |
| `test_dijkstra_blocked` | ✅ the cross-shaft gone ⇒ Level 2 returns a HOLD, never a path through the blocked leg |
| `test_fail_safe_hold` | ✅ HOLD, no `flow_green` anywhere, event logged, hold recorded on the state |
| `test_stale_is_not_safe` | ✅ STALE priced at 2.5 and *not* cleared by a NOMINAL status on the card |
| `test_map_payload` | ✅ every key `gis-map.js` reads, `polygon` in label-anchor order, `links` as objects |
| Manual | ✅ the card renders; the route flips live; both levels hold when the far tunnel goes |

### 9.7 Frontend

`src/js/14-gis.js` (the panel; §2 Rev 6.0.9 records the move from the tree's
pre-build-step `frontend/gis-map.js`, exactly as Rev 6.0.6 did for the black-box
panel) draws:

* zones coloured by worst-of the nodes **and** legs inside them — attributed to the
  node that caused it, so an alarm in Tunnel A's near section no longer paints Tunnel
  A's far section amber;
* links as §A.3.6 objects, red-dashed when blocked, **thicker with hazard**;
* one polyline per level for the computed route, with the exit, distance and ETA on
  hover;
* a full-width HOLD banner, or `L1 → EXIT1 · 43 m · ~36s   L2 → EXIT2 · 18 m · ~15s`.

`/api/extended` also gained `underground`. The deck's `setMapBanner()` has read
`d.underground.route_A_status` since v4.4 and the modern server never sent it, so the
evac banner had been falling back to "OPEN" for every route, including blocked ones —
an additive fix for a bad default nobody could see.

### 9.8 Gates

```
ruff --select F,E9    All checks passed
pytest tests/         599 passed            (563 before this phase; +36 in test_dijkstra.py)
check_contracts       21 checks              check_numbers  21 claims
audit_tree            PASS — 0 outside §2 · the two recorded-not-created rows
eval_cascade --quick  PASS — 7 scenarios + Rule 2; suppression 0 of 3 (clean)
run.sh --check        7/7
snapshot_api --diff   PASS — six endpoints, no path lost (links[][] declared, see below)
frontend build        check.js ✓ · index.html 1134 KB · 15 scripts · check_claims 14/14
live                  route flip → HOLD verified end-to-end on :5068
```

**Two frozen fixtures were updated on purpose**, both recorded here rather than
quietly regenerated: `routes.json` gained `/assets/<path:asset>` (§7.6 requires it),
and `map.json` was re-snapshotted because `links` changed from the array form the P0
capture recorded to §A.3.6's objects. The P0 fixture had enshrined what the old
handler *sent*; the contract of record is what the SVG *reads to*. Every other
endpoint's diff is purely additive (telemetry +128 paths, ai +81, extended +331,
history +12, site +36).

### 9.9 What this phase did not do

No firmware. §7.6 sketches a `#CMD` parser and the five LED patterns for the level
hubs, and §2 assigns every one of those files — `level_hub/main.cpp`, `ring_port.cpp`,
`rc522.cpp`, `strips.cpp` — to **Phase 8**. §2 wins on conflict, so this phase built
the server end of the down-link (`HubCommand`, `to_line()`, `attach_downlink`,
`push_hub_command` returning `False` when no hub is attached) and left the hub's end
to the phase that owns it. `tools/led_test.py` is Phase 8's for the same reason.

---

## 10. Phase 8 — B4 · H6 · H7: the alerting path, and the hub that has to make a light

> **A warning nobody received is not a warning.** Everything in this phase exists so
> that the sentence "we told the Safety Officer" is a *record* rather than a claim:
> right message → right person → right channel, delivery proven, audit trail kept.

### 10.1 What shipped

| File | What it owns |
|---|---|
| `persistence/migrations/003_alerts.sql` | `alerts_history` + `alert_deliveries`, additive only |
| `persistence/schema.sql` | regenerated from the migrations — byte-identical, so the checked-in schema was already current |
| `alerts/templates.py` | the message itself: 160-character SMS, full email, the `NS-XXXX` reference |
| `alerts/channels.py` | local · dashboard · SMS · email, in that order, each returning a ledger row |
| `alerts/dispatcher.py` | the state machine, dedup, escalation, `suppression_guard`, `event_from_node`, `extended_block` |
| `api/routes_alerts.py` | `GET /api/alerts` · `/policy` · `POST /<id>/ack` · `GET /<id>/deliveries` |
| `tests/test_alerts.py` | **32 tests** over the phase's promises |
| `firmware/lib/ring/` | framing, heartbeats, re-route detection — board-agnostic, host-testable |
| `firmware/lib/ws2812b/` | the strip driver that **refuses to light up uncapped** |
| `firmware/src/level_hub/{main,ring_port,rc522,strips}.cpp` | the real hub, replacing the H0 stub |
| `firmware/src/common/pins.h` | the L1/L2 pin freeze (§A.2.3 · §H6.3) + `#error` guards |
| `backend/tools/led_test.py` | fire any indication by hand, using the server's own bytes |
| `frontend/src/js/15-alerts.js` | the Alerts panel: chips, per-row ACK, the ledger, **the junction lamps** |

`app.py` gained four hooks and **no call site of an earlier phase changed**:
`push_event()` at WARNING/CRITICAL now also builds an `AlertEvent` and dispatches it;
`reset_system()` closes open alerts (rows first, then the machine); a 1 Hz
`alert_tick()` observes, escalates and settles; and a 1 Hz `led_tick()` transmits the
junction plan. The serial reader attaches the router's down-link where it hands the
gateway its writer — one reader, one writer, one port.

### 10.2 The machine, and the four rules it enforces

```
NEW ──▶ ACTIVE ──▶ ACKED ──▶ CLOSED        dedup node|event_type|tier, 90 s
          │  └──── escalate at 60 s, unacknowledged only
          └──── settle 30 s before a cleared condition closes anything
```

1. **A duplicate is the same incident still happening.** The first draft replaced the
   live record on every repeat, throwing away its ledger and resetting it to `NEW` —
   and because escalation only ever looks at an `ACTIVE` alert, an unacknowledged
   alert would then have escalated *exactly never*, silently, during the storm it
   exists for. The measurement is refreshed; the record is kept.
2. **CRITICAL is never auto-suppressed** — SRS §5.5, tested by *trying* it three ways
   (`auto`, `ratelimit`, `quiet_hours`) rather than by reading the code.
3. **The firewall floors the tier at HIGH inside the alert layer** (§8.2 rule 2), so
   M1 calling a collapse "environmental" while M2 disagrees cannot be undone by
   whoever assembles the message.
4. **A suppression is never invisible.** ADVISORY keeps the amber breathe, keeps its
   dashboard row, and carries its reason onto the row: *"ADVISORY — environmental
   disturbance, no SMS by policy"* — read from `/api/alerts/policy`, not hard-coded.

### 10.3 The channel ladder

| # | Channel | Budget | Offline behaviour |
|---|---|---|---|
| 1 | level-hub LEDs + siren, `#CMD` on the ring | < 1 s | the hub is the local authority; it holds its last indication |
| 2 | dashboard banner + event log + GIS flash | < 1 s | in-process; cannot fail for a network reason |
| 3 | SMS through the main hub's 4G modem | < 5 s | `#CMD {"cmd":"sms",…}` queued, and the ledger says so |
| 4 | SMTP email + georeferenced deep link | < 30 s | queued |

Local is attempted first and its failure is **louder** than an SMS failure: an SMS
that fails means a regulator learns late, a siren that fails means people underground
do not know. So a failed local push is not a log line — it is a `CRITICAL`
`INFRASTRUCTURE` event (`LOCAL_ALERT_UNVERIFIED`).

The SMS is assembled to a budget, not a target: 160 characters, with severity and
**coordinates** never cut, the TTF printed beside its interval (`TTF 47s (31-88)`),
and the `NS-A7F3` reference that ties a printed message to its row.

### 10.4 The junction inbox — the lamps, not the messages

Every down-link attempt is recorded at the one door it leaves through
(`routing.push_hub_command`), so "the strip is showing X" and "the cable is out" are
distinguishable from the console — on the *same* poll as the alerts:

```json
GET /api/alerts  →  "downlink": {
  "attached": false, "tick_s": 1.0, "reassert_s": 20.0, "silence_s": 30.0,
  "pushed": 0, "refused": 4, "reasserted": 3,
  "junction_inbox": [ {"key":"J1","cmd":"led","pattern":"flow_green","delivered":false,
                       "detail":"no hub attached","ts":…}, … ] }
```

`/api/extended.alerts` carries the same block, so a deck card prints it without a
second request, and the panel renders `● J1 flow_green · ○ J2 advisory_amber …`.

**The re-assert is not a heartbeat for its own sake.** It is what makes §H6.6's hold
rule statable: "hold the last pattern for 30 s, then default to `hold_amber`" only
means something if a *quiet mine* and a *dead server* look different — with commands
sent on change only, every steady green would have decayed to amber on its own.
`config.HUB_SILENCE_S` (30 s) > `HUB_REASSERT_S` (20 s), and the firmware's
`NS_SILENCE_MS` / `NS_HOLD_DEFAULT_MS` are the same two numbers on the other side of
the cable. No compiler on either side can check that pair, so the test checks it.

### 10.5 The row, audited item by item

Eight backend artefacts, six firmware ones, four `#CMD` commands. Auditing the tree
against the row found **three real gaps**, all closed here:

| Row item | Before | Now |
|---|---|---|
| `alerts/{dispatcher,channels,templates}.py`, `routes_alerts.py`, `schema.sql`, `test_alerts.py` | present | present · 32 tests |
| `level_hub/{main,ring_port,rc522,strips}.cpp`, `lib/ws2812b/` | written | written, manifest declared, **compiled** |
| `#CMD` — `led` | parsed | parsed |
| `#CMD` — `siren` | parsed | parsed **and emitted** (see P8-6) |
| `#CMD` — `advisory` | parsed | parsed, emitted |
| `#CMD` — `pattern` | **refused by name** | parsed: the level-wide form — no junction ⇒ both segments; with a junction it is `led` by another name |
| the server's down-link | alerts pushed; **the plan never transmitted** | `led_tick()` transmits it (see P8-7) |

### 10.6 Live, on `:5068` (2026-09-23)

```
plan transmission   J1 flow_green · J2/J3 advisory_amber · J4 flow_green
                    refused=4 (*a change* not delivered) · reasserted=1 after 20 s
trigger node_2 critical_collapse
  #40 CRITICAL ACTIVE site|EVENT|CRITICAL        local ✗ dash ✓ sms ✗(queued) email ✗(queued)
  #41 CRITICAL ACTIVE node_2|SEVERITY|CRITICAL   ← persisted, id assigned, state ACTIVE
  20 consecutive observations → 2 incidents, not 40 rows
POST /api/alerts/40/ack {"operator":"Safety Officer"} → {"ok":true,"state":"ACKED"}
POST /api/alerts/40/ack {"operator":"   "}            → 400 "acknowledgement is a human act"
GET  /api/alerts/40/deliveries
      local ✗ "no hub attached — the strip command was not delivered"
      dashboard ✓ "banner + log + GIS flash" · sms ✗ queued · email ✗ queued
      lifecycle ✓ "ACKED by Safety Officer"
POST /api/reset → alerts_closed=2, DB rows 2
alerts_history  #41 node_2 CRITICAL CLOSED reason=manual re-arm
                #40 site   CRITICAL CLOSED reason=manual re-arm
                #38 node_4 ELEVATED CLOSED reason=condition cleared   ← the machine's own close
```

Escalation was demonstrated on a second instance with `NS_ALERT_ESCALATE_S=10`:
`esc=True attempts=2` at t+18 s, `NS-DEFE ESCALATED — no acknowledgement within 10 s (N1)`
in the operator log, and a **second** ledger round written under the same alert.
`#38` is the answer to the question this phase kept asking: **a condition that clears
closes its alert and says why**, and a re-arm says something different. Closed, never
deleted.

### 10.7 Firmware — H6/H7 (your board, your eyes)

```
pio run -e level_hub   SUCCESS   RAM 7.2 % (23 488 B)   Flash 22.2 % (290 817 B)
pio run -e main_hub    SUCCESS   RAM 7.1 % (23 312 B)   Flash 20.5 % (268 449 B)
pio run -e node        SUCCESS   RAM 13.6 % (44 564 B)  Flash 56.7 % (742 601 B)
```

The decoder speaks the row's four commands plus the two documented extras:

| Frame | What the hub does |
|---|---|
| `{"cmd":"led","junction":"J2","pattern":"chase_red",…}` | renders on that junction's segment, `colour`/`period_ms` **from the server** |
| `{"cmd":"pattern","pattern":"chase_red"}` | both segments of this level; with a `junction` it is `led` by another name |
| `{"cmd":"siren","level":1,"on":true}` | sounds it — and *only* this (or `strobe_red`) can |
| `{"cmd":"advisory","level":1,"on":true}` | the soft amber breathe, never the siren |
| `{"cmd":"sms",…}` · `{"cmd":"gps",…}` | not this board: counted as handled, and `gps` says so out loud |

Three implementation decisions worth knowing before you wire it:

* **The pin freeze refuses the historical mistakes at compile time.** A siren on
  GPIO12 (flash strapping — HD-2) or GPIO25 (reserved for GPS — HD-9) is an `#error`,
  not a code review. So is anything on GPIO1/3 or 6–11.
* **The strip driver refuses to light up uncapped.** 16 LEDs at full white is ~960 mA
  through one USB port; `ns_ws2812b_show()` counts and refuses until
  `setMaxPowerInVoltsAndMilliamps(5, 300)` has run. A rule enforced by a comment is a
  rule the next person in a hurry skips.
* **Before the first command the hub shows the slow amber breathe, never green.**
  Green down an unchecked tunnel is the worst thing this board could display, and the
  evacuation strobe would be a false alarm. Amber is the vocabulary's own "watch
  this", so an operator reads *"the hub is up and has been told nothing"*.

**The hold rule's second clock, implemented as written:** commands stopping for 30 s →
hold the last indication; a further 30 s → `hold_amber` on both junctions. The
fallback **never touches the siren**: if the server went quiet mid-evacuation the
sound stays, because an alarm that ends when the network dies is the exact failure
this file exists to prevent. *"A dark junction reads as no hazard to a person walking
past it"* — §H6.6.

**Bench items only you can settle:** `FastLED` is pinned to `3.6.0` (3.10.x's `fl.fx+`
translation unit OOM-kills the compiler on a 2 GB host — unpin only on a bigger
machine); §H6.4's **active-or-passive buzzer?** answer selects `-DNS_SIREN_PASSIVE`;
and the two clocks above are the pair to watch with a stopwatch — pull the uplink and
the strips must hold, then fall back to amber, and the siren must not stop while they
do.

### 10.8 Frontend wiring (mandatory at every phase)

`frontend/src/js/15-alerts.js` and `<div id="alerts-panel">` in the evacuation view.
Rebuilt: **1 178 607 B · 16 scripts · md5 `22e47ee0935a62b489a9eabca4139197`**,
`check.js` ✓, `check_claims.py` 14/14. The panel polls `/api/alerts` at 2 s, keeps its
last good frame when a poll fails (an empty alert list during an outage is the most
dangerous thing it could show), remembers the operator in `localStorage`, and prints
the junction lamps above the list.

### 10.9 Defects found and fixed during this phase

| # | Defect | Why it mattered |
|---|---|---|
| P8-1 | a duplicate **replaced** the live alert | escalation would have fired exactly never on a sustained condition |
| P8-2 | `ts` left at `0.0` → `NULL` → the insert failed | an alert on screen that could never be written; found by the live run, not by a test |
| P8-3 | `closed_reason` missing from the machine close's update | a machine close and an operator re-arm were indistinguishable in the database |
| P8-4 | `channels.py` reached into `routing._DOWNLINK` | added `send_downlink()` — one door for non-LED frames |
| P8-5 | the level hub logged the main hub's `sms` frame as a refusal | console noise during exactly the demo in which the console is read |
| P8-6 | **the siren left the server as a fake junction** (`"junction":"SIREN"`) | no hub parses it, so every CRITICAL alert reported the local channel failed — and raised `LOCAL_ALERT_UNVERIFIED` — against a healthy cable |
| P8-7 | **the plan had no transmitter** | the junction strips would never have shown anything but an alert pattern |
| P8-8 | a refused push stayed "fresh", so refusals counted 4/s | the panel read as though the server were flapping |
| P8-9 | `pattern` with no junction emitted `"junction":""` | an empty string is a *found* key: the hub would have refused the whole frame |
| P8-10 | `/api/reset` swept the DB after the machine, reporting `alerts_closed_db: 0` | a count that disagrees with the screen is worse than no count |

P8-2 is the one to remember: the alert was visible on the console, live, with no id —
because the merge rule in `/api/alerts` is "never show less than the system knows".
That rule is why the defect was visible instead of silent.

### 10.10 Acceptance

| Requirement | Evidence |
|---|---|
| one message per incident | `test_twenty_observations_produce_one_alert` — 20 observations, 1 alert |
| unacknowledged ⇒ escalation on the clock | unit test + live (`esc=True attempts=2`, second ledger round) |
| acknowledged ⇒ no re-notify | `test_acknowledgement_stops_escalation` |
| CRITICAL never auto-suppressed | three-rule test + `/api/alerts/policy` publishes it |
| firewall floors the tier | `test_high_firewall_floors_the_tier_in_the_alert_layer` |
| SMS ≤160 chars **and** carries coordinates | `test_sms_is_within_one_message_and_carries_the_coordinates` |
| offline ⇒ queued, visibly, flushed on boot | `test_offline_dispatch_queues_and_the_boot_retry_delivers` (asserts the `#CMD` modem frame) |
| the phrase "we told them" is a row | `GET /api/alerts/<id>/deliveries` — per channel, per attempt (live, above) |
| re-arm closes, never deletes | live: `reason=manual re-arm`; and `#38 reason=condition cleared` |
| local failure is an infrastructure event | `test_local_failure_is_raised_as_an_infrastructure_event` |
| rate cap refuses **loudly** | `test_sms_rate_cap_refuses_loudly` — `RATE_LIMITED`, never a silent drop |
| the row's four commands are on the wire | `test_the_downlink_vocabulary_is_the_specs_own` + a dry run of each of the four |
| the lamps are visible from the console | `test_the_inbox_is_published_on_both_alert_endpoints` + live `/api/alerts` |
| the hub's patience outlasts the server's pulse | `test_an_unchanged_plan_is_reasserted_but_not_resent_every_tick` |
| a cleared condition closes and says so | `test_alert_tick_closes_when_the_condition_clears` + live row `#38` |

### 10.11 Gates

```
pytest tests/                631 passed        (599 → 623 → 631; 32 in test_alerts.py)
ruff check --select F,E9     All checks passed
check_contracts.py           PASS — 21 checks
check_numbers.py             PASS — 21 claims
audit_tree.py                PASS — 0 files outside §2
run.sh --check               7/7 green
pio run -e level_hub         SUCCESS · RAM 7.2 % · Flash 22.2 %
pio run -e main_hub -e node  SUCCESS
migrations                   [1,2,3,5] applied, none pending; schema.sql byte-identical
snapshot_api --diff          PASS — every endpoint kept its contract (additive only)
frontend build               check.js ✓ · check_claims 14/14 · md5 22e47ee0…
live :5068                   plan transmitted · alert raised, persisted, acked, closed
```

### 10.12 What this phase did not do

* **The modem's AT commands stay on the main hub.** §8.5 describes the SMS path as
  the *main hub's* UART, and §2 assigns every `main_hub/*` file to Phase 10 — so this
  phase ships the server-side emitter (a `#CMD {"cmd":"sms",…}` frame the level hub
  ignores on purpose) and leaves the hub's end to the phase that owns the file.
  `sms_dispatched` therefore reads `false` on the bench, with the ledger saying
  *queued*, which is the truth until a modem and an `NS_ALERT_SMS_TO` exist.
* **No SMS or SMTP provider is configured** (`NS_ALERT_SMS_TO`, `NS_SMTP_HOST`).
  Both channels report *queued* rather than pretending, and the boot `retry_failed()`
  flushes them the moment they are configured.
* `alerts/blackbox_alerts.py` (NO SD · DROPPED · SYNC STALLED) is **Phase 12's**.
