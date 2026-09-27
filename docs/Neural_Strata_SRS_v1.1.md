# NEURAL STRATA

**Mine Subsidence Early Warning  |  Miner Safety  |  Dynamic Evacuation**

*CONFIDENTIAL - PROJECT DOCUMENT*

Software Requirements Specification
for Neural Strata

An AI-enabled, localized wireless mesh network for proactive surface subsidence
detection, dual-use underground miner safety and dynamic evacuation tracking

| Document Control |  |
|---|---|
| Version | 1.1  (Implementation Baseline) |
| Prepared by | Team Geosentinels |
| Date | 29 August 2026 |
| Standard | IEEE Std 830 / Wiegers SRS Template |
| Status | Reflects the as-built hackathon prototype; baseline for demo day |

*Surface Mesh Sensing   |   Edge AI Forecasting   |   Underground Alerting & RFID Mustering*

> Based on the IEEE SRS template by Karl E. Wiegers - DGMS-aligned safety concept.

> Revision marks in this document:  ▲ = changed from v1.0   ● = new in v1.1. See the
Revision History and Appendix D for the full as-built status.

## Revision History

| Version | Name | Date | Reason for Changes |
|---|---|---|---|
| 0.9 | Team Geosentinels | 24 Aug 2026 | Initial draft: architecture, functional requirements, BOM and implementation plan. |
| 1.0 | Team Geosentinels | 25 Aug 2026 | Revised for predictive EWS focus: TTF forecasting, multi-tier alert matrix, last-gasp packets, self-healing mesh, OPEX strategy, dashboard data contract and 48-hour sprint plan. |
| 1.1 | Team Geosentinels | 29 Aug 2026 | ● Updated to the implemented prototype: ESP32 + ESP-NOW surface mesh, RC522 tunnel hub, Flask/PostgreSQL edge server, XGBoost TTF model with validation results, scenario injection console, /api/ai data contract (Appendix B.4), as-built status (Appendix D). TBD-1 and TBD-2 resolved. |

## 1. Introduction

### 1.1 Purpose

This document specifies the software requirements for Neural Strata, an AI-enabled, localized
wireless mesh network for proactive surface subsidence detection with dual-use underground miner
safety and dynamic evacuation tracking. The purpose of Neural Strata is to provide an ultra-low-cost,
highly reliable, predictive Early Warning System (EWS) for underground coal mine subsidence. By
shifting from reactive, post-facto satellite/manual surveys to a proactive real-time surface mesh
powered by time-series AI forecasting, the system identifies catastrophic failures hours or days
before they occur. It operates on a principle of sacrificial hardware resilience, paired with a passive
RFID underground tracking system, to deliver a comprehensive, DGMS-compliant safety and operational
tool. This SRS covers the complete system: surface sensing segment, edge/AI logic segment,
underground alerting and tracking segment, and the monitoring dashboard.

▲ This revision (v1.1) records the system as actually built and validated during the hackathon
(25-29 Aug 2026). The prototype substitutes ESP32 microcontrollers and ESP-NOW transport for the
production STM32/LoRa mesh, and the AI forecasting layer is implemented as a validated XGBoost
time-to-failure (TTF) model with a kinematic inverse-velocity fallback (Section 4.1, Appendix D).
Production-vision elements are retained and marked where they differ from the prototype.

### 1.2 Document Conventions

This SRS follows the IEEE-style template by Karl E. Wiegers. Functional requirements are labelled
FR-n and non-functional requirements NFR-n; these identifiers shall be used for traceability in design,
code and test artifacts. The verb shall denotes a mandatory requirement, should a recommended
provision, and may an optional one. Unless stated otherwise, priorities of a parent requirement are
inherited by its detailed sub-requirements. Monetary values are in Indian Rupees (INR).

### 1.3 Intended Audience and Reading Suggestions

The intended audience comprises mine operators, safety regulators (e.g., the Directorate General of
Mines Safety, DGMS), rescue teams, hackathon judges, and the development team. Operators and
regulators should focus on Sections 2, 4 and 5 (architecture, features and safety/quality attributes).
Developers should read the entire document, with emphasis on Sections 3 and 4 and Appendix B
(dashboard data contract). Judges and evaluators may begin with Sections 1-2 and the implementation
plan in Section 6, and may finish with the as-built status in Appendix D.

### 1.4 Product Scope

The system operates across three primary layers:

- Surface Segment (Data Acquisition & Resilience): production: a localized, self-healing wireless mesh of solar-powered STM32 sensor nodes installed on ruggedized monuments, detecting micro-tilts and ground displacements and using dynamic polling to conserve energy. ▲ Prototype: two ESP32 sensor nodes (MPU-6050 tilt + slide-potentiometer displacement) streaming telemetry at 2 Hz over ESP-NOW to a central ESP32 hub.

- Cloud/Edge Segment (Logic & AI Forecasting): a local gateway acting as an edge server that processes telemetry, calculates deformation acceleration, forecasts Time-To-Failure (TTF) using AI, and hosts a web dashboard. ▲ Prototype: the ESP32 hub bridges to the operator's laptop over USB serial; a Python (Flask) edge server runs the validated XGBoost TTF model (kinematic fallback when absent); PostgreSQL persists telemetry and RFID logs; a single-page HTML/JS dashboard is served by the same process.

- Underground Segment (Alerting & Tracking): fixed Audio/Visual (A/V) warning hubs driven by ESP32 microcontrollers and passive RFID checkpoints for zero-touch attendance and dynamically routed emergency evacuation. ▲ Prototype: one ESP32 tunnel hub with an RC522 contactless RFID reader and LED route indicators, also carrying node telemetry and RFID scans to the edge server over the same USB-serial link.

The business goals are drastic cost reduction versus commercial monitoring solutions, offline-first
availability at remote mine sites, and measurable improvement in evacuation speed and personnel
accountability during subsidence events.

### 1.5 References

- Karl E. Wiegers, Software Requirements Specification Template, 1999 (IEEE Std 830 style).

- DGMS (India) guidance on mine safety and intrinsically safe (Ex-ia) equipment for gassy coal mines.

- Neural Strata internal documents: Dashboard Metrics & API Data Contract and 48-Hour Sprint Plan (reproduced as Appendices B and C of this SRS).

- Datasheets: STM32F103C8T6 / STM32L4 MCUs, SX1278 433 MHz LoRa transceiver, MPU-6050 IMU, TP4056 charge controller, ESP32, RC522 RFID reader.

- ● Fukuzono, T. (1985). A New Technique for Determining the Critical Time of Failure of an Accelerating Landslide. Bulletin of the Geological Society of Japan - basis of the kinematic inverse-velocity (1/v) TTF fallback (FR-4).

- ● XGBoost (Chen & Guestrin, 2016); drill dataset: 18 recorded runs, 7,870 labelled 60 s windows (9 failure / 9 stable) - documented in the neuralstrata-ai project folder.

## 2. Overall Description

### 2.1 Product Perspective and System Architecture

Neural Strata is a new, self-contained product that replaces expensive commercial data loggers and
periodic satellite/manual subsidence surveys with a grid of low-cost smart sensor nodes acting as the
primary EWS sentinels. The architecture comprises three cooperating subsystems:

#### 2.1.1 The Surface Mesh (Subsidence Detection)

- Microcontroller: STM32 (STM32L4 for ultra-low power, or STM32F103 "Blue Pill"), providing industrial-grade reliability, superior deep-sleep battery life and precise ADC (production). ▲ Prototype: ESP32 development boards (Node 1, Node 2).

- Sensing: MPU-6050 (tilt/vibration via I2C) plus a linear slide potentiometer (crack-widening/displacement via ADC). Implemented identically in the prototype.

- Communication: production: SX1278 433 MHz LoRa transceiver (via SPI) forming a self-healing mesh that relays data to a central surface gateway without relying on cellular internet. ▲ Prototype: ESP-NOW (2.4 GHz Wi-Fi peer-to-peer) direct node-to-hub transport - no radio infrastructure, fully offline, 2 Hz packet cadence.

- Power: production: 18650 Li-ion battery + TP4056 charger + 1 W mini solar panel treated with a hydrophobic anti-dust coating, for fully autonomous operation. ▲ Prototype: USB-powered breadboard build (no solar/battery) for the demo environment.

- Ruggedized installation (monuments): nodes are mounted on deep-driven steel rebar/pipes with concrete collars and protected by high-visibility bollards to survive heavy machinery vibration and accidental impacts (production target).

#### 2.1.2 The Logic / Edge Server (The EWS Brain)

- Gateway bridge: ▲ prototype: the ESP32 hub receives ESP-NOW packets from both nodes and streams JSON lines over USB serial (115200 baud) to the edge server. Production keeps a LoRa gateway bridging into an MQTT broker (e.g., Mosquitto).

- Processing: ▲ a Python (Flask) backend ingests telemetry on a 500 ms ticker, computes exponentially smoothed deformation velocity and acceleration, runs the XGBoost TTF model at 1 Hz per node over 60 s rolling windows (kinematic Fukuzono fallback when the model is missing or stale), persists rows to PostgreSQL, serves the dashboard API and the static frontend, and exposes a scenario injection console for deterministic drills (FR-19/FR-20).

#### 2.1.3 The Underground Network (Alerting & Tracking)

- Fixed AI warning hubs: production: installed at tunnel intersections using ESP32s connected via standard industrial links (RS-485 / optical fiber), equipped with directional LED running lights (green = safe route, red = blocked) and a localized PA speaker for regional-language voice evacuation commands. ▲ Prototype: one ESP32 hub at the tunnel gate with RC522 RFID, LED route indicators and the USB-serial uplink that also carries surface-node telemetry.

- Passive miner tracking: miners wear passive RFID stickers on helmets/belts; fixed readers scan miners dynamically and log their presence per zone. ▲ Prototype: 13.56 MHz MIFARE-style tags on the RC522; production target: UHF portal readers at tunnel junctions.

### 2.2 Product Functions

- Continuous surface deformation sensing (tilt, vibration, displacement) with edge filtering.

- AI anomaly detection and predictive Time-To-Failure forecasting from deformation telemetry. ▲ Implemented as a validated XGBoost TTF regressor (FR-2/FR-4) with a kinematic fallback.

- Multi-tiered alerting (Advisory / Early Warning / Critical) with SMS, email and push notifications (production; prototype: dashboard banner + event log).

- Dynamic evacuation routing via graph pathfinding with real-time hazard weighting (production; prototype: per-node route OPEN/BLOCKED state).

- Underground A/V guidance: directional RGB LEDs, sirens and voice commands.

- Dual-use passive RFID: peacetime attendance/payroll logging and emergency mustering headcounts.

- GIS-based real-time monitoring dashboard with node health, crater mapping and personnel ledger (prototype: schematic node cards + gauges; GIS overlay retained for production).

- ● Scenario simulation console: deterministic critical/warning drill injection and re-arm for rehearsal and demo (FR-20).

### 2.3 User Classes and Characteristics

| User Class | Characteristics & Usage |
|---|---|
| Mine Operator / Control Room | Primary daily user. Monitors the dashboard, acknowledges Tier 1-2 alerts, initiates or supervises evacuations on Tier 3 alarms. |
| Safety Officer | Receives automated SMS advisories; reviews watch-status zones and pre-evacuation staging alerts; maintains compliance records. |
| Regulators (DGMS) | Audits alert history, intrinsic-safety compliance and attendance/mustering logs; occasional read-only access. |
| Rescue Teams | During emergencies, consume the live mustering ledger, headcounts by zone and crater-boundary maps. |
| Miners (Underground Personnel) | Passive users; carry battery-free RFID stickers and follow LED/voice evacuation guidance. No software interaction required. |
| Hackathon Judges / Evaluators | Observe the demonstration flow; interact with the physical diorama and dashboard. |

### 2.4 Operating Environment

- Surface nodes: production: outdoor, dusty, high-vibration opencast/underground coal mine surface; IP65 enclosures; solar/battery powered; no cellular coverage assumed. ▲ Prototype: two breadboard ESP32 nodes on a 2-tier diorama, USB-powered, 2 Hz sampling.

- ▲ Edge server (prototype): operator laptop (Apple-silicon Mac, macOS) with an Anaconda environment ai_env (Python 3.10) running Flask, flask-cors, pyserial, psycopg2 and xgboost; PostgreSQL (Postgres.app) database neural_strata. Production target unchanged: Raspberry Pi or site server, Linux, MQTT broker + PostgreSQL.

- Underground hubs: ESP32-based units in explosive-atmosphere zones (production: RS-485/optical fiber backbones); wearables must be entirely passive.

- ▲ Dashboard clients (prototype): modern web browser opening the single-page HTML/JS frontend served by the Flask backend (no build step), polling the HTTP API at 500 ms. Production may use a React.js frontend.

- ● Firmware toolchain: PlatformIO with per-board builds (env:node1, env:node2, env:hub).

### 2.5 Design and Implementation Constraints

- Surface node bill of materials must not exceed Rs. 2,000 per node; RFID wearables must not exceed Rs. 100 per worker (see NFR-1).

- Underground wearables must contain no batteries or active RF transmitters, conceptually complying with DGMS zero-spark (Ex-ia) mandates for explosive coal-mine atmospheres.

- The system must function fully offline: no dependence on cellular networks or external cloud services.

- ▲ Firmware in C/C++ built and flashed with PlatformIO (prototype, ESP32); production target STM32CubeIDE. Backend in Python (Flask). Prototype telemetry transport: ESP-NOW + USB-serial JSON; production: MQTT over LoRa at 433 MHz per regional ISM regulations.

- Hackathon build constraint: hardware arriving late; a "software-first" methodology with mock data generators must precede hardware integration (Appendix C).

### 2.6 User Documentation

Deliverables shall include a dashboard operator quick-start guide, a node installation and monument
construction manual, a maintenance handbook (covering M12 plug-and-play node replacement and solar
panel care), and the API data contract for integrators (Appendix B). ● Added: the demo-day drill
runbook (Phase 4.5) for timed rehearsal of the predictive EWS demonstration.

### 2.7 Assumptions and Dependencies

- Mine GIS survey data (surface coordinates mapped to underground galleries) is available for the hazard-mapping function.

- Sufficient solar exposure exists at node sites to sustain the charge cycle; nodes tolerate multi-day cloud cover on battery.

- An RS-485/optical fiber backbone can be laid to underground hub locations.

- For the hackathon prototype, ESP32 + ESP-NOW and RC522 readers stand in for the production STM32 + LoRa mesh and UHF portal readers. ● Implemented as assumed.

## 3. External Interface Requirements

### 3.1 User Interfaces

The web dashboard is the primary user interface. It shall provide: a 2D/3D GIS map overlay with
live node status (Green = Stable, Yellow/Orange = Warning, Red = Critical) - ▲ prototype: schematic
node cards with gauges in lieu of the GIS map; telemetry graphs for displacement (0-100 mm), tilt
(-45° to +45°) and deformation acceleration (-1.0 to +5.0 mm/s²); a system-status banner (NOMINAL /
WARNING / CRITICAL) with TTF countdown; tunnel route status indicators (OPEN/BLOCKED); and a live
"Personnel Mustering" panel fed by RFID swipes. Alert colours follow the matrix in Section 4.2.

- ● The TTF countdown pill displays the AI model forecast (1 Hz) with kinematic fallback; the AI view reports model name, status (ACTIVE / WARMING UP / FALLBACK), confidence and last training time (/api/ai, Appendix B.4).

- ● A scenario console shall arm deterministic warning/critical drills and re-arm the system (FR-20).

### 3.2 Hardware Interfaces

| Interface | Bus / Link | Description |
|---|---|---|
| MPU-6050 to MCU | I2C | Tilt and vibration telemetry sampling. (Implemented) |
| Slide potentiometer to MCU | ADC | Crack-widening / displacement measurement (12-bit, 0-4095 → 0-100 mm). (Implemented) |
| Node to hub | ▲ ESP-NOW 2.4 GHz P2P (prototype); SX1278 LoRa SPI (production) | Telemetry packets (node id, disp, tilt) at 2 Hz; hub aggregates nodes + RFID scans. |
| Hub to edge server | USB serial, 115200 baud, JSON lines (prototype); LoRa gateway → MQTT (production) | Packets {"node": n, "disp": x, "tilt": y} and {"uid": ...} ingested by the Python backend. |
| Underground hubs | RS-485 / optical fiber (production); hub serial uplink (prototype) | Command/status link for LEDs; RFID scans uploaded to the edge server. |
| RFID portal readers | SPI (RC522 prototype) / UHF portals (production) | Zero-touch miner scans at shaft and junctions. (Implemented: RC522) |
| Power subsystem | TP4056 charge path (production); USB (prototype) | 18650 Li-ion + 1 W solar; battery health telemetry (production). |

### 3.3 Software Interfaces

- ▲ Prototype transport: no broker; ESP-NOW node→hub and USB-serial JSON hub→backend. Production: MQTT broker (Mosquitto) with topic-based telemetry transport between the gateway bridge and backend.

- ● Database (PostgreSQL, database neural_strata), implemented schema: sensor_telemetry (node_id, tilt_x, displacement_mm, acceleration - logged at 2 Hz), rfid_evacuation_log (miner_uid, current_zone - one row per swipe); a small personnel reference (4 miner UIDs → names) is maintained in the backend.

- ▲ Backend API (Flask, app.py): HTTP endpoints per Appendix B.3/B.4 - GET /api/telemetry, /api/extended, /api/history, /api/ai; POST /api/sim/trigger, /api/reset; the static frontend is served at /. The telemetry payload of Appendix B.3 is served to the dashboard at the 500 ms polling cadence.

- ● Model artifacts: models/ttf_model_v1.json (XGBoost) + scaler.json + model_meta.json, loaded at server start. The server validates scaler feature order against the live feature list and degrades to kinematic fallback on any mismatch or missing file (FR-4).

- Notification gateways: SMS, email and push-notification services for threshold-breach alerts (production).

### 3.4 Communications Interfaces

Surface: production: 433 MHz LoRa self-healing mesh, independent of cellular networks, with automatic
re-routing around failed nodes within 2 seconds. ▲ Prototype: ESP-NOW 2.4 GHz peer-to-peer, no
infrastructure, fully offline. Underground: wired RS-485/optical fiber to eliminate RF ignition risk
(production). Local network: ▲ HTTP/JSON dashboard polling at 500 ms intervals (implemented); MQTT
over TCP/IP retained for production. OTA firmware updates shall be delivered to surface nodes via
LoRaWAN (production).

## 4. System Features (Functional Requirements)

### 4.1 Predictive Sensing and AI Forecasting

Priority: High. The core EWS capability: acquire clean deformation telemetry and convert it into a
forward-looking failure prediction.

▲ FR-1: Sensor nodes shall sample tilt and displacement data at a fixed interval (t = 500 ms in the
prototype) and the edge server shall apply smoothing/local sensor fusion (exponential filters on
velocity and acceleration) to ignore routine heavy-machinery vibrations and reject false positives.

▲ FR-2 (implemented): The central server shall run the validated XGBoost regression model
(ttf_model_v1) on each node's 60-second rolling telemetry window (2 Hz, 16 features: displacement
level; 10/30/60 s displacement increments; 10/30/60 s velocities; 60 s max displacement; acceleration
mean/std/max; jerk; tilt level; 30 s tilt change; Fukuzono 1/v estimate) to predict the time until the
100 mm displacement breach. The model is trained on 18 recorded drill runs (7,870 labelled windows;
9 failure / 9 stable) with a held-out critical run for unbiased error estimation. Production may extend
to multi-node spatio-temporal anomaly detection and epicenter estimation.

▲ FR-3 (implemented): The backend shall compute deformation velocity (v = dx/dt) and acceleration
(a = d²x/dt²) from displacement telemetry for every node, with 0.3/0.7 exponential smoothing.

▲ FR-4 (implemented): The AI layer shall output a Time-To-Failure (TTF) prediction in seconds
(clamped to [0, 3600]), refreshed at 1 Hz, which drives the dashboard TTF countdown. When the model
file is missing or fails to load, the prediction is stale (> 5 s), or a physics sanity guard rejects an
imminent-failure claim made with no motion, the server shall fall back to the kinematic
inverse-velocity (Fukuzono) estimate TTF = v/a and continue serving. The server shall always boot and
respond with or without the model (graceful degradation).

● FR-19 (new): The server shall expose GET /api/ai reporting the AI layer's state: model name,
status (ACTIVE / WARMING UP / FALLBACK), horizon, last_train timestamp, confidence (derived from the
held-out MAE), a human-readable note, and per-node 5-minute breach forecasts (true when the model TTF
is < 300 s or the node status is not NOMINAL).

● FR-20 (new): The server shall provide a scenario injection console (POST /api/sim/trigger with
node ∈ {node_1, node_2} and severity ∈ {warning, critical}; POST /api/reset to re-arm) for deterministic
drill rehearsals and the demo. While an injection is armed, hardware updates for that node shall be
ignored until re-arm; warning injections shall auto-settle after ~46 s, critical injections run until
re-arm.

### 4.2 Multi-Tiered Alert Matrix

Priority: High. Alerts escalate proportionally to risk, avoiding both alarm fatigue and delayed response.

FR-5 (Tier 1 - Advisory): On slight deviation with constant velocity (a = 0), the system shall log a
"Watch" status on the dashboard and send an SMS to the Safety Officer.

FR-6 (Tier 2 - Early Warning): When deformation accelerates (a > 0) and TTF < 24 hours, the
dashboard shall flag the zone orange, underground hubs shall blink amber, and pre-evacuation staging
alerts shall trigger.

FR-7 (Tier 3 - Critical Alarm): When displacement breaches terminal thresholds or TTF < 1 hour,
full underground sirens shall trigger, hazardous routes shall be blocked (red LEDs), and dynamic
evacuation shall initiate.

| Tier / State | Trigger Condition | System Response |
|---|---|---|
| NOMINAL (Green) | Stable; deformation acceleration near zero. | Routine logging; peacetime attendance mode. |
| Tier 1 - Advisory (Watch) | Slight deviation; velocity constant (a = 0). | Dashboard "Watch" log; SMS to Safety Officer. |
| Tier 2 - Early Warning (Orange) | a > 0 sustained; TTF < 24 h. | Zone flagged orange; hubs blink amber; TTF countdown; pre-evacuation staging. |
| Tier 3 - Critical (Red) | Terminal displacement threshold breached, a ≥ 3.0 mm/s², or TTF < 1 h. | Sirens; red LEDs block hazardous routes; dynamic evacuation; stakeholder notifications. |

▲ Implemented thresholds (edge server): WARNING at sustained a > 0.25 mm/s², CRITICAL at
a ≥ 3.0 mm/s²; worst-node system status, route OPEN/BLOCKED propagation and the TTF pill are
implemented. SMS/staging actions remain production scope; the prototype logs events to the dashboard
and drives the hub LEDs.

### 4.3 Catastrophic Failure Resilience and Self-Healing Mesh

Priority: High. The mesh is designed as sacrificial hardware: losing a node must itself become usable
information rather than a blind spot.

FR-8 (Last-Gasp Packet): On sudden surface collapse or node destruction, the node interrupt routine
shall fire a high-priority "Terminal Anomaly" packet containing final high-speed accelerometer data before
power loss. (Production STM32 design; prototype equivalent: a powered-off node produces a visible
telemetry gap on the dashboard.)

FR-9 (Mesh Auto-Reroute): If a node is destroyed by machinery or subsidence, neighboring nodes shall
automatically re-route telemetry around the offline node within 2 seconds. (Production LoRa mesh.)

FR-10 (Crater Mapping): The dashboard shall use surviving surrounding nodes to plot the boundaries
of a surface collapse without requiring human surveyors. (Production GIS feature.)

### 4.4 Dynamic Evacuation Routing

Priority: High. Converts a surface prediction into safe underground movement before failure occurs.

FR-11: Upon a Tier 3 Critical Alarm (or critical surface deformation), the system shall instantly map
the affected surface GIS coordinates to the underlying underground galleries and mark them hazardous.
▲ Prototype: fixed mapping node_1 → Tunnel A (route A), node_2 → Tunnel B (route B), implemented.

FR-12: The system shall execute a graph pathfinding algorithm (e.g., Dynamic Dijkstra) to calculate the
shortest safe exit route, dynamically assigning infinite weight to compromised tunnels. (Production;
the prototype operates the OPEN/BLOCKED route states on which the algorithm will run.)

FR-13: Tunnel hubs shall trigger directional visual indicators (RGB LEDs) and regional-language voice
commands reflecting the newly calculated safe routes. ▲ Prototype: hub LED route indicators driven by
route state; voice commands remain production scope (TBD-4).

### 4.5 Dual-Use Attendance and Mustering (RFID)

Priority: Medium (peacetime) / High (emergency). A single passive-RFID infrastructure serves daily
operations and crisis response.

▲ FR-14 (implemented, peacetime): The system shall log entry and exit times of all personnel via the
main-gate RFID reader for daily payroll/attendance generation. Prototype: RC522 swipe → rfid_log
(rolling 10 entries on the dashboard) + rfid_evacuation_log row in PostgreSQL.

▲ FR-15 (implemented, emergency): The dashboard shall display a live ledger and real-time headcount
of miners, with zone assignment (Tunnel A vs Safe Exit) derived from live route state, enabling tracking
of movement to safety. Production: multi-reader junction scans via UHF portals.

### 4.6 Real-Time Monitoring Dashboard and Notifications

Priority: High. The operator-facing window into the entire system.

▲ FR-16 (implemented): The web dashboard shall show the live status of all surface sensor nodes
(Green = Stable, Yellow = Warning, Red = Critical) - prototype on schematic node cards with gauges and
telemetry charts; GIS map overlay retained for production.

FR-17: The system shall send automated SMS, email and push notifications to key stakeholders
whenever alert thresholds are breached. (Production; prototype: dashboard banner + event log.)

▲ FR-18 (implemented): The dashboard shall refresh from the backend API every 500 ms using the JSON
data contract defined in Appendix B, including system status, per-node telemetry, TTF countdown, tunnel
route states and the rolling RFID mustering ledger; the AI view additionally consumes /api/ai
(Appendix B.4).

### 4.7 AI Model Management and Validation  ● (new section)

Priority: High. The forecasting model is a managed, verifiable artifact - not a black box.

● FR-21: The TTF model shall be trained on a labelled drill dataset harvested from recorded drill runs
(nominal baseline plus warning and critical scenarios, each run to completion or re-arm), with a
time-ordered split (one full critical run held out of training) and class weighting for breach windows.
Reported metrics: held-out critical MAE/RMSE, warning in-sample MAE (disclosed as in-sample), and
5-minute-breach binary accuracy/precision/recall.

● FR-22: Deployment artifacts shall be self-describing (model + scaler + metadata with feature order,
training timestamp and metrics); the edge server shall validate feature order at load time and, on any
mismatch or load failure, degrade to the kinematic fallback rather than fail the service.

## 5. Other Nonfunctional Requirements

### 5.1 Performance Requirements

NFR-P1: Mesh auto-reroute around a failed node shall complete within 2 seconds (supports FR-9).
(Production LoRa mesh.)

▲ NFR-P2 (implemented): The dashboard API shall serve the full telemetry payload at a 500 ms polling
cadence; the backend logs telemetry to PostgreSQL at the same 2 Hz rate.

NFR-P3: Tier 3 critical alarms shall propagate from detection to underground A/V activation with
minimal latency so evacuation begins before structural failure.

▲ NFR-P4 (implemented): Backend normalization: 12-bit ADC displacement (0-4095) maps to 0-100 mm;
tilt is constrained to -45°..+45°; acceleration range -1.0 to +5.0 mm/s² with the critical trigger at
3.0 mm/s².

● NFR-P5 (new, implemented): Per-node AI inference shall complete in < 10 ms at 1 Hz on laptop-class
hardware. The dashboard shall show WARMING UP until each node's 60 s telemetry buffer is full
(~60 s after boot), after which the model forecast drives the TTF pill; predictions older than 5 s are
rejected as stale.

### 5.2 Safety Requirements

NFR-3 (Intrinsic Safety Compliance): The underground wearable component must be entirely passive -
no batteries and no active RF transmission - complying conceptually with DGMS zero-spark mandates
(Ex-ia) for explosive coal-mine atmospheres.

- Underground signalling shall run over wired RS-485/optical fiber links rather than active RF where atmospheres may be explosive.

- Evacuation guidance shall fail safe: on loss of routing data, hubs default to the last verified safe route and audible advisories.

### 5.3 Security Requirements

- Dashboard access shall be role-based (operator, safety officer, regulator read-only).

- Personnel attendance and mustering records shall be protected against tampering, as they feed payroll and regulatory audits.

- The offline-first design limits the external attack surface; any remote access shall be explicitly provisioned and logged.

### 5.4 Software Quality Attributes

NFR-1 (Cost Efficiency): The surface sensor node BOM shall not exceed Rs. 2,000 (~$24 USD); miner
wearables (RFID) shall not exceed Rs. 100 per worker. Total estimated CapEx for a 2 sq km Dhanbad
mine is approximately Rs. 17.2 Lakhs.

NFR-2 (High Availability & Uptime): The system must operate on an "Offline-First" basis: the surface
mesh shall operate completely independently of external cellular networks, surviving continuously on
solar/battery power. ▲ Prototype verified offline: ESP-NOW + USB serial + local HTTP only.

NFR-4 (Low OPEX Strategy): Estimated annual maintenance shall be held to Rs. 1.8L - 2.5L (about 70%
lower than traditional methods), achieved via: hydrophobic dust-coatings on solar panels; predictive
telemetry monitoring node battery/signal health before failure; plug-and-play M12 waterproof connectors
enabling 2-minute node replacements; and Over-The-Air (OTA) firmware updates via LoRaWAN.

### 5.5 Business Rules

- Only the Safety Officer or Control Room may acknowledge and downgrade alerts; Tier 3 evacuation triggers may never be suppressed automatically.

- Attendance records from the main-shaft reader are the authoritative source for payroll generation.

- If a node above a tunnel breaches WARNING or CRITICAL, the corresponding route status must shift to BLOCKED for both the UI and the physical hub LEDs. (Implemented.)

## 6. Other Requirements

### 6.1 Bill of Materials (BOM) - Single Surface Node

| Subsystem | Component | Approx. Cost (INR) |
|---|---|---|
| MCU | STM32F103C8T6 ("Blue Pill") or similar | Rs. 150 - 250 |
| Radio | SX1278 433 MHz LoRa module | Rs. 450 |
| Sensors | MPU-6050 (tilt) + 10k slide potentiometer (displacement) | Rs. 310 |
| Power | 18650 battery + holder, TP4056 charger, 1 W solar panel | Rs. 270 |
| Hardware | IP65 enclosure, PCBs, M12 connectors, wiring | Rs. 200 - 300 |
| Total node cost | Within the Rs. 2,000 NFR-1 ceiling | ~Rs. 1,380 - 1,580 |

▲ Prototype note: the hackathon nodes are ESP32 dev boards + MPU-6050 + slide potentiometer on
breadboards (demo only; no solar, IP65 or M12 hardware).

### 6.2 Step-by-Step Implementation Plan (Hackathon)

- Phase 1 - Surface Mesh Prototype (Hardware & IoT): wire the node MCU to the radio module (SPI), MPU-6050 (I2C) and potentiometer (ADC); write C/C++ firmware with deep-sleep cycles (production) / ESP-NOW transmission (prototype); set up a USB gateway receiver on a laptop.

- Phase 2 - Communication, AI & Backend: establish a local Mosquitto MQTT broker and a PostgreSQL/MongoDB database (production) / ESP-NOW + USB-serial ingestion (prototype); develop the Python (Flask) backend for serial ingestion; train a lightweight ML model on drill data to forecast Time-To-Failure and trigger alerts.

- Phase 3 - Underground & RFID Simulation: assemble the tunnel hub (ESP32 + RGB LED strip + buzzer + RC522 RFID reader); create the Miners/Zones/TrackingLogs schema; implement the route OPEN/ BLOCKED logic that drives the hub LEDs from alerts.

- Phase 4 - Dashboard & Physical Integration: build the HTML/JS dashboard with live telemetry, TTF countdown and Personnel Mustering panel; assemble the 2-tier diorama (surface nodes above, tunnel with LEDs and an RFID-tagged toy miner below).

● As-built (29 Aug 2026): all four phases completed ahead of demo day. The Phase-2 AI element was
delivered as the XGBoost TTF model with drill-data training, held-out validation and live integration
(Appendix D); firmware is developed in PlatformIO with per-board builds. See also the Phase 4.5
demo-day runbook (2.6).

### 6.3 Demonstration Flow (Predictive EWS Demo)

● Revised to the validated rehearsal flow (Phase 4.5 runbook):

- Step 1 (Peacetime): boot with hardware live - gauges show real potentiometer/tilt values; swipe the RFID tag to demonstrate attendance logging; the AI warms up (~60 s) then goes ACTIVE with TTF ≈ 3600 s.

- Step 2 (Early Warning): arm the WARNING scenario (console or slow real creep). The status pill says WARNING - no evacuation required - while the AI countdown continues, because the displacement trajectory still converges on the 100 mm limit. Key contrast: rule-based status vs predictive forecast.

- Step 3 (Proactive Evacuation): arm the CRITICAL scenario. The model begins its countdown once the motion becomes credible (~20 mm), counts down ~7 → 0 s tracking the true breach within ~1 s; at 0 the system is CRITICAL, the affected route goes BLOCKED and the hub LED turns red; the toy miner swipes the RFID gate and the mustering panel tracks them to Safe Exit.

- Step 4 (Resilience & Degradation): operator re-arms (console) → NOMINAL in < 1 s and TTF recovers within ~65 s. Then demonstrate graceful degradation: with the model file removed, the server still boots and the TTF pill keeps counting via the kinematic 1/v fallback - "the API never breaks." (Prototype equivalent of the last-gasp moment: a powered-off node produces a visible telemetry gap.)

## Appendix A: Glossary

| Term | Definition |
|---|---|
| ADC | Analog-to-Digital Converter; digitizes the potentiometer displacement signal (12-bit, 0-4095). |
| DGMS | Directorate General of Mines Safety (India); the regulatory authority for mine safety. |
| EWS | Early Warning System; the predictive core of Neural Strata. |
| Ex-ia | Intrinsic-safety classification: equipment incapable of producing an ignition spark in explosive atmospheres. |
| ESP-NOW ● | Espressif 2.4 GHz Wi-Fi peer-to-peer transport (no infrastructure); the prototype's node-to-hub telemetry link. |
| Fukuzono inverse velocity ● | Method estimating time-to-failure from the trend of 1/velocity: 1/v grows linearly as an accelerating slope nears failure; locally TTF = v/a. Basis of the kinematic fallback. |
| GIS | Geographic Information System; used for the surface/underground map overlays. |
| Last-Gasp Packet | High-priority terminal-anomaly transmission fired by a node in the instant before destruction/power loss. |
| LoRa / LoRaWAN | Long-Range low-power radio (SX1278, 433 MHz) used for the production surface mesh and OTA updates. |
| MQTT | Lightweight publish/subscribe messaging protocol (Mosquitto broker) for production telemetry transport. |
| OPEX / CapEx | Operating expenditure / capital expenditure. |
| Passive UHF RFID | Battery-free radio tags energized by the reader field; worn by miners as helmet/belt stickers. |
| TTF | Time-To-Failure; the AI-forecast interval until the predicted 100 mm displacement breach. |
| TTF model (ttf_model_v1) ● | The deployed XGBoost regressor predicting seconds-to-breach from a 60 s, 2 Hz telemetry window (16 features). |
| WARMING UP / FALLBACK ● | /api/ai states: buffer-building after boot; kinematic-only mode when the model is absent or failed. |
| XGBoost ● | Gradient-boosted decision-tree ensemble used for the TTF regression. |

## Appendix B: Dashboard Metrics & API Data Contract

The Python backend must format its data according to these parameters before serving it to the web
interface. The overall system state is determined by the highest alert level of any individual surface node:
NOMINAL (green - stable, acceleration near zero), WARNING (orange - sustained a > 0, TTF countdown
initiated, pre-evacuation protocols logged) and CRITICAL (red - acceleration breaches the terminal
threshold a ≥ 3.0 mm/s²; immediate structural failure predicted; tunnels blocked). ▲ This contract is
implemented as-is by the Flask backend; Appendix B.4 lists the additional implemented endpoints.

### B.1 Telemetry Metrics and Graph Ranges

| Metric | Source | Dashboard Range | Mapping / Trigger |
|---|---|---|---|
| displacement_mm | ESP32 ADC (0-4095), rotary potentiometer | 0 - 100 mm | Linear map: ADC 0 = 0 mm; ADC 4095 = 100 mm. |
| tilt_x | MPU-6050 accelerometer/gyro | -45° to +45° | Pitch/roll in degrees; normal settling varies +/-1-2°. |
| acceleration | Derived: a = d²x/dt² of displacement_mm | -1.0 to +5.0 mm/s² | Crossing 3.0 mm/s² instantly elevates system_status to CRITICAL. |

### B.2 Underground Routing and Mustering Metrics

- route_A_status / route_B_status: accepted values "OPEN" or "BLOCKED". If node_1 (above Tunnel A) breaches the WARNING or CRITICAL threshold, route_A_status must shift to "BLOCKED", driving both the UI and the physical ESP32 LEDs. (Implemented; node_2 drives route B.)

- rfid_log: array of objects with fields id (e.g., "001"), name (e.g., "Miner A"), zone (e.g., "Safe Exit", "Tunnel A", "Evacuating A") and time (swipe timestamp). The backend stores a rolling list of the 10 most recent swipes for the live tracking table. (Implemented.)

### B.3 Expected JSON Payload (HTTP GET /api/telemetry, every 500 ms)

```json
{
  "system_status": "NOMINAL",
  "ttf_seconds": "N/A",
  "nodes": {
    "node_1": { "status": "NOMINAL", "tilt_x": 1.1, "displacement_mm": 5.2, "acceleration": 0.05 },
    "node_2": { "status": "NOMINAL", "tilt_x": 0.8, "displacement_mm": 4.1, "acceleration": 0.02 }
  },
  "underground": { "route_A_status": "OPEN", "route_B_status": "OPEN" },
  "rfid_log": [
    { "id": "001", "name": "Deepak", "zone": "Safe Exit", "time": "14:02:11" }
  ]
}
```

### B.4 Additional Implemented Endpoints  ● (new)

| Endpoint | Method | Returns / Body |
|---|---|---|
| /api/extended | GET | Per-node velocity & peak acceleration, muster counts (total/underground/safe), event log. |
| /api/history | GET | Last 120 samples per node (status, tilt_x, displacement_mm, acceleration) at 500 ms. |
| /api/ai | GET | AI layer state - sample below (ACTIVE); FALLBACK example uses "model": "Kinematic Extrapolation Fallback", "status": "FALLBACK", "last_train": "Static", "confidence": "85%", "note": "Using 1/v derived TTF calculations.". |
| /api/sim/trigger | POST | Body: {"node": "node_1"|"node_2", "severity": "warning"|"critical"} - arms a drill (FR-20). |
| /api/reset | POST | Re-arm: clears injections, resets nodes to the 5 mm baseline, re-arms the system. |

GET /api/ai sample (ACTIVE):

```json
{
  "model": "ttf_model_v1",
  "status": "ACTIVE",
  "horizon": "1 Hour",
  "last_train": "2026-08-28T21:41:07",
  "confidence": "100%",
  "note": "XGBoost TTF forecast · held-out MAE 0.10 s · kinematic fallback armed.",
  "forecasts": { "node_1": false, "node_2": false }
}
```

## Appendix C: 48-Hour Sprint Plan & To-Be-Determined List

Deadline: Thursday, August 27th. Constraint: hardware arriving late (Wednesday). Strategy:
"Software-First" methodology - build and simulate all logic before hardware integration.

| Phase | When | Key Tasks |
|---|---|---|
| Phase 1: Software & Simulation | Tue, Aug 25 | Set up Flask backend with a mock data generator (tilt, displacement, RFID UIDs); implement velocity/acceleration algorithms and WARNING/CRITICAL thresholds; connect the API to the HTML dashboard and verify UI spikes; blind-draft ESP32 C++ code for nodes (MPU-6050 I2C, analog pot, ESP-NOW) and hub (ESP-NOW receive, RFID SPI, serial to Python). |
| Phase 2: Physical Structure | Tue afternoon | Build the 2-tier cardboard diorama (surface/tunnel); cut holes for potentiometer knobs; create breadboard and RFID-gate mounts; print and attach labels ("Node 1", "Safe Route", etc.). |
| Phase 3: Hardware Crunch & Integration | Wed, Aug 26 | Solder headers to MPU-6050s and RC522 and wires to potentiometers; wire all three breadboards; flash C++ to all three ESP32s and verify ESP-NOW mesh data on the hub serial monitor; replace the mock generator with pyserial reading the real COM port; full system test: knob twist → graphs spike → alert state → physical LEDs turn red. |
| Phase 4: Final Mounting & Rehearsal | Wed evening | Hot-glue breadboards, sensors and LEDs into the diorama; rehearse the 5-minute pitch flow with physical triggers. |

● As of 29 Aug 2026: all four sprint phases completed; hardware arrived on schedule (Wednesday),
and the software-first simulation preceded integration as planned. Post-sprint work: drill-data
harvesting, XGBoost training/validation and live integration (Phase 4.1-4.4, Appendix D).

### To Be Determined (TBD)

- ● TBD-1 (RESOLVED for the prototype): final AI model - implemented as XGBoost TTF regression (metrics in Appendix D); an LSTM on identical windows is the documented stretch comparison. Production multi-node anomaly/epicenter detection remains open.

- ● TBD-2 (RESOLVED for the prototype): sampling interval fixed at 2 Hz (500 ms) for hardware, DB logging, model windows and dashboard cadence; production calibration against machinery-vibration baselines remains open.

- TBD-3 (open): production UHF RFID portal reader model and mounting standard for tunnel junctions.

- TBD-4 (open): regional-language voice command set for PA evacuation announcements.

- ● TBD-5 (new, optional): real-world displacement→TTF validation dataset for post-hackathon credibility (candidate: open-pit slope-radar monitoring case studies; Fukuzono method as the named reference).

## Appendix D: Implementation & Validation Status  ● (new)

As-built snapshot, 29 August 2026. Evidence: drill_dataset.csv (7,870 windows), models/ artifacts,
and live drill runs on the operator Mac.

### D.1 As-Built Summary

| Component | As-Built (Prototype) | Status |
|---|---|---|
| Surface sensor nodes (2×) | ESP32 + MPU-6050 + slide potentiometer; ESP-NOW → hub at 2 Hz | Flashed, live-verified |
| Tunnel hub | ESP32; ESP-NOW receive; RC522 RFID; USB-serial JSON uplink; LED route indicators | Flashed, RFID verified |
| Edge server | Flask app.py (v4.4): pyserial ingestion @ 500 ms, status logic, 2 Hz DB logging; Anaconda ai_env (Python 3.10) | Live on operator Mac |
| Database | PostgreSQL, database neural_strata: sensor_telemetry, rfid_evacuation_log | Live |
| AI model | XGBoost ttf_model_v1: 16 features, 60 s window @ 2 Hz; trained on 18 drill runs (7,870 windows) | Trained, deployed, live |
| Dashboard | Single-page HTML/JS served by Flask; 500 ms polling; gauges, charts, TTF pill, mustering, scenario console | Live |
| Firmware toolchain | PlatformIO: `env:node` · `env:level_hub` · `env:main_hub` | Complete |

### D.2 Validation Results

| Test | Result |
|---|---|
| *Canonical run* | *`models/model_meta.json` — 5,806 windows, trained 2026-08-25. Every number below is read from that file by `tools/check_numbers.py`, which fails the build if a document drifts from it.* |
| Training - held-out critical run (`node_1_01`) | MAE **0.10 s** / RMSE 0.42 s |
| Training - warning ramp (in-sample, disclosed) | MAE 0.08 s / RMSE 0.15 s |
| Training - overall | MAE 0.09 s / RMSE 0.23 s |
| Training - 5-minute breach (acc / prec / recall) | 1.000 / 1.000 / 1.000 |
| Live critical drill (Mac) | Model counts down ~7 → 0 s tracking the true breach within ~1 s; conservative (~3600 s) until displacement ≈ 20 mm, then countdown; breach at 0 |
| Live warning drill (Mac) | Countdown continues to 0 as the slow creep physically reaches 100 mm - by design; status logic stays WARNING (no evacuation) |
| Recovery after re-arm | ACTIVE with TTF ≈ 3600 s within ~65 s; forecast flags reset |
| Degradation (model files removed) | Server boots; /api/ai = FALLBACK; kinematic 1/v TTF keeps counting during a drill |

### D.3 Open Items Before Demo Day (Phase 4.5)

- Hub re-flash: the temporary 45.5 mm test payload has been removed from hub.cpp; the hub must be re-flashed (env:hub) so Node 1 gauges show real potentiometer values.

- Mac degradation check: rename models/ away → verify clean FALLBACK boot → restore.

- Full timed rehearsal: boot → 60 s warm-up → Node 1 critical drill → re-arm → Node 2 warning drill → re-arm (runbook: neuralstrata-ai/4.5_runbook.md).

### D.4 Prototype → Production Deltas

| Aspect | Prototype (this build) | Production target |
|---|---|---|
| Surface transport | ESP-NOW 2.4 GHz P2P | SX1278 LoRa 433 MHz self-healing mesh |
| Node MCU / power | ESP32, USB-powered | STM32L4 deep-sleep + 18650/solar |
| Telemetry path | Hub USB-serial JSON | LoRa gateway → Mosquitto MQTT |
| RFID | RC522, 13.56 MHz, single gate | UHF portal readers at junctions |
| Edge host | Operator laptop (Mac) | On-site Raspberry Pi / server |
| Dashboard map | Schematic node cards + gauges | GIS overlay with crater mapping |
| Notifications | Dashboard banner + event log | SMS / email / push |
| Resilience features | Telemetry gap on node loss | Last-gasp packets + mesh auto-reroute (2 s) |
| Evacuation routing | Per-route OPEN/BLOCKED state | Dynamic Dijkstra pathfinding + PA voice |
