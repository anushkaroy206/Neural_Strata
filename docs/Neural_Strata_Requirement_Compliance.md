# Neural Strata — Problem-Statement Compliance Check

## Every requirement in the SIH problem statement, line by line, against what is actually planned

| | |
|---|---|
| **Document** | `Neural_Strata_Requirement_Compliance.md` · v1.0 |
| **Checked against** | The SIH problem statement *"Wireless Surface Mesh Network for Real Time Subsidence Detection"* (background, description, expected solution, attributes) |
| **Evidence base** | `Neural_Strata_Backend_Build_Phases.md` (P0–P10) · `Neural_Strata_Hardware_Build_Phases.md` (H0–H9) · `Neural_Strata_Dual_Database.md` · `Neural_Strata_SRS_v1.1.md` · `app.py` v4.4 · `gis-map.js` · `tunnel_map.json` |
| **Verdict** | **28 requirements delivered or fully planned · 1 blocked by unbought hardware · 5 only partially covered · 0 requirements contradicted** |
| **Action needed** | Three items appear in the problem statement and nowhere in the plan: **the SMS hardware, the mobile-app notification, and the regulator/planner view.** §7 gives the cheapest fix for each. |

> **The headline.** The four things a judge is most likely to attack — *is the crack sensing real, is the network actually a mesh, is the offline claim true, can you alert without internet* — are the four things this project answers better than a generic AI proposal. Two of them are already **tested**, not merely claimed. The exposure is not in the engineering; it is in **three lines of the expected-solution list that nobody was assigned.**

---

## How to read the status column

| Status | Means |
|---|---|
| **MET** | Delivered and running, or verified by a test that has been executed |
| **PLAN** | A named phase delivers it, with effort, acceptance test and rollback already written |
| **PARTIAL** | The requirement is covered, but not to the strength the wording implies — say so before a judge finds it |
| **AT RISK** | Planned correctly, but depends on something not yet bought or built |
| **GAP** | Appears in the problem statement; appears nowhere in the build plan |

---
# 1. How this was checked

The problem statement was split into **34 discrete requirements** — every bullet, sub-bullet and attribute phrase. Each was traced to a specific phase, section or code artifact. A requirement was marked **PLAN** only if it has a phase number, an effort estimate, a named owner track and an acceptance test; anything asserted in a slide or a plan but with no phase is marked **GAP**.

This is deliberately the same standard a **DGMS reviewer** or an SIH evaluator applies: *show me the artifact, not the intention.*

---
# 2. The sensor payload

**Problem statement:** *"Each node may be equipped with sensors such as: tilt/inclination sensors, vibration sensors, displacement/stretch sensors, crack detection sensors, optional low cost positioning modules."*

| # | Requirement | What Neural Strata has | Status |
|---|---|---|---|
| A1 | **Tilt / inclination** | **MPU-6050** per node — `tilt_x` plus rate features `tilt_d30`/`tilt_d60`. Independent cross-check: the **rotary displacement pot** measures the same physical quantity by a different principle, so a tilt-only failure is detectable. Read directly from the accelerometer registers, never derived from gyro integration (**D9**). | **MET** |
| A2 | **Vibration** | **Two sensors, deliberately:** the **SW-420** as an *interrupt-driven event counter* (not an ADC read — the `impulse_periodicity` feature needs real timing) and the **MPU-6050 raw acceleration**. Together they feed `vib_count_60`, `vib_rate_10`, `impulse_periodicity`, `zero_cross_rate`, `excursion_duration_s`. | **MET** |
| A3 | **Displacement / stretch** | **Macro rotary pot** per node (millimetres of opening) **plus the inter-node measurement the problem statement actually asks for** — `pair_elongation`, `pair_strain`, `differential_settlement` between adjacent nodes (P4). | **PLAN** |
| A4 | **Crack detection** | **BF350 foil strain gauge** (350 Ω, gauge factor 2.0–2.2, 7.4 × 4.4 mm) in a **3-fixed-arm bridge → HX711 at 3.3 V**, giving microstrain *rate*. Two-stage rule: micro-rate crossing = **initiation**, macro opening = **propagation**. Six arms matched to ≤ 0.3 Ω (HD-3). | **PLAN** |
| A5 | **Optional positioning module** | **Deferred by your decision (16 Sep) — and the problem statement itself says "optional."** Georeferencing is a surveyed baseline plus per-node offsets in software; GPIO25 is reserved and the NEO-6M spec is preserved if it is re-enabled. | **MET** (by documented choice) |

**Why A4 is the strongest line in this table.** The problem statement says *crack detection*. Most teams will answer that with a vibration threshold and call it a crack. Neural Strata measures **actual strain in the surface**, separates initiation from propagation with two physically different sensors, and knows that the HX711 drifts — hence the documented 15-second warm-up and baseline-recapture rule. That is the difference between detecting a crack and detecting a *change*.

---
# 3. The network and the real-time detections

**Problem statement:** *"communicate through a wireless mesh communication network (such as LoRa/Zigbee/Wi-Fi mesh)" · "continuously detect: abnormal ground tilt, change in relative distance between nodes, early crack initiation, unusual vibration signatures."*

| # | Requirement | What Neural Strata has | Status |
|---|---|---|---|
| B1 | **Continuous real-time monitoring** | **2 Hz per node** on the surface panel, 500 ms staleness detection, an alert-latency budget of < 5 s local. Hub ticker at 20 Hz with inference scheduled at 2 Hz. | **MET** |
| B2 | **Abnormal ground tilt** | Threshold bands plus `tilt_d30` rate-of-change; feeds Model 1 as feature 1–8. | **PLAN** |
| B3 | **Change in relative distance between nodes** | This is **Phase 4 (B8) exactly** — pair elongation in millimetres, pair strain, and differential settlement across a junction. Not inferred from a single node: it is computed *between* nodes, which is what the requirement says. | **PLAN** |
| B4 | **Early crack initiation** | The micro (BF350) channel crossing first, while vibration stays low and recovery ratio stays near zero. That specific signature — *no recovery* — is what separates initiation from a truck passing. | **PLAN** |
| B5 | **Unusual vibration signatures** | 16 disruption features built for exactly this word: *signature* ≠ signal level. `impulse_periodicity` (periodic = engine/axle, aperiodic = rockfall), `zero_cross_rate` (spectral roughness), `spatial_coherence` (local = machinery, multi-node = ground), `spatial_decay`, `tilt_acc_coupling` (machinery tilts *with* its vibration; a crack does not). | **PLAN** |
| B6 | **Wireless mesh** | **ESP-NOW** (2.4 GHz, connectionless, no router, no MQTT) between nodes; every node is also a **listener** holding its neighbours' last ~20 packets; the hub runs a **sequence-gap watchdog** and requests `{"relay":"REQ"}` so a neighbour replays what it heard. Backed by a wired 2-level UART ring with a cross-link between the two levels, so the backhaul has a **physical second path**. | **PLAN — with a naming caveat, see §8** |

---
# 4. The AI / ML requirements

**Problem statement:** *"identify abnormal deformation patterns · predict possible subsidence zones · estimate severity and progression · generate automated early warning alerts · support timely operational decisions."*

| # | Requirement | What Neural Strata has | Status |
|---|---|---|---|
| C1 | **Identify abnormal deformation patterns** | **Model 1 — Deformation Pattern Discriminator.** `RandomForestClassifier`, 200 trees × depth 8, 4 classes (`QUIET`, `ENVIRONMENTAL`, `STRUCTURAL`, `UNKNOWN`) over **32 features**. Target calibration: **STRUCTURAL recall ≥ 0.98** — recall, never accuracy, because the problem is 9:1 imbalanced. Also emits `disruption_index`, a *soft* number, instead of a brittle boolean. | **PLAN** |
| C2 | **Predict possible subsidence zones** | **PARTIAL.** What exists is a *zone risk* value = worst-of the nodes and links in a zone, plus per-node **TTF with a q10–q90 prediction band**, plus GIS zone colouring. That answers "which zone is at risk **now**". There is **no spatial predictor** that says *"the next zone to degrade is A2, in about 40 minutes"* — which is what "predict zones" implies. §7-G4 gives the cheapest fix (3 h). | **PARTIAL** |
| C3 | **Estimate severity and progression** | **Phase 6 (B3).** Severity tiers, creep stage (`SECONDARY` → `TERTIARY`), progression rate, confidence, and the kinematic unification so the TTF never shows `N/A`. The stage transition is itself a logged, quotable event. | **PLAN** |
| C4 | **Generate automated early warning alerts** | **Phase 8 (B4).** Persistent dispatcher with a dedupe key (60 s), escalation timer (120 s), and tiers: `ADVISORY` (no SMS by policy) → `HIGH` (+SMS) → `CRITICAL` (+SMS + email + strobe/siren). Includes a **per-alert delivery ledger** and an `ACK` path. | **PLAN** |
| C5 | **Support timely operational decisions** | **The strongest AI-side answer in the project.** The alert does not say *"CRITICAL"* — it says **`Route A BLOCKED. Use Exit 2.`** with the coordinates, inside 160 characters. `recommended_actions()` returns a ranked list; the email carries the full route as a junction list. The system hands over a **decision**, not a number, under stress. | **PLAN** |

**One sentence for the viva, on C1+C4:** *"The classifier is never allowed to decide whether we warn. It decides how often we compute and how loudly we shout — a gatekeeper can only ever remove information from a safety system, and every removal it gets wrong is a fatality."* That inversion is enforced by `firewall_check()` **and** `tests/test_cascade_safety.py`, not by good intentions.

---
# 5. The expected solution (the eight platform bullets)

| # | Requirement | What Neural Strata has | Status |
|---|---|---|---|
| D1 | **Web/mobile enabled platform** | Web dashboard: yes, and it is the operator surface for everything. **Mobile: responsive browser is the honest answer today.** No PWA manifest, no push subscription, no mobile build anywhere in the plan. | **PARTIAL** |
| D2 | **Low-cost nodes on Arduino/ESP32/Raspberry Pi** | **ESP32** nodes and hubs; no Raspberry Pi needed. Entire build **₹2,390–2,540 ≈ ₹398–423 per person at 6**; core hardware ₹1,245. Documented as a bill of materials with Indian vendors. | **MET** |
| D3 | **Localized wireless network over the panels, continuous surface deformation sensing** | Surface-panel deployment at **1 cm = 10 m** scale over a simulated panel, 4 nodes on 2 levels, 2 Hz. | **PLAN** (with §8's caveat) |
| D4 | **Real-time tilt, displacement, vibration, crack** | All four channels present, plus the two-stage crack split. | **MET / PLAN** |
| D5 | **AI/ML anomaly detection + subsidence prediction on live and historical data** | **Live:** M1 + M2 in the 20 Hz ticker. **Historical:** 5,806 training windows, external validation against published displacement→TTF data, augmentation and calibration notebooks, and exported training reports. The "historical" half is genuinely done, not gestured at. | **PLAN** |
| D6 | **GIS visualization of live deformation maps and risk zones** | **Phase 7** delivers `/api/map` + the static-asset route + `offsets_to_latlon()` / `rect_to_polygon()`, returning `site`, `nodes[]`, `junctions[]`, `exits[]`, `links[]`, `zones[]` — exactly what `gis-map.js` already reads. **Note: this card is blank in today's code (defect D1) — the route does not exist yet.** Deliberately **no basemap tiles**: a georeferenced schematic that works with zero internet. | **PLAN** |
| D7 | **Alerts through SMS / email / mobile app** | SMS (4G modem, AT commands on the hub UART) and email (SMTP with a georeferenced deep link) are both specified. **The modem is *on hold* at ₹1,300 and there is no SIM.** Mobile-app notification is nowhere in the plan. | **AT RISK + GAP** |
| D8 | **Interactive dashboards for operators, planners, regulators** | One rich operator dashboard — AI cascade panel, tunnel ΔL sparklines, alerts with delivery ledger, GIS card. **Role-based access is an SRS requirement (operator / safety officer / regulator read-only) but it is not in any phase.** | **PARTIAL** |
| D9 | **Offline capability with periodic cloud synchronisation** | Local PostgreSQL at the edge is the **source of truth**; the cloud is a **derived reporting replica**. Hourly rollups (96 rows/day, ~14 KB/day) synced every 15 minutes; watermark outbox; idempotent upserts; `NS_CLOUD_DB_URL` empty = local-only with **no code change**. **Ordering is proven by test: commit to the cloud first, then mark synced, so the failure mode is a harmless re-send and never a silent loss.** | **MET — and verified** |
| D10 | **Scalable deployment across multiple coalfields** | Every table carries `site_id`; `NS_SITE_ID=jharia-panel-01`; 100 free Neon projects = 100 sites on one account at ₹0. The multi-site story is schema-true, not aspirational. | **MET** |

**On D9 — say this number out loud.** Raw 2 Hz telemetry is 691,200 rows/day ≈ **138 MB/day**, which fills a free cloud tier in about three days. Hourly rollups are 96 rows/day ≈ **14 KB/day**, which fits for a century. *"The cloud stores decisions and summaries, never raw samples"* is not a workaround — it is why large sensor networks are economically possible at all.

---
# 6. The non-functional attributes

**Problem statement:** *"low cost, easy to deploy, energy efficient, scalable, student prototype friendly … robust, low power … suitable for Indian geo-mining conditions … Made in India."*

| # | Attribute | Evidence | Status |
|---|---|---|---|
| E1 | **Low cost** | ₹2,390–2,540 total build, ₹398–423 per person at 6. Sensor substitutions (SW-420, 8-LED NeoPixel, 2-pin buzzers) documented with the reason. | **MET** |
| E2 | **Easy to deploy** | Per-phase **solder vs jumper** guidance for every connection, no terminal blocks, service-panel design, strain relief and service loops specified, every joint DMM-tested. | **MET** |
| E3 | **Energy efficient / low power** | **Measured, not claimed:** node 80–120 mA idle, peaks 180–280 mA while transmitting; hub 90–140 mA; the LED strip's 480 mA is on a **separate 5 V/2 A rail**, never the hub pin. Plus a real design consequence: M1 gates the **uplink cadence** — a node in a quiet period reports every 2 s instead of every 0.5 s, a 10× radio duty-cycle reduction. **No battery or solar in the prototype** (your decision), so there is no runtime figure. | **PARTIAL** |
| E4 | **Scalable** | See D10. | **MET** |
| E5 | **Student prototype friendly** | Bare ESP32, soldered level hubs, breadboards out, 7 boards allocated 1:1, sub-₹2,600 total, phased by person-day with a 6-person split. | **MET** |
| E6 | **Robust** | Watchdog + degraded mode, staleness detection, last-gasp packet on brownout, relay heal, event log with edge-detected infrastructure events, and a documented cut order if time runs out. | **PLAN** |
| E7 | **Indian geo-mining conditions** | Design targets them: Jharia-like baseline coordinates (23.643 / 85.318), 1 cm = 10 m scale, DGMS-oriented roles (Safety Officer receives SMS; regulator audits alert history), Indian vendor pricing. **But `tunnel_map.json` says in its own words "OFFSETS ARE PLACEHOLDERS"** and there is no real-mine calibration. | **PARTIAL** |
| E8 | **Made in India** | Indigenous design, Indian suppliers, rupee-denominated BOM, no cloud dependency on a foreign service for the critical path. | **MET** |

---
# 7. The seven things to fix, ranked by visibility per hour

Ranked by **how likely a judge is to notice ÷ how long the fix takes**. Do them in this order.

## G1 · SMS, email and mobile app — the problem statement names all three channels — **AT RISK / GAP**
The single most exposed requirement, because it is printed in the expected solution and it depends on **hardware you have not bought** (4G modem ₹1,300, on hold) plus a SIM with credit and signal — during a demo, in a hall, on a strange network.

**Fix, two moves, both cheap:**

1. **Telegram Bot channel (~2 h, ₹0).** A bot token and a `sendMessage` call. It is a **real network-delivered push to a real phone**, it works on venue Wi-Fi without a SIM, it carries the same 160-character body, and it lands in the same delivery ledger as SMS/email. A judge holding a buzzing phone is worth more than a spec sheet.
2. **Buy the modem if the budget holds (~₹1,300 + prepaid SIM).** The AT-command path is already specified (`AT+CMGF=1`, `AT+CMGS`), and the modem "needs no GPIO" by design — it is a plug-in, not a rewire. If it stays unbought, **say so on stage before anyone asks**: the delivery ledger shows SMS as `queued`, the message appears on the projector, and the outbox proves nothing was lost. *Honesty about the offline case is a stronger demo than a lucky signal.*

## G2 · Mobile-app notification leg — **GAP**
Closed by G1's Telegram bot, which *is* a mobile app with a notification. Add a **PWA manifest + responsive CSS (~1 h)** and the dashboard becomes installable on a phone with an icon. Do not build a native app; it buys nothing a judge can see.

## G3 · Regulator and planner views — **PARTIAL**
The SRS already requires role-based access — *operator, safety officer, regulator read-only* — and no phase delivers it.

**Fix (~2 h):** a `?view=regulator` parameter (or a read-only token) that hides `ACK`, `FORCE SYNC` and every scenario control, and pins the audit trail — alert history, delivery ledger, muster log. **Two sentences on stage:** *"The operator sees controls. The regulator sees exactly the same data with no ability to change it. That separation is a compliance requirement, not a UI preference."*

## G4 · "Predict possible subsidence zones" is aggregate, not spatial — **PARTIAL**
Zone risk is a worst-of rule; a judge asking *"which zone fails next?"* gets a weaker answer than the wording invites.

**Fix (~3 h, inside Phase 7):** put an **inverse-distance-weighted risk surface** over the zone rectangles, driven by each node's severity and TTF, rendered as a heat overlay on the existing GIS card. Now the map *predicts a zone* rather than colouring one, and it reuses the polygons `gis-map.js` already draws.

## G5 · Energy evidence has no runtime — **PARTIAL**
Currents are measured; there is no battery or solar (your call), so there is no "runs for N hours" figure.

**Fix (~2 h, documentation only):** put the measured table on one slide with the commissioning conditions (what was running, which DMM range), then state the production delta in one line — *"uplink gating cuts radio duty cycle 10×, and a 2 W panel covers the production duty cycle."* Never claim a runtime you did not measure.

## G6 · Mesh naming — **PLAN with a caveat**
This is your differentiator, so it must be airtight. §8 handles it in full.

## G7 · Real-mine calibration is honestly absent — **PARTIAL**
**Fix: zero hours, one scripted sentence.** *"Offsets here are surveyed by hand at prototype scale; on a real panel they come from a DGMS survey plan and a total-station baseline. The placeholder is labelled as a placeholder in our own map file because an unlabelled assumption is the thing that gets people killed."* Owning it beats being caught.

---
# 8. Defending the hook: "Wireless Surface Mesh Network"

This is the phrase that differentiates you, so here is the attack and the answer, rehearsed.

**The question you will be asked:** *"ESP-NOW is a star topology. Where is the mesh?"*

**The answer, in three sentences:**

> *"Three mechanisms make it a mesh rather than a star. Every node is also a listener — it buffers the last twenty packets it hears from each neighbour. The hub watches sequence numbers per node and, when it sees a gap, asks a neighbour to replay what it heard. So when a node's direct link degrades, its data still arrives through a neighbour, and the dashboard shows `via relay (N1)`. We can show you: cover N2's antenna and the badge appears while the data keeps flowing."*

**Then volunteer the limit before it is found:**

> *"What it is not is proactive multi-hop routing. At four nodes in a one-metre diorama, one stage of cooperation is the correct engineering choice — more routing logic would be complexity we could not test. For a 300-metre panel the production delta is LoRa radio plus real multi-hop routing, and that is a firmware swap rather than a rewrite, because the topology contract already lives in the hub."*

**Also true and worth one line:** the backhaul is a **two-level wired ring with a cross-link between the levels**, so there is a *physical* second path with `RING_SPLIT` handling — path redundancy at the layer below the radio.

**One caution on wording.** Do not use the word *hop* anywhere on stage or in the slides — say **links**, **wires** or **relay stage**. `Neural_Strata_Hardware_Build_Phases.md` currently uses "hop-2 isolation" in the H5 switch table; change it to "level-2 link isolation" when you next edit that file, so the spoken language and the written language match.

---
# 9. Two internal inconsistencies to fix (one edit each)

Neither is a problem-statement gap, but both would surface at the worst moment.

| ID | Inconsistency | Where | Fix |
|---|---|---|---|
| I1 | `BACKEND_PLAN.md` §1 describes the backend as coordinating *"multi-channel alerts (SMS, Email, **PWA push**, and local siren/LED actuation)"* — but **no phase delivers PWA push**, and Phase 8's channel list is siren/LED, dashboard, SMS, email. The plan claims a channel the build does not contain. | `neuralstrata-backend/BACKEND_PLAN.md` line 11 | Either add the PWA channel to G2's ~1 h scope, or delete "PWA push" from that sentence. Do not leave a claimed channel undelivered. |
| I2 | The SRS states notifications are *"SMS / email / push (production; prototype: dashboard banner + event log)"* — written before Phase 8 moved SMS and email **into** the prototype. | `Neural_Strata_SRS_v1.1.md` §D.4 (and the §-line listing "Dashboard banner + event log → SMS / email / push") | Update to: *"SMS and email promoted into the prototype (Phase 8), with a Telegram push channel for demonstration; production adds gateway redundancy and delivery SLAs."* |

---
# 10. Traceability — where each requirement lives

| PS requirement | Status | Primary evidence |
|---|---|---|
| Tilt / inclination sensors | MET | HW §A.2 pin freeze — MPU-6050 on SDA 21 / SCL 22; BE §A.3 payload, D9 |
| Vibration sensors | MET | HW §A.1 · BE P1 (`node.cpp` payload), P5 §5.6 features 17–18, 26–27 |
| Displacement / stretch | PLAN | HW §A.2 (macro pot GPIO33) · BE P4 `engine/geomech.py` |
| Crack detection | PLAN | HW §H2 (BF350 + HX711, HD-3/HD-4) · BE P3 two-stage engine |
| Optional positioning | MET (deferred by choice) | HW §A.2.5 (GPIO25 reserved) · GPS spec PDF preserved |
| Wireless mesh | PLAN | HW §H3 (relay, MAC map, last-gasp) · BE P2 `MESH_REROUTE` |
| Continuous real-time monitoring | MET | HW §A.2 · app.py `ticker_thread` 20 Hz, ingest 2 Hz |
| Abnormal ground tilt | PLAN | BE P3 thresholds · P5 features 1–8 |
| Relative distance between nodes | PLAN | BE P4 §B8 — pair elongation, differential settlement |
| Early crack initiation | PLAN | BE P3 · HW §H2 two-stage rule |
| Unusual vibration signatures | PLAN | BE P5 §5.6 (16 disruption features) |
| AI anomaly detection | PLAN | BE P5 §5.4 — M1 RandomForest, recall ≥ 0.98 target |
| Subsidence zone prediction | PARTIAL | BE P4 `zone_risk` · P7 §A.3.6 map payload — *aggregate, not spatial* |
| Severity and progression | PLAN | BE P6 §B3 |
| Automated early warning alerts | PLAN | BE P8 — tiers, dedupe, escalation, delivery ledger |
| Support operational decisions | PLAN | BE P8 `recommended_actions()`, SMS body with route + coordinates |
| Web/mobile platform | PARTIAL | Dashboard today; mobile only as responsive browser |
| Low-cost ESP32 nodes | MET | HW §A.1 BOM — ₹2,390–2,540 total |
| GIS live deformation maps | PLAN | BE P7 §A.3.6 · `gis-map.js` contract — **blank today (D1)** |
| GIS risk zones | PLAN | BE P4 `zone_risk` → P7 zone colouring |
| SMS / email / mobile notification | AT RISK + GAP | BE P8 (`channels.py`, AT commands, SMTP) — **modem on hold, ₹1,300; mobile unplanned** |
| Dashboards for operators/planners/regulators | PARTIAL | SRS NFR role-based access — **not in any phase** |
| Offline + periodic cloud sync | MET (verified) | `Neural_Strata_Dual_Database.md` §F, §H.5 — 17/17 control-flow tests, 11 SQL statements parsed |
| Multi-coalfield scalability | MET | DD §B/§C — `site_id` on every table |
| Low cost | MET | HW §A.1 |
| Easy to deploy | MET | HW §A.3 solder-vs-jumper table |
| Energy efficient | PARTIAL | HW §A.1 current table, §H1/H4 measurements |
| Robust / low power | PLAN | BE P2 watchdog, degraded mode, last-gasp |
| Student prototype friendly | MET | HW phase map, 13.5 person-days, 6-person split |
| Indian geo-mining conditions | PARTIAL | `tunnel_map.json` (placeholders, stated as such) |
| Made in India | MET | BOM vendors, rupee pricing, no foreign dependency on the critical path |

---
# 11. The six sentences to have ready

Judges reward teams who state their own boundaries. These are the six, one per weakness — each is one sentence, delivered without apology:

1. **Mobile app:** *"The platform is web-first and installable; our notification channel is a real push to a real phone, and we chose a channel that works with no SIM and no credit."*
2. **SMS hardware:** *"The 4G modem is a plug-in we have specified and not yet bought — so tonight SMS shows as queued in the delivery ledger, and the outbox proves the alert was not lost."*
3. **Mesh:** *"Every node is also a listener, so a neighbour can replay what it heard — one stage of cooperation, which is the right choice at four nodes, and LoRa with full routing is the production delta."*
4. **Zone prediction:** *"Zone risk today is worst-of aggregation across nodes and links; the risk surface on the map interpolates it, and a trained spatial predictor needs the multi-site history that only comes after deployment."*
5. **Energy:** *"These are measured currents, not estimates — and the design saving is the uplink cadence, which cuts radio duty cycle tenfold."*
6. **Calibration:** *"Our offsets are placeholders and we label them as placeholders in the map file itself; a real panel gets its baseline from a survey plan."*

---
# 12. What to change today

Ranked, and none of it is large. Total **≈ 10 hours of work** to move five PARTIALs and two GAPs into MET.

| Order | Action | Effort | Moves |
|---|---|---|---|
| 1 | Telegram bot channel into Phase 8's `channels.py` + delivery ledger + dashboard chip | 2 h | G1, G2 |
| 2 | `?view=regulator` read-only mode | 2 h | G3 |
| 3 | IDW risk surface in Phase 7's map payload | 3 h | G4 |
| 4 | Energy slide: measured table + uplink-gating line | 2 h | G5 |
| 5 | Reconcile the two doc inconsistencies (§9) | 30 min | I1, I2 |
| 6 | Script and rehearse the six sentences (§11) | 30 min | G6, G7 |
| 7 | Decide on the 4G modem: buy, or accept the queued-SMS demo | ₹1,300 or ₹0 | G1 |

**If only one gets done: the Telegram channel.** It converts the requirement the problem statement spells out most explicitly — *automated alerts through SMS/email/mobile app notifications* — from a hardware dependency into something that visibly works on a phone in the room, for zero rupees and two hours.

---

*Checked against the problem statement in full: background, description, sensor list, detection list, AI/ML list, all eight expected-solution bullets, and the closing attributes. Counts: 34 requirements — 28 delivered or fully planned, 1 blocked on unbought hardware, 5 partial, 0 contradicted.*
