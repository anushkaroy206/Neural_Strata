# Frontend architecture

Neural Strata is a dependency-free, framework-free control-room dashboard: one HTML document, four stylesheets and fourteen classic scripts that share the page's global scope. The sources live in `src/` (one HTML shell, four stylesheets, fourteen scripts); `npm run build` inlines them into the self-contained `index.html` at the project root, which is the file that gets opened, previewed and deployed. For development, open `src/index.html` through any static server and it runs. `tools/build-single.js` folds everything back into one self-contained file for deployment.

## Load order

The scripts are **classic scripts loaded synchronously in document order**; later files rely on globals defined by earlier ones. The numeric prefix *is* the contract — keep it ascending in `src/index.html` (`npm run check` enforces this).

| # | File | Owns | Exposes |
|---|---|---|---|
| 00 | `src/js/00-config.js` | backend base URL (`window.NEURAL_STRATA_API`) | read once by `06-app.js` |
| 01 | `src/js/01-landing-shell.js` | clock + markup-level helpers of the sign-in shell; runs even if the deck layer fails | — |
| 02 | `src/js/02-micro3d.js` | tiny flat-shaded painter's-algorithm 3D engine (orbit, zoom, tween, labels) used by the underground cutaway | `micro3d` scene factory |
| 03 | `src/js/03-sensor-cells.js` | the single roster of the ten underground sensor cells E3–E12, mini hubs and junction boxes | `window.NS_MESH` |
| 04 | `src/js/04-surface-plan.js` | overview canvas: 2D north-up surface plan, surface array S1–S4 + main hub M-01, failure chips | `window.SURF` |
| 05 | `src/js/05-underground-cutaway.js` | route/crew state bridge for the underground views | `window.UG`, `window.UG_SAFE` |
| 06 | `src/js/06-app.js` | **the application**: gateway polling + `SIM` fallback, view router (`show`), overview/telemetry/control/records/AI views, sirens & horns, scenario console, keyboard map | `window.__NS`, `window.MINE`, plus page-level functions (`show`, `rearm`, `pollExt`, `apiUrl`, `post`, `getJSON`, `LINK` …) |
| 07 | `src/js/07-underground-3d.bundle.js` | **prebuilt esbuild bundle**: three.js (MIT, licence kept at the end of the file) + the RFID 3D shaft / evacuation map engine. Treat as vendor code — do not hand-edit; rebuild from its source if it ever changes. | `window.__THREE__`, `window.__SURFACE` |
| 08 | `src/js/08-decks-core.js` | shared runtime for the three role decks: rAF loop, deck tab engine, modal, toast, hash-chained audit ledger, print/report helpers | `window.NSD`, wraps `window.show` |
| 09 | `src/js/09-access-control.js` | sign-in gate, roles, role → view access matrix, session pill | wraps `window.show` (role gate) |
| 10 | `src/js/10-deck-operator.js` | Mine Operator deck: shift HUD, network topology (surface 433 MHz mesh S1–S4 → M-01 · underground double ring M-01 → MH-1 → MH-2 → M-01 · one evac-light bus with two junction boxes per mini hub · buzzers at the mini hubs), tilt dials/sparklines, PPV, alarm triage, EAP, SMS broadcast | `window.__OP` |
| 11 | `src/js/11-deck-planner.js` | Mine Planner / Geologist deck: GIS superimposition, RMR + trough model, forecasting, DGMS Form 1-A export | `window.__PL` |
| 12 | `src/js/12-deck-regulator.js` | DGMS Regulator deck: multi-mine risk matrix, audit ledger, SLA, uptime calendar, calibration, radar, inquiry reports | `window.__RG` |
| 13 | `src/js/13-blackbox-cloud.js` | Underground blackbox & cloud sync: SD flight-recorder / outbox model, `BBX.ingest()` live feed, drills, Offline Outbox Console (Force Cloud Sync · CSV dump · system re-arm · stateless recovery pipeline), overview LED | `window.BBX` |

Stylesheets, also order-dependent (later files override earlier ones):

| File | Scope |
|---|---|
| `src/css/core.css` | palette (`:root` variables), icon rail, stage, overview cards, ticker, overlay views, RFID HUD, responsive rules |
| `src/css/decks.css` | the three role decks (panels, KPI tiles, dials, tables, modal, blackbox panel + overview LED, sliders, GIS, radar, print sheet) |
| `src/css/access.css` | sign-in console, session pill, locked shell, hazard strips, industrial plates |
| `src/css/deck-tabs.css` | deck tab bar and per-tab row visibility |

## Runtime model

```
                ┌──────────────┐  500 ms   /api/telemetry
 gateway ◄──────┤ 06-app.js    ├──────────►  render(d)        overview · telemetry · records · evac map
 (Flask)        │  poll loops  │  1.5 s    /api/extended  ─►  renderExt(e) + BBX.ingest(raw)
                │  SIM fallback│  5 s      /api/ai        ─►  pollAi()
                └──────┬───────┘
                       │ window.__NS / globals
        ┌──────────────┼──────────────────┐
   08-decks-core   09-access-control   13-blackbox-cloud
   NSD.* (rAF loop, tabs, ledger)      BBX.* (LED, panel, drills)
        │
   10 / 11 / 12 decks (NSD.onTick, NSD.onEnter, NSD.onTab hooks)
```

* **Gateway or simulator.** `poll()` tries the gateway; on any failure it renders `SIM.tick()` instead and the header reads *Sim mode*. Nothing else in the app knows which source it is looking at.
* **One render path.** All views read the same cleaned telemetry object (`cleanTelemetry`, `cleanExtended` clamp and default every field), so a partial or malformed payload can never throw inside a view.
* **Role decks** are separate scripts that register hooks on `NSD` (`onTick`, `onEnter`, `onTab`) instead of touching the core loop.
* **Blackbox module** runs its own 1 Hz local model of the SD → outbox → cloud pipeline. As soon as `/api/extended` carries `blackbox`/`cloud` blocks, `BBX.ingest()` takes over; if the feed stops for 8 s it falls back to the local model.
* **Offline Outbox Console** (operator deck → BLACKBOX tab, below the live panel and the drills) is rendered by `renderConsole()` from the same `BBX.state`: cloud outbox queue (rows pending, bar scaled to one 1 000-row chunk, header chip = sealed chunks awaiting `ACK_DELETE`), MicroSD / VSPI status rows, and the five-step stateless recovery pipeline (① cloud `latest_id` = last acked chunk, ② chunk files on the card, ③–⑤ result of the most recent upload run — fed by `runUpload()` locally or by `lastUpload`/`lastAck` from the gateway feed). Buttons: **Force Cloud Sync** (`BBX.forceSync`, `POST /api/cloud/force_sync` when linked), **Dump Blackbox Log (CSV)** (`BBX.dumpCsv` — the ring buffers of node 1/2 stamped with chunk/row numbers: `chunk_id,row,ts_ms,node,tilt_x,displacement_mm,acceleration,flag`; falls back to a chunk manifest when no series exist) and **System Re-arm / Clear Buffer** (`BBX.rearm` — clears every armed drill, flushes the RAM ring back to the card, resets the pipeline readout, keeps the outbox untouched). Both actions write a ledger entry.

## Site network model (cross-dashboard hardware architecture)

* **Surface — sensing.** Four wireless nodes S1–S4 (tilt / displacement / acceleration, battery + solar) form a 433 MHz mesh and report to the **main hub M-01** (surface gateway). The mesh is independent of the wired circuit: a silent node is rerouted around, nothing underground changes.
* **Underground — signalling only, no sensor nodes.** M-01 → **MH-1** → **MH-2** → M-01 is a **double ring** (primary ↻ + secondary ↺). One segment cut wraps the ring and every hub stays reachable; a hub is isolated only when both of its segments are cut.
* Each mini hub carries the **buzzers** (evacuation sirens) and runs its own **bus** with **two junction boxes** (`JB-1A/1B`, `JB-2A/2B`); junction boxes hold **evacuation light controls only**. A bus cut darkens the boxes downstream of the break; a hub fault darkens its whole bus.
* **Cross-dashboard consistency**:
  1. **Mine Operator deck (`10-deck-operator.js`)**: Interactive MESH schematic with zero overlapping text, segment cut badges (`[ ✕ R1/R2/R3 CUT ]`), port-level WRAP indicators (`[ WRAP ]`), live bus polling status, drill injection tools, buzzer triggers, and surface telemetry dials.
  2. **Mine Safety Planner deck (`11-deck-planner.js`)**: Form 1-A / SMP statutory report compiles the full hardware inventory: 4 surface strata mesh nodes S1–S4 + underground double ring (M-01 ↔ MH-1 ↔ MH-2 ↔ M-01) with 4 buzzers and 4 junction boxes (24 evacuation lights), confirming CMR 1957 compliance with zero underground sensor nodes.
  3. **DGMS Regulator deck (`12-deck-regulator.js`)**: Network availability tracking (`surface mesh + double ring, 90-day mean`), ring wrap inspection observations, and audit reports.

## Views and access

| View id | Content | Hotkey |
|---|---|---|
| `overview` | surface plan / underground toggle, attention card, blackbox LED, ticker | `1` |
| `telemetry` | per-node instruments, inverse-velocity, thresholds | `2` |
| `control` | scenario injection console | `3` / `?` |
| `records` | RFID log, event ledger | `4` |
| `evacmap` | 3D underground evacuation map (three.js) | `5` |
| `ai` | forecast panel, model status | `6` |
| `node` | per-cell page for E3–E12 (`[` / `]` cycles cells) | — |
| `operator` / `planner` / `regulator` | role decks | `7` / `8` / `9` |

Role gate (`09-access-control.js`, `ROLE_ACCESS`): operator → overview, telemetry, control, records, evacmap + operator deck; planner → overview, telemetry, records, ai + planner deck; regulator → overview, telemetry, records + regulator deck.
Demo credentials live in `ROLES` (`ECL-4471/4471`, `GEO-2290/2290`, `DGMS-1107/1107`) — replace with a real identity provider before any deployment.

Other keys: `S` / `U` surface ↔ underground, `R` re-arm, `I` scenario console, `Esc` back / close, `Alt+1…5` deck tabs.

## Deck tab engine

Every deck row carries `data-tab="<tab>"`; rows without it are visible on every tab. `NSD.setTab(deck, tab)`, `NSD.tabsOf(deck)`, `NSD.onTab(deck, fn)` drive it. To add a tab: add a `<button class="dtab" data-tab="x">` to the deck's `<nav class="dtabs">`, give the new rows `data-tab="x"`, done — key handling and repaint hooks are generic.

## Conventions

* Plain ES2017+ (no modules, no transpiling); every file must parse with `node --check`.
* No comments in shipped code — this document and `docs/API.md` are the commentary.
* IDs are the DOM contract between markup and scripts; grep before renaming.
* Internal flag names such as `api.central` / `fault.central` are deliberately kept even though the UI now says *Main hub M-01*.
* Keep third-party code confined to `src/js/07-underground-3d.bundle.js`.
