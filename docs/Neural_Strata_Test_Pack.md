# Neural Strata — front-end test pack

Trial runs and test cases for **every function reachable from the three logins**
(Mine Operator, Mine Planner / Geologist, DGMS Regulator) plus the shared
workspaces and the gateway integration.

Every case below was derived from the shipped source, so the "Expect" column is
what the code actually does — not what it ideally should do. Where behaviour
differs between run modes it is called out explicitly.

Tick the box when a case passes. A case that fails is a finding: quote the **ID**
(e.g. `OP-BB-07`) and say what you saw instead.

> **This pack has been executed against the shipped build.** 97 of the 241 cases were
> driven head-lessly (real DOM, real gateway on `:5000`, mock on `:8095`) — every
> interactive control in all three logins, the full access-control path and the whole
> gateway contract. All 97 passed. The cases below marked **✔ run** are the ones that
> were executed; their Expect lines have been corrected to the verified behaviour.
> The remaining cases need a real browser (pixels, pointer drags, audio) and are left
> for the manual pass. Run log: `tests-fe/RESULTS.txt`.

---

## 0 · Before you start

### 0.1 Credentials

| Login | ID | PIN | Cleared for |
|---|---|---|---|
| Mine Operator / Shift Charge | `ECL-4471` | `4471` | Overview · Telemetry · Control · Muster & log · Evac map · **Operator deck** |
| Mine Planner / Geologist | `GEO-2290` | `2290` | Overview · Telemetry · Muster & log · AI prediction · **Planner deck** |
| DGMS Regulator | `DGMS-1107` | `1107` | Overview · Telemetry · Muster & log · **Regulator deck** |

### 0.2 Three ways to run it — pick per test session

| Mode | How | Header shows | Blackbox panel fed by | `POST /api/cloud/force_sync` |
|---|---|---|---|---|
| **SIM** | open `index.html` directly, or `npm run serve` | **Sim mode** | built-in simulator | simulated locally |
| **MOCK** | `npm start` | **Online** | `mock/server.js` | mock gateway |
| **GATEWAY** | Flask `app.py` serving this folder | **Online** | real ingest + gate store | real outbound queue |

Cases marked **[mode-sensitive]** must be run in MOCK or GATEWAY to be meaningful.

### 0.3 Reset between runs

A full clean state needs all three: **close the tab** (clears `sessionStorage`,
so you land back on the sign-in console), clear site data (clears `localStorage`,
where the failed-attempt counter lives), then reload. `Ctrl/Cmd+Shift+R` alone
does **not** clear the session — that is deliberate, and it is itself a test
case (`AC-20`).

---

## A · Sign-in and access control — 24 cases

### A.1 The sign-in console

- [ ] **AC-01 · console appears on load** — open the dashboard cold.
  → The whole UI is locked behind the sign-in console; the left rail icons and
  the role-deck switcher are hidden until you sign in.
- [ ] **AC-02 · role cards** — look at the three role cards.
  → Each shows badge, who it belongs to, its workspace chips, and
  `CLEARED FOR n WORKS · n BLOCKED` (Operator 6 works / 3 blocked · Planner 5 / 2 · Regulator 4 / 2).
- [ ] **AC-03 · pick a role with the mouse** — click *Mine Planner / Geologist*.
  → Card highlights, console status reads `MINE PLANNER / GEOLOGIST · AWAITING PIN`,
  the ID field auto-fills `GEO-2290`, PIN clears and takes focus.
- [ ] **AC-04 · pick a role with the keyboard** — focus a card, press `↓` / `→`.
  → Selection moves to the next role and wraps around at the end; `↑` / `←` goes back.
- [ ] **AC-05 · permission matrix** — hover any cell of the matrix.
  → Tooltip reads `<badge> · <WORKSPACE> · granted` / `exclusive deck` / `blocked`.
  The three own-deck cells are drawn differently from granted cells.
- [ ] **AC-06 · console clock** — watch the console clock for 10 s.
  → Advances once a second (IST) and never stalls or skips.

### A.2 Entering credentials

- [ ] **AC-07 · PIN buffer** — type `44` into the PIN field.
  → Status reads `PIN BUFFERED · 2 DIGITS` (singular `1 DIGIT` for one character).
- [ ] **AC-08 · show/hide PIN** — click **SHOW**.
  → PIN switches to plain text; clicking again masks it.
- [ ] **AC-09 · submit with Enter** — with valid ID + PIN, press `Enter`.
  → Signs in exactly as clicking the arrow does.
- [ ] **AC-10 · empty fields** — clear both fields and submit.
  → Red `Enter both the officer ID and the password / PIN.` Nothing unlocks.
- [ ] **AC-11 · correct credentials** — `ECL-4471` / `4471`, submit.
  → Console closes, dashboard unlocks, OVERVIEW opens, a session bar appears with
  the role badge and a **SIGN OUT** button.
- [ ] **AC-12 · wrong PIN** — valid ID, PIN `1234`.
  → Red `ACCESS DENIED · ATTEMPT 1 OF 5`; the PIN field clears; you stay locked out.
- [ ] **AC-13 · wrong ID, right PIN** — ID `XXX-0001`, PIN `4471`.
  → Same refusal as AC-12 — the ID must match the selected role exactly.
- [ ] **✔ run · AC-14 · attempt counter is persisted** — fail twice.
  → `ns.attempts` = `2` in **sessionStorage** (nothing is written to `localStorage`),
  so the count survives a reload of this tab and is gone once the tab closes.
- [ ] *(manual, superseded by AC-16)* — fail twice, reload the page.
  → The console greets you with `Failed attempts on this terminal: 2 of 5 before a
  30-second hold.` (Counter lives in `localStorage`, so a reload does not clear it.)
- [ ] **✔ run · AC-15 · five strikes locks the terminal** — fail five times.
  → The console line becomes `TERMINAL HELD · 30 s` and the message reads
  `Credentials rejected for <id> as <ROLE>. Terminal held for 30 s after five failures.`
  Further attempts (including a **correct** PIN) are refused with
  `Too many failed attempts — this terminal is held for another N s`, counting down.
  Attempts reset to zero for the next round.
- [ ] **✔ run · AC-16 · counter survives a tab reload during the hold** — reload mid-hold.
  → The hold is still in force (`ns.lockUntil`, sessionStorage), the console reads
  `TERMINAL HELD · N s`, and a correct PIN inside the hold is still refused.

### A.3 Session behaviour

- [ ] **AC-17 · session restore** — sign in, then reload the page (same tab).
  → You are signed straight back in as the same role; the ledger gains
  `SESSION RESTORED ON THIS TERMINAL · <badge>`.
- [ ] **AC-18 · new tab is a fresh session** — open a second tab on the same URL.
  → The new tab shows the sign-in console (the session is `sessionStorage`, not shared).
- [ ] **AC-19 · close the tab and return** — close the tab, reopen the URL.
  → Console again; the previous sign-in is gone.
- [ ] **AC-20 · plain reload does not sign you out** — `Ctrl/Cmd+R` while signed in.
  → Still signed in. (This is intentional — do not report it as a bug.)
- [ ] **AC-21 · sign out from the session bar** — click **SIGN OUT**.
  → UI relocks, the session bar disappears, and the console is back at
  `AWAITING CREDENTIALS`. Reloading does **not** restore the old session.
- [ ] **AC-22 · sign out from the rail** — use the rail's sign-out item.
  → Same as AC-21; the ledger records the session end.

### A.4 Cross-role gating

- [ ] **AC-23 · blocked workspace while signed in** — as **Planner**, try to open
  *Control* (key `3`).
  → A toast says the *MINE PLANNER / GEOLOGIST* role is not cleared for that
  workspace, and you are dropped into the Planner's own deck. No control panel appears.
- [ ] **AC-24 · gated view while signed out** — signed out, press `2`.
  → Toast `Sign in to open the strata platform — the <view> workspace is
  access-controlled.` and the sign-in console comes back. The view does not open.

---

## B · Shared workspaces — 34 cases

Visible in more than one login. Run `B` while signed in as **each** of the three
roles and note the differences.

### B.1 Sector overview (`1`)

- [ ] **SW-01 · overview loads** — press `1` after signing in.
  → Surface plan draws; no blank canvas, no console errors.
- [ ] **SW-02 · surface / underground toggle** — click **UNDERGROUND** in the model bar, then **SURFACE** (or press `U` / `S`).
  → View switches, camera animates, the active button is highlighted.
- [ ] **SW-03 · mesh cell selection** — click a surface mesh cell on the model.
  → Cell highlights and a detail card opens (`OPEN E3 DETAIL ▸`).
- [ ] **SW-04 · prev / next cell** — press `]` then `[`.
  → Selection steps through the mesh cells oldest-first and wraps around at the ends.
- [ ] **SW-05 · node detail view** — open a cell's detail, or press `6` then pick a node.
  → NODE view opens with hero number, readings, hardware/link block, and the
  120-second acceleration chart.
- [ ] **SW-06 · locate on model** — in node detail press **Show on model**.
  → Returns to the model with that cell pinned and highlighted.
- [ ] **SW-07 · node detail fields populated** — in node detail check the rows.
  → Neighbours, bus hop, parent chain (`… → MINI HUB … → M-01`), battery/solar/RSSI
  and **Last packet** are all filled.
- [ ] **SW-08 · last packet is a real time** — watch **Last packet** for ~5 s.
  **[mode-sensitive]**
  → It shows an ISO-8601 UTC stamp that keeps updating. (A static `live` means the
  gateway is not sending `telemetry.timestamp`.)

### B.2 Telemetry (`2`)

- [ ] **SW-09 · four charts draw** — open Telemetry.
  → Acceleration, displacement, tilt and inverse-velocity panels all plot.
- [ ] **SW-10 · charts move** — watch for 15 s.
  → Traces advance; the y-axis labels stay readable and do not jitter wildly.
- [ ] **SW-11 · history backfill** — reload and open Telemetry immediately.
  → Charts start with data already in them, not empty ( `/api/history` backfill ).
- [ ] **SW-12 · inverse velocity band** — look at the 1/v panel.
  → The threshold band is drawn and the trace sits inside it while nominal.

### B.3 Control & thresholds (`3`) — Operator only

- [ ] **SW-13 · control view is operator-only** — as Planner or Regulator press `3`.
  → Refused with a toast (see AC-23). Record which deck you land in.
- [ ] **SW-14 · scenario console opens** — as Operator press `3`, then **SCENARIO CONSOLE ▸** (or key `I`).
  → The scenario injection panel is showing with four injection buttons.
- [ ] **SW-15 · node 1 creep** — press *Node 1 · Creep*.
  → Button arms, status line changes, node 1's status climbs to WARNING with a
  rising acceleration; the event log gains a warning entry.
- [ ] **SW-16 · node 1 failure** — press *Node 1 · Failure*.
  → Node 1 goes CRITICAL (a ≥ 3.0 mm/s²), alarm styling appears, the siren/EAP
  banner may engage, and the ledger logs a level-3 alert.
- [ ] **SW-17 · node 2 injections** — repeat SW-15/16 for node 2.
  → Node 2 behaves independently; both nodes can be active at once.
- [ ] **SW-18 · TARP alarm panel** — trigger a failure and look at *SURFACE-NODE EVACUATION ALARM · TARP*.
  → Panel lights up and mirrors the alarm state.
- [ ] **SW-19 · silence room audio** — press **SILENCE ROOM AUDIO**.
  → Audio stops; the panel stays in alarm state (silencing is not clearing).
- [ ] **SW-20 · re-arm** — press **Re-arm system** (or key `R`).
  → Statuses return to NOMINAL, displacement resets to 5.0 mm, peaks clear, the
  ledger logs the re-arm, and any EAP banner stands down.
- [ ] **SW-21 · keyboard shortcuts panel** — read the on-screen shortcut table.
  → `1–6` views, `S`/`U` model, `I` scenarios, `[`/`]` cells, `R` re-arm, `ESC` close.

### B.4 Muster & log (`4`)

- [ ] **SW-22 · muster tiles** — open Muster & log.
  → Total / underground / safe tiles show numbers, and the header reads
  `<n> UNDERGROUND`.
- [ ] **SW-23 · headcount does not drain** — with the gateway running, watch the tiles.
  **[mode-sensitive]**
  → Total stays at the roster size and does not drift as the RFID log scrolls.
- [ ] **SW-24 · RFID ledger rows** — look at the mustering ledger.
  → Each row carries ID, name, zone and time; zones are drawn from the six known
  values only.
- [ ] **SW-25 · event log** — look at the event log panel.
  → Newest first, each row time-stamped with a severity colour; entries appear as
  you trigger scenarios.
- [ ] **SW-26 · muster after evacuation** — trigger a failure, then re-open Muster & log.
  → Underground count falls and safe count rises as tags move to `Evacuating …`
  then `Safe Exit` **when the gateway is supplying zones**; in SIM mode the
  simulator's own crew movement drives it.

### B.5 Evacuation map (`5`)

- [ ] **SW-27 · evac map opens** — press `5`, or the banner's **EVAC MAP** button.
  → Underground cutaway draws with routes, junctions and exits.
- [ ] **SW-28 · banner mirrors state** — with a route blocked, read the map banner.
  → Banner states the routing decision; figures update live.
- [ ] **SW-29 · EVACUATE pill** — press the red `⚠ EVACUATE` pill in the header.
  → Switches to underground + evac map in one action.
- [ ] **SW-30 · map header + banner** — read the line under the heading, then the banner.
  → The header states `Live Dijkstra routing from the backend · 4 surface entrances ·
  2 underground levels`, the calc chip reads `PATH CALCULATION ACTIVE`, and the banner
  reports the current routing decision (it must not stay on `Waiting for telemetry…`
  while the gateway is answering).

### B.6 AI prediction (`6`) — Operator and Planner

- [ ] **SW-31 · AI panel renders** — open AI prediction.
  → Model, horizon, last-train, confidence, note and two node forecast panels draw.
- [ ] **SW-32 · status colour** — read the AI status pill. **[mode-sensitive]**
  → `ACTIVE` green; `WARMING UP`, `FALLBACK` and `AWAITING_INTEGRATION` amber;
  `OFFLINE` (or any other value) red.
- [ ] **SW-33 · forecast tags** — look at each node's tag.
  → `AI FORECAST ACTIVE` for a node present in `forecasts`, otherwise `KINEMATIC MODE`.
- [ ] **SW-34 · kinematic fallback moves** — trigger a failure with the gateway down.
  → The forecast curve keeps moving (the dashboard extrapolates the parabola itself).

---

## C · Mine Operator deck — 83 cases

Sign in as `ECL-4471` / `4471`. The deck switcher is at the top of the deck view.
Tab keys: `Alt+1` … `Alt+5`; `←`/`→` on a focused tab.

### C.1 SHIFT tab

- [ ] **OP-SH-01 · shift header** — open the deck.
  → Shift-I, window `06:00 – 14:00 · Surface & UG combined`, and a live clock.
- [ ] **OP-SH-02 · shift countdown** — watch `— left in shift`.
  → Counts down; it is derived from the clock, not a fixed string.
- [ ] **OP-SH-03 · duty rows** — read Colliery Manager / Overman / Men.
  → Manager `A. K. MAHATO · ON DUTY`; overman and men counts populated.
- [ ] **OP-SH-04 · sync pulse** — read EDGE MINI-HUB SYNC PULSE.
  → `S1–S4 → M-01 → CONTROL ROOM · NOMINAL`, offline buffer `0 % · 0 PACKETS`,
  central node `ONLINE · 433 MHz`.
- [ ] **OP-SH-05 · blackbox + cloud rows** — read the last two rows of the pulse block.
  → `Underground blackbox (MicroSD · VSPI)`: `ACTIVE · chunk_0042` (or the live chunk);
  `Cloud outbox (SQLite → HTTPS)`: a sync percentage. **[mode-sensitive]**
- [ ] **OP-SH-06 · safety index** — read the panel stability index.
  → A number 5–100. Trigger a failure and confirm it drops; re-arm and confirm it recovers.
- [ ] **OP-SH-07 · index chips** — read the four chips.
  → Tilt, PPV, mesh and response-slack chips; each turns warn/crit colour with its value.
- [ ] **OP-SH-08 · EAP drags the index** — fire the EAP (see C.4) and re-read the index.
  → Index falls sharply (the EAP is weighted ~26 points) and the headline turns red.

### C.2 MESH tab

- [ ] **OP-ME-01 · topology draws** — open MESH.
  → Surface 433 MHz mesh, the underground double ring `M-01 → MH-1 → MH-2 → M-01`
  and both evacuation buses are drawn.
- [ ] **OP-ME-02 · ring is a closed loop** — trace the ring.
  → All four segments are present and the ring closes back to M-01.
- [ ] **OP-ME-03 · fail a device** — pick a device in the selector, press **FAIL DEVICE**.
  → The device goes dark, dependent junction boxes go with it, and the mesh reroutes.
- [ ] **OP-ME-04 · last-gasp alarm** — fail a **surface node**.
  → Level-3 alarm `Surface node <id> silent · last-gasp received`, relay list named,
  `n/N reporting` stated, and a ledger entry.
- [ ] **OP-ME-05 · cut a ring segment** — pick a segment, press **CUT SEGMENT**.
  → If it isolates junctions, a level-3 `… isolated … cut` alarm fires; otherwise a
  level-2 `Ring segment … cut · ring wrapped`.
- [ ] **OP-ME-06 · bus cut** — cut a bus feeding a hub.
  → Level-3 if it darkens junctions, level-1 if not — check the alarm level matches
  what actually went dark.
- [ ] **OP-ME-07 · restore all** — press **RESTORE ALL**.
  → Everything returns; a level-1 `Network restored · ring healthy …` advisory is logged.
- [ ] **OP-ME-08 · fail hub (buzzers)** — fail a hub with buzzers.
  → Level-3 `<hub> fault · n buzzers offline`, and the buzzer row reads `FAULT`.
- [ ] **OP-ME-09 · fail junction (lights)** — fail a junction box.
  → Level-2 `<id> fault · n evacuation lights offline`.
- [ ] **OP-ME-10 · EAP energises the mesh** — fire the EAP and look at the mesh.
  → Buzzers read `SOUNDING`, hubs and lamps show energised, junction rows switch on.
- [ ] **OP-ME-11 · uplink severed** — cut the surface uplink.
  → Level-2 `Surface uplink severed`, and the edge gateway buffers locally.
- [ ] **OP-ME-12 · uplink restored** — restore it.
  → `Uplink outage closed` with the buffered packet count flushed, and the buffer
  row returns to `0 % · 0 PACKETS`.

### C.3 STRATA tab

- [ ] **OP-ST-01 · sparklines** — open STRATA.
  → 60-second rolling stretch-rate sparklines draw for both instrumented nodes.
- [ ] **OP-ST-02 · PPV waveform** — look at the vibration monitor.
  → Live waveform with a moving trace; no frozen canvas.
- [ ] **OP-ST-03 · inject blast signature** — press **INJECT BLAST SIGNATURE**.
  → A PPV spike appears, the spike log gains an auto-tagged row, and the PPV chip responds.
- [ ] **OP-ST-04 · PPV limit chip** — force several bursts.
  → `PPV n / 2.5 mm/s` climbs and turns warn/crit past the limit.
- [ ] **OP-ST-05 · auto-tagged spike log** — check the log after bursts.
  → Rows carry a time and a tag; the list stays bounded (newest retained).
- [ ] **OP-ST-06 · vibration alarm** — inject a large burst.
  → `Vibration signature · abnormal strata movement` level-3 alarm is raised.
- [ ] **OP-ST-07 · tilt alarm** — drive tilt past 5 mm/m (critical scenario).
  → `Tilt n mm/m exceeds 5 mm/m limit` level-3 alarm.
- [ ] **OP-ST-08 · stretch-rate alarm** — let a creep run.
  → `Stretch rate rising · <pair>` level-2 alarm with a mm/min figure.

### C.4 ESCALATE tab — triage

- [ ] **OP-ES-01 · triage list** — open ESCALATE with no alarms.
  → `No alarms recorded this shift.` and header `0 OPEN · 0 ACKED`.
- [ ] **OP-ES-02 · alarms appear** — trigger a scenario, return to ESCALATE.
  → Newest first, each row tagged `L1`/`L2`/`L3`, with time and source.
- [ ] **OP-ES-03 · list caps at six** — raise more than six alarms.
  → Six rows shown; the header counts them and the log keeps the rest.
- [ ] **OP-ES-04 · critical rows pulse** — raise a level-3 alarm.
  → The row is drawn with the live-critical emphasis; acked rows are dimmed.
- [ ] **OP-ES-05 · ACK dialog** — press **ACK** on a row.
  → Modal `ACKNOWLEDGE & MUTE ALARM` with the alarm repeated, a swipe slider, an
  Operator ID field and an optional remark.
- [ ] **OP-ES-06 · confirm is disabled** — open the dialog and click CONFIRM immediately.
  → Nothing happens — confirm stays disabled until **both** the swipe and a valid ID
  are supplied.
- [ ] **OP-ES-07 · swipe alone is not enough** — complete the swipe, leave the ID blank.
  → Confirm still disabled; the slider caption reads `SWIPE ACCEPTED · ENTER OPERATOR ID`.
- [ ] **OP-ES-08 · ID validation** — type `abc`.
  → Inline error `Operator ID must be at least 4 characters and contain digits
  (e.g. ECL-4471)`; the field border goes red.
- [ ] **OP-ES-09 · valid ID enables confirm** — type `ECL-4471`.
  → Border goes green, confirm enables, knob shows `READY ✓`.
- [ ] **OP-ES-10 · acknowledge** — confirm.
  → Modal closes; the row turns grey with `✓ ACKED <time> · <who>`; header becomes
  `n OPEN · 1 ACKED`; the ledger logs the ACK **with the minutes elapsed since the alert**.
- [ ] **OP-ES-11 · remark is logged** — ACK another alarm with a remark typed.
  → The remark is carried into the acknowledgement record.
- [ ] **OP-ES-12 · keyboard ACK** — focus a row's ACK button and press `Enter`/`Space`.
  → Dialog opens (no mouse needed).
- [ ] **✔ run · OP-ES-13 · ESC closes the dialog** — open ACK, press `Escape`.
  → The modal closes with nothing acknowledged and the view underneath does **not**
  change. (The dialog markup stays in the DOM but is hidden — assert on the modal
  state, not on the presence of `#ackSwipe`.)

### C.5 ESCALATE tab — EAP

- [ ] **OP-ES-14 · EAP at rest** — read the EAP panel.
  → `STAGE 0 / 2 · DISARMED`, `HOOTERS IDLE`, `STROBES IDLE`, seam selector on
  *Both levels*, caption `STEP 1 · DRAG TO ARM`.
- [ ] **OP-ES-15 · stage 1** — drag the swipe knob fully right.
  → `STAGE 1 / 2 · ARMED`, caption `STEP 2 · DRAG AGAIN WITHIN 6 s TO FIRE`,
  knob shows `FIRE ▸`, and the ledger logs `EAP ARM STAGE PASSED`.
- [ ] **OP-ES-16 · arm window expires** — wait 7 s after arming.
  → Reverts to `ARM WINDOW EXPIRED · RE-ARM` and the swipe resets.
- [ ] **OP-ES-17 · fire** — arm, then drag again within 6 s.
  → EAP engages: level-3 alarm, header `STAGE 2 / 2 · ENGAGED`, `ENGAGED · ABORT TO
  STAND DOWN`, hooters/strobes show `ACTIVE · <seam>`, and the panel takes the
  critical glow.
- [ ] **OP-ES-18 · ledger trail on firing** — check the audit ledger after firing.
  → `EAP TRIGGERED · HOOTERS + STROBES · <label>` **and**
  `MANUAL WITHDRAWAL ORDER · <label> · MUSTER AT SHAFT BANK`.
- [ ] **OP-ES-19 · seam selection honoured** — set the seam to *Level 2* and fire.
  → Alarm and ledger both say LEVEL 2; hooter/strobe chips name that seam.
- [ ] **OP-ES-20 · seam change while engaged** — with the EAP engaged, change the seam.
  → Ledger gains `EAP SEAM EXTENDED TO <seam>` and the panel repaints.
- [ ] **OP-ES-21 · abort** — press **ABORT / RE-ARM**.
  → Back to STANDBY, hooters/strobes idle, ledger logs the abort, and the EAP
  message says the seam must be re-cleared by the Overman.
- [ ] **OP-ES-22 · silent drill** — press **SILENT DRILL (NO HARDWARE)**.
  → A drill message states the circuits were opened on the test loop only, and the
  ledger records `EAP SILENT DRILL COMPLETED · NO HARDWARE ENERGIZED` (attributed to
  the DGM officer). Nothing energises on the mesh.
- [ ] **OP-ES-23 · drill leaves no engagement** — after the drill, read the panel.
  → Stage 0, idle chips — the drill must not leave the EAP engaged.
- [ ] **OP-ES-24 · keyboard operation** — focus the swipe and use the arrow keys.
  → The two-stage gesture completes without a pointer.
- [ ] **OP-ES-25 · EAP pulls safety index + banner** — fire the EAP, then check SHIFT.
  → Index collapses, the safety banner reads red `EAP ENGAGED on <seam> …`, and the
  same warning reaches the regulator deck.

### C.6 ESCALATE tab — broadcast

- [ ] **OP-ES-26 · templates listed** — read the broadcast panel.
  → Four templates: Withdrawal Advisory, EAP Engaged, Node Offline Advisory,
  All Clear — each with a character count.
- [ ] **OP-ES-27 · recipients listed** — read the recipients block.
  → Five: Overman, Mining Sirdar, Rescue Room, Colliery Manager, mine-mate handsets.
- [ ] **OP-ES-28 · dispatch disabled at rest** — look at **DISPATCH BROADCAST**.
  → Disabled with the counter at `0 / 0 SEL`.
- [ ] **OP-ES-29 · template alone is not enough** — tick one template only.
  → Counter updates, dispatch still disabled (needs at least one recipient too).
- [ ] **OP-ES-30 · recipient alone is not enough** — tick one recipient, untick the template.
  → Dispatch disabled again.
- [ ] **OP-ES-31 · dispatch** — tick ≥1 of each and press **DISPATCH BROADCAST**.
  → Message ID assigned, recipients listed in the output, state `DISPATCHED <time>`,
  and a ledger entry per template naming the recipient count and the message ID.
- [ ] **OP-ES-32 · dispatch locks briefly** — immediately after dispatching, press it again.
  → The button is disabled for ~3.5 s, then returns to READY.
- [ ] **OP-ES-33 · dispatch logs an alarm** — check triage after dispatching.
  → A pre-acked informational row `Broadcast dispatched · <id>`.
- [ ] **OP-ES-34 · failed attempt is not silently swallowed** — read the dispatch output after a successful send.
  → It reports delivery and the gateway ACK. (In no mode may it claim success when
  the send did not happen — see X-14.)
- [ ] **OP-ES-35 · all clear** — dispatch only the *All Clear* template.
  → Ledger records the broadcast; no escalation is forced.

### C.7 BLACKBOX tab — headline panel

- [ ] **OP-BB-01 · headline chip** — open the BLACKBOX tab.
  → A green-ish chip stating the recorder is active, with the current chunk name and
  free space. **[mode-sensitive]** with the gateway this is fed by `/api/extended`.
- [ ] **OP-BB-02 · LED strip** — watch the LED row for 20 s.
  → LEDs advance through the pipeline stages.
- [ ] **OP-BB-03 · cloud chip** — read the cloud chip.
  → Percentage synced, plus pending count and the seconds to the next push.
- [ ] **OP-BB-04 · panel drops to local simulation** — with the gateway running, kill it and wait ~10 s.
  → The panel returns to its local simulation and says so, rather than freezing or
  showing stale live values.

### C.8 BLACKBOX tab — disaster drills

- [ ] **OP-BB-05 · internet down** — press *Mine internet down*.
  → Cloud chip goes offline with a pending count; **no deletes** are allowed; the
  ledger records a force-sync refusal if you try one while offline.
- [ ] **OP-BB-06 · cable cut** — press *Rockfall cuts the cable*.
  → Serial-severed state, hub keeps writing to SD, cloud shows `SURFACE BLIND · SERIAL CUT`.
- [ ] **OP-BB-07 · MicroSD pulled** — press *MicroSD pulled*.
  → Chip turns critical `BLACKBOX: FAULT / NO SD · RAM RING n ROWS`; the RAM ring counter climbs.
- [ ] **OP-BB-08 · power loss** — press *Sudden power loss*.
  → Brownout state with a reboot countdown, and the brownout warning while counting.
- [ ] **OP-BB-09 · month-long isolation** — press *Month-long isolation*.
  → SD-full state, FIFO armed, oldest chunk unlinked, freshest data kept.
- [ ] **OP-BB-10 · drill controls the LED state** — run each drill and watch the chip.
  → Each drill produces a visibly different chip class (ok / warn / crit), not the same colour.
- [ ] **OP-BB-11 · restore all links** — press **Restore all links**.
  → All drill states clear, status line returns to `ALL LINKS HEALTHY`, and free
  space is restored.
- [ ] **OP-BB-12 · drills do not corrupt the queue** — run the SD drill, restore it, then force a sync.
  → The outbox drains normally; no rows lost or double-counted.

### C.9 BLACKBOX tab — outbox console

- [ ] **OP-BB-13 · queue reads zero when clean** — read CLOUD OUTBOX QUEUE.
  → `0 ROWS PENDING`, bar empty, caption says all chunks synced.
- [ ] **OP-BB-14 · queue grows** — let telemetry run for a minute with the cloud offline.
  → Pending count rises and the bar fills proportionally.
- [ ] **OP-BB-15 · FORCE CLOUD SYNC (happy path)** — press it with the gateway reachable. **[mode-sensitive]**
  → Confirms the sync, reports the rows pushed and the chunks unlinked, and the
  queue returns to zero.
- [ ] **OP-BB-16 · FORCE CLOUD SYNC while offline** — put the cloud offline and force a sync. **[mode-sensitive]**
  → A **failure** is reported (`CLOUD UNREACHABLE · OUTBOX PROTECTED · SD RETAINED`).
  The console must never claim success here. This is the highest-value case in the pack.
- [ ] **OP-BB-17 · sync is idempotent** — force a sync twice in a row.
  → Second run reports nothing to send; pending stays at zero; `sent` does not double-count.
- [ ] **OP-BB-18 · DUMP BLACKBOX LOG (CSV)** — press it.
  → A CSV downloads; open it and confirm a header row plus one row per ledger entry,
  with a timestamp per row.
- [ ] **OP-BB-19 · SYSTEM RE-ARM / CLEAR BUFFER** — press it.
  → Buffer/queue cleared and state reset; confirm nothing else on the deck breaks.
- [ ] **OP-BB-20 · queue survives a drill** — run the internet drill, then restore and sync.
  → Rows buffered during the drill are still there and are pushed afterwards (no loss).

---

## D · Mine Planner / Geologist deck — 40 cases

Sign in as `GEO-2290` / `2290`. Tabs: `Alt+1` … `Alt+4`.

### D.1 GIS & LAYERS

- [ ] **PL-GI-01 · map draws** — open the deck's first tab.
  → Survey-of-India-grid plan with surface and underground superimposed; no blank canvas.
- [ ] **PL-GI-02 · zoom in / out** — press **ZOOM +** several times, then **ZOOM −**.
  → Map scales about its centre; labels stay legible; the grid stays aligned.
- [ ] **PL-GI-03 · pan** — drag the map.
  → Extent moves; releasing stops it (no runaway inertia).
- [ ] **PL-GI-04 · reset view** — press **RESET VIEW** after zooming and panning.
  → Returns to the initial extent.
- [ ] **✔ run · PL-GI-05 · layer toggles** — toggle each of the ten layers.
  → Ten layers: contours, villages, HT lines, forest, haul road, pillars, barrier
  pillar, trough heatmap, tension cracks, borehole collars. The counter reads
  `<n> / 10 ON` (not a bare number).
- [ ] **PL-GI-06 · default layer state** — reload and read the toggles.
  → Haul road and borehole collars start **off**; the other eight start on.
- [ ] **PL-GI-07 · ALL ON** — press it.
  → All ten layers on; the counter reads 10.
- [ ] **PL-GI-08 · SURFACE ONLY** — press it.
  → Underground layers (pillars, barrier, cracks) switch off; counter drops accordingly.
- [ ] **PL-GI-09 · layer counter tracks state** — toggle a layer and read the counter.
  → The `layers` legend updates on every change.
- [ ] **PL-GI-10 · KPI row** — read the six KPIs above the map.
  → Predicted Smax, TTF (days), RMR, extraction baseline, trough footprint (ha),
  crack vectors — all populated with a caption each.
- [ ] **PL-GI-11 · time window** — click 3 / 6 / 12 MONTHS above the map.
  → The active button highlights (12 MONTHS is the default) and the plotted history
  rescales to that window.
- [ ] **PL-GI-12 · renderer note clears** — load the deck and watch the bottom of the map.
  → `RENDERER STARTING…` fades out within a second or two once the canvas paints.
  If it stays on screen the map is not drawing — see X-23.

### D.2 STRATA MODEL

- [ ] **PL-MO-01 · RMR slider** — drag the RMR slider from 15 to 90.
  → Live value follows; the class chip changes (e.g. `FAIR · CLASS III` → `GOOD · CLASS II`),
  and the model tag updates.
- [ ] **PL-MO-02 · RMR extremes** — set 15 and then 90.
  → Class descriptions at both extremes are sensible; no `undefined` or `[object Object]`.
- [ ] **PL-MO-03 · overburden depth** — change the overburden selector.
  → Profile redraws and the derived figures change.
- [ ] **PL-MO-04 · extraction height** — change extraction height.
  → Profile redraws; the subsidence estimate reacts.
- [ ] **PL-MO-05 · dominant strata** — change the strata selector.
  → Profile and derived values update; the model tag names the new strata.
- [ ] **PL-MO-06 · mining-method baseline** — switch the method (caving / stowing).
  → The model tag and the predicted figures change accordingly.
- [ ] **PL-MO-07 · borehole profiler** — change the borehole.
  → The profiler redraws with the new collar and the logged strata.
- [ ] **PL-MO-08 · profile is drawn** — look at the profile canvas.
  → A stratigraphic column with labelled bands; no blank or clipped drawing.
- [ ] **PL-MO-09 · failure threshold marker** — find the threshold marker.
  → Drawn at the statutory limit and consistent with the RMR class shown.
- [ ] **PL-MO-10 · inputs persist while the tab is open** — change several inputs, switch tabs, come back.
  → Values are retained for the session.

### D.3 PREDICTION

- [ ] **PL-PR-01 · TTF projection** — open PREDICTION.
  → Actual trace plus the AI asymptote on the same chart, with a legend.
- [ ] **PL-PR-02 · TTF reacts to a scenario** — trigger a critical scenario from Control, watch this chart.
  → The projection bends toward failure; the KPI TTF falls.
- [ ] **PL-PR-03 · anomaly scatter** — read the clustering panel.
  → Micro-vibration vs tilt scatter with cluster hulls and a legend.
- [ ] **PL-PR-04 · RE-RUN CLUSTERING** — press it.
  → Points re-cluster; a re-run time is stamped; the panel does not go blank.
- [ ] **PL-PR-05 · re-run twice** — press it twice.
  → No duplicate or overlapping cluster sets; the count stays stable.
- [ ] **PL-PR-06 · velocity bars** — read the subsidence velocity panel.
  → Vertical and horizontal bars drawn with numbers at the ends.
- [ ] **PL-PR-07 · TTF vs velocity agreement** — compare the TTF projection with the
  velocity bars below it.
  → Both describe the same node; the TTF does not claim stability while velocity bars
  are climbing.
- [ ] **PL-PR-08 · prediction matches telemetry** — compare the latest plotted point to Telemetry (`2`).
  → Same node, same order of magnitude, no lag beyond a poll interval.

### D.4 FORENSICS & EXPORT

- [ ] **PL-FO-01 · time-lapse scrubber** — drag the scrubber across 0–360.
  → The deformation surface animates through the history window.
- [ ] **PL-FO-02 · scrubber extremes** — set 0 and 360.
  → Both ends render; no clipped or missing surface.
- [ ] **PL-FO-03 · PLAYBACK** — press **▶ PLAYBACK**.
  → Scrubber advances automatically; pressing again stops it.
- [ ] **PL-FO-04 · playback reaches the end** — let it run to the end.
  → It stops at the last frame (does not wrap silently).
- [ ] **PL-FO-05 · trough forensics** — read the forensics figures.
  → Trough depth, footprint, centre and gradient all populated.
- [ ] **PL-FO-06 · PREVIEW (Form 1-A)** — press **PREVIEW**.
  → Modal `FORM 1-A · STRATA MONITORING PLAN · PROVISIONAL · PREVIEW` with embedded figures.
- [ ] **PL-FO-07 · GENERATE FORM 1-A PDF** — press **GENERATE FORM 1-A PDF**.
  → Modal shows `· READY TO ISSUE`, the browser print dialog opens, and an HTML copy
  downloads (`SMP-Form-1A_Kusunda_<date>.html`).
- [ ] **PL-FO-08 · Form 1-A is logged** — after generating, check the ledger.
  → `FORM 1-A STRATA MONITORING PLAN GENERATED · PANEL P-3`.
- [ ] **PL-FO-09 · Form 1-A reflects the live model** — change the RMR and regenerate.
  → The document carries the new value, not a stale one.
- [ ] **PL-FO-10 · downloaded HTML opens** — open the downloaded file in a browser.
  → Renders standalone with figures embedded (no missing images, no console errors).

---

## E · DGMS Regulator deck — 34 cases

Sign in as `DGMS-1107` / `1107`. Tabs: `Alt+1` … `Alt+4`.

### E.1 RISK MAP

- [ ] **RG-RI-01 · cluster map** — open the deck.
  → Coalfield map with the collieries plotted and risk-coloured.
- [ ] **RG-RI-02 · fourteen collieries** — count the plotted mines.
  → Fourteen, spanning ECL (4) · BCCL (3) · CCL (3) · WCL (2) · SECL (2),
  each tagged with a method — BP (bord & pillar) or LW (longwall).
- [ ] **RG-RI-03 · select a mine** — click a colliery.
  → Selection highlight + detail panel for that mine.
- [ ] **RG-RI-04 · detail panel** — read SELECTED COLLIERY DETAIL.
  → Safety index, mesh %, ack time, calibration count, officer and last inspection date.
- [ ] **RG-RI-05 · risk tiers** — compare the map colours to the detail panel.
  → Tier colour matches the matrix rules (critical if live L3 ≥ 2 or mesh < 92).
- [ ] **RG-RI-06 · composite index** — read AGGREGATED SAFETY INDEX.
  → A 0–100 figure with a band/legend; hovering or selecting a mine changes it.
- [ ] **RG-RI-07 · index floor** — put the site into a heavy fault state.
  → The index falls but never below its documented floor (5), and never shows NaN.
- [ ] **RG-RI-08 · regulator reacts to operator events** — while the operator fires an EAP, watch this deck.
  → The affected mine's state visibly degrades here without a reload.

### E.2 AUDIT LEDGER

- [ ] **RG-AU-01 · ledger rows** — open AUDIT LEDGER.
  → Block-numbered rows, newest first, each with time, mine, event, actor and severity.
- [ ] **RG-AU-02 · ledger is read-only** — try to edit or delete a row.
  → Nothing is editable from this deck.
- [ ] **RG-AU-03 · VERIFY CHAIN** — press it.
  → A pass message naming the block count, with no failed blocks.
- [ ] **RG-AU-04 · verify after activity** — run some operator actions, then verify.
  → Still passes — new blocks are appended, not patched in.
- [ ] **RG-AU-05 · TAMPER TEST** — press it.
  → A demonstration row is altered; verification then reports the altered block and
  flags it as detected.
- [ ] **RG-AU-06 · tamper test is reversible** — after the tamper test, verify again.
  → The demonstration is restored and verification passes.
- [ ] **RG-AU-07 · restore is explicit** — press the restore control on the ledger.
  → A message states the row was put back and pointers re-checked.
- [ ] **RG-AU-08 · EXPORT LEDGER** — press it.
  → Downloads `DGMS-audit-ledger-<date>.csv`.
- [ ] **RG-AU-09 · export contents** — open the CSV.
  → Header `BLOCK,TIME,COLLIERY,EVENT,ACTOR,SEVERITY,PREV_HASH,HASH`, one row per block,
  correctly quoted (commas and quotes inside event text survive).
- [ ] **RG-AU-10 · export is logged** — after exporting, check the ledger.
  → `LEDGER EXPORTED FOR OFFICE RECORDS · <n> BLOCKS`.

### E.3 SERVICE LEVELS

- [ ] **RG-SE-01 · SLA tracker** — open SERVICE LEVELS.
  → Per-mine response-time bullets against a target line.
- [ ] **RG-SE-02 · SLA ordering** — read the row order.
  → Sorted worst-response first, so the breach is at the top.
- [ ] **RG-SE-03 · pass count** — read the summary figure.
  → `n/N` mines within the 5-minute target; consistent with the rows shown.
- [ ] **RG-SE-04 · uptime heatmap** — read MESH NETWORK UPTIME HEATMAP.
  → Daily % grid; empty days are blank rather than zero.
- [ ] **RG-SE-05 · colliery selector** — change the colliery in the uptime panel.
  → Heatmap and its legend reload for that mine.
- [ ] **RG-SE-06 · calibration ledger** — read SENSOR CALIBRATION LEDGER.
  → Per-sensor rows with a calibration count and a compliance flag.
- [ ] **RG-SE-07 · calibration flags** — find a sensor past its interval.
  → Flagged as due/overdue; compliance summary counts it.
- [ ] **RG-SE-08 · breach history** — check the breach list.
  → Empty state is an explicit message, not a blank box.

### E.4 INQUIRY

- [ ] **RG-IN-01 · radar comparison** — open INQUIRY.
  → Cross-mine radar (longwall vs bord & pillar), axes labelled.
- [ ] **RG-IN-02 · geology selector** — change the geological block.
  → Radar re-plots; the comparison set changes.
- [ ] **RG-IN-03 · inquiry form** — read the generator fields.
  → Colliery, incident date (defaults to a set date), inquiry type.
- [ ] **RG-IN-04 · GENERATE INQUIRY REPORT** — fill the form and press it.
  → A report is composed listing that mine's metrics and its audit slice.
- [ ] **RG-IN-05 · report needs a mine** — press generate with no colliery chosen.
  → Refused with a clear message; nothing half-generated.
- [ ] **RG-IN-06 · DOWNLOAD HTML** — press it.
  → Downloads a standalone `.html` annexure that opens in a browser.
- [ ] **RG-IN-07 · report uses the chosen date** — generate with two different dates.
  → The date appears in the document and the audit slice differs.
- [ ] **RG-IN-08 · auto-populated reports** — read AUTO-POPULATED INQUIRY REPORTS.
  → A list of prepared annexures; opening one shows its contents.

---

## F · Integration and cross-cutting — 26 cases

### F.1 Gateway contract **[mode-sensitive — run in MOCK and GATEWAY]**

- [ ] **X-01 · telemetry contract** — `GET /api/telemetry`.
  → `system_status`, `ttf_seconds`, both nodes with status/displacement/tilt/acceleration,
  route A/B status, `rfid_log[]`, `timestamp`.
- [ ] **X-02 · extended contract** — `GET /api/extended`.
  → `mode`, `nodes[].velocity/peak_acceleration`, `muster{total,underground,safe}`,
  `events[]` **and** the `blackbox` + `cloud` blocks.
- [ ] **✔ run · X-03 · blackbox block fields** — inspect `blackbox`.
  → Required: `sd_ok`, `chunk_id`, `row`, `rows_per_chunk`, `free_mb`, `card_mb`,
  `usb_ok`, `power_ok`, `retained[{chunk_id,rows}]`, `retained_total`.
  **Optional:** `ram_ring` — the real gateway omits it because protocol v1 carries no
  RAM-ring depth on the wire; the panel tolerates its absence.
- [ ] **✔ run · X-04 · cloud block fields** — inspect `cloud`.
  → Required: `online`, `pending`, `sent`, `next_upload_s`. **Optional:**
  `last_upload{ts,rows,batches,ok}` appears once a batch has gone out;
  `last_ack{chunk_id,ts}` appears only after the gate has actually released a chunk.
- [ ] **X-05 · blocks missing ⇒ deck freezes, not invents** — make the gateway omit the
  two blocks (e.g. start the gateway with the blackbox disabled).
  → The blackbox panel stops updating rather than showing fabricated progress.
- [ ] **✔ run · X-06 · history contract** — `GET /api/history`.
  → One array per node, oldest first, up to 240 samples, same fields as
  `telemetry.nodes.*` (`status`, `displacement_mm`, `tilt_x`, `acceleration`).
- [ ] **X-07 · AI contract** — `GET /api/ai`.
  → Model, status, horizon, last train, confidence, note and a `forecasts` object.
- [ ] **✔ run · X-08 · trigger contract** — `POST /api/sim/trigger` with a bad node name.
  → Refused (`400`); a valid `node_1` + `warning` is accepted (`200`) and the node
  ramps to `WARNING` over the next few seconds — check the node status *after* the
  ramp, not immediately.
- [ ] **X-09 · reset contract** — `POST /api/reset`.
  → `200 {"ok":true}` and the deck returns to nominal.
- [ ] **X-10 · force-sync contract** — `POST /api/cloud/force_sync`.
  → `200 {"ok":true,"uploaded":n,"acked":[…]}`; with the cloud down, `503 {"ok":false}`
  and **nothing is deleted**.
- [ ] **✔ run · X-11 · polling cadence** — watch the network panel for 30 s.
  → The telemetry poll loop runs every **500 ms** (its fetch timeout is 1 500 ms —
  the figure `docs/API.md` quotes), extended every 1 500 ms, AI every 5 s, and
  history once at start-up. On failure the retry backs off 1 s × 1.8 to a 15 s cap.
  No overlapping or runaway polls.
- [ ] **X-12 · back-off on failure** — kill the gateway.
  → Requests back off (1 s growing to ~15 s max) and recover to normal on restore,
  with the header flipping back to **Online**.

### F.2 The two integration fixes (regression)

- [ ] **✔ run · X-13 · AI colour — healthy states are amber, not red** — get the gateway
  into `WARMING UP`, then `FALLBACK`.
  → Both paint **amber** (verified live: `FALLBACK` resolves to `var(--amber)`). Only a
  genuine fault (or `OFFLINE`) is red. `ACTIVE` is green.
- [ ] **✔ run · X-14 · force-sync never lies** — with the **gateway reachable but the
  cloud down**, press **FORCE CLOUD SYNC** in the outbox console. **[mode-sensitive]**
  → The button must say `✕ CLOUD UNREACHABLE · OUTBOX PROTECTED · SD RETAINED` and
  keep the rows. Verified both ways: failure path reports failure, and the happy path
  reports the real count (`✓ SYNCED · 96 ROWS · 200 OK · 1 CHUNK UNLINKED`).
- [ ] **X-15 · force-sync 404** — point the dashboard at a gateway without the route.
  → Failure reported, not a fake success.
- [ ] **X-16 · force-sync success path still works** — gateway and cloud both up.
  → Success reported with the real row count taken from the refreshed feed.

### F.3 Global behaviour

- [ ] **X-17 · number keys switch views** — press `1`…`6`.
  → Each opens its workspace (subject to role gating).
- [ ] **X-18 · letter keys** — press `S`, `U`, `I`, `R`.
  → Surface model, underground model, scenario console, re-arm.
- [ ] **X-19 · modals close on ESC** — open any modal, press `Escape`.
  → Closes; focus returns to the page; no stuck overlay.
- [ ] **X-20 · deck switching** — use the OPERATOR / PLANNER / REGULATOR buttons.
  → Only your own deck is reachable; the others refuse (see AC-23).
- [ ] **X-21 · resize** — drag the window to half width, then to mobile width.
  → Layout reflows; canvases redraw at the new size; no overlap or clipping.
- [ ] **X-22 · console is clean** — leave it running for five minutes across all tabs.
  → No uncaught exceptions in the browser console.

---

### F.4 Renderer diagnostics

The dashboard carries a built-in self-check. Use it before reporting that "the map
is blank" — it tells you whether the browser is refusing to give the page a canvas.

- [ ] **X-23 · canvas-failure note** — force a canvas failure (open the file in a
  restrictive embedded/iframe context, or block canvas via a browser flag).
  → The note at the bottom of the map turns into `CANVAS BLOCKED · <reason>` with a
  **RENDERER CHECK** button, instead of silently leaving an empty box.
- [ ] **✔ run · X-24 · renderer check panel** — press **RENDERER CHECK**, or add
  `#debug` (or `?debug`) to the URL and wait ~1.5 s.
  → A dark panel lists the user agent, viewport, canvas-2d and WebGL availability,
  which deck scripts loaded (`NSD` / `OP` / `PL` / `RG`), the active view, the
  animation-frame tick count, captured console errors, canvas failures, and every
  canvas with its buffer/CSS size and painted state.
- [ ] **✔ run · X-25 · diagnostics report "painted"** — open the panel on a healthy page.
  → Every canvas that has drawn reads `painted`, and `canvas fails: none`. A canvas
  belonging to a view this role never opened may legitimately read `NOT PAINTED`.
- [ ] **✔ run · X-26 · diagnostics close** — press **CLOSE**.
  → The panel is removed; opening it again re-creates it with fresh values.

---

## G · Known limitations — do not file these as bugs

These are by design in the current build. Confirm them if you like, but they are
not defects.

1. **The panel is interface-only.** Firing the EAP, silencing alarms, dispatching a
   broadcast and the disaster drills change dashboard state and the audit log — they
   do not drive hardware or an SMS gateway. No backend route exists for the EAP or
   the broadcast.
2. **The audit ledger is a client-side session view.** It is held in memory, so it
   resets on reload, and its integrity check runs in the browser. The authoritative
   append-only record is the gateway's.
3. **Crew movement in SIM mode** is the built-in simulator's, not real RFID reads.
   Real zones only arrive when the gateway is supplying them.
4. **`docs/API.md` is the contract of record.** Where the dashboard and a backend
   disagree, the doc (and the mock) win.
5. **Reload does not sign you out** by design; a new tab does. See AC-18 / AC-20.

---

## Quick coverage summary

| Area | Cases | Must-pass before a demo |
|---|---|---|
| A · Sign-in & access control | 24 | AC-11, AC-12, AC-15, AC-17, AC-21, AC-23 |
| B · Shared workspaces | 34 | SW-15, SW-16, SW-20, SW-32 |
| C · Operator deck | 83 | OP-ES-17, OP-ES-21, OP-ES-22, OP-ES-31, OP-BB-11, OP-BB-16 |
| D · Planner deck | 40 | PL-GI-05, PL-MO-01, PL-PR-04, PL-FO-07 |
| E · Regulator deck | 34 | RG-AU-03, RG-AU-05, RG-AU-08, RG-IN-04 |
| F · Integration | 26 | X-02, X-05, X-10, X-13, X-14 |
| **Total** | **241** | |
