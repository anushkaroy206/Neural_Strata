# Neural Strata — Frontend Change Request

## Work order CR-01 → CR-14 · `neuralstrata-frontend/index.html`

| | |
|---|---|
| **For** | The frontend developer |
| **File** | `neuralstrata-frontend/index.html` — 13,062 lines · 1.04 MB · one file · 11 inline `<script>` blocks |
| **Baseline** | `python3 tools/check_claims.py` → **1 / 14 rules pass** (measured 18 Sep 2026, 09:02) |
| **Target** | **14 / 14 rules pass**, then the manual checks in §7 |
| **Verify** | `cd neuralstrata-frontend && python3 tools/check_claims.py` — prints PASS/FAIL per rule with line numbers and exits 1 on failure |
| **Ground rule** | **The live layer (script block 4) works. Do not restructure it.** Every endpoint and field it uses was verified field-for-field against the running backend. The changes below are additions, deletions and string replacements — not a rewrite. |
| **Effort** | ≈ 2 days. **CR-01 → CR-07 are 7 items / ≈ 9 hours** and they remove the claims the system cannot back. Do those first. |

> **Read this first.** Everything in Part A is a change to something the UI *asserts*. None of it is a bug in your code — the patterns are well written. The problem is that the screen makes claims the hardware and the backend cannot support, and on stage a judge tests claims, not code.

---
# 1 · How to use this document

1. **Run the checker first**, before touching anything, so you see the 13 failures for yourself.
2. Work **CR-01 → CR-14** in order. They are ordered by risk, not by file position.
3. After each rule, re-run the checker. It should go from 1/14 → 2/14 → … Each rule tells you exactly which lines are still failing.
4. §6 lists what **not** to touch. §7 has the manual checks the script cannot do.
5. If you disagree with a rule, change it in `tools/check_claims.py` **and tell the team why** — do not silently skip it. Two rules are deliberately "dual acceptance" (you may delete the feature *or* label it honestly), and that is a judgement call worth recording.

**The checker lives in the repo** at `neuralstrata-frontend/tools/check_claims.py`. It has no dependencies and runs in under a second.

---

# 2 · Reference facts — the truth table

Use these values. They come from the hardware pin freeze, the frozen tunnel map and the live backend.

| | **Truth** | Where it comes from |
|---|---|---|
| Node MCU | **ESP32** (not STM32) | hardware doc §A.1 |
| Radio between nodes | **ESP-NOW, 2.4 GHz** (no LoRa, no SX1278, no 433 MHz) | hardware doc §A.1 |
| Sensor nodes | **4** — `node_1`…`node_4` | `tunnel_map.json` |
| Nodes reporting **today** | **2** — `node_1`, `node_2` (`node_3`/`node_4` are `"active": false`) | `tunnel_map.json` |
| Level hubs (MCUs) | **1 per level = 2 total** | your standing constraint |
| Junction LEDs | **passive WS2812B strips** on J1–J4 — no MCU, no telemetry | hardware doc §A.2 |
| Tunnel layout | **1 tunnel per level** — J1/J2 on level A, J3/J4 on level B, cross-link **J2 ↔ J3** | your decision |
| Exits | **2** — EXIT1 (west), EXIT2 (east) | `tunnel_map.json` |
| Zones | **4** — A1, A2 (level 1), B1, B2 (level 2) | `tunnel_map.json` |
| Scale | **1 cm diorama = 10 m** | `tunnel_map.json` |
| Site centre | 23.643 N, 85.318 E (**placeholder offsets**) | `tunnel_map.json` |
| Solar | **none** | your decision |
| Battery monitoring | not fitted — nodes are USB powered | hardware doc power table |
| GPS / DGPS | **no module** — GPS is deferred; georeferencing is a fixed baseline + offsets | your decision |
| RTK / survey grade | **none claimed** | — |
| Cloud | local PostgreSQL = source of truth; free hosted Postgres = derived replica | `Neural_Strata_Dual_Database.md` |
| Backend version | **v4.4** | `app.py` |
| Alert channels | local siren/LEDs, dashboard, **Telegram**; SMS modem **not fitted** | `GAP_FIXES.md` |

**Two fields that ARE real and should stay** — do not over-delete: `rssi` and `batt` are in the planned Phase 1 node payload (`uint32_t ms; float acc; uint16_t vib; float micro; float macro; int8_t rssi; uint16_t batt; uint8_t flags;`). They are legitimate *columns* for the four real nodes. What is fabricated is the **values for ten nodes that do not exist**. Keep the fields; delete the fictional rows.

---
# 3 · The two layers in this file

This matters because it explains where each change belongs.

| Block | Lines | Size | `fetch()` | What it is |
|---|---|---|---|---|
| **4** | 3,319–5,255 | 88 KB | **2** | **THE LIVE LAYER** — main dashboard, telemetry poll, charts, AI panel, SIM fallback, 2D evac map + `MINE` graph |
| 5 | 5,256–9,091 | 549 KB | 0 | three.js bundle (53 % of the file) |
| 6 | 9,097–9,711 | 31 KB | 0 | Deck core — `window.NSD`, the RAM ledger |
| 7 | 9,712–10,166 | 22 KB | 0 | Login / RBAC |
| 8 | 10,167–11,288 | 55 KB | 0 | Operator deck |
| 9 | 11,289–12,343 | 50 KB | 0 | Planner deck |
| 10 | 12,344–13,060 | 36 KB | 0 | Regulator deck |

**Blocks 6–10 (3,963 lines) never contact the server.** They render a self-contained simulation. That is why so many changes below are "delete or label" rather than "wire up": wiring 4,000 lines to real data is a project, and you do not have that time before the demo.

### Endpoints — live vs planned

| Endpoint | Today | Notes |
|---|---|---|
| `GET /api/telemetry` | **200** | `system_status`, `ttf_seconds`, `nodes{}`, `underground{}`, `rfid_log[]` |
| `GET /api/extended` | **200** | `mode` (`LIVE`/`SIM`), `nodes{}.{velocity,peak_acceleration}`, `muster`, `events[]` |
| `GET /api/history` | **200** | `{node_1:[…], node_2:[…]}` |
| `GET /api/ai` | **200** | `model`, `status`, `horizon`, `last_train`, `confidence`, `note`, `forecasts` |
| `POST /api/sim/trigger` | **200** | `{node, severity}` |
| `POST /api/reset` | **200** | — |
| `GET /api/map` | **404** | Phase 7 — the georeferenced payload `gis-map.js` already expects |
| `GET /api/alerts/channels/status` | **planned** | Phase 8 — for the delivery-ledger chip |
| `POST /api/alerts/broadcast` | **planned** | Phase 8 — for CR-07 |
| `GET /api/audit/export.csv` | **built** | `roles.py`; works today, no database required |
| `GET /api/whoami` | **built** | `roles.py`; returns the server-side role |
| `GET /api/sync` | **404** | Phase 9 |

---
# 4 · The change list

| CR | Priority | Change | Where | Effort |
|---|---|---|---|---|
| **CR-01** | **P0** | Remove/relabel both 12-node mesh models | 3339, 10201, 10204–10230 | 3 h |
| **CR-02** | **P0** | Remove `solar` (9 lines) | 1657, 1749, 3031, 3349, 4350, 10209, 10517, 11190, 11259 | 1 h |
| **CR-03** | **P0** | Replace hardcoded `12/12` with API-derived values | 1234, 1276, 1534, 1650, 1991, 2031, 3883, 3884, 11151, 11153 | 1 h |
| **CR-04** | **P0** | `STM32` → `ESP32` | 3331, 3332 | 15 min |
| **CR-05** | **P0** | `433 MHz` / `SX1278` → ESP-NOW 2.4 GHz | 1277, 1658, 1751, 1970, 4346, 10026, 10479, 10780 | 1 h |
| **CR-06** | **P0** | Delete the hash-chained ledger claim, or point it at the real export | 1279, 1363, 2518, 9094, 9757, 9771, 12252, 12347 | 2 h |
| **CR-07** | **P0** | Wire the SMS panel, or label it a preview | ~11110–11135 | 3 h |
| **CR-08** | P1 | Remove hardcoded PINs | 9751, 9760, 9769 | 1 h |
| **CR-09** | P1 | Read the node set from the API | 3323 | 3 h |
| **CR-10** | P1 | Align the graph with the frozen layout | 1904, 4644–4700 | 4 h |
| **CR-11** | P1 | Remove or label the seeded multi-mine history | 9386–9401 | 2 h |
| **CR-12** | P1 | `v4.2` → `v4.4`; remove `DGPS RL +200 m` | 1266, 1364, 2265, 9724, 11934, 12957 | 30 min |
| **CR-13** | P2 | Link the PWA | `<head>` | 30 min |
| **CR-14** | P2 | Promote the existing LIVE/SIM indicator into the decks | 1679, 4004, 4563 + decks | 1 h |

---
# 5 · Change cards

## CR-01 · Remove or relabel both 12-node mesh models — **P0 · 3 h**

**Where** — Model 1: `const MESH_CELLS=` at **3339** (12 cells E1–E12, used at 3652, 3867, 4179, 4214, 4223, 4234). Model 2: `const RISK=[…]` at **10201** and the `NODES.push({…})` loop at **10204–10230** in the operator deck, plus its tick at ~11165–11195.

**Why** — Your build has **4 sensor nodes and 2 level hubs**. There are no relay cells, no battery, no solar, no per-cell RSSI, no packet counters, no latency measurement, and no mesh node that can "fail and reroute". Model 2 is worse than Model 1 because it is *live-simulated*: nodes creep, batteries drain (`n.batt - (n.solar>13?0.000004:0.000012)*dt*60`), signal wanders, and the outage state machine writes fabricated entries into the audit ledger — including *"UPLINK RESTORED · n BUFFERED PACKETS FLUSHED IN 1.2 s"*.

**Do** — pick one:
- **(a) Delete** both models and the panels built on them; or
- **(b) Relabel** the panel `ILLUSTRATIVE LAYOUT · NOT INSTRUMENTED` and make sure nothing in it is styled like live telemetry. The layout drawing is genuinely useful for the narrative — a labelled schematic is an asset, a simulated one is a liability.

**Accept** — `CR-01` passes when neither declaration exists, **or** the label is present.

**Watch out** — Model 2's `const NODES` is inside an IIFE opening at line 10172, so deleting it does **not** affect the live `const NODES` at 3323. Verified: all 11 blocks concatenated in document order pass `node --check` (exit 0), so no block is currently dead.

---

## CR-02 · Remove `solar` — **P0 · 1 h**

**Where** — 1657 (*"12 solar child nodes"* hintbox), 1749 (`Battery · solar` row), 3031 (solar-panel mesh in the 3D surface scene), 3349 (generator), 4350 (`batt V · solar W · rssi dBm`), 10209 (ops model), 10517, 11190 (battery-drain formula), 11259.

**Why** — No solar was procured and no panel exists. Any solar figure on screen is invented, and the drain formula makes it *look* measured.

**Do** — delete the field, the row, and the panel mesh. Keep `rssi` and `batt` if you like (see §2) — but only for the four real nodes, and only once Phase 1 sends them.

**Accept** — `CR-02` passes when `solar` appears nowhere.

---

## CR-03 · Replace the hardcoded `12 / 12` counts — **P0 · 1 h**

**Where** — `12 / 12` at **1276** (`lgStatMesh`), **1534** (`ccSub`), **1650** (`meshOn`), **3883–3884**. `12/12` at **1234** (login plate), **1991** (`opChMesh`), **2031** (`opTopoTag`), **11151** (alarm text), **11153** (ledger text).

**Why** — Today the truth is **2 / 4**: `node_3` and `node_4` are `"active": false`. Hardcoding a count that must match reality is the bug, not the number.

**Do** — derive from the API, once, and reuse:
```js
const ids   = Object.keys(t.nodes||{});              // ['node_1','node_2', …]
const live  = ids.filter(id => t.nodes[id].status !== 'OFFLINE');
const label = `${live.length} / ${ids.length}`;      // renders "2 / 4" today
```
For the deck strings, prefer *"RING A · N OF M NODES REPORTING"* over a bare fraction — it reads as status rather than as a spec.

**Accept** — `CR-03` passes when neither string remains.

---

## CR-04 · `STM32` → `ESP32` — **P0 · 15 min**

**Where** — 3331, 3332.
```js
node_1:{name:'Node 1 — Mesh E5', sub:'STM32 MONUMENT · NORTH GRID · OVER DRIFT A (LEVEL 1)'}
node_2:{name:'Node 2 — Mesh E8', sub:'STM32 MONUMENT · WEST GRID · OVER DRIFT B (LEVEL 2)'}
```
**Why** — The nodes are ESP32. The first question a hardware judge asks is *"show me the STM32"*, and there isn't one.

**Do** — replace `STM32` with `ESP32`, and update the grid/level wording to match the frozen map (`N1 — Tunnel A · near` / `N2 — Tunnel A · collapse face`).

**Accept** — `CR-04` passes when `STM32` appears nowhere.

---

## CR-05 · `433 MHz` / `SX1278` → ESP-NOW 2.4 GHz — **P0 · 1 h**

**Where** — 1277, 1658, 1751, 1970, 4346, 10026, 10479, 10780.

**Why** — The prototype uses **ESP-NOW** between nodes and the hub; LoRa/SX1278 is explicitly deferred (*"Roadmap. ESP-NOW + the relay carries the prototype."*). The *mechanism* your deck describes elsewhere — relay, ring, reroute — is correct; only the radio is wrong.

**Do** — replace with `ESP-NOW · 2.4 GHz`. Where the sentence describes range or penetration, keep the honest framing: *"short-range 2.4 GHz locally; LoRa is the production delta for full-panel range."*

**Accept** — `CR-05` passes when neither `433 MHz` nor `SX1278` appears.

---

## CR-06 · The hash-chained ledger — **P0 · 2 h**

**Where** — implementation at 9356–9390; claims at **1279** (*"hash-chained · tamper-evident"*), **1363**, **2518** (panel heading `IMMUTABLE INCIDENT & RESPONSE AUDIT LOG · HASH-CHAINED (READ-ONLY)`), **9094**, **9757**, **9771** (the regulator role's privilege list), **12252**, **12347**.

**Why** — It is an array in browser RAM hashed with **32-bit FNV**, with **no `localStorage` or `sessionStorage` anywhere** (verified). It is **wiped on every reload**, and anyone can rewrite it from devtools in ten seconds. `LED.verify()` proves only that an in-memory array is internally consistent. Claiming *immutable* and *tamper-evident* to a DGMS officer is a regulatory claim the system cannot support — worse than having no ledger.

**Do** — pick one:
- **(a) Point the panel at the real artefact.** `GET /api/audit/export.csv` is **built and tested** (`roles.py`, 11/11 green) and works with **no database**. It returns CSV with `kind,time,severity,node_id,zone,text,sms_dispatched,email_dispatched,telegram_dispatched,acknowledged,user,role,delivery_ref,error`. Render those rows in the same panel, with an **EXPORT CSV** button hitting that URL.
- **(b) Delete the claim words** and call it *"SESSION ACTIVITY LOG (not persisted)"* — honest, and still useful on screen.

**Accept** — `CR-06` passes when the claim words are gone **or** `/api/audit/export.csv` is referenced.

---

## CR-07 · The SMS panel — **P0 · 3 h**

**Where** — `wireSms()` at ~**11110–11135**.

**Why** — Verified line by line: **no `fetch(`, no `post(`, no `apiUrl` anywhere in the function.** It mints `SMS-xxxxxx`, prints *"delivered to Overman · S. Das · Mining Sirdar · R. Sen · Colliery Rescue Room…"*, claims *"gateway ACK received, no queue backlog"*, writes `BROADCAST … DISPATCHED` into the ledger, and sets the chip to `DISPATCHED 14:22:07`. Nothing leaves the browser. A judge who is told the SMS was dispatched while their phone stays silent will trust nothing else on the screen.

**Do** — pick one:
- **(a) Wire it (preferred, and cheap now).** The modem is no longer being bought, so the honest channel set is **local, dashboard, Telegram, email**. The Telegram channel is built and tested (`alerts/channels_telegram.py`, 14/14 green):
  ```js
  const r = await fetch(apiUrl('/api/alerts/broadcast'), {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ text: t.t, recipients: rcpNames, severity: sev })
  }).then(r=>r.json());
  // render r.deliveries[] — one row per channel, each showing ok / ref / error
  ```
  Show the **per-channel result**, including the honest line **`sms · not fitted`**. A real delivery line with one `✗` beats a fabricated all-green.
- **(b) Label it.** If wiring does not land in time, retitle the panel `BROADCAST PREVIEW · NOT DISPATCHED`, remove the *"gateway ACK received"* sentence, and change `DISPATCHED` to `COMPOSED`. The panel is still useful for demonstrating message templates.

**Accept** — `CR-07` passes when `wireSms()` either calls the backend **or** carries a preview label inside the function.

---

## CR-08 · Remove the hardcoded PINs — **P1 · 1 h**

**Where** — 9751, 9760, 9769 — three named people with PINs (`4471`, `2290`, `1107`) in client source.

**Why** — Anyone can read them with View Source, so it is not access control. The real control exists: `roles.py` returns **403** on every mutating request from a regulator session (verified live against `app.py` v4.4).

**Do** — keep the role picker and the beautiful login screen. Drop the credential strings, or move them behind an env/`.env`-injected demo file that is not committed. Then bind the chosen role to the server:
```js
await fetch('/api/whoami?view=' + role);      // server tells the page what it is
fetch('/api/telemetry', { headers: { 'X-NS-Role': role } });
```
A regulator session that tries to inject a scenario will get **403** and should show that refusal on screen — *"refused by the server"* is a better demo than a disabled button.

**Accept** — `CR-08` passes when `pin:'` appears nowhere.

---

## CR-09 · Read the node set from the API — **P1 · 3 h**

**Where** — **line 3323**: `const NODES=['node_1','node_2'];`

**Why** — Phase 1 delivers `node_1`…`node_4` across two levels. With a hardcoded literal, the third and fourth nodes can never appear no matter what the backend sends. This is the single change that unblocks the multilevel story.

**Do** —
```js
let NODES = ['node_1','node_2'];                       // bootstrap for SIM mode
// after the first /api/telemetry response:
const ids = Object.keys(t.nodes||{});
if (ids.length) NODES = ids;                            // 4 when Phase 1 lands
```
`NODE_META` should be keyed with a **fallback** for unknown ids so a new node renders with a default card rather than `undefined`.

**Accept** — `CR-09` passes when the literal is gone.

---

## CR-10 · Align the graph with the frozen layout — **P1 · 4 h**

**Where** — the `MINE` graph at **4644–4700**; the legend prose at **1904**.

**Why** — Two things, one of them a hard constraint:
- **Five hubs** (`L1 HUB 1/2/3`, `L2 HUB 1/2`) versus **one MCU per level**. A frontend that draws three hubs on Level 1 will be read as three ESP32s, and the judge will ask to see them.
- **Four exits** (EAST AIR SHAFT, WEST PORTAL, WEST AIR SHAFT, SECONDARY EMERGENCY INCLINE) versus the frozen **two** (EXIT1, EXIT2). Four exits is also a claim about the diorama: there are two.

**Do** — rebuild the graph to the values in **Appendix A** (they are exact and already surveyed in `tunnel_map.json`). Then, at Phase 7, feed it from `/api/map` and keep the local graph only as a clearly-labelled offline fallback — otherwise you will have two routing engines that disagree on stage.

**Accept** — `CR-10` passes when `HUB 2`, `HUB 3` and `SECONDARY EMERGENCY INCLINE` are gone.

---

## CR-11 · The seeded multi-mine history — **P1 · 2 h**

**Where** — `seedLedger()` at **9386–9401**: 16 rows across 11 mines (ARGADA, BISHRAMPUR, DHANSAR, GIDI, GOVINDPUR, JEENAGORA, KATRAS, KORBA, KUSUNDA, SAYAL, SUDAMDIH) including failure events, SLA breaches (`OPERATOR ACK · 22.4 min AFTER ALERT`), an expired certificate (`CALIBRATION CERT EXPIRED · TL-07`) and a monthly uptime figure.

**Why** — Multi-site is architecturally real: every table carries `site_id`, and 100 free cloud projects genuinely means 100 sites. So a multi-mine regulator view is legitimate. **Fabricated incident history for mines you have never monitored is not** — it reads as an audit record and it is invented.

**Do** — seed **one** site, or keep the multi-mine view and add a `DEMO DATA · ILLUSTRATIVE` label to the panel. Note that `KUSUNDA` also appears as the site name in the login chip, so the label route is usually the faster one.

**Accept** — `CR-11` passes when the mine names are gone **or** `DEMO DATA` is present.

---

## CR-12 · Version and survey claims — **P1 · 30 min**

**Where** — `v4.2` at 1364, 2265, 11934, 12957 (and `BUILD` is derived from `fnv('ns-v4-2')` at 9724). `DGPS RL +200 m` at **1266**.

**Why** — `app.py` is **v4.4**. And there is **no GPS module** — georeferencing is a fixed baseline plus offsets — so `DGPS RL +200 m` claims a survey grade you do not have. That is the claim most likely to be probed, because it is the easiest to disprove.

**Do** — `v4.2` → `v4.4` (and update the `fnv` seed so the build chip changes). Replace `DGPS RL +200 m` with `SURVEYED BASELINE + OFFSETS`. Keep `KUSUNDA COLLIERY · ECL · JHARIA` but as a **representative site** if the offsets are still placeholders — the map file says so itself.

**Accept** — `CR-12` passes when neither `v4.2` nor `DGPS` appears.

---

## CR-13 · Link the PWA — **P2 · 30 min**

**Where** — `<head>`.

**Why** — `manifest.webmanifest`, `service-worker.js`, `pwa/pwa.js`, `pwa/pwa.css` and four generated icons already sit in this folder. Four lines activate installability, the offline shell and the stale-data chip.

**Do** —
```html
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#10233f">
<link rel="apple-touch-icon" href="/pwa/icons/apple-touch-icon.png">
<script src="/pwa/pwa.js" defer></script>
```
`pwa.js` is deliberately additive — it targets only `ns-` prefixed ids and wraps `fetch` once to detect stale payloads. It must load **after** the dashboard's own scripts (it uses `defer`, which is enough).

**Accept** — `CR-13` passes when `manifest.webmanifest` is referenced.

---

## CR-14 · Promote the LIVE/SIM indicator — **P2 · 1 h**

**Where** — it already exists at **1679** (`<span>SOURCE <b id="tkMode">…</b></span>`), set at **4004** from `/api/extended`'s `mode` field and at **4563** from whether the poll succeeded.

**Why** — This is the one place where the previous review was wrong and this change request is the corrected version: **you already have the indicator, and it is driven correctly two independent ways.** The problem is only placement — it renders at the same size and weight as the clock beside it, and **blocks 7–10 (the decks) contain no mode indicator at all**. A judge looking at the operator deck's mesh has nothing on that screen telling them it is simulated.

**Do** — keep `tkMode` exactly as it is, and render the same state inside each deck header, at chip size and in the warning colour when the value is `SIM`. Do not build a second mechanism; reuse the existing one.

**Accept** — `CR-14` passes while `tkMode` is present. The visual prominence is a §7 manual check.

---
# 6 · Do not touch

| Keep | Why |
|---|---|
| **Script block 4's polling, cleaning and rendering** | Verified field-for-field against the live backend. `cleanTelemetry`, `cleanNode`, `cleanExtended`, `num()` clamping — all correct. |
| **The "never NOMINAL while a node is CRITICAL" rule** | Correct safety instinct. Keep it. |
| **`esc()` on every interpolation** | Real XSS discipline. Keep it. |
| **The client-side simulator** | Right pattern for a laptop-only rehearsal. Keep it — just make sure the mode chip is visible (CR-14). |
| **The role picker, the access matrix and the three decks** | The SRS requirement, already visualised. CR-08 removes credentials, not the screen. |
| **The scenario console and the four injections** | The `machinery_pass` vs `warning_creep` contrast is the pitch. |
| **The EAP / muster / acknowledgement flow** | Matches mine-emergency practice. |
| **The `MINE` graph's structure** | Only its *topology constants* are wrong (CR-10). The Dijkstra implementation can stay as the offline fallback. |

---
# 7 · Manual checks the script cannot do

Run these on the real page, in a browser, before you call it done.

1. **Open with the backend running** (`python3 serve_with_roles.py --no-serial`). The ticker must read `SOURCE LIVE`, the counters must show **2 / 4**, and no cell panel may show a battery or solar figure.
2. **Open with the backend stopped.** The ticker must read `SOURCE SIM`, the `SIM` chip must be **visible on the deck you are looking at**, and everything on screen must be identifiably simulated.
3. **Reload twice.** Anything that claims to be an audit record must survive the reload or must not claim to be one.
4. **Click through every control as regulator** (`?view=regulator`). Writes must fail with the server's **403**, and the refusal must be visible on screen — not a silently disabled button.
5. **Load on a phone** (or DevTools device mode). Install prompt appears, the layout survives, and the mode chip is still legible at that width.
6. **Check the node cards after Phase 1** — four nodes, two levels, `node_3`/`node_4` appearing when they are marked active.
7. **Every number on screen must be answerable.** Ask of each panel: *which endpoint produced this?* If the answer is "a formula in the frontend", it must be labelled as illustrative.

---
# 8 · Suggested pull-request split

Three PRs, reviewable independently.

| PR | Contents | Checker after |
|---|---|---|
| **PR 1 — "Remove unbacked claims"** | CR-01 → CR-07 | **8 / 14** |
| **PR 2 — "Correct facts and topology"** | CR-08 → CR-12 | **13 / 14** |
| **PR 3 — "Activate PWA + mode chip"** | CR-13, CR-14 | **14 / 14** + §7 manual checks |

**If only PR 1 can land:** that is the one that matters. It removes every claim the system cannot currently support, and it is about nine hours.

---
# 9 · Open questions — the team owes you an answer

These are judgement calls, not defects. Do not guess; ask.

1. **Keep the 3D surface/underground scenes?** They are impressive and they carry the layout narrative, but they are also where the solar panels, the 12-cell mesh and the 433 MHz labels live. Decide: delete the fabrications inside them, or label the whole scene as a visualisation.
2. **Keep the multi-mine regulator view?** If yes, it needs the `DEMO DATA` label; if no, the regulator deck loses its headline feature.
3. **Is `/api/alerts/broadcast` in scope for Phase 8?** If not, CR-07 takes route (b) and the panel is labelled, not wired.
4. **Do the decks get wired to the live API at all before the demo?** If the answer is no, every fabricated value in blocks 6–10 must be visibly labelled — that is the whole of CR-01, CR-11 and CR-14.
5. **Where does the operator deck get its node count from** once Phase 1 ships four nodes — the same `/api/telemetry` call as the main dashboard, or a new endpoint?

---
# Appendix A · Frozen topology — use these exact values

From `neuralstrata-backend-app-v44/tunnel_map.json`. Scale: **1 cm diorama = 10 m**, offsets measured from the SW corner (`d_e_m = x_cm × 10`, `d_n_m = y_cm × 10`). The file itself states: **"OFFSETS ARE PLACEHOLDERS"**.

### Nodes

| id | label | level | zone | d_n_m | d_e_m |
|---|---|---|---|---|---|
| `node_1` | N1 — Tunnel A · near | 1 | A1 | 15 | 20 |
| `node_2` | N2 — Tunnel A · collapse face | 1 | A2 | 15 | 90 |
| `node_3` | N3 — Tunnel B · near | 2 | B1 | 45 | 20 |
| `node_4` | N4 — Tunnel B · far | 2 | B2 | 45 | 90 |

`node_3` and `node_4` carry `"active": false` — **2 nodes report today**, 4 once Phase 1 lands.

### Junctions (passive WS2812B — no MCU)

| id | level | d_n_m | d_e_m |
|---|---|---|---|
| `J1` | 1 | 15 | 45 |
| `J2` | 1 | 15 | 105 |
| `J3` | 2 | 45 | 45 |
| `J4` | 2 | 45 | 105 |

### Exits

| id | label | d_n_m | d_e_m |
|---|---|---|---|
| `EXIT1` | Exit 1 (west) | 30 | 5 |
| `EXIT2` | Exit 2 (east) | 30 | 115 |

### Links (9)

```
EXIT1–J1 · J1–J2 · J2–J3 · J3–J4 · J4–EXIT2
node_1–J1 · node_2–J2 · node_3–J3 · node_4–J4
```
`J2 ↔ J3` is the **cross-link between the two levels** — the only path between them. It is the edge the evacuation story turns on: block Route A and `J2–J3` becomes the only route to EXIT2.

### Zones

| id | name | rect (cm) |
|---|---|---|
| `A1` | Tunnel A · near | 0, 0 → 60, 30 |
| `A2` | Tunnel A · far | 60, 0 → 120, 30 |
| `B1` | Tunnel B · near | 0, 30 → 60, 60 |
| `B2` | Tunnel B · far | 60, 30 → 120, 60 |

### Site

`Jharia panel — 1:1000 diorama` · centre `[23.643, 85.318]` · scale `1 cm = 10 m` · **offsets are placeholders until the diorama is surveyed**.

---
# Appendix B · Acceptance run

```bash
cd neuralstrata-frontend

# 1. the automated rules (13 failures expected at baseline)
python3 tools/check_claims.py

# 2. machine-readable, for CI
python3 tools/check_claims.py --json | python3 -m json.tool

# 3. the page still parses — no script block silently dead
python3 - <<'EOF'
import re
src = open('index.html', encoding='utf-8').read()
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S)
open('/tmp/ns_all.js','w').write("\n;\n".join(blocks))
print("blocks:", len(blocks))
EOF
node --check /tmp/ns_all.js          # must print nothing and exit 0

# 4. the live contract still matches (backend must be running)
curl -s localhost:5000/api/telemetry | python3 -m json.tool | head -20
curl -s localhost:5000/api/whoami
curl -s -o /dev/null -w '%{http_code}\n' -X POST 'localhost:5000/api/reset?view=regulator'   # 403
```

**Definition of done**

- [ ] `python3 tools/check_claims.py` reports **14 / 14**
- [ ] `node --check` on the concatenated blocks exits 0
- [ ] All seven manual checks in §7 pass
- [ ] Every number on screen can be traced to an endpoint or is labelled illustrative
- [ ] The live layer (block 4) still renders against the real backend, unchanged in behaviour

---

*Baseline measured 18 Sep 2026. Every line number in this document was produced by exact match against `neuralstrata-frontend/index.html`, and every backend claim was executed against `app.py` v4.4. Where a line number differs from the earlier review (for example `const NODES` is at **3323**, not 3325), this document is the corrected one — the checker is the source of truth.*
