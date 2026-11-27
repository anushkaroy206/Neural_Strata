# Neural Strata — Phase 4 · AI Early Warning

Goal: an XGBoost model predicting **seconds until 100 mm displacement breach**,
integrated into `app.py` so the dashboard's TTF pill + AI view go from
"kinematic estimate" to "model forecast" with zero frontend changes.

## STATUS
| Sub-phase | What | Status |
|---|---|---|
| 4.1 | Harvest: nominal + failure drills → `datasets/drill_dataset.csv` | ✅ done — 18 runs (9 breach / 9 stable), 7870 windows |
| 4.2 | Feature windows (60 s) + StandardScaler | ✅ inside 01 |
| 4.3 | Train XGBoost, MAE/RMSE + 5-min-breach accuracy, export `models/ttf_model_v1.json` | ✅ done — held-out critical MAE 0.10 s, 5-min-breach acc 1.000 (v2 split: 1 held-out critical run, warning rows in-sample+disclosed, breach weight 5×) |
| 4.4 | app.py loads model, 1 Hz inference overwrites `ttf_seconds`, `/api/ai` real metadata | ✅ done + sandbox dress rehearsal passed (7→0 live countdown; missing-model → FALLBACK boots clean) |
| 4.5 | Live drill validation ✅ + degradation check + hub re-flash + rehearsal | **next** (runbook: `4.5_runbook.md`) |
| 4.6 | **Scaling & Validation (3-Tier AI Strategy)**: <br>• In-domain synthetic augmentation (50,000+ windows, zero scale contamination) <br>• External geotechnical benchmark validation (Landau open-pit radar series) <br>• Fukuzono kinematic fallback calibration | ✅ scripts implemented (`03`, `04`, `05`) |

Stretch (only if time): 1-layer 64-unit LSTM on identical windows; deploy
whichever has better RMSE.

## Layout
```
neuralstrata-ai/
├── README.md                 ← this file
├── datasets/
│   ├── drill_dataset.csv     ← 4.1 output (one row per 0.5 s window)
│   └── drill_dataset_augmented.csv ← 4.6 output (50k+ physics windows)
├── notebooks/
│   ├── 01_data_extraction.py ← run this after the harvest session
│   ├── 02_training.py        ← written in 4.3
│   ├── 03_synthetic_augmentation.py ← in-domain physics generator (50k windows)
│   ├── 04_external_validation.py    ← Landau open-pit radar validation harness
│   └── 05_calibrate_kinematic.py    ← Fukuzono fallback calibration
└── export/
    ├── training_report.txt
    └── external_validation_report.txt
```

## Env (created once)
```
conda create -n ai_env python=3.10 -y
conda activate ai_env
pip install pandas numpy scikit-learn xgboost psycopg2-binary jupyterlab
```

## 4.1 harvest session (~35–45 min)
1. Postgres.app running.
2. Backend: `python app.py --no-serial`  (no hardware — keeps the nominal
   baseline clean; the hub's temporary 45.5 mm test payload stays out of the data)
3. Dashboard open at http://localhost:5000
4. **3 minutes idle** (NOMINAL, no injections)
5. 10 drills, Re-arm between each:
   - 3 × Node 1 CRITICAL (let it run to evacuation)
   - 3 × Node 2 CRITICAL
   - 2 × Node 1 WARNING
   - 2 × Node 2 WARNING
6. `python neuralstrata-ai/notebooks/01_data_extraction.py`
   (add `--since "YYYY-MM-DD HH:MM"` to include only today's session;
   old Phase-3 drill rows in the DB are otherwise picked up too)

## Before the 4.5 live demo (remember!)
- ✅ Done: the two TEMPORARY test lines in `hub.cpp` (fixed
  `{"node": 1, "disp": 45.5, "tilt": 12.0}` every 2 s) are deleted.
- ⏳ Still to do: **re-flash the hub** (PlatformIO `env:hub`) — until then
  Node 1's real readings are still replaced by the old firmware's 45.5 mm.
- Then see `4.5_runbook.md` for the full morning sequence.

## Dataset provenance

Three different window counts appear across these documents. They are not
contradictions — they describe three different datasets. This table exists so that
a reader who sees 5,806 and 7,870 and 50,000 on the same page can understand
rather than doubt.

| Dataset | Windows | Runs | Trained on? | Where it lives |
|---|---|---|---|---|
| **Raw field harvest** | **7,870** | 18 (9 breach / 9 stable) | no — it is the harvest | `datasets/drill_dataset.csv` |
| **The canonical training run** | **5,806** | v2 split, held-out critical run `node_1_01` | **yes — this is `ttf_model_v1.json`** | `models/model_meta.json` |
| **In-domain synthetic augmentation** | **50,000+** | physics-generated (Tier 3) | not yet — target for the augmented retrain | `datasets/drill_dataset_augmented.csv` |
| **Counterfactual corpus (Phase 5)** | **43,092** | 420 runs × 7 profiles, 32 features | Model 1 — `pattern_model_v1.json` is trained on this | `datasets/pattern_dataset.csv` |
| **Drill set (Phase 5)** | **43,092** | the *same* 420-run pass, 16 core features + `ttf_label` | Model 2's synthetic retrain (`ttf_model_v2.json`) — **not** the shipped v1 | `datasets/drill_dataset.csv` |

**Canonical numbers**, read from `models/model_meta.json` and enforced by
`backend/tools/check_numbers.py` (which fails the build if any document disagrees):

| Metric | Value |
|---|---|
| Held-out critical MAE | **0.10 s** |
| Held-out critical RMSE | 0.42 s |
| Warning ramp (in-sample, disclosed) | MAE 0.08 s / RMSE 0.15 s |
| Overall | MAE 0.09 s / RMSE 0.23 s |
| 5-minute breach accuracy / precision / recall | 1.000 / 1.000 / 1.000 |

> The 5.4 s MAE quoted in earlier revisions of the SRS came from a smaller
> pre-v2 split (n = 62 windows). It was superseded by the run above. If you are
> reading an older document, that is the number it means.

## Model contract (4.4)
- File: `neuralstrata-backend/models/ttf_model_v1.json` (+ `scaler.json`)
- Input: the **16 model features** in `contracts`/`engine.features.FEATURES` order (the CSV's numeric columns; label and metadata columns are not features)
- Output: TTF seconds, clamped [0, 3600]; NaN/flat window → kinematic fallback
- Missing model file → app boots fine, kinematic only (demo-proofing)
