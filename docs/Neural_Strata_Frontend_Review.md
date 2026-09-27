# Neural Strata — Frontend Review

## Verified defect list with line numbers · v1.1

| | |
|---|---|
| **Document** | `Neural_Strata_Frontend_Review.md` · **v1.1** (supersedes v1.0) |
| **Reviewed** | `neuralstrata-frontend/index.html` — 13,062 lines · 1.04 MB · **one file** · 11 inline `<script>` blocks · three.js bundled inline (549 KB = 53 % of the file) |
| **Method** | Every endpoint, field and string the UI reads was extracted and executed against the **live** `app.py` v4.4, then re-verified in a second pass with exact counts and line numbers. §0 records what that second pass changed. |
| **For** | The frontend developer — every item below has a line number and a runnable check. |
| **Verdict** | The **live layer talks to the backend correctly and defensively**. The **role-deck layer behind the login talks to nothing at all** — 3,963 lines with **zero** network calls. |
| **Corrections** | **6 numbers in v1.0 were wrong and 1 claim was wrong in substance.** All corrected below, and §0 lists them explicitly so you can check the rest of this document against the file rather than against me. |

> **The headline for the dev.** Eleven script blocks. **One of them talks to the server.** The other ten — including the entire login/RBAC layer, the operator deck, the planner deck and the regulator deck — render a self-contained simulation of a mine that does not exist: 12 LoRa nodes on STM32 monuments with solar panels and batteries, five level hubs, four exits, and a tamper-evident audit ledger that lives in browser RAM.

---

# §0 · Corrections from the first pass

Read this first. In v1.0 I estimated several counts by scanning rather than counting, and got six of them wrong. They are corrected here, and every figure in this document has now been produced by an exact match. **One v1.0 claim was wrong in substance — §0.2 — and the recommendation that depended on it has been rewritten.**

## 0.1 Numbers that changed

| v1.0 said | **Verified truth** | Lines |
|---|---|---|
| "433 MHz — 7 occurrences" | **6** | 1277, 1658, 1751, 1970, 10026, 10780 |
| "solar — 11 references" | **9** | 1657, 1749, 3031, 3349, 4350, 10209, 10517, 11190, 11259 |
| "tamper — 11" | **10** | 1279, 2428, 2442, 9356, 9771, 12369, 12638–12640, 12648 |
| "'12 / 12' appears 10 times … lines 1534, 1650, 1779, 3883–3884, 10024" | **10 total**, but as two distinct strings, and **line 1779 was wrong** | `12 / 12` ×5 → 1276, 1534, 1650, 3883, 3884 · `12/12` ×5 → 1234, 1991, 2031, 11151, 11153 |
| "Twelve named collieries" | **16 ledger rows across 11 distinct mines** | 9386–9401 |
| "`micro` appears twice (both incidental)" | correct — both are comments (`micro 3D engine`, `micro utils`) | 2677, 9108 |

## 0.2 The claim I got wrong

**v1.0 said: "The UI should render a persistent `SIMULATED DATA` chip. It currently has no such indicator."**

**That is false.** The indicator already exists and it works:

```js
// line 4563 — set from whether the backend answered
function setLink(ok){ …
  document.getElementById('linkTxt').textContent = ok?'Online':'Sim mode';
  document.getElementById('tkMode').textContent  = ok?'LIVE':'SIM'; }

// line 4004 — set from the backend's own mode field
document.getElementById('tkMode').textContent = e.mode;
```
```html
<!-- line 1679 -->
<span>SOURCE <b id="tkMode">…</b></span>
```

So the ticker already says **`SOURCE LIVE`** or **`SOURCE SIM`**, and it is driven both by `/api/extended`'s `mode` field and by whether the poll succeeded. That is correct engineering and v1.0 did not give it credit.

**The corrected recommendation** (and it is cheaper than v1.0's): **make the existing indicator prominent rather than build a new one.** Today it renders at the same size and weight as the clock beside it, in a status strip, and it is *only* in that strip — the role decks (blocks 7–10) sit behind a login overlay that blurs the app, and the decks themselves contain no mode indicator at all. A judge who is looking at the operator deck's 12-node mesh has nothing on that screen telling them it is simulated.

## 0.3 The finding v1.0 missed

**Only one of the eleven script blocks makes any network call.**

| Block | Lines | Size | `fetch()` | What it is |
|---|---|---|---|---|
| 0 | 1,380–1,416 | 2 KB | 0 | Login-markup fallback helper |
| 1 | 2,675–2,846 | 8 KB | 0 | "micro 3D engine" |
| 2 | 2,848–3,290 | 22 KB | 0 | `SURF` — 3D surface scene |
| 3 | 3,292–3,317 | 1 KB | 0 | `UG` — underground scene |
| **4** | **3,319–5,255** | **88 KB** | **2** | **THE LIVE LAYER** — main dashboard, telemetry poll, charts, AI panel, SIM fallback, 2D evac map + `MINE` graph |
| 5 | 5,256–9,091 | **549 KB** | 0 | three.js bundle |
| 6 | 9,097–9,711 | 31 KB | **0** | `ROLE DECKS · CORE` — `window.NSD`, the hash-chained ledger |
| 7 | 9,712–10,166 | 22 KB | **0** | `DECK · AUTH / ACCESS CONTROL` |
| 8 | 10,167–11,288 | 55 KB | **0** | `DECK · MINE OPERATOR` — 12-node mesh, EAP, SMS broadcast |
| 9 | 11,289–12,343 | 50 KB | **0** | `DECK · MINE PLANNER / GEOLOGIST` |
| 10 | 12,344–13,060 | 36 KB | **0** | `DECK · REGULATOR / DGMS OFFICER` |

Both `fetch()` calls in block 4 are wrappers (`getJSON()` and `post()`); every live call goes through them.

**Blocks 6–10 = 3,963 lines and 194 KB with zero server contact.** The login gate, the operator deck with its twelve nodes, the planner deck's GIS/Form 1-A export, the regulator deck's audit ledger and SLA tracker, the mass-SMS broadcast — all of it is generated in the browser. This one table is the single most useful thing to hand the developer.

## 0.4 One thing v1.0 got right that is worth restating

I checked whether the two node models collide, because `const NODES` appears twice (line 3325 as `['node_1','node_2']`, line 10204 as `[]`). In classic scripts a duplicate top-level `const` is a hard `SyntaxError`, which would have killed an entire block silently.

**They do not collide.** Line 3325 is at the top level of block 4; line 10204 is inside an IIFE that opens at line 10172 in block 8, so it shadows only within that scope. I verified by concatenating all eleven blocks in document order and running `node --check` — **exit 0, no errors**. The page does not throw. Two node models simply coexist and disagree.

### Verify any of this yourself

```bash
cd neuralstrata-frontend

# counts and lines
grep -n "STM32"            index.html        # 2  → 3331, 3332
grep -n "433 MHz"          index.html        # 6  → 1277,1658,1751,1970,10026,10780
grep -n "SX1278"           index.html        # 4  → 1658,1970,4346,10479
grep -c "12 / 12\|12/12"   index.html        # 10
grep -n "solar"            index.html        # 9
grep -n "pin:'"            index.html        # 3  → 9751, 9760, 9769
grep -n "tkMode"           index.html        # 3  → 1679, 4004, 4563   (the existing LIVE/SIM indicator)

# confirm wireSms() never touches the network
sed -n '11110,11135p' index.html | grep -c "fetch\|apiUrl"     # 0

# confirm the whole file parses and no block dies
python3 - <<'EOF'
import re
src=open('index.html',encoding='utf-8').read()
b=re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",src,re.S)
open('/tmp/all.js','w').write("\n;\n".join(b))
EOF
node --check /tmp/all.js    # exit 0
```

---
# PART A · REMOVE — fabricated data presented as live

## R1 · The 12-node mesh — and there are **two** of them

**Model 1 — the surface mesh (block 4, lines 3335–3353):**
```js
const MESH_CELLS = Array.from({length:12}, (_,i) => {
  const id='E'+(i+1), col=i%4, row=Math.floor(i/4); …
  batt:  (3.71+((i*37)%23)/100).toFixed(2),   // fabricated
  solar: (11+((i*53)%40)/10).toFixed(1),      // fabricated
  rssi:  -(62+((i*29)%34))                    // fabricated
});
```
The code comment is honest: *"the other ten are relay/telemetry cells that still report displacement, tilt and link health to the parent."* Those ten cells do not exist. Rendered at line 4350 (`ndBatt` = `batt V · solar W · rssi dBm`) and in the hintbox at 1657 (*"12 solar child nodes"*).

**Model 2 — the operator deck's mesh (block 8, lines 10204–10230):** a second, **live-simulated** set of twelve nodes `N01…N12`, with:
```js
n.pitch = clamp(n.pitch + … , -9.6, 9.6);            // creep
n.roll  = clamp(n.roll  + … , -9,   9);
n.batt  = Math.max(3.35, n.batt - (n.solar>13 ? 0.000004 : 0.000012)*dt*60);   // battery DRAINS
n.rssi  = clamp(n.rssi + U.rr(-1.2,1.2), -104, -52);  // signal wanders
n.tx += Math.round(dt*4); n.rxs += Math.round(dt*14); // packet counters
```
plus a hardcoded `RISK=[0.18,0.26,0.44,0.88,0.72,0.31, 0.22,0.34,0.58,0.49,0.24,0.16]` commented *"deterministic risk profile: the western edge of the goaf is creeping"*, and a mesh-failure/reroute state machine (`OP.failed`, `OP.reroute`) that writes fabricated entries into the audit ledger — including an **uplink-outage simulation** that logs *"UPLINK RESTORED · n BUFFERED PACKETS FLUSHED IN 1.2 s"* (line 11171).

**Your build has 4 sensor nodes, 2 level hubs, and passive WS2812B junctions.** Nothing measures battery, solar, RSSI, packets or latency.

**Do:** delete both models, or gate them behind a build flag and relabel the panel **`ILLUSTRATIVE LAYOUT · NOT INSTRUMENTED`**. Keep the *layout drawing* if it helps the story — a labelled schematic is useful; a simulated one is a liability.

## R2 · `12 / 12` — 10 occurrences, all wrong

`12 / 12` at 1276, 1534, 1650, 3883, 3884 · `12/12` at 1234, 1991, 2031, 11151, 11153.
**Truth today: `2 / 4`** (node_1, node_2 active; node_3/node_4 are `"active": false` in the frozen map). Hardcoding a count that must match reality is itself the bug — derive it from `/api/telemetry`.

## R3 · Solar — 9 occurrences, and you decided there is no solar

`solar` at 1657, 1749, 3031, 3349, 4350, 10209, 10517, 11190, 11259. Includes a rendered `Battery · solar` row (1749) and a solar-panel mesh in the 3D surface scene (3031). Remove the field entirely — there is no panel, no MPPT and no measurement.

## R4 · The "hash-chained, tamper-evident" ledger

```js
const LED={blocks:[], head:'00000000', subs:[]};
function hashBlock(b,prev){ return U.fnv(prev+'|'+b.i+'|'+b.t+'|'+b.mine+'|'+b.ev+'|'+b.actor); }
```
Rendered as **`IMMUTABLE INCIDENT & RESPONSE AUDIT LOG · HASH-CHAINED (READ-ONLY)`** (heading, line 2518), *"hash-chained · tamper-evident"* (1279), and in the regulator role's privilege list: *"Immutable hash-chained audit ledger with tamper test"* (9771).

It is an **array in browser RAM**, hashed with 32-bit FNV, with **no `localStorage` or `sessionStorage` anywhere** — verified: the `LED` object and every `ledger` reference touch no storage API. **It is wiped on every page reload**, and any user can rewrite the chain from devtools in ten seconds. `LED.verify()` proves only that an in-memory array is internally consistent.

This is worse than no ledger: it makes a **regulatory claim** the system cannot support.

**Do:** point the panel at the real thing you already have — `GET /api/audit/export.csv` (built, tested, 11/11 green), written to PostgreSQL, where a row that exists is a row that exists. Same screen, true claim.

## R5 · Seeded multi-mine incident history — 16 rows, 11 mines

Lines 9386–9401. Mines: ARGADA, BISHRAMPUR, DHANSAR, GIDI, GOVINDPUR, JEENAGORA, KATRAS, KORBA, KUSUNDA, SAYAL, SUDAMDIH. Rows include **failure events, SLA breaches, an expired calibration certificate** (`CALIBRATION CERT EXPIRED · TL-07`) and *"MONTHLY MESH UPTIME 96.4 % · OBSERVATION"*, timestamped across three days and presented as an audit trail.

The multi-site architecture is real — every table carries `site_id`, and 100 free cloud projects genuinely means 100 sites — so a multi-mine regulator view is a legitimate design. **Fabricated incident history for mines you have never visited is not.** Seed one site and label the rows `DEMO DATA`.

## R6 · The SMS panel claims delivery it never performs

`wireSms()` (block 8, ~11110–11135). Verified: **no `fetch(`, no `post(`, no `apiUrl` anywhere in the function.** It mints a fake reference `SMS-A1B2C3`, prints *"delivered to Overman · S. Das · Mining Sirdar · R. Sen · Colliery Rescue Room…"*, claims *"gateway ACK received, no queue backlog"*, writes `BROADCAST … DISPATCHED` into the RAM ledger, and sets the state chip to `DISPATCHED 14:22:07`.

This is the **false-success pattern** the backend forbids everywhere else — the same defect found and fixed three times in the sync path and the audit export. On stage it is worse: a judge can be told the SMS was dispatched while the phone in their pocket stays silent.

**Do:** wire it to a real `POST /api/alerts/broadcast` and show **what actually happened per recipient** — `telegram ✓ · sms not fitted`. The Telegram channel is built and tested (14/14 green) and the modem is no longer being bought, so a *real* delivery line is now two hours of work and infinitely more convincing than a fabricated one.

## R7 · Gate the simulation visibly

`window.SIM` regenerates the whole telemetry contract client-side. That is the right pattern for a laptop-only rehearsal — keep it. But it must be **obvious** whenever it is active. §0.2 explains the existing `SOURCE LIVE/SIM` indicator; extend it (see Part C, A1).

---
# PART B · CORRECT — wrong facts stated confidently

## B1 · `STM32` → `ESP32` (lines 3331, 3332)
```js
node_1:{name:'Node 1 — Mesh E5', sub:'STM32 MONUMENT · NORTH GRID · OVER DRIFT A (LEVEL 1)'}
node_2:{name:'Node 2 — Mesh E8', sub:'STM32 MONUMENT · WEST GRID · OVER DRIFT B (LEVEL 2)'}
```
Your nodes are **ESP32**. Visible on the node card. As written, the reviewer's first question is *"show me the STM32 board"* — and there isn't one.

## B2 · `433 MHz` / `SX1278` → ESP-NOW 2.4 GHz

| Text | Line |
|---|---|
| `433 MHz · offline buffer 0 %` | 1277 |
| `SX1278 433 MHz) all report to the Command Centre gateway` | 1658 |
| `Every cell reports … over the 433 MHz mesh` | 1751 |
| `ONLINE · 433 MHz` | 1970 |
| `if(e2)e2.textContent='433 MHz · offline buffer …'` | 10026 |
| `OP.linkDown?'BUFFERING · 433 MHz':'ONLINE · 433 MHz'` | 10780 |

`SX1278` also appears at 4346 and 10479. **LoRa is deferred** — the hardware doc says *"Roadmap. ESP-NOW + the relay carries the prototype."* The mesh *mechanism* described elsewhere in the frontend (relay, ring, reroute) is right; only the radio is wrong.

## B3 · The graph contradicts the frozen layout — and breaks a hard constraint

Extracted from the `MINE` graph in block 4:

| | Frontend | **Frozen** (`tunnel_map.json` + your decision) |
|---|---|---|
| Exits | **4** — EAST AIR SHAFT, WEST PORTAL, WEST AIR SHAFT, SECONDARY EMERGENCY INCLINE | **2** — EXIT1 (west), EXIT2 (far east) |
| Level hubs | **5** — L1 HUB 1/2/3, L2 HUB 1/2 | **1 per level** |
| Junction nodes | 12 `jt` entries (aJ1–aJ3, bJ1–bJ3 + terminals) | **J1–J4** |
| Structure | 8 named drifts/stopes | **1 tunnel per level** |
| Cross-link | aJ3 ↔ bJ3 | **J2 ↔ J3** |
| Zones | none (`Headframe` fallback) | **A1, A2, B1, B2** |

**The five hubs are the serious one.** Your standing constraint is *exactly one MCU per level hub — no per-junction MCUs* — because every extra hub is a board to power, flash and test. A frontend that draws three hubs on Level 1 will be read as three ESP32s, and the judge will ask to see them.

## B4 · Two Dijkstra implementations will disagree

The frontend routes client-side. **Phase 7 puts Dijkstra on the hub** and serves `/api/map` — verified: the frontend never calls it (`api/map` → 0 occurrences; live `GET /api/map` → **404**). Once Phase 7 lands there will be **two routing engines with two topologies**, and the evac map will differ from the backend's answer on stage.

**Do:** consume `/api/map` (`site`, `nodes`, `junctions`, `exits`, `links`, `zones` — the payload `gis-map.js` already expects) and keep the local graph only as a clearly-labelled offline fallback.

## B5 · Version and site identity

| Shown | Line | Reality |
|---|---|---|
| `v4.2` (×4, and `BUILD` is derived from `fnv('ns-v4-2')`) | 1364, 2265, 11934, 12957 · 9724 | `app.py` is **v4.4** |
| `DGPS RL +200 m` | 1266 | **No GPS module** — deferred by your decision |
| `KUSUNDA COLLIERY · ECL · JHARIA` | 1266 | A real colliery; your map says *"OFFSETS ARE PLACEHOLDERS"* → label it **representative site** |
| `SHIFT B · OVERMAN S. DAS` | 1680 | A named individual, hardcoded in the ticker |

`DGPS RL +200 m` costs the most credulity — it claims a survey grade you explicitly do not have.

## B6 · Hardcoded PINs as the role gate (lines 9751, 9760, 9769)

```js
{key:'operator',  id:'ECL-4471',  pin:'4471', who:'Shift-II charge · A. K. Mahato' …}
{key:'planner',   id:'GEO-2290',  pin:'2290', who:'Strata planning cell · P-3 depillaring' …}
{key:'regulator', id:'DGMS-1107', pin:'1107', who:'Eastern Coalfields · statutory inspection' …}
```
Three named people with PINs in **client source**, checkable with View Source. Fine as a **demo role switcher**; must not be described as access control. The real control is built and tested — `roles.py` returns **403** on every mutating request from a regulator session (verified live against `app.py` v4.4). **Keep the login screen; make it choose a role and let the server enforce it.**

**Credit where due:** the login layer already exposes exactly the three roles the SRS names — operator, planner, regulator. That is the SRS requirement, already beautifully designed. It needs a server behind it, not a rewrite.

---
# PART C · ADD — features with no UI at all

| # | Missing | Evidence | Effort |
|---|---|---|---|
| **A1** | **Promote the existing `SOURCE LIVE/SIM` indicator** so it is visible on the decks, not just the ticker strip (§0.2) | `tkMode` at 1679, 4004, 4563 — and **zero occurrences** of any mode indicator in blocks 7–10 | 1 h |
| **A2** | **node_3 / node_4 and the two-level story** — `const NODES=['node_1','node_2']` at 3325 is a hardcoded literal; Phase 1 delivers four nodes | `node_3` and `node_4`: **0 occurrences** | 3 h |
| **A3** | **The crack panel** — micro (initiation) and macro (propagation) channels with their two thresholds; your signature sensor | `macro`: **0 occurrences**; `micro`: 2, both comments in 3D-engine headers | 4 h |
| **A4** | **The Double AI panel** — M1 pattern class, disruption index, and the M2 q10–q90 band | `model_1`, `cascade`, `ttf_band`, `disruption_index`, `pattern_model`: all **0** | 4 h |
| **A5** | **The delivery ledger** — per-alert `local ✓ · dashboard ✓ · telegram ✓ · sms not fitted · email ✗` | `delivery`: **0 occurrences** | 3 h |
| **A6** | **GIS zone risk from `/api/map`** — georeferenced deformation map with risk-coloured zones | `zone_risk`, `pair_elongation`: **0**; `api/map`: **0** | 4 h |
| **A7** | **Cloud sync state** — pending rollups, last sync, cloud reachability | `/api/sync` → **404** (Phase 9) | 3 h |
| **A8** | **Link the PWA** — `manifest.webmanifest`, `service-worker.js` and `pwa/pwa.js` sit unlinked in the same folder; four lines in `<head>` activates installability, the offline shell and the stale-data chip | `manifest`, `service-worker`, `whoami`, `view=regulator`: all **0 occurrences** | 30 min |

**Note on A8 + roles:** `roles.py` provides the server guard, `/api/audit/export.csv` and `/api/whoami`. Two role systems currently exist — the client one (blocks 7–10) and the server one — **neither aware of the other.** That is a guaranteed inconsistency the first time someone clicks a control in a regulator session.

---
# PART D · Against the problem statement

| Requirement | Frontend today | Verdict |
|---|---|---|
| Real-time tilt / displacement / vibration | Live, correct, well displayed | **met** |
| Early crack initiation | No crack channel | **missing** (A3) |
| Relative distance between nodes | No pair-elongation readout | **missing** (A6) |
| AI anomaly detection + prediction | TTF panel live; M1/M2 and the band absent | **partial** (A4) |
| GIS live deformation maps + risk zones | Evacuation schematic only | **partial** (A6) |
| Automated alerts SMS / email / app | Panel exists, **delivers nothing** | **fails as built** (R6) |
| Dashboards for operator / planner / regulator | Three decks, well designed, **client-side only** | **partial** (B6, §0.3) |
| Offline capability + periodic cloud sync | Client simulator (good); no sync UI | **partial** (A7) |
| Web / **mobile** enabled | Desktop-only; PWA unlinked | **partial** (A8) |
| Scalable across coalfields | Multi-mine decks on **fabricated** data | **cosmetic** (R5) |

**2 fully met · 6 partial · 2 missing or failing.**

The pattern is the point: **the requirements you are weakest on are the ones the frontend already appears to have.** SMS, mobile, regulator, multi-mine — all present as interface, none present as function.

---
# PART E · Priority

Ranked by *risk of embarrassment × hours to fix*. **≈ 2 days** for full alignment; **≈ 5 hours** to remove every claim the system cannot back.

| # | Action | Effort | Lines |
|---|---|---|---|
| 1 | **Delete or relabel both 12-node models, solar, battery, RSSI** — the largest fabrication, and solar contradicts a decision you already made | 3 h | 3335–3353, 10204–10230, 1657, 1749, 3031, 4350, 10517, 11190 |
| 2 | **Wire the SMS panel to the real dispatcher**, or relabel it `PREVIEW · NOT DISPATCHED` | 3 h | 11110–11135 |
| 3 | **Delete or replace the RAM ledger** with `/api/audit/export.csv` | 2 h | 9356–9390, 2518, 1279, 9771 |
| 4 | **Derive `12 / 12` from the real node set** (today `2 / 4`) | 1 h | 1234, 1276, 1534, 1650, 1991, 2031, 3883, 3884, 11151, 11153 |
| 5 | **Fix `STM32` → `ESP32`, `433 MHz`/`SX1278` → ESP-NOW 2.4 GHz** | 1 h | 3331, 3332 · 1277, 1658, 1751, 1970, 4346, 10026, 10479, 10780 |
| 6 | **Promote the existing `SOURCE LIVE/SIM` chip** to the decks | 1 h | 1679, 4004, 4563 |
| 7 | **Align the graph: 2 exits, 1 hub per level, J1–J4, cross J2↔J3, zones A1/A2/B1/B2** — includes a hard-constraint violation | 4 h | 4644–4700 |
| 8 | **Add the crack panel (micro / macro)** | 4 h | new |
| 9 | **Add the M1 pattern + q10–q90 band** to the AI panel | 4 h | 4021–4157 |
| 10 | **Add the delivery ledger; bind the client role to `/api/whoami`** | 3 h | new |
| 11 | **Link the PWA** — four lines in `<head>` | 30 min | 1–10 |
| 12 | **Read the node list from the API** instead of the literal at 3325 | 3 h | 3325 |

**If only two get done today: items 1 and 2.** About six hours, and they remove every claim the system cannot currently back.

---
# PART F · Keep, unchanged

These are genuinely good and should survive the cleanup:

1. **Contract discipline** — six endpoints, every field matched exactly, verified live against `app.py` v4.4.
2. **The defensive normalisation layer** — `cleanTelemetry`, `cleanNode`, `cleanExtended`, `num()` clamping, and the rule that the UI must never show `NOMINAL` while a node is `CRITICAL`.
3. **`esc()` before every interpolation into `innerHTML`** — real XSS discipline.
4. **The client-side simulator** — the right offline pattern, and it proves the contract a second time.
5. **The `SOURCE LIVE/SIM` indicator** — already built, already driven by the backend's own `mode` field and by poll success (§0.2). It needs prominence, not replacement.
6. **The three-role login and deck design** — the SRS requirement, already visualised. Keep the screen; add the server.
7. **The scenario console and the four injections** — the `machinery_pass` vs `warning_creep` contrast is the pitch, and the UI already frames it.
8. **The EAP / muster / acknowledgement flow** — matches mine-emergency practice.

**The frontend is not the problem.** It is a very well-engineered version of a *slightly different project*: one where the mesh has twelve LoRa nodes on STM32 boards with solar, the router runs in the browser, and the audit ledger is immutable. Align those two projects and this becomes the strongest artefact in the submission.

---

*Re-verified against: `app.py` v4.4 (6 endpoints live, 5 planned endpoints 404), `tunnel_map.json` (frozen topology), `Neural_Strata_Backend_Build_Phases.md` (P1, P3, P5, P6, P7, P8, P9), `Neural_Strata_Hardware_Build_Phases.md` (pin freeze; no solar; no LoRa; one hub per level), `Neural_Strata_Dual_Database.md`, and the SIH problem statement. All counts produced by exact match on 18 Sep 2026; `node --check` on the concatenated script blocks returned exit 0.*
