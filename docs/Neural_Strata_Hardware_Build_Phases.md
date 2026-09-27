# Neural Strata — Hardware Build Phases
## Phase-by-Phase Wiring Plan · Master GPIO Registry · Soldering & Termination Specification

| | |
|---|---|
| **Document** | `Neural_Strata_Hardware_Build_Phases.md` |
| **Version** | v1.0 (build plan) |
| **Companion to** | `Neural_Strata_Backend_Build_Phases.md` (the software build order) and `BACKEND_PLAN.md` (what the system is) |
| **Written against** | The bought-and-verified inventory (`Neural_Strata_Have_vs_Buy.pdf`, 16 Sep), `neural-strata-system-map.html` (v2 wiring map), `neural-strata-crack-sensor.md` (crack decision, 16 Sep), `Neural_Strata_Shopping_List.pdf`, and `Neural_Strata_SRS_v1.1.md` §3.2 |
| **Audited on** | 18 Sep 2026 |
| **Phases** | 10 (H0 → H9) |
| **Total bench effort** | ~13.5 person-days, parallelisable to ~5 bench days for 6 people |
| **Minimum demo path** | H0, H1, H2, H3, H4, H5, H6, H7 (≈11.5 days) |

---

## How to read this document

This is the **hardware** twin of the backend build plan, and it follows the same shape: every phase states what exists today, what the target is, the exact **pin-to-pin wiring that changes**, the **termination method for every joint** (solder / jumper / bond / USB), a **bench acceptance test with the multimeter readings you should expect**, risks, and the one-line viva answer.

Five rules the whole plan obeys:

> **Rule 1 — Nothing is soldered until it works loose.** Every subsystem is proven on the bench with jumper wires first, then soldered, then re-verified. Soldering is a *commitment*, not a debugging tool. When something fails after soldering you cannot tell whether the fault is the joint or the logic.
>
> **Rule 2 — Measure before you power.** No new board gets plugged in until you have (a) checked for shorts between 5 V and GND, and (b) confirmed the 3.3 V and 5 V rails are where they should be. One multimeter check saves an ESP32.
>
> **Rule 3 — One ground, one truth.** There is exactly **one** common GND bus for all 7 boards, all strips, and both power sources. Any second ground path (or a missing one) is the single most common cause of "it worked yesterday".
>
> **Rule 4 — Nothing above 3.3 V touches an ESP32 GPIO.** ESP32 pins are *not* 5 V tolerant. Every 5 V sensor output is either powered at 3.3 V or explicitly divided. There are no exceptions, and there are no level shifters in this build except one optional diode trick on the LED strips.
>
> **Rule 5 — No MCU at a junction.** There is exactly **one ESP32 per level hub** (L1, L2) and **no processor at J1–J4**. Junctions are passive WS2812B strips driven by their level hub. This was decided and is not reopened.

---

## Table of contents

**Part A — The whole hardware system at a glance**
- A.1 **Hardware requirements — the complete bill** (owned · to buy · not buying · consumables)
- A.2 **Master GPIO registry** (all 7 boards, every pin, every conflict)
- A.3 Electrical contracts (voltage domains, current budget, grounding, keep-outs)
- A.4 **Termination registry** (solder / jumper / bond / USB — for every joint in the build)
- A.5 Tools, consumables & workspace
- A.6 **Hardware defect audit** — 16 defects and inconsistencies found in the current documents
- A.7 Phase map, dependencies & effort

**Part B — The phases**
- H0 — Bench truth: inventory reconciliation, pin freeze, measurement discipline
- H1 — The standard sensor node (N1–N4)
- H2 — Two-stage crack system (N2, N4)
- H3 — Surface mesh (ESP-NOW, relay, last-gasp)
- H4 — Main Hub (uplink + two ring ports)
- H5 — Ring backbone (the 7 wires + cut switch)
- H6 — Level hubs L1 / L2 (RFID, LED strips, siren, gate lights)
- H7 — Underground junctions J1–J4 (passive strips)
- H8 — Power distribution & grounding (the shared 5 V and the LED rail)
- H9 — Diorama integration, service panel & pre-demo check

**Part C — Appendices**
- C.1 Complete cable schedule
- C.2 Soldering standard operating procedure
- C.3 GPIO conflict matrix (all boards × all peripherals)
- C.4 Pre-power and cold-start checklists
- C.5 Fault-finding table (symptom → likely cause → fix)
- C.6 Cost summary
- C.7 Viva question bank
- C.8 Glossary (plain language)

---
---

# PART A — THE WHOLE HARDWARE SYSTEM AT A GLANCE

## A.1 Hardware requirements — the complete bill

Seven ESP32 boards, four surface nodes, two level hubs, one main hub. Everything below is split four ways: **already owned and verified**, **still to buy**, **deliberately not buying**, and **consumables that are easy to forget**.

### A.1.1 Board allocation (seven boards, no spares)

| Board | Env (`platformio.ini`) | Role | Feeds | Carries |
|---|---|---|---|---|
| **N1** | `env:node` (NODE_ID=1) | Sensor node, Tunnel A near | USB from hub | MPU-6050, 10 kΩ slide pot, SW-420, ESP-NOW |
| **N2** | `env:node` (NODE_ID=2) | Sensor node, Tunnel A collapse face | USB from hub | + BF350/HX711, macro crack pot |
| **N3** | `env:node` (NODE_ID=3) | Sensor node, Tunnel B near | USB from hub | MPU-6050, slide pot, SW-420 |
| **N4** | `env:node` (NODE_ID=4) | Sensor node, Tunnel B far | USB from hub | + BF350/HX711, macro crack pot |
| **MAIN** | `env:hub` | Gateway: ESP-NOW RX, USB uplink, 2 ring ports | USB from hub | nothing else — bare ESP32 |
| **L1** | `env:lvl1` | Level-1 hub | USB from hub | RC522, 2× WS2812B, siren, 2 gate LEDs, 2 ring ports |
| **L2** | `env:lvl2` | Level-2 hub | USB from hub | RC522, 2× WS2812B, siren, 2 gate LEDs, 2 ring ports |

> **Board-count check:** 7 boards, 7 ports on the powered USB hub. The laptop powers itself from its own charger — do **not** take a port for it. The LED rail is powered separately (§A.3.3) so it never loads a hub port.

### A.1.2 Already owned — from the previous prototype (₹2,453) and the recent buy

| Item | Qty | Verified use in this plan |
|---|---|---|
| ESP32 DevKit board | **7** (3 existing + 4 bought) | N1–N4, MAIN, L1, L2 |
| MPU-6050 (GY-521) | **4** (2 + 2) | one per node, I2C on GPIO21/22 |
| 10 kΩ rotary pot | **4** (2 + 2) | one per node — displacement/stretch channel, GPIO32 |
| RC522 RFID reader | **2** (1 + 1) | L1 gate, L2 gate |
| MIFARE 13.56 MHz tags | **5** | 3 demo miners + 2 spares |
| SW-420 vibration/tilt sensor | **4** | one per node, DO → GPIO34, powered from **3V3** |
| NeoPixel WS2812B 8-LED strip | **4** | junctions J1–J4 (passive, no MCU) |
| 330 Ω resistor | **5** | one per strip data line (4 used + 1 spare) |
| 5 V buzzer, 2-pin | **2** | L1 siren, L2 siren |
| 5 mm LEDs (built-in resistors) | **6** | gate lights at L1/L2 (green/red) + spares |
| Toggle switch SPST | **2** | #1 ring-cut, #2 J4 feed cut |
| 22 AWG solid wire kit, 10 colours × 1 m | **2 kits** | ring backbone (kit 1) + junction feeds (kit 2) |
| Glow-in-the-dark arrow stickers | **4** | J1–J4 fixed fallback direction |
| PVC foam board 4 mm | 2 sheets | tunnel walls + service panel |
| Miner figurines / rescue figurine | 3 + 1 | RFID carriers + rescue entry |
| Acrylic paint + rockfall prop | 1 lot | tunnels + visible collapse |
| Breadboards / jumper wire set | 3 + 1 | **bench bring-up only** (Rule 1) |

### A.1.3 Still to buy — core

| # | Item | Exact model to search | Qty | ₹ | Goes to |
|---|---|---|---|---|---|
| 1 | 22 AWG solid wire kit | 22 AWG solid PVC, 10 colours × 1 m | 1 kit | 250 | junction feed trunks J2/J4 |
| 2 | Powered USB hub, 7-port | 7-port USB 2.0 hub with **5 V/4 A** DC adapter — verify the adapter label really says 4 A | 1 | 500 | powers MAIN + N1–N4 + L1 + L2 |
| 3 | USB cable, 1 m | Type-A to **micro-B** or **Type-B** — *check your boards' port first* | 7 | 175 | one per board |
| 4 | BF350-3AA foil strain gauge | BF350-3AA, 350 Ω (Robu SKU 975711 / Rees52 RS5503) | 2 | 50 | micro-crack at N2, N4 |
| 5 | HX711 amplifier module | HX711 24-bit load-cell/strain amplifier | 2 | 140 | drives each BF350 |
| 6 | 10 kΩ rotary pot | same type as the ones you own | 2 | 50 | macro-crack at N2, N4 |
| 7 | Electrical tape + glue dots | replaces heat-shrink / ties / conduit | 1 set | 80 | joint insulation and cable anchors |

**Core subtotal: ₹1,245**

### A.1.4 Still to buy — corrected quantities (see §A.6 HD-3)

| # | Item | Why the quantity changed | Qty | ₹ |
|---|---|---|---|---|
| 8 | **350 Ω 1% metal film resistor** | **6, not 2.** A single-gauge Wheatstone bridge needs **three** fixed arms, not one. One per channel is not enough — see HD-3. | 6 | 15 |
| 9 | **1N4148 diode** | 4 + 4 spare — series diode on the first LED's VDD to fix the 5 V data-level margin (HD-5) | 8 | 20 |
| 10 | **1000 µF 6.3 V electrolytic capacitor** | one across VDD/GND at each strip — WS2812B inrush protection (HD-6) | 4 | 40 |
| 11 | **5 V 2 A USB charger (old phone charger is fine)** | the dedicated LED rail. Do **not** run 4 strips through the ESP32 boards. | 1 | 0–150 |
| 12 | **USB-A breakout or a sacrificial USB cable** | to bring the 5 V rail out to soldered bus wires | 1 | 0 (cut an old cable) |

**Corrected subtotal: ₹75–225.** If the multimeter in §A.1.5 is already owned, add nothing.

### A.1.5 Buy only if not already owned

| Item | Model | Qty | ₹ | Note |
|---|---|---|---|---|
| Digital multimeter | UNI-T UT33+ class | 1 | 600 | **You cannot build this without one.** Continuity + DC volts + 10 A range |
| 50 Ω trimpot (optional) | 3296W 50 Ω | 2 | 30 | only if you want to null the strain-bridge offset instead of software-zeroing |
| DPST toggle switch (optional) | 6 A 250 V, 6-pin | 1 | 80 | cuts *both* hop-2 signal wires for a cleaner ring-cut demo (HD-8) |

### A.1.6 Never buy for this prototype

| Item | Decision |
|---|---|
| **GPS module (NEO-6M)** | **Deferred.** Georeferencing is a fixed baseline + offsets in software. The spec exists if it is re-enabled (§A.2.5 records the pin reservation). |
| **LoRa modules (SX1278 / RFM95)** | Roadmap. ESP-NOW + the relay carries the prototype. |
| **Solar panel / 18650 / TP4056** | Not in this build. The energy slide uses deep-sleep numbers already measured. |
| **Screw terminal blocks** | **Solder directly to headers.** Decision made. |
| **3-core flex / ribbon cable** | Replaced by 22 AWG solid, one continuous piece per wire. |
| **PVC conduit / cable ties / heat-shrink** | Replaced by electrical tape + glue dots. |
| **Piezo analog vibration sensors** | Replaced by SW-420 (digital → event rate). |
| **10-LED strips** | 8-LED is fine; set the count to 8 in firmware. |
| **Plug-in "crack sensor module"** | Bought units are **bench spares only.** The crack channel is the two-stage BF350 + rotary pot. Do not mount them. |
| **FSR408 force strip** | An FSR measures compression, not tension — it structurally cannot see a crack opening. Optional impact channel on the roadmap. |
| **Per-junction MCUs, per-junction batteries** | Rejected. Junctions are passive. |

### A.1.7 Consumables and small parts that stall a build day

Nobody lists these, and then the build stops at 9 p.m.

| Item | Qty | Why |
|---|---|---|
| Solder, 60/40 or lead-free, 0.8 mm | 1 spool | ~80 joints in this build |
| Rosin flux pen | 1 | header soldering quality |
| Solder wick / desoldering pump | 1 | you *will* bridge two adjacent header pins |
| Isopropyl alcohol + cotton bud | 1 | clean flux before taping |
| Side cutters + wire strippers (22 AWG) | 1 each | 22 AWG solid strips cleanly only with the right notch |
| Precision tweezers | 1 | BF350 lead forming |
| Thin cyanoacrylate or 2-part epoxy | 1 | **bonding the strain gauge** (never hot glue) |
| Double-sided foam tape | 1 roll | mounting boards without drilling |
| M2/M3 nylon standoffs | 20 | board mounting without shorting to anything |
| Cable labels / masking tape + marker | 1 | **label both ends of every backbone wire.** Non-negotiable for 7 wires across 3 boards |

### A.1.8 Cost summary

| Block | ₹ | Per person (6) |
|---|---|---|
| Core to buy (§A.1.3) | 1,245 | 208 |
| Corrected quantities (§A.1.4) | 75–225 | 13–38 |
| Multimeter, if needed (§A.1.5) | 600 | 100 |
| Consumables (§A.1.7) | ~400 | 67 |
| **Total to spend now** | **₹1,720 – ₹2,470** | **₹287 – ₹412** |
| On hold: 4G modem (§A.1.9) | 1,300 | 217 |

### A.1.9 The one deferred item with a hardware footprint

The **USB 4G/LTE modem + prepaid SIM (₹1,300)** is the only remaining item that touches the hub physically. It attaches to the **Main Hub or the laptop** by USB and needs no GPIO. Design so that adding it later is a plug-in, not a rewire:

- Reserve a **free USB port** on the hub (use a 7-port hub for 7 boards → buy the modem a separate port only if you later reduce to 6 boards).
- The modem's SIM slot must face outward on the finished panel.
- Firmware hook already specified in the backend plan: `{"cmd":"sms","to":"…","body":"…"}` over serial, AT commands driven from the hub.

**Plan for it, don't buy it yet.** The alert path degrades gracefully to dashboard-only (already implemented), which is what you demonstrate if it does not arrive.

---

## A.2 Master GPIO registry

> **This section is the single source of truth for every pin in the build.** Where it disagrees with `neural-strata-system-map.html`, the GPS guide, or the have-vs-buy list, **this table wins** and those documents are corrected in H0 (§A.6 HD-1, HD-2, HD-9, HD-10).

### A.2.0 ESP32 GPIO ground rules (read once, apply forever)

| GPIO range | Status | Consequence |
|---|---|---|
| **6, 7, 8, 9, 10, 11** | Connected to the **internal SPI flash** on ESP32-WROOM-32 modules | **Never use.** Anything here risks a boot failure or a corrupt flash. This is why the old ring plan's UART1 on 9/10 is being remapped (HD-1). |
| **34, 35, 36, 39** | **Input only.** No output driver, no internal pull-up/pull-down | Perfect for ADC sensors (SW-420 DO, spare analog). Cannot drive an LED. |
| **32, 33, 34, 35, 36, 39** | **ADC1** | **Use these for all analog reads.** Recommended, because ADC2 is unusable while Wi-Fi is on — and ESP-NOW *is* Wi-Fi. |
| 0, 2, 4, 5, 12, 15 | Strapping pins | Usable, but they have a required level **at boot**. Avoid for anything that must behave predictably from the first millisecond (HD-2). |
| **12** | MTDI — sets the flash regulator voltage | If pulled HIGH at boot, the board may fail to start. **Do not use for a siren or a pull-up-heavy module.** |
| 1, 3 | UART0 / USB | Reserved. Never repurpose. |
| 21, 22 | Default I²C | Used on **nodes** for the MPU-6050. Free on the hubs. |
| 18, 19, 23, 5 | Default SPI | Used for the RC522 on level hubs; on nodes 18/19 are reused for the HX711 (bit-banged, different board — no conflict). |
| 25, 26 | DAC capable | Reserved for the deferred GPS. |
| 27, 4, 13, 14, 16, 17 | Free general purpose | The workhorses of this build. |

> **UART budget fact (a mistake in the GPS guide, corrected here):** the ESP32 has **exactly three** hardware UARTs — UART0, UART1, UART2. There is **no UART3.** With USB on UART0 and the ring occupying UART1 and UART2, there is **no hardware UART left for a GPS**. If GPS is ever re-enabled, it must run **receive-only on a software serial** (see §A.2.5). Also: **no ESP32 pin is 5 V tolerant** — the GPS guide's "5 V-tolerant pin" note is wrong and is corrected in H0.

### A.2.1 SENSOR NODE — N1, N2, N3, N4 (ESP32 DevKit, USB powered)

Identical firmware and identical wiring on all four nodes. The **crack column applies to N2 and N4 only**; on N1 and N3 those two fields are transmitted as `0` so the JSON contract stays identical across nodes.

| # | Signal | GPIO | Bus / mode | Sensor pin | Present on | Wire colour | Termination |
|---|---|---|---|---|---|---|---|
| 1 | MPU-6050 **SDA** | **21** | I²C data | GY-521 `SDA` | all | yellow | **SOLDER** |
| 2 | MPU-6050 **SCL** | **22** | I²C clock | GY-521 `SCL` | all | green | **SOLDER** |
| 3 | MPU-6050 **VCC** | 3V3 | power | GY-521 `VCC` | all | red | **SOLDER** |
| 4 | MPU-6050 **GND** | GND | power | GY-521 `GND` | all | black | **SOLDER** |
| 5 | Displacement pot **wiper** | **32** | ADC1_CH4 | pot middle pin | all | blue | **SOLDER** |
| 6 | Displacement pot **end A** | 3V3 | power | pot outer pin | all | red | **SOLDER** |
| 7 | Displacement pot **end B** | GND | power | pot outer pin | all | black | **SOLDER** |
| 8 | SW-420 **DO** (digital out) | **34** | digital in, input-only | `DO` | all | white | **SOLDER** |
| 9 | SW-420 **VCC** | 3V3 | power — **3V3, never 5 V** | `VCC` | all | red | **SOLDER** |
| 10 | SW-420 **GND** | GND | power | `GND` | all | black | **SOLDER** |
| 11 | SW-420 **AO** (optional analog) | **35** | ADC1_CH7, input-only | `AO` | optional | — | leave open unless diagnosing |
| 12 | HX711 **DT / DOUT** | **19** | bit-banged input | `DT` | **N2, N4** | orange | **SOLDER** |
| 13 | HX711 **SCK** | **18** | bit-banged output | `SCK` | **N2, N4** | yellow | **SOLDER** |
| 14 | HX711 **VCC** | **3V3** | power — **3V3, not 5 V** (HD-4) | `VCC` + `AVCC` jumpered | **N2, N4** | red | **SOLDER** |
| 15 | HX711 **GND** | GND | power | `GND` | **N2, N4** | black | **SOLDER** |
| 16 | Macro-crack pot **wiper** | **33** | ADC1_CH5 | pot middle pin | **N2, N4** | blue | **SOLDER** |
| 17 | Macro-crack pot **end A** | 3V3 | power | pot outer pin | **N2, N4** | red | **SOLDER** |
| 18 | Macro-crack pot **end B** | GND | power | pot outer pin | **N2, N4** | black | **SOLDER** |
| 19 | Onboard status LED | 2 | digital out | — | all | — | onboard |
| — | Power | — | **USB 5 V** from the powered hub | USB port | all | — | **USB** |

**Spare pins on a node:** 4, 13, 14, 16, 17, 25, 26, 27.

**Why GPIO32 and GPIO33 for the two pots, and not ADC2 pins:** because ESP-NOW keeps the Wi-Fi radio on permanently, ADC2 is unavailable for the entire runtime (Rule: ADC1 only). GPIO32/33 are ADC1_CH4/CH5 — the only ADC1 pins that are *not* input-only, which keeps them flexible and matches the already-working N1/N2 harness.

**Why the HX711 shares GPIO18/19 with SPI:** the ESP32 SPI peripheral is idle on a node, and the HX711 protocol is two bare wires with no clock-speed requirement. `HX711` libraries bit-bang both pins. No conflict — but note this differs from the level hub, where 18/19 belong to the RC522.

```
        SENSOR NODE (N1–N4) — all four identical
        ┌───────────────────────────────────────────────────┐
   USB ─┤ 5V  ●                                        ● 3V3 ├──┬── MPU-6050 VCC   (red)
        │                                                        ├── SW-420 VCC     (red)
        │   ESP32 DevKit (30-pin, ESP32-WROOM-32)                ├── disp pot end A (red)
        │                                                        └── macro pot end A (red)  [N2,N4]
        │ GND ●                                        ● GND  ├──┬── MPU / SW-420 / pot ends (black)
        │                                                        └── HX711 GND       (black) [N2,N4]
        │ 21  ● SDA ──────────────────────────── MPU-6050 SDA     (yellow)
        │ 22  ● SCL ──────────────────────────── MPU-6050 SCL     (green)
        │ 32  ● ──────────────────────────────── disp pot wiper   (blue)
        │ 34  ● ◄─────────────────────────────── SW-420  DO       (white)
        │ 18  ● ──────────────────────────────── HX711   SCK      (yellow)  [N2,N4]
        │ 19  ● ◄─────────────────────────────── HX711   DT       (orange)  [N2,N4]
        │ 33  ● ──────────────────────────────── macro pot wiper  (blue)    [N2,N4]
        │  2  ● onboard LED
        └───────────────────────────────────────────────────┘
```

### A.2.2 MAIN HUB (ESP32 DevKit, USB to laptop, bare board)

| # | Function | GPIO | Bus | Wire colour | Termination |
|---|---|---|---|---|---|
| 1 | Uplink to laptop | 1 (TX), 3 (RX) | UART0 via onboard USB | — | **USB cable** |
| 2 | ESP-NOW to 4 nodes | — | internal radio | — | wireless |
| 3 | **Ring port A** → L1: TX | **17** | UART2 TX | orange | **SOLDER** |
| 4 | **Ring port A** → L1: RX | **16** | UART2 RX | orange | **SOLDER** |
| 5 | **Ring port B** → L2: TX | **4** | UART1 remapped TX | teal | **SOLDER** |
| 6 | **Ring port B** → L2: RX | **13** | UART1 remapped RX | teal | **SOLDER** |
| 7 | Common ground bus | GND | power | black | **SOLDER** |
| 8 | Onboard status LED | 2 | digital out | — | onboard |
| — | *Deferred:* GPS module TX | *25* | *software serial RX, receive-only* | — | *not this build* |
| — | *Deferred:* GPS module RX | *none needed* | — | — | *ESP32 never transmits to the GPS* |

**Why UART1 is remapped to 4/13 and not left on GPIO9/10:** GPIO9 and GPIO10 are connected to the internal SPI flash on ESP32-WROOM-32 modules. This is HD-1 — the old plan's pin choice risks boot failure and flash corruption. Remapping costs one line of firmware: `Serial1.begin(115200, SERIAL_8N1, 13, 4)`.

```
        MAIN HUB — port map
        ┌──────────────────────────────────────────────┐
   USB ─┤ ● 1/3  UART0 → laptop (JSON lines @ 2 Hz)    │
        │                                              │
        │ ● 17 TX ┐                                    │
        │ ● 16 RX ┘ UART2 ──► RING PORT A → L1  (orange)
        │                                              │
        │ ●  4 TX ┐                                    │
        │ ● 13 RX ┘ UART1 ──► RING PORT B → L2  (teal) │
        │                                              │
        │ ● GND ──────────► common GND bus (black)     │
        │ ●  2  onboard LED                            │
        └──────────────────────────────────────────────┘
```

### A.2.3 LEVEL HUB — L1 and L2 (identical hardware, different env)

| # | Function | GPIO | Bus | Wire colour | Termination |
|---|---|---|---|---|---|
| 1 | **Ring port A** TX (toward Main) | **17** | UART2 TX | orange | **SOLDER** |
| 2 | **Ring port A** RX (from Main) | **16** | UART2 RX | orange | **SOLDER** |
| 3 | **Ring port B** TX | **4** | UART1 remapped TX | purple | **SOLDER** |
| 4 | **Ring port B** RX | **13** | UART1 remapped RX | purple | **SOLDER** |
| 5 | RC522 **SCK** | **18** | SPI clock | yellow | **SOLDER** |
| 6 | RC522 **MISO** | **19** | SPI data in | orange | **SOLDER** |
| 7 | RC522 **MOSI** | **23** | SPI data out | green | **SOLDER** |
| 8 | RC522 **CS / SDA** | **5** | SPI chip select | white | **SOLDER** |
| 9 | RC522 **RST** | **27** | digital out | grey | **SOLDER** (or tie to 3V3 — see note) |
| 10 | RC522 **VCC** | **3V3** | power — **3.3 V only, never 5 V** | red | **SOLDER** |
| 11 | RC522 **GND** | GND | power | black | **SOLDER** |
| 12 | WS2812B strip **route A** DIN (J1 / J3) | **21** | RMT data out | **blue** | **SOLDER** + inline 330 Ω |
| 13 | WS2812B strip **route B** DIN (J2 / J4) | **22** | RMT data out | **blue** | **SOLDER** + inline 330 Ω |
| 14 | Siren (2-pin buzzer) — drive | **14** | digital out, LOW = sounds | yellow | **SOLDER** |
| 15 | Siren — 5 V feed | 5 V | power | red | **SOLDER** |
| 16 | Gate LED **green** | **32** | digital out (LEDs have built-in resistors) | green | **SOLDER** |
| 17 | Gate LED **red** | **33** | digital out | red | **SOLDER** |
| 18 | Common ground | GND | power | black | **SOLDER** |
| — | Power | — | **USB 5 V** from the hub | — | **USB cable** |

**Notes that matter:**
- **RC522 VCC is 3.3 V. Never 5 V.** This is the fastest way to destroy an RC522. Also add a **100 nF ceramic + 10 µF electrolytic across the module's VCC/GND** right at the module — the RC522 browns out on antenna transmit and this is the usual cause of "it reads one tag then hangs."
- **RC522 RST:** on a GPIO (**27**) is the reliable option if your library needs a hardware reset. If your build has run fine with RST tied to 3V3, keep it and GPIO27 becomes a spare. Decide once in H0 and write it on the panel.
- **Siren on GPIO14, not GPIO12 and not GPIO25.** GPIO12 is the flash-voltage strapping pin (HD-2 — a 3-pin low-level-trigger module can hold it high at boot and the board will not start). GPIO25 was specified in the old system map, which contradicts the have-vs-buy list's GPIO12 — that contradiction is resolved here (HD-9).
- **Gate LEDs on 32/33** — these were assigned to a "25/26 remap" in the GPS guide and to 25 in the system map. 25 and 26 are now reserved for the deferred GPS, and 32/33 are free on a level hub (no analog sensors there).
- **Two separate junction feeds per level hub** (route A and route B), each with its own 330 Ω. The two strips are **not** chained — chaining means one dead strip kills the other.

```
        LEVEL HUB (L1 or L2)
        ┌───────────────────────────────────────────────────────────┐
   USB ─┤ ● 17 TX ┐                                                │
        │ ● 16 RX ┘ UART2 ──► ring port A (± common GND)            │
        │ ●  4 TX ┐                                                │
        │ ● 13 RX ┘ UART1 ──► ring port B (± common GND)            │
        │                                                           │
        │ ● 18 SCK  ┐                                               │
        │ ● 19 MISO │ RC522 (SPI @ 3V3 + 100 nF + 10 µF decoupling) │
        │ ● 23 MOSI │                                               │
        │ ●  5 CS   │                                               │
        │ ● 27 RST  ┘                                               │
        │                                                           │
        │ ● 21 ──[330 Ω]──► route A strip DIN  (J1 on L1, J3 on L2) │
        │ ● 22 ──[330 Ω]──► route B strip DIN  (J2 on L1, J4 on L2) │
        │                                                           │
        │ ● 14 ──► siren −   ;  5V ──► siren +                      │
        │ ● 32 ──► gate LED green     ● 33 ──► gate LED red         │
        └───────────────────────────────────────────────────────────┘
```

### A.2.4 JUNCTIONS J1–J4 — no MCU, by design

| Junction | Level | Route | Driven by | Strip DIN comes from | LEDs |
|---|---|---|---|---|---|
| **J1** | 1 | A | L1 | L1 **GPIO21** via 330 Ω | 8 (strip set to 8 in firmware) |
| **J2** | 1 | B | L1 | L1 **GPIO22** via 330 Ω | 8 |
| **J3** | 2 | A | L2 | L2 **GPIO21** via 330 Ω | 8 |
| **J4** | 2 | B | L2 | L2 **GPIO22** via 330 Ω | 8 |

Each junction feed is **three wires**: **blue = data**, **red = 5 V**, **black = GND**. Termination: **SOLDER both ends.**

> **The trap that catches everyone:** the **red and black in the junction bundle are the LED rail, not the level hub's 5 V pin.** The data wire (blue) is the only wire that comes from the ESP32. See §A.3.3 for why, and HD-6 for what happens if you get it wrong.

### A.2.5 Reserved for the deferred GPS (documented so nobody re-invents it)

| GPS pin | Main Hub | Note |
|---|---|---|
| VCC | 5 V | module has its own regulator |
| GND | common GND | same bus as everything |
| TX (module → ESP32) | **GPIO25** | **receive-only software serial** — the ESP32 never needs to send to a GPS |
| RX (ESP32 → GPS) | **not connected** | saves a pin and an entire UART channel |

**Why software serial and not a hardware UART:** all three ESP32 hardware UARTs are already committed (USB, ring port A, ring port B). At 9600 baud a receive-only software serial is reliable even with Wi-Fi active. The GPS guide's claim that GPIO13 is a "5 V-tolerant UART3 RX" is factually wrong on both counts and is corrected in H0.

---

## A.3 Electrical contracts

### A.3.1 Voltage domains — three rails, one ground

| Rail | Where it exists | Feeds | Never connect to |
|---|---|---|---|
| **5 V** | USB from the powered hub, and the separate LED rail | WS2812B strips, siren's + lead, ESP32 `5V` pin (internal regulator input), RC522 is *not* here | Any GPIO; the HX711; the MPU-6050; SW-420 |
| **3.3 V** | Each board's onboard regulator, `3V3` pin | MPU-6050, SW-420, both pots' end A, HX711 (VCC **and** AVCC), RC522 | The siren's 5 V lead |
| **GND** | **One common bus** across all 7 boards, both strips' rails, *both* power sources, and the ring | everything's return path | — |

**Two independent 5 V sources on one ground is normal and correct.** The USB hub adapter and the LED-rail charger must share **GND only** — never tie their 5 V outputs to each other.

### A.3.2 Current budget (why the LED rail exists)

| Load | Idle | Peak | Notes |
|---|---|---|---|
| ESP32 board (Wi-Fi on, ESP-NOW 2 Hz) | ~90 mA | **~250 mA** | peak during radio TX bursts |
| MPU-6050 + SW-420 + pots (per node) | ~5 mA | ~8 mA | negligible |
| HX711 (N2, N4) | ~1.5 mA | ~1.5 mA | negligible |
| RC522 (per level hub) | ~25 mA | ~100 mA | antenna transmit bursts |
| **7 boards subtotal** | **~0.75 A** | **~1.9 A** | compared to the hub's 4 A adapter — comfortable |
| **WS2812B strip, 8 LEDs, full white** | — | **480 mA** | per strip |
| **4 strips, worst case** | — | **1.92 A** | **this is the load that must not go through the ESP32 boards** |

**Design conclusion:** the 4 A adapter runs the 7 boards with real margin (≈2× headroom on peak). The LED strips get their own 5 V / 2 A source. In practice the demo never shows 4 strips at full white simultaneously — the patterns are chases and strobes at partial brightness — but a worst case that browns out the main hub mid-demo is exactly the failure you cannot explain on stage.

### A.3.3 The LED rail — the single most important power decision in this build

```
   USB hub (5V/4A)                         5V/2A charger  (LED rail)
        │                                        │
        ├── USB ──► MAIN hub                     ├── 5V  ──┬── J1 strip VDD
        ├── USB ──► N1 … N4 (4 ports)            │         ├── J2 strip VDD
        ├── USB ──► L1                           │         ├── J3 strip VDD
        └── USB ──► L2                           │         └── J4 strip VDD
                                                 │
   GND ●───────●──────────●───────────●──────────┘      (ONE common ground bus)
        └─ all 7 boards ─┴─ all 4 strips ─┴─ both supplies
```

**Rules for the rail:**
1. A **1000 µF** electrolytic across 5 V/GND **at each strip** (inrush protection — WS2812B draws a surge on power-up).
2. A **1N4148 in series with the first LED's VDD** at each strip head — see HD-5 for why this is what makes a 3.3 V data signal reliable.
3. **330 Ω** in series on the data line, soldered inline, physically **at the hub end** (before the long run).
4. The rail's 5 V wire gauge: 22 AWG solid is fine for these lengths and currents (≤ 1.5 A per branch, ≤ 2.5 m).
5. **Never power a strip from the level hub's 5 V pin.**

### A.3.4 Antenna and mechanical keep-outs

| Rule | Why |
|---|---|
| **15 mm clear around the ESP32 PCB antenna** (the end opposite the USB connector) | Wires and foil tape detune the antenna; ESP-NOW range collapses |
| **No backbone wire crossing over an antenna** | Same reason |
| Mount all 7 boards with the **antenna edge overhanging** the panel edge or facing open air | Measurable difference at 1 m |
| Keep the **USB hub's power brick ≥ 20 cm** from all antennas | Switching noise + metal |
| The "cover N2's antenna" demo requires **nothing** metallic within 15 mm of N2 | Otherwise the demo does not work |
| RFID tags: keep them **≥ 10 cm** from the RC522 when not being read | Passive tags near the reader cause ghost reads |

### A.3.5 ESD and hot-plug rules

- **Never solder or rewire a powered board.** Unplug the USB first, every time.
- Touch the metal of the laptop's chassis before handling the BF350 or the RC522 (both ESD-sensitive).
- Never hot-plug the ring wires: power down L1, L2 and MAIN before changing any backbone connection.
- Keep the BF350's leads **shorted together with a bit of foil** until you solder them — a bare foil gauge picks up static easily.

---

## A.4 Termination registry — solder, jumper, or bond, for every joint

This is the table the user asked for: **for each connection in the build, how it is made.**

### A.4.1 By connection class

| Connection | Method | Where it happens | Detail |
|---|---|---|---|
| Sensor → node ESP32 (MPU, pots, SW-420, HX711) | **SOLDER** | H1, H2 | Bench with jumpers first, then solder, then re-test (Rule 1). 22 AWG solid, pre-tinned, ≤ 8 mm stripped |
| Node / hub / main power | **USB cable** | H4, H8 | One 1 m A-to-(micro-)B cable per board |
| HX711 ↔ BF350 bridge network | **SOLDER** | H2 | 4 terminals + 3 completion resistors; solder on the HX711 terminal block or directly to pads |
| BF350 gauge → its leads | **SOLDER** (already done by the vendor) | H2 | Never re-solder to the gauge itself — solder to the supplied leads only |
| **BF350 gauge → rigid strip** | **BOND (cyanoacrylate or epoxy)** | H2 | **Never hot glue. Never foam.** See §A.4.2 |
| Macro pot body → seam half | **BOND (glue dot)** + lever | H2 | Body on one half, finger on the other |
| Inline 330 Ω on strip data | **SOLDER**, insulated with electrical tape | H7 | Physically at the hub end |
| Ring backbone (7 wires) | **SOLDER** | H5 | Directly to header pins, **one continuous piece per wire, no mid-run splices** |
| Cut switch in the ring | **SOLDER** into the switch tabs | H5 | Put the switch on the **service panel**, not buried in the diorama |
| Junction feed (3 wires per junction) | **SOLDER** both ends | H7 | One continuous piece per wire, no mid-run splices |
| RC522 / strips / siren / LEDs → level hub | **SOLDER** | H6 | Same SOP as nodes |
| LED rail distribution | **SOLDER** | H8 | Bus joints taped, strain-relieved |
| Common GND bus | **SOLDER** | H8 | One bus, star-pointed at the hub |
| Boards → diorama | **Double-sided foam tape / nylon standoffs** | H9 | No drilling through boards |
| Cables → diorama | **Glue dots + electrical tape** | H9 | Anchors every 15 cm; **never through an antenna keep-out** |
| Toggle switch mounting | Through the **PVC foam service panel** | H5 | Panel is removable for demo handling |

### A.4.2 The four joints that decide whether this build works

**1. The strain gauge bond (H2).** This is the only joint in the project whose *quality* is the measurement.
- Surface prep: light sand (240 grit), wipe with isopropyl, let it dry fully.
- A **thin** layer of cyanoacrylate or 2-part epoxy. Thick glue is a compliant layer that absorbs the strain you are trying to measure — a thick bond can halve your sensitivity.
- Gauge centred on the crack line; leads dressed so they cannot pull on the gauge.
- Cure fully (cyanoacrylate: minutes; epoxy: the full cure time on the pack) **before** zeroing the baseline in firmware.
- **Never hot glue, never foam.** Foam strain exceeds the gauge's 2% limit and the glue will not hold. This is already a written decision — it is repeated here because it is the single most common way this sensor is wasted.

**2. The header joints (H1–H8).** 22 AWG solid in a 2.54 mm header is a *tight* fit that feels like it works. It does not survive handling.
- Strip ≤ 8 mm, pre-tin the wire, insert, reflow for 2–3 s.
- **Tug test every joint** with gentle force after it cools — a joint that moves is a joint that fails on stage.
- Check adjacent pins for bridges with the multimeter in continuity mode **before** power.
- Label both ends with tape + marker while the wire is still in your hand.

**3. The bridge completion network (H2).** Four resistors in a specific topology, and it must be balanced before it is useful.
- Solder the network on a small piece of perfboard or directly across the HX711 terminals — **not** floating in air where a wire can touch the next one.
- Select-match the three fixed resistors: measure each with the DMM, pick the three closest to each other (**within 0.3 Ω**). Free with the meter you already have, and it removes most of the offset that would otherwise eat your ADC range.
- Verify E+/E− and A+/A− are not shorted before powering.

**4. The cut-switch joints (H5).** These are **demo actuators**. They will be flipped dozens of times in rehearsal.
- Use a **service loop** (a 5 cm slack loop) on both sides of the switch so flipping does not pull on the solder joint.
- Anchor the wires to the panel with a glue dot 2 cm from each switch tab.
- Verify continuity across the switch in both positions before the first rehearsal.

---

## A.5 Tools, consumables and workspace

| Tool | Mandatory? | Used for |
|---|---|---|
| **Digital multimeter** | **YES — non-negotiable** | continuity, 3.3 V/5 V checks, current draw, resistor matching, joint testing |
| Soldering iron (fine tip, ~330 °C) + stand + sponge | YES | ~80 joints |
| Solder (0.8 mm) + flux pen | YES | — |
| Solder wick / pump | YES | you will bridge adjacent header pins |
| Side cutters + 22 AWG wire strippers | YES | — |
| Precision tweezers | YES | BF350 lead forming, SMD-free but fiddly work |
| Helping-hands / small vice | Recommended | two-hand soldering |
| Isopropyl alcohol + cotton buds | YES | flux cleanup before taping |
| Masking tape + marker | YES | **label every wire both ends.** The 7-wire backbone is unbuildable without this |
| Laptop with PlatformIO | YES | flashing each board |
| Phone camera | Useful | photograph the wiring **before** you close the panel up |

### A.5.1 Workspace rules

1. **One subsystem on the bench at a time.** Never have two partially wired boards powered simultaneously — you will cross-feed and misdiagnose.
2. **A "done" bin and an "unknown" bin.** Anything whose continuity has not been verified goes in unknown.
3. **Photograph every completed board** with the wires labelled, from the top. This picture is your wiring diagram when something fails at 11 p.m.
4. **Keep the diorama out of the soldering area** until H9 — flux spatter and dropped solder destroy paint and foam.
5. **The BF350 and the RC522 stay in their anti-static bags** until their phase.

---

## A.6 Hardware defect audit — 16 findings that shape the phase order

Every item below was read out of the current documents or derived from the electrical facts in §A.2.0. Each is closed by a named phase. **Items HD-1 through HD-4 will cost you a board or a build day if they are not resolved before wiring.**

| # | Finding | Where it lives | Consequence | Closed in |
|---|---|---|---|---|
| **HD-1** | **Ring port A is assigned to GPIO9/GPIO10 (UART1 defaults).** GPIO9/10 are connected to the internal SPI flash on ESP32-WROOM-32 modules. | `neural-strata-system-map.html` §2.2/§2.3 | Intermittent boot failures, potential flash corruption, and a bug that appears only on some boards — the worst kind to debug on stage. | **H4, H5** (remap to GPIO4/13) |
| **HD-2** | **The siren is assigned to GPIO12** in the have-vs-buy list. GPIO12 is MTDI — the flash-regulator strapping pin. A 3-pin low-level-trigger module can hold it high at boot. | `Have_vs_Buy.pdf` §1 | The level hub may **fail to boot** whenever the siren module is connected. Phantom fault that "goes away" when you unplug the buzzer. | **H6** (move to GPIO14) |
| **HD-3** | **Only one 350 Ω completion resistor per crack channel.** A single-gauge Wheatstone bridge needs **three** fixed arms. | `neural-strata-crack-sensor.md` BOM, `Have_vs_Buy.pdf` | The bridge cannot be built as drawn; the measurement would be meaningless or railed. Deliverable blocked at the bench. | **H2** (buy 6, select-match) |
| **HD-4** | **HX711 is specified at 5 V VCC** with `AVCC jumpered`. HX711's `DOUT` swings to DVDD, so a 5 V-powered module feeds **5 V logic into GPIO19** — an ESP32 pin that is not 5 V tolerant. | `neural-strata-crack-sensor.md` wiring block | GPIO19 degradation or failure; a slow, confusing death rather than an obvious one. | **H2** (run HX711 at 3.3 V) |
| **HD-5** | **WS2812B is a 5 V part with V_IH ≈ 0.7 × VDD = 3.5 V**, driven by a 3.3 V GPIO. | Not documented anywhere | Flickering, wrong colours, or the first LED ignoring commands — classically blamed on "bad strips" | **H7** (1N4148 series diode on the first LED's VDD) |
| **HD-6** | **The junction strips are fed from the level hub's 5 V pin** (implied by the wiring map's "data + 5V + GND" per strip). 4 strips at full white ≈ 1.9 A — far beyond what a USB-powered ESP32 board should pass. | `neural-strata-system-map.html` §2.3 | Mid-demo brownout of L1/L2 (the hubs reset when the strips flash white). Exactly the failure that ruins a live demo. | **H8** (separate LED rail) |
| **HD-7** | **"Cut hop-2 GND" is listed as an option for the ring-cut demo.** With a **common GND bus** across all three hubs, cutting one hop's GND does nothing — current simply returns through the other two hubs' ground. | `neural-strata-system-map.html` §2.4 rule ④ | The headline "cut the spine" demo silently fails; L2 keeps working and the presenter has no explanation. | **H5** (switch a signal wire, not GND) |
| **HD-8** | **One SPST switch cuts only one direction of hop 2.** Each hop is full-duplex over two wires; breaking one leaves the other direction alive. | `neural-strata-system-map.html` §2.4 | A "broken" segment that still delivers commands — looks like a firmware bug rather than a wire cut. | **H5** (switch W4; optional DPST for both) |
| **HD-9** | **The siren pin contradicts itself across documents:** GPIO25 (system map) vs GPIO12 (have-vs-buy). | Two documents | Wiring-day ambiguity; someone wires it wrong and blames the firmware. | **H0, H6** (resolved to GPIO14) |
| **HD-10** | **The Main Hub's second ring port contradicts itself:** GPIO17/16 (system map) vs a "25/26 remap" (GPS guide), while the GPS guide also claims "only L1/L2 use 13/14 for gate LEDs" — but 13/14 are assigned to nothing else in the map. | Two documents | Same failure mode as HD-9, on the most critical board. | **H0, H4** |
| **HD-11** | **The GPS guide states the ESP32 has a "UART3" and that GPIO13 is "5 V-tolerant".** The ESP32 has exactly three UARTs, and **no ESP32 pin is 5 V tolerant.** | `Neural_Strata_GPS_Implementation.pdf` §3 | A judge with embedded experience reads this and the credibility of the whole document set drops. | **H0** (correct the document) |
| **HD-12** | **No decoupling specified on the RC522.** The MFRC522 browns out on antenna transmit; this is the number-one cause of RC522 flakiness. | Not documented | "It reads one tag, then stops." Usually misdiagnosed as a bad module. | **H6** (100 nF + 10 µF at the module) |
| **HD-13** | **No strain relief or service loop specified anywhere.** Wires soldered straight to headers with no anchoring. | Not documented | Fatigue fractures at joints during handling; intermittent faults that only appear when the panel is moved. | **H5, H9** |
| **HD-14** | **The hub's MAC address is compiled into every node's firmware.** Swapping the Main Hub board requires reflashing all four nodes. | Firmware/SRS Appendix D | A dead hub on demo morning becomes a multi-hour recovery instead of a 10-minute swap. | **H0, H3** (document it; label the board "DO NOT SWAP") |
| **HD-15** | **No current measurement plan** — the inline USB meter was deliberately not bought, so the power slide has no measurement behind it. | `Have_vs_Buy.pdf` §3 | An unsupported number on a slide. | **H8** (measure with the DMM in 10 A series — free) |
| **HD-16** | **Board-variant risk:** 3 existing boards + 4 bought. If they are different ESP32 form factors (30-pin DevKit v1 vs 38-pin DevKitC), the GPIO *numbers* still match but the **physical header positions do not.** | Not documented | Soldering day: you follow a diagram and land a wire on the wrong physical pin. Destroys hours. | **H0** (photograph and compare all 7) |

---

## A.7 Phase map, dependencies and effort

```
   ┌───────────────────────────────────────────────────────────────┐
   │  H0  Bench truth: inventory, pin freeze, measurement rules     │  0.5 d
   └───────────────┬───────────────────────────────────────────────┘
                   ▼
   ┌───────────────────────────────────────────────────────────────┐
   │  H1  Standard sensor node ×4  (MPU · pot · SW-420)             │  1.5 d
   └───────┬───────────────────────────────────────┬───────────────┘
           ▼                                       ▼
 ┌───────────────────────────┐         ┌───────────────────────────────┐
 │  H2  Two-stage crack      │         │  H3  Surface mesh (ESP-NOW,    │  1.5 d
 │      (BF350 · HX711 · pot)│  2.0 d  │      MAC map, relay, last-gasp)│
 └───────────┬───────────────┘         └───────────┬───────────────────┘
             │                                     ▼
             │                     ┌───────────────────────────────────┐
             │                     │  H4  Main Hub (uplink + 2 ports)  │  1.0 d
             │                     └───────────────┬───────────────────┘
             │                                     ▼
             │                     ┌───────────────────────────────────┐
             │                     │  H5  Ring backbone (7 wires + cut)│  1.5 d
             │                     └───────────────┬───────────────────┘
             └────────────────────►┌───────────────────────────────────┐
                                   │  H6  Level hubs L1 / L2           │  2.0 d
                                   └───────────────┬───────────────────┘
                                                   ▼
                                   ┌───────────────────────────────────┐
                                   │  H7  Junctions J1–J4 (passive)    │  1.0 d
                                   └───────────────┬───────────────────┘
                                                   ▼
                                   ┌───────────────────────────────────┐
                                   │  H8  Power distribution & GND bus │  1.0 d
                                   └───────────────┬───────────────────┘
                                                   ▼
                                   ┌───────────────────────────────────┐
                                   │  H9  Diorama integration + panel  │  1.5 d
                                   └───────────────────────────────────┘
```

| Phase | Title | Effort | Demo-critical | Depends on | Closes |
|---|---|---|---|---|---|
| H0 | Bench truth, inventory & pin freeze | 0.5 d | **YES** | — | HD-9, HD-10, HD-11, HD-14, HD-16 |
| H1 | Standard sensor node ×4 | 1.5 d | **YES** | H0 | — |
| H2 | Two-stage crack system (N2, N4) | 2.0 d | **YES** | H1 | HD-3, HD-4 |
| H3 | Surface mesh | 1.5 d | **YES** | H1 | HD-14 |
| H4 | Main Hub | 1.0 d | **YES** | H3 | HD-1, HD-10 |
| H5 | Ring backbone | 1.5 d | **YES** | H4 | HD-1, HD-7, HD-8, HD-13 |
| H6 | Level hubs L1 / L2 | 2.0 d | **YES** | H5 (or H1 for bench) | HD-2, HD-9, HD-12 |
| H7 | Junctions J1–J4 | 1.0 d | **YES** | H6, H8 (rail) | HD-5 |
| H8 | Power distribution & grounding | 1.0 d | **YES** | H6 | HD-6, HD-15 |
| H9 | Diorama integration & pre-demo | 1.5 d | **YES** | all | HD-13 |

**Parallel split for 6 people:**
- **Pair A (soldering lead + 1):** H1 → H2 → H6 → H7
- **Pair B (firmware lead + 1):** H3 → H4 → H5 (firmware and wiring interleaved; the same person should flash and wire the ring)
- **Pair C (mechanical + 1):** preparations for H8/H9 (panel cutting, prop painting, cable labelling) can all happen in parallel from day 1, then join at H7/H8

**Critical path:** H0 → H1 → H3 → H4 → H5 → H6 → H7 → H8 → H9. H2 hangs off H1 and only feeds the AI story, so if time collapses, H2 slips and the demo still runs (see C.6 cut list).

---

# PART B — THE PHASES

Every phase uses the same ten-part template. **§5 "Changes to the previous prototype" is the one to read twice** — it is what stops a working subsystem from being broken by the next one.

---
---

## PHASE H0 — Bench Truth: Inventory, Pin Freeze & Measurement Discipline

> **Goal** — Freeze the wiring map, reconcile the contradictions between the existing documents, and establish the measurement habits that make the other nine phases safe.
> **Effort** — 0.5 day (half a day, and it is the best-spent half day in the build) · **Demo-critical — YES** · **Depends on** — nothing
> **Demoable result** — a printed one-page pin map taped above the bench, all 7 boards photographed and identified, and not a single ambiguity left in the documents.

### H0.1 Audit — what exists today

- **`neural-strata-system-map.html`** — a v2 wiring map. Specifies the ring ports on GPIO10/9 and 17/16 (HD-1, HD-10), the siren on GPIO25, the strips on 21/22, and the RC522 on 18/19/23/5.
- **`Neural_Strata_Have_vs_Buy.pdf`** — the bought inventory. Specifies the siren on **GPIO12** (HD-2, HD-9) and the crack channels at SCK 18 / DT 19, macro pot 33.
- **`neural-strata-crack-sensor.md`** — the crack decision. Full pin map for N2/N4, the bridge wiring diagram, and the purchase list with **one** completion resistor per channel (HD-3), HX711 at 5 V (HD-4).
- **`Neural_Strata_GPS_Implementation.pdf`** — a deferred add-on that nonetheless claims a "UART3" and a "5 V-tolerant pin" (HD-11) and a "25/26 remap on the Main Hub" (HD-10).
- **`Neural_Strata_Shopping_List.pdf`** — the original buy list, already partially superseded.
- **Physical state:** 3 ESP32 boards previously wired into a working 3-board prototype (hub + N1 + N2, MPU-6050 on I2C 21/22, pot on 32, USB serial to a Mac). 4 new boards, 2 new MPUs, 2 new pots, 4 SW-420s, 4 strips, 2 RC522s, crack parts — bought, mostly unopened.

**Nothing is wrong with the previous prototype.** It works. H0 is about making sure the *next* 13 days do not invalidate it.

### H0.2 Target behaviour

1. **One pin map.** The registry in §A.2 is printed, taped above the bench, and every other document is corrected to match it.
2. **Every board identified.** Part number, pin count, header layout, USB connector type, MAC address — recorded on a card taped to each board.
3. **Every wire labelled.** Both ends, before it leaves the bench.
4. **Continuity and voltage measured, not assumed.** The multimeter is used on every new joint from this point forward.
5. **The known-good baseline is preserved.** The working N1/N2 firmware and wiring is photographed, so it can be restored if an experiment goes wrong.

### H0.3 The reconciliation work (do this first, it takes 40 minutes)

| # | Task | Output |
|---|---|---|
| 1 | Lay out all 7 boards side by side | Photo. Confirm all are the **same form factor**. If any differ (30-pin DevKit v1 vs 38-pin DevKitC), write the pin position differences on the card for that board. **(HD-16)** |
| 2 | Note each board's **USB connector type** (micro-B / Type-B / C) and its port position | This determines the 7 USB cables you buy. Do it before shopping. |
| 3 | Power each board over USB, run `esptool`/PlatformIO `device monitor` to read and record its **MAC address** | MAC table. The hub's MAC is compiled into all four nodes **(HD-14)** — label the hub **"DO NOT SWAP"**. |
| 4 | Confirm each board's `3V3` pin reads **3.28–3.33 V** and `5V` reads **4.90–5.10 V** | Catches a board with a failing regulator *before* it is soldered into the build. |
| 5 | Print §A.2 and tape it above the bench | One source of truth on the wall. |
| 6 | Correct the two PDFs' factual errors (HD-9, HD-10, HD-11) | A v1.1 of the system map and the GPS guide, so no judge finds the contradiction. |

### H0.4 Wiring

**None.** H0 adds no hardware. It is the only phase with no wiring table, and that is deliberate — Rule 1 starts here.

### H0.5 Firmware changes

- **None yet.** But record in `platformio.ini` the board variant actually in use (`board = esp32dev` for the 30-pin DevKit) so all 7 envs match the physical boards.
- Create the 5 envs now, even though 4 are empty stubs: `env:node` (with `-D NODE_ID=n` build flag), `env:hub`, `env:lvl1`, `env:lvl2`. Building a stub for each board proves the toolchain sees all 7 before you depend on it.

### H0.6 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| Working N1/N2 wiring | **Photograph and do not touch.** | It is your reference implementation and your rollback. |
| `system-map.html` | Correct pin assignments per §A.2 | HD-9, HD-10 resolved |
| `GPS_Implementation.pdf` | Correct "UART3" → "three UARTs total", "5 V-tolerant" → "not 5 V tolerant, level shift required if used" | HD-11 |
| `Have_vs_Buy.pdf` | Correct siren GPIO12 → GPIO14; crack BOM 2 → 6 completion resistors | HD-2, HD-3 |
| Mac-side | Install PlatformIO board definitions; confirm each port enumerates | Avoids a toolchain scramble on wiring day |

### H0.7 Assembly order

1. Unbox all parts. Lay out by subsystem (node kit ×4, crack kit ×2, hub kit, level kit ×2, strip kit ×4).
2. **Count against the BOM.** Anything missing that would stall H1 or H2 gets ordered **today** — Indian component orders take 2–5 days and a missing ₹5 resistor costs you three days.
3. Photograph everything, including part numbers on labels.
4. Label the boards `N1 N2 N3 N4 MAIN L1 L2` with tape. **Permanent marker on a label, not on the board's silk screen.**
5. Write the MAC of each board on its label.

### H0.8 Bench acceptance test

| Check | Instrument | Expected |
|---|---|---|
| Each board enumerates on USB | Mac `ls /dev/cu.*` | 7 device nodes appear, one per board |
| Each board's regulator | DMM DC volts, `3V3` → GND | 3.28 – 3.33 V on all 7 |
| Each board's 5 V rail | DMM DC volts, `5V` → GND | 4.90 – 5.10 V on all 7 |
| Multimeter's own sanity | DMM continuity across its own probes | **0.1 – 0.5 Ω** (a meter with a dying battery reads 2–3 Ω and silently ruins every later "good joint" test) |
| Continuity across each 330 Ω | DMM resistance | 326 – 334 Ω (5 pieces) |
| Completion resistors | DMM resistance | each 346.5 – 353.5 Ω; **select the 3 closest per channel, within 0.3 Ω of each other** |
| Toggle switches | DMM continuity, both positions | open in one, < 1 Ω in the other |

**Pass criterion:** all seven boards alive, all values in range, three matched resistor trios set aside in a labelled bag.

### H0.9 Risks & rollback

| Risk | Mitigation |
|---|---|
| A board is dead on arrival | Discovered now, not on wiring day. 4 new boards arrived; if one is dead you have ~10 days to replace it. |
| Different form factors among the 7 boards | Discovered now, while it is a labelling task, not a mis-solder. |
| Ordering delay on the corrected BOM items | Order on day 1. The ₹75 of resistors and diodes is not worth a stalled build. |

**Rollback:** H0 changes no hardware, so there is nothing to roll back. It can only save time.

### H0.10 Viva line

> *"Before we soldered anything, we froze the pin map and reconciled every document against the ESP32's actual electrical constraints — flash-bus pins, strapping pins, ADC2 being unavailable while Wi-Fi is on. Two of our own earlier documents disagreed about which pin drove the siren. Finding that on paper costs ten minutes; finding it on the bench costs a day."*

---
---

## PHASE H1 — The Standard Sensor Node (N1, N2, N3, N4)

> **Goal** — Four identical nodes, each with a 6-axis IMU, a displacement potentiometer and a vibration sensor, all transmitting on ESP-NOW.
> **Effort** — 1.5 person-days (four nodes × ~1.5 h each, plus a shared bring-up) · **Demo-critical — YES** · **Depends on** — H0
> **Demoable result** — four node cards on the dashboard, each showing live tilt and displacement, and a vibration event counter that ticks when you tap the SW-420.

### H1.1 Audit — what exists today

- **N1 and N2 already work**: ESP32 + GY-521 on I2C (GPIO21/22) + a 10 kΩ slide pot on GPIO32, streaming JSON over ESP-NOW at 2 Hz. This is the reference implementation.
- **N3 and N4 are new boards** with new MPUs and new pots.
- **All four SW-420s are new and unmounted.**
- The have-vs-buy list notes a **retrain is needed (~30 min)** once vibration becomes an event-rate feature rather than an analog RMS: *"Piezo vibration sensors → replaced by SW-420 (digital — AI feature becomes vibration event rate)"*.
- The SRS §3.2 states the pot maps 12-bit 0–4095 → 0–100 mm.

### H1.2 Target behaviour

1. **One firmware image, four build flags.** `env:node` with `-D NODE_ID=1..4`; nothing else differs. If a node needs a different firmware, the design has failed.
2. **Identical wiring on all four** — including the two crack pins (18/19/33) which simply read 0 on N1/N3. This keeps the JSON contract and the wiring diagram single.
3. **Swift, mechanical check on every join:** the node is complete when a *finger press on the pot* and a *tap on the SW-420* both show up on the dashboard within ~2 s.
4. **SW-420 event counting, not analog.** The firmware counts digital pulses in a 2 s window and transmits the **count** (`vib`). This is the field the AI's `vib_count_60` / `impulse_periodicity` features are built from.
5. **A 60-second "quiet" baseline** on each node before it is trusted — the MPU's tilt offset and the pot's rest position are recorded at boot.

### H1.3 Wiring — one node, complete

| # | From (ESP32) | To (sensor) | Wire | Colour | Length | Termination |
|---|---|---|---|---|---|---|
| 1 | `3V3` | GY-521 `VCC` | 22 AWG solid | red | 70 mm | **SOLDER** (jumper for bring-up) |
| 2 | `GND` | GY-521 `GND` | 22 AWG solid | black | 70 mm | **SOLDER** |
| 3 | `GPIO21` | GY-521 `SDA` | 22 AWG solid | yellow | 70 mm | **SOLDER** |
| 4 | `GPIO22` | GY-521 `SCL` | 22 AWG solid | green | 70 mm | **SOLDER** |
| 5 | `GPIO32` | slide pot **wiper** (middle) | 22 AWG solid | blue | 120 mm | **SOLDER** |
| 6 | `3V3` | slide pot **end A** | 22 AWG solid | red | 120 mm | **SOLDER** |
| 7 | `GND` | slide pot **end B** | 22 AWG solid | black | 120 mm | **SOLDER** |
| 8 | `GPIO34` | SW-420 `DO` | 22 AWG solid | white | 120 mm | **SOLDER** |
| 9 | `3V3` | SW-420 `VCC` | 22 AWG solid | red | 120 mm | **SOLDER** — **3V3, never 5 V** |
| 10 | `GND` | SW-420 `GND` | 22 AWG solid | black | 120 mm | **SOLDER** |
| 11 | — | board → powered USB hub | USB cable 1 m | — | 1 m | **USB** |

**Total: 10 soldered wires + 1 USB cable per node. 40 wires across four nodes.**

**Spares on every node:** 4, 13, 14, 16, 17, 25, 26, 27 (17 reserved for nothing on a node — it belongs to the hubs).

### H1.4 Firmware changes

| Change | Detail |
|---|---|
| Node payload gains `vib` and `micro`/`macro` fields | `{"v":2,"node":n,"seq":s,"ms":t,"disp":d,"tilt":x,"acc":a,"vib":c,"micro":0,"macro":0,"rssi":r,"flags":f}` — crack fields are **0 on N1/N3** so the contract is uniform |
| SW-420 handling | **Interrupt-driven pulse counter on GPIO34**, count reset and transmitted every 2 s. Do not poll — polling misses short pulses and destroys the `impulse_periodicity` feature input. |
| Real acceleration transmitted | Read the MPU-6050's accelerometer registers and send `acc` directly instead of differentiating tilt in software. (Matches backend Phase 1, HD-9 equivalent on the software side.) |
| Node `ms` uptime field | Enables the backend's measured-Δt fix |
| Pot mapping | `disp = analogRead(32) / 4095.0 * 100.0` mm, with `analogSetWidth(12)` and a documented rest position |
| Boot sequence | 2 s LED blink, read MPU WHO_AM_I, initialise the pot baseline, then start transmitting |

### H1.5 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| **N1 and N2 (working)** | **Retrofit the SW-420** — three new wires (34 / 3V3 / GND) and the shared firmware. Nothing else changes; the MPU and pot stay exactly as they are. | Brings the working nodes onto the same contract as the new ones |
| N1/N2 firmware | Replace the existing node sketch with the shared `env:node` build | One firmware, four instances — no drift |
| Node JSON | Additive fields only. The backend's `migrate_payload` accepts the old shape too, so you can flash one node at a time. | No all-or-nothing flash day |
| Breadboards | **Remove.** Nodes are soldered to the sensors; the breadboard goes back to the bench bin. | Project decision |
| Old piezo sensors (if any) | Retire to the spares bin | SW-420 supersedes them |
| AI retrain | Re-run `01 → 02` so `vib` becomes a real feature | ~30 min, per the earlier decision |

### H1.6 Assembly order (per node)

1. **Bench-first (Rule 1).** Breadboard the MPU + pot + SW-420 with jumper wires. Flash the firmware. Confirm all three channels move on the dashboard.
2. **Power down.** Unplug USB.
3. Cut and pre-tin 10 wires to length. **Label both ends** with tape + marker.
4. Solder them to the ESP32 header pins first (one wire at a time, tug-test each).
5. Solder the sensor ends. Keep the SW-420's three wires side-by-side so they cannot swap.
6. **Continuity check** every pin before applying power: `3V3`↔`GND` must read **open** (not 0 Ω).
7. Power up, re-verify all three channels.
8. Photograph the finished node from above. Repeat for the next node.

### H1.7 Bench acceptance test

| Check | Instrument / action | Expected |
|---|---|---|
| No short before power | DMM continuity, `3V3` → `GND` | **Open circuit** (> 1 MΩ) |
| Rail voltage | DMM DC V, `3V3` → `GND` | 3.28 – 3.33 V |
| Node draw (idle, no Wi-Fi TX) | DMM in 10 A range, in series with the 5 V USB line | 80 – 120 mA |
| Node draw (ESP-NOW transmitting) | Same, while streaming | peaks 180 – 280 mA |
| I²C presence | Serial console | MPU-6050 WHO_AM_I = `0x68` |
| Tilt channel | Tilt the node by hand | `tilt` moves smoothly, returns near baseline |
| Displacement channel | Slide the pot end to end | `disp` sweeps ~0 → ~100 mm, monotonic |
| Vibration channel | Tap the SW-420 firmly | `vib` count increments in that 2 s window, returns to 0 |
| **Cross-talk check** | Tap the SW-420 | `disp` and `tilt` must **not** move (if they do, the SW-420 is injecting noise into the 3V3 rail → add a 100 nF cap at the module) |
| Radio link | Dashboard, all 4 nodes | Four cards, updating at 2 Hz, no gaps > 1 s |

### H1.8 Risks & rollback

| Risk | Mitigation |
|---|---|
| SW-420 false-triggers from table bumps | It is a *vibration* sensor; this is information, not noise. Mount each node on a foam pad and set the on-board sensitivity pot to its least-sensitive usable position. |
| MPU-6050 I²C lockups on long runs | Runs are ≤ 150 mm. If a node locks up, shorten and re-route away from the pot wires. |
| A node's header pads lift while soldering | Pre-tin fast, ≤ 3 s dwell. If a pad lifts, that node becomes a spare bench node and a new one takes its place. |
| 4 nodes at 2 Hz saturate the hub's radio | 8 packets/s is trivial for ESP-NOW. Non-issue at this scale; it *would* matter at 50 nodes. |

**Rollback:** N1 and N2's existing wiring is photographed (H0). Any node can be reverted to the v1 firmware by reflashing.

### H1.9 Viva line

> *"All four nodes run one firmware with a build flag for the node number. That matters because it means the calibration, the packet format and the failure behaviour are identical everywhere — a mine with four sensors that behave differently is four separate systems to maintain."*

---
---

## PHASE H2 — Two-Stage Crack System (N2, N4)

> **Goal** — Detect the *start* of a crack (micro-strain, foil gauge) separately from its *growth* (macro opening, rotary pot), because they are physically different events that happen seconds apart.
> **Effort** — 2.0 person-days (1 day per channel, plus a shared calibration session) · **Demo-critical — YES** (it is the differentiator; cutting it removes the "initiation vs progression" story) · **Depends on** — H1
> **Demoable result** — bend the test strip: the **µε** reading moves first, the **mm** reading follows. Two distinct events, seconds apart, on the dashboard.

### H2.1 Audit — what exists today

- **No crack channel exists in hardware or firmware.** The `crack_sensor` concept was the original plan; it was superseded on 16 Sep by the two-stage decision.
- **Parts on hand:** 2 × BF350-3AA foil gauges, 2 × HX711 modules, 2 × 10 kΩ rotary pots (macro), plug-in crack-sensor modules (bench spares only).
- **The reference design** is `neural-strata-crack-sensor.md` (in this `docs/` folder): bridge wiring, pin map, mounting rules, and a four-step bench test.
- **Three defects must be fixed while building this (HD-3, HD-4, and the mount rule).**

### H2.2 The physics, in one paragraph (so the wiring makes sense)

A foil strain gauge is a resistor whose resistance changes when it is stretched. Its **gauge factor** is ~2.0, so a 350 Ω gauge stretched by 100 µε changes by ~0.07 Ω — far too small to measure directly. So you put it in a **Wheatstone bridge** and let the HX711 measure the bridge's tiny imbalance with a 24-bit ADC. **The bridge must be a real bridge**: two midpoints, one on each side. That is why three fixed resistors are required and one is not enough (HD-3).

```
        THE BRIDGE  (gauge = 1 active arm, 3 × 350 Ω fixed = the other three)
        
              3V3  ──────┬───────────────────┬──────
                         │                   │
                      [ BF350 ]            [R2 350Ω]
                      (active)             (fixed)
                         │                   │
        A+  ●────────────┴───────┐      ┌────┴────────●  A−
                                │      │
                             [R3 350Ω][R4 350Ω]
                             (fixed)  (fixed)
                                │      │
              GND ──────────────┴───────┴──────
              
        HX711   E+ = 3V3   E− = GND   A+ = left midpoint   A− = right midpoint
                VCC & AVCC = 3V3      DT → GPIO19         SCK → GPIO18
```

### H2.3 Wiring — one crack channel (repeated identically on N2 and N4)

| # | From | To | Wire | Colour | Termination |
|---|---|---|---|---|---|
| 1 | ESP32 `3V3` | HX711 `VCC` **and** `AVCC` (jumper them) | 22 AWG | red | **SOLDER** |
| 2 | ESP32 `GND` | HX711 `GND` | 22 AWG | black | **SOLDER** |
| 3 | ESP32 `GPIO18` | HX711 `SCK` | 22 AWG | yellow | **SOLDER** |
| 4 | ESP32 `GPIO19` | HX711 `DT` | 22 AWG | orange | **SOLDER** |
| 5 | HX711 `E+` | Bridge node: gauge top **and** R2 top (the 3V3 rail of the bridge) | 22 AWG | red | **SOLDER** |
| 6 | HX711 `E−` | Bridge node: R3 bottom **and** R4 bottom (the GND rail of the bridge) | 22 AWG | black | **SOLDER** |
| 7 | HX711 `A+` | Bridge midpoint: gauge bottom **and** R3 top | 22 AWG | green | **SOLDER** |
| 8 | HX711 `A−` | Bridge midpoint: R2 bottom **and** R4 top | 22 AWG | white | **SOLDER** |
| 9 | — | BF350 gauge leads → bridge (gauge sits as the top-left arm) | gauge leads | — | **SOLDER to the supplied leads only** |
| 10 | BF350 body | **Rigid strip / concrete chip spanning the seam** | — | — | **BOND — thin cyanoacrylate or epoxy** |
| 11 | ESP32 `GPIO33` | Macro pot **wiper** | 22 AWG | blue | **SOLDER** |
| 12 | ESP32 `3V3` | Macro pot **end A** | 22 AWG | red | **SOLDER** |
| 13 | ESP32 `GND` | Macro pot **end B** | 22 AWG | black | **SOLDER** |
| 14 | Macro pot body | Fixed to one seam half | — | — | **BOND (glue dot)** |
| 15 | Macro pot shaft | Small rigid finger on the moving seam half | — | — | **BOND / mechanical** |

**Line 1 is the HD-4 fix.** The HX711's `DOUT` swings to whatever DVDD is. At 5 V, GPIO19 would see 5 V logic. Running the HX711 at **3.3 V** (both VCC and AVCC) keeps every logic level in the safe domain, with no divider and no level shifter. The cost is half the bridge excitation voltage — irrelevant here because the HX711 has 24 bits and we subtract the baseline in firmware.

**Line 10 is the mount rule.** Light sand the strip, wipe with isopropyl, **thin** glue, gauge centred on the crack line, leads dressed so they cannot tug the gauge. **Never hot glue. Never foam.** Foam strain exceeds the gauge's 2 % limit and the bond will creep, which reads exactly like a slowly cracking seam — the worst possible false positive.

### H2.4 Firmware changes

| Change | Detail |
|---|---|
| HX711 driver | `HX711` library, `RATE` pin **left low (10 SPS)** for lowest noise, then `read_median(4)` at 2 Hz |
| **15-second warm-up** | Ignore the first ~15 s after power-up. This is the documented HX711 drift, and it is the difference between a stable baseline and a phantom crack. Set `flags bit 2` while settling and report `micro = 0`. |
| Baseline | After settling, average 20 readings → `micro_baseline`. Transmit `micro = raw − baseline`. |
| Saturation flag | If the reading rails (open gauge / broken lead) set `flags bit 0`. An open gauge must read as **fault**, never as *zero strain* — those look identical in the data otherwise. |
| Macro channel | `analogRead(33)` after a settling delay; record a rest baseline; transmit `macro = raw − baseline` in mm via a one-time ruler calibration (full sweep = measured seam travel) |
| N1/N3 unchanged | They transmit `micro = 0, macro = 0` — the contract stays uniform |
| Negative clamp | Macro channel clamps at 0 (mechanical backlash must never subtract) |

### H2.5 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| N2 and N4 only | **Add** the HX711 + bridge + macro pot. N1 and N3 are untouched. | The crack channel is a property of the collapse-face nodes by design |
| Node JSON | `micro` and `macro` become non-zero **only** on N2/N4 | Backend already handles this (Phase 3 of the backend plan) |
| N2/N4 mounting | The node board is now carrying two extra sensors → rebalance it on the diorama so it does not topple | A falling node during a drill reads as a catastrophic event |
| Backend | `micro_rate` / `macro_d30` thresholds, the 15 s settle flag, and the hysteresis rule are in backend **Phase 3**. **Build them together.** | Hardware without the firmware→backend path is just an expensive thermometer |
| AI augmentation | Replace `03_synthetic_augmentation.py`'s hard-coded micro/macro couplings with the **measured** ones from this phase | Makes the training data physics-measured instead of physics-guessed |
| Plug-in crack modules | Keep in the spares bin. **Do not mount.** | Written decision |

### H2.6 Assembly order

1. **Bench the bridge loose (Rule 1).** Assemble the 4-resistor bridge on a small perfboard. **Select-match the three fixed resistors first** (DMM, within 0.3 Ω).
2. DMM-check: E+↔E− must **not** be shorted; A+↔A− must **not** be shorted.
3. Power the HX711 at 3.3 V. Read raw values on the serial console for 60 s with **no** gauge connected — confirm a stable, non-railing baseline.
4. Connect the gauge. Watch 15 s of drift. **This is the warm-up you must code around.**
5. **Bend test:** flex the strip gently with pliers. The reading must move **steadily and in one direction**. Erratic or non-monotonic → re-check A+/A− polarity and gauge lead solder joints.
6. **Mount:** sand → clean → thin glue → cure fully.
7. **Re-zero** the baseline after curing. **Curing changes the strain state** — a baseline taken before the glue sets is worthless.
8. Ruler-calibrate the macro pot: measure the seam travel for a full sweep, write the mm-per-count on the node's label.
9. Final tug-test and continuity check on every bridge joint; then tape-insulate the perfboard.

### H2.7 Bench acceptance test

| Check | Instrument / action | Expected |
|---|---|---|
| Bridge resistance, E+→E− | DMM | ≈ 350 Ω (two 350 Ω arms in parallel with the other two) |
| A+→A− with no strain | DMM, mV | **within ±10 mV** of 0 (with matched resistors) |
| HX711 supply | DMM, VCC→GND and AVCC→GND | 3.28 – 3.33 V on both |
| **DOUT logic level** | DMM, GPIO19 → GND while idle | **3.28 – 3.33 V, never ~5 V** (this is the HD-4 verification) |
| Warm-up drift | Serial console, 15 s after power | Settles within ~2 s of the final value |
| Bend, one direction | Flex the strip | `micro` rises monotonically; **sign is consistent** |
| Bend, release | Stop flexing | `micro` returns toward baseline (elastic) |
| Macro pot | Turn the shaft full sweep | `macro` sweeps 0 → full travel, monotonic |
| **Cross-check** | Bend the strip | `macro` must **not** move (the seam has not opened); if it does, the pot's finger is touching the strip |
| Noise floor | Serial console, 60 s quiet | `micro` peak-to-peak < ~5 counts — if it is jumping by hundreds, add a 100 nF across the gauge's supply and shorten the leads |
| Node current | DMM in series | +1.5 mA over H1 — negligible |

### H2.8 Risks & rollback

| Risk | Mitigation |
|---|---|
| **Glue thickness kills the sensitivity** | This is *the* classic failure. Thin bond, full cure, and always re-zero after curing. If sensitivity is poor, the strip is too compliant — move to a stiffer strip. |
| BRIDGE OFFSET eats the ADC range | 0.1 % matched arms, or select-match with the DMM (free), or null with a 50 Ω trimpot in series with one arm (§A.1.5). |
| Open gauge reads as zero strain | Firmware saturation flag (`flags bit 0`) plus a rail check. **Test by unplugging a gauge lead on purpose.** |
| Gauge damaged during mounting | Buy 2 and treat one as a spare. A foil gauge cannot be reused once bonded. |
| HX711 drift confuses the demo | 15 s warm-up plus `flags bit 2`, and the backend refuses to fire a crack event while settling |

**Rollback:** disconnect the HX711 and pot, set both "present" flags to false, and the node behaves exactly like H1. The backend already tolerates `micro`/`macro` being null.

### H2.9 Viva line

> *"Two channels because a crack does two different things. The foil gauge on a rigid strip measures micro-strain — damage has started, seconds before anything visible moves. The rotary pot measures the seam actually opening in millimetres. Together they give initiation and progression as separate events, and the *time gap* between them is the early warning."*

---
---

## PHASE H3 — Surface Mesh: ESP-NOW, Relay and Last-Gasp

> **Goal** — Four nodes talking to one hub reliably, with a relay path when a node's direct link degrades, and a final packet when a node dies.
> **Effort** — 1.5 person-days · **Demo-critical — YES** (the "kill a link, the mesh heals" moment) · **Depends on** — H1
> **Demoable result** — cover N2's antenna for ten seconds: the dashboard shows **`N2 · via relay (N1)`** and the data never stops. Flip N3's power switch: **`OFFLINE · last-gasp received`**.

### H3.1 Audit — what exists today

- **ESP-NOW already works** between the existing hub and N1/N2 at 2 Hz with a `seq` number. This is proven.
- The system map specifies the full design: seq-gap watchdog on the hub, each node storing the last ~20 packets of each neighbour, `{"relay":"REQ"}` retransmission, and a brownout-driven last-gasp packet.
- **The existing hub's MAC is compiled into each node's firmware** — swapping the hub means reflashing every node (HD-14).
- **Nothing about antenna placement is currently specified** (HD: antenna keep-out now added in §A.3.4).

### H3.2 Target behaviour

1. **Every node hears its neighbours** and keeps a small ring buffer of their last ~20 packets. Storing is cheap; the mesh only becomes possible because every node is a listener.
2. **The hub detects a `seq` gap per node**, issues `{"relay":"REQ","node":n,"from":a,"to":b}`, and the neighbour with the oldest copy retransmits. Badge appears on the dashboard: `via relay (N1)`.
3. **Last-gasp:** the ESP32's brownout detector fires one final packet before the radio dies: `{"node":3,"last":true,"disp":…,"acc":…}`. The hub marks N3 `OFFLINE · last-gasp received` and the dashboard shades the zone.
4. **Antenna hygiene is a build requirement**, not an afterthought (§A.3.4).
5. **MAC addresses are documented and the hub is labelled DO NOT SWAP.**

### H3.3 Wiring

**No new wires.** H3 is a firmware and placement phase — its "wiring" is where the boards physically sit.

| Placement action | Rule |
|---|---|
| Node antenna orientation | The PCB antenna end (**opposite the USB connector**) faces open air, **not** toward another board or a wire bundle |
| Node spacing | 30–90 cm apart on the surface panel; the diorama is small enough that range is never the issue — **interference and detuning are** |
| Keep-out | 15 mm clear around each antenna; no backbone wire crossing any antenna |
| USB hub brick | ≥ 20 cm from all antennas |
| The "cover N2's antenna" demo | Practise it. Nothing metallic may sit within 15 mm of N2, or the demo does not work. A piece of card wrapped in kitchen foil, held ~20 mm from the antenna, is a reliable and repeatable attenuator. |

### H3.4 Firmware changes

| Change | Detail |
|---|---|
| Node peer table | Each node registers the **hub plus two neighbours** as ESP-NOW peers |
| Neighbour ring buffer | Store `{node, seq, payload}` for the last 20 packets heard from each neighbour |
| Relay responder | On `{"relay":"REQ"}`, replay the requested range if held |
| Brownout handler | `esp_register_shutdown_handler()` or the brownout ISR → fire one last-gasp packet within ~2 ms |
| `seq` monotonicity | Never reset `seq` on a soft restart except by design; a non-increasing `seq` is a **discontinuity** the backend uses to invalidate the AI window |
| Hub seq-gap watchdog | Per-node expected sequence, gap > 4 → request relay, log a `MESH_REROUTE` event |
| Hub MAC constant | One `#define HUB_MAC` shared by all node envs, with a comment containing the physical board's label |

### H3.5 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| Existing hub firmware | Gains the seq-gap watchdog and relay request logic | Turns "it works" into "it heals" |
| Existing N1/N2 firmware | Gains neighbour listening + relay buffer + last-gasp; **the hub MAC constant stays unchanged** so they keep talking | Backward compatible: flash nodes one at a time |
| Boards | Get **antenna-side labels** so nobody mounts one backwards | HD-14-adjacent |
| Backend | `MESH_REROUTE` and `NODE_STALE` events + the `via relay` badge come from backend Phase 2 | Build the visible evidence, not just the mechanism |

### H3.6 Bench acceptance test

| Test | How | Expected |
|---|---|---|
| Baseline link | All four nodes on, dashboard | 4 cards at 2 Hz, zero missing seq over 5 minutes |
| **Relay heal** | Wrap N2's antenna in foil for 10 s | `via relay (N1)` badge appears within 2 s; **data continuity maintained**; on removal it returns to direct within 2 s |
| Relay correctness | Compare N2's values during relay vs before | Values are the *actual* recent packets, not stale ones — check the timestamp |
| Last-gasp | Flip N3's power switch | A final packet is logged with `last:true`; the dashboard shows `OFFLINE · last-gasp received HH:MM:SS` |
| Survives 3 nodes | Leave N3 off | N1, N2, N4 carry on; the collapse-boundary story works |
| **Range margin** | Walk N4 across the room | Link holds well beyond the diorama; note the actual distance as a talking point |
| Antenna detuning check | Lay a bundle of 22 AWG wires across an antenna | Packet loss rises measurably — **prove to yourself why the keep-out rule exists** |

### H3.7 Risks & rollback

| Risk | Mitigation |
|---|---|
| Relay logic introduces duplicate packets | The hub deduplicates on `(node, seq)`; duplicates are dropped silently and counted in metrics |
| Brownout handler unreliable on the DevKit | Test it explicitly. If the brownout interrupt does not fire cleanly, fall back to a **power-switch demo** plus a "telemetry gap" story (the SRS already documents the prototype equivalent honestly) |
| Foil demo not repeatable | Rehearse it 5 times. Have a second method ready (turning the node away from the hub). |
| WiFi channel congestion at the venue | ESP-NOW on a fixed channel; if the venue is busy, fix the channel explicitly in firmware and re-test on site |

**Rollback:** the relay is a firmware feature; reverting the node firmware restores plain direct transmission. The hub keeps working with either version.

### H3.8 Viva line

> *"Every node is also a listener. It keeps the last twenty packets it heard from each neighbour, so when the hub notices a sequence gap it can ask a neighbour to replay them. The node can't tell us it's failing — so the mesh notices on its behalf. And if a node is destroyed, the destruction itself becomes the final data point."*

---

## PHASE H4 — Main Hub: Uplink + Two Ring Ports

> **Goal** — The hub that already works becomes the hub that also owns the underground backbone — with the port remap that fixes HD-1.
> **Effort** — 1.0 person-day · **Demo-critical — YES** · **Depends on** — H3
> **Demoable result** — the hub streams ESP-NOW telemetry up the USB cable **and** exchanges heartbeats with L1 and L2 over two soldered UART pairs.

### H4.1 Audit — what exists today

- The existing hub is **a bare ESP32** doing ESP-NOW RX → USB-serial JSON. It is the one board that already works end-to-end.
- **The ring does not exist yet.** `system-map.html` plans it on UART1 (GPIO10/9) and UART2 (GPIO17/16).
- **HD-1:** GPIO9/GPIO10 are on the flash bus. **HD-10:** the GPS guide contradicts the map and mentions a "25/26 remap".
- The hub has **no other peripherals** — no RC522 (that moved to L1), no strips, no siren. Its GPIOs are almost entirely free, which is why it can afford two remapped UARTs.

### H4.2 Target behaviour

1. **Two full-duplex ring ports**, both on safe pins: **UART2** on 17/16 and **UART1 remapped** to 4/13.
2. **The uplink is untouched** — USB on UART0, 115200 8N1, JSON lines at 2 Hz.
3. **Heartbeats every 2 s per port**, so a dead segment is detected within ~4 s.
4. **The down-link exists**: alert and LED commands from the laptop are forwarded down the ring.
5. **The board is labelled `MAIN HUB — DO NOT SWAP`** with its MAC address written on it (HD-14).

### H4.3 Wiring — Main Hub

| # | From (Main Hub) | To | Wire | Colour | Length | Termination |
|---|---|---|---|---|---|---|
| 1 | `GPIO17` (UART2 TX) | Ring port A → **L1 RX (GPIO16)** — via the hop-1 bundle | 22 AWG solid | **orange** | per layout (~1 m) | **SOLDER** both ends |
| 2 | `GPIO16` (UART2 RX) | Ring port A ← **L1 TX (GPIO17)** — via the hop-1 bundle | 22 AWG solid | **orange** | ~1 m | **SOLDER** both ends |
| 3 | `GPIO4` (UART1 remap TX) | Ring port B → **L2 RX (GPIO13)** — via the hop-3 bundle | 22 AWG solid | **teal** | ~1 m | **SOLDER** both ends |
| 4 | `GPIO13` (UART1 remap RX) | Ring port B ← **L2 TX (GPIO4)** — via the hop-3 bundle | 22 AWG solid | **teal** | ~1 m | **SOLDER** both ends |
| 5 | `GND` | **Common GND bus** | 22 AWG solid | **black** | ~1 m | **SOLDER** — shared with every other board |
| 6 | USB port | Powered USB hub (port 1) | USB cable 1 m | — | 1 m | **USB** |

**Only 5 wires leave the Main Hub.** That is the design: everything else is wireless.

```
        MAIN HUB — the only board with two ring ports + USB

        17 TX ──┐
        16 RX ──┘──► hop 1 ──► L1   (orange pair)
         4 TX ──┐
        13 RX ──┘──► hop 3 ──► L2   (teal pair)
        GND ───────► common GND bus (black, shared by all 7 boards)
        USB ───────► laptop  (JSON telemetry up, commands down)
```

### H4.4 Firmware changes

| Change | Detail |
|---|---|
| UART init | `Serial2.begin(115200, SERIAL_8N1, 16, 17)` for port A; `Serial1.begin(115200, SERIAL_8N1, 13, 4)` for port B. **Never** the default UART1 pins (HD-1). |
| Line discipline | Newline-delimited JSON both ways. A hub must be able to **recover from a partial line** — always buffer until `\n` before parsing. |
| Heartbeat | Every 2 s per port: `{"id":"MAIN","hop":"A","seq":n}` |
| Ring fault detection | No heartbeat on a port for > 4 s → `{"ring":"fault","seg":"MAIN-L1","reroute":"direct-via-L2"}` upstream |
| Command router | Commands from the laptop (`{"cmd":"led",…}`, `{"cmd":"siren",…}`, `{"cmd":"advisory",…}`) are forwarded to the addressed level hub. **The Main Hub makes no decisions.** |
| `#CMD` sentinel | Every server→hub line begins with `#CMD`; every hub→server line does not. The hub ignores non-`#CMD` lines on its USB read path, the server ignores `#CMD` lines on its read path. |
| Uplink unchanged | The existing telemetry forwarding stays byte-identical, so the backend needs no change to keep working |

### H4.5 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| **Bare ESP32 hub** | Add 5 soldered wires (4 signal + GND). **No breadboard** — project decision. | The hub must be mechanically solid; it is the board that never gets touched |
| `hub.cpp` | Add UART2 + remapped UART1 init, heartbeats, the fault reporter, the command router, and the `#CMD` sentinel | — |
| Old pin plan (10/9) | **Abandoned.** Any existing notes or diagrams showing GPIO10/9 for the ring are struck out. | HD-1 |
| Hub board | Label with MAC. **Never swap this board** without reflashing all four nodes. | HD-14 |
| USB port allocation | MAIN takes hub port 1. Record the port map on a label on the hub. | Wiring day sanity |

### H4.6 Assembly order

1. **Bench-test both UARTs before they are wired.** Cross-connect the hub's port A pins to a second ESP32 (or a USB-serial adapter) with jumper wires and confirm heartbeats at both ends. **Two UARTs, one at a time.**
2. Only then solder the backbone wires (H5).
3. Keep the hub's USB cable plugged in throughout — it is your console and your power.
4. Photograph with the labels visible.

### H4.7 Bench acceptance test

| Check | Instrument / action | Expected |
|---|---|---|
| Uplink | `curl /api/telemetry` | Data flowing, 2 Hz |
| Port A loopback | Jumper hub TX (17) to hub RX (16) briefly | Bytes transmitted are received → proves the UART instance |
| Port A live | Connect to L1 (after H5/H6) | Heartbeats both ways every 2 s |
| Port B live | Connect to L2 | Idem |
| **No flash conflict** | Boot the hub 20 times in a row | **20/20 successful boots.** Nothing on GPIO9/10 (HD-1 verification) |
| Fault detection | Unplug hop 1 | `ring fault` reported upstream within 5 s |
| GPIO sanity | DMM, GPIO4 and GPIO13 to GND | Steady 3.3 V idle (UART idles high), no 5 V anywhere |

### H4.8 Risks & rollback

| Risk | Mitigation |
|---|---|
| The remap breaks the existing (working) hub firmware | The uplink code path is untouched. Only new UARTs are added. If it misbehaves, comment out the two `begin()` calls and the hub returns to H3 behaviour exactly. |
| Confusing UART1 and UART2 pins on the hub | Label both wire pairs **before** soldering: *"hop 1 — orange"* and *"hop 3 — teal"* |
| Backbone wires soldered before the UARTs are proven | Rule 1: prove loose, then solder. This is the phase where skipping it hurts most. |

**Rollback:** comment out the two UART initialisations; the hub is a working ESP-NOW gateway again.

### H4.9 Viva line

> *"The hub's only job is to move data: ESP-NOW up to the laptop, and JSON both ways down the ring. It makes no decisions — the routing and the alerting live on the edge server where the algorithm can be seen, tested and explained."*

---
---

## PHASE H5 — Ring Backbone: The Seven Wires and the Cut Switch

> **Goal** — A full-duplex triangle between MAIN, L1 and L2, soldered, labelled, strain-relieved — and one switch that produces a clean, explicable "the mine's spine is cut" demo.
> **Effort** — 1.5 person-days · **Demo-critical — YES** · **Depends on** — H4
> **Demoable result** — flip the service-panel switch: the dashboard shows the L1↔L2 segment red, the route recomputed via MAIN, **and L2's junction LEDs keep flashing alerts**.

### H5.1 Audit — what exists today

- **Nothing is built.** The 22 AWG kit (10 × 1 m) is bought and allocated to the ring.
- The system map specifies **7 wires total: 6 signal + 1 common GND**, as three full-duplex legs of 2 wires each.
- **HD-7:** the map offers "toggle switch in series with W4 **(or hop-2 GND)**". **The GND option cannot work** — with one shared ground bus, cutting a hop's GND just routes the return current through the other two hubs. The demo silently fails.
- **HD-8:** one SPST cuts one *direction* of a full-duplex leg. The other direction keeps working, so the "cut" looks like a software bug.
- **HD-13:** no strain relief specified anywhere.

### H5.2 Target behaviour

1. **Six signal wires + one GND bus**, exactly as specified — every hop full-duplex over 2 wires.
2. **One cut kills hop 2's uplink direction** (`W4`: L2 TX → L1 RX), which is the direction that carries L2's *telemetry and RFID* to the server. The fault is therefore immediately visible on the dashboard, and L1 can still send *commands* to L2 — which is precisely the point: **the ring is not fully dead, it is degraded, and the system says so.**
3. **The switch lives on a hinged service panel** on the front of the diorama, labelled. Judges flip a labelled switch; the wiring stays hidden and it looks intentional.
4. **Service loops and anchors at every joint** so handling the panel cannot fatigue a solder joint.
5. **Every wire labelled at both ends.**

### H5.3 Wiring — the seven wires (complete, exact)

| Wire | From | To | Colour | Length | Termination |
|---|---|---|---|---|---|
| **W1** | MAIN `GPIO17` (UART2 TX) | L1 `GPIO16` (UART2 RX) | **orange** | hop 1 | **SOLDER** both ends |
| **W2** | L1 `GPIO17` (UART2 TX) | MAIN `GPIO16` (UART2 RX) | **orange** | hop 1 | **SOLDER** both ends |
| **W3** | L1 `GPIO4` (UART1 TX) | L2 `GPIO13` (UART1 RX) | **purple** | hop 2 | **SOLDER** both ends |
| **W4** | L2 `GPIO4` (UART1 TX) | L1 `GPIO13` (UART1 RX) | **purple** | hop 2 | **SOLDER** both ends — **the cut switch goes here** |
| **W5** | L2 `GPIO17` (UART2 TX) | MAIN `GPIO13` (UART1 RX) | **teal** | hop 3 | **SOLDER** both ends |
| **W6** | MAIN `GPIO4` (UART1 TX) | L2 `GPIO16` (UART2 RX) | **teal** | hop 3 | **SOLDER** both ends |
| **W7** | **COMMON GND** — MAIN + L1 + L2 + all nodes' USB-hub GND | one black bus | **black** | routed | **SOLDER** at a star point |

> **Correction to W5/W6 (this is why H0 exists).** The system map writes *"W6: MAIN U2TXD (GPIO17) → L2 U1RXD (GPIO9)"*. That line contains **two** errors: GPIO9 is a flash-bus pin (HD-1), **and** GPIO17 is already committed to hop 1 on MAIN, so MAIN cannot transmit to two hubs from one pin.
>
> The corrected hop 3 is **MAIN UART1 (remapped to 4/13) ↔ L2 UART2 (17/16)**:
> **W5** = L2 `GPIO17` (UART2 TX) → MAIN `GPIO13` (UART1 RX) · **W6** = MAIN `GPIO4` (UART1 TX) → L2 `GPIO16` (UART2 RX).
>
> The reason this works — and why the triangle is elegant rather than lucky — is that **each of the three boards spends each of its two UARTs exactly once**, and the hop pairs are matched deliberately:**

| Board | UART | Pins | Connected to |
|---|---|---|---|
| **MAIN** | UART2 | 17 (TX) / 16 (RX) | **L1** — wires W1, W2 |
| **MAIN** | UART1 | 4 (TX) / 13 (RX) | **L2** — wires W5, W6 |
| **L1** | UART2 | 17 (TX) / 16 (RX) | **MAIN** — wires W1, W2 |
| **L1** | UART1 | 4 (TX) / 13 (RX) | **L2** — wires W3, W4 |
| **L2** | UART2 | 17 (TX) / 16 (RX) | **MAIN** — wires W5, W6 |
| **L2** | UART1 | 4 (TX) / 13 (RX) | **L1** — wires W3, W4 |

**Every board uses both UARTs, once each. Every hop is a matched pair. Nothing is shared, nothing conflicts, and no flash pin is touched.** This is the corrected backbone.

```
                    THE RING (triangle, full duplex)

                          ┌──────────────┐
                          │   MAIN HUB   │
                          │ UART2 ─ UART1│
                          └───┬──────┬───┘
                     W1,W2    │      │    W5,W6
                    (orange)  │      │    (teal)
                    hop 1     │      │    hop 3
                    UART2 ◄───┘      └───► UART2
                          ┌──────────────┐
                    ┌─────│  LEVEL HUB 1 │
                    │     │ UART2 ─ UART1│
                    │     └──────┬───────┘
                    │            │  W3,W4 (purple) · hop 2
                    │            │  ◄── W4 carries the CUT SWITCH
                    │     ┌──────┴───────┐
                    └────►│  LEVEL HUB 2 │
                          │ UART1 ─ UART2│
                          └──────────────┘

   W7 = common GND, one black bus, star-pointed at the USB hub's ground.
```

### H5.4 The cut switch (HD-7, HD-8 resolved)

**Where:** in series with **W4 only** — the L2→L1 transmit line of hop 2.
**Why not GND:** cutting one hop's GND on a shared ground bus does nothing; the return current takes the other two legs and the "cut" has no electrical effect at all.
**Why W4 and not W3:** W4 carries L2's *outgoing* traffic — its telemetry and its RFID swipes. Cutting it means **the server loses sight of L2** while L2 itself keeps working and keeps obeying commands sent down W3. That is a genuine degradation, visible on the dashboard, with the ring still partly alive.
**The honest demo line:** *"We cut the spine. L2 keeps receiving alerts on the other leg, but we've lost its telemetry — so the system marks that segment degraded and re-routes the data path. What it doesn't do is pretend everything is fine."*

| Switch | Wired into | Purpose | Termination |
|---|---|---|---|
| **#1 (SPST)** | **W4** (L2 → L1 TX, hop 2) | The ring-cut demo | **SOLDER** to switch tabs, service loop both sides |
| **#2 (SPST)** | **J4's 5 V branch** on the LED rail | "Junction J4 goes dark" | **SOLDER**, service loop both sides |
| *Optional (DPST)* | **W3 + W4 together** | Complete hop-2 isolation for a stronger visual (HD-8 fully closed) | Buy if you want it — ₹80 |

**Also add a jumper position:** a short jumper wire across the switch tabs, so if the switch fails on demo day you can bypass it in 5 seconds.

### H5.5 Firmware changes

| Change | Detail |
|---|---|
| Level hubs | Mirrors of the Main Hub's ring code: two UART instances, 2 s heartbeats, fault reporting |
| Fault semantics | A missing heartbeat on L1's hop-2 RX (**W4**) is the *expected* signal when the switch flips. The firmware must report **exactly one** fault event, not a storm. |
| Re-route flag | `{"ring":"fault","seg":"L1-L2","dir":"L2→L1","reroute":"direct-via-MAIN"}` upstream, edge-triggered |
| Recovery | Closing the switch again must clear the fault within ~4 s, with a `segment restored` event |
| Baud | 115200 8N1 everywhere. If you see framing errors, drop to 38400 — 22 AWG at 1 m is electrically trivial, so errors mean a wiring fault, not a speed problem. |
| Partial-line resilience | Same as the Main Hub — buffer to `\n` |

### H5.6 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| Nothing exists yet | This phase **builds** the backbone | — |
| `system-map.html` | W5/W6 corrected; the "or hop-2 GND" option struck out; hop colours confirmed | HD-1, HD-7 |
| Service panel | New: a hinged 100 × 60 mm PVC offcut with the two toggles labelled `RING SEGMENT L1–L2` and `JUNCTION J4 POWER` | The demo is only convincing if the switch is obvious |
| Cable routing | Route the ring down the **back-left (hop 1)**, **back-centre (hop 2)**, **back-right (hop 3)** edges — as the map specifies. Uniform routing with service loops at each board. | Hidden wiring, clean panel |

### H5.7 Assembly order

1. **Cut all six signal wires to length with 15 % slack.** Longer is easy to hide; shorter is impossible to fix.
2. **Label both ends of every wire before soldering** — `MAIN-A-TX`, `L1-A-RX`, `L1-B-TX`, etc. Use masking tape + marker. This is the single most valuable 10 minutes in the build.
3. Solder to **MAIN** first (one hop at a time), tug-test, then continuity-test end to end with the DMM before powering anything.
4. **Power on and verify hop 1 alone.** Then hop 2 (with L1 and L2), then hop 3.
5. Only after all three hops are live, install the switch into W4: cut W4 at the panel position, solder to the switch tabs, **test both positions**.
6. Route everything through the diorama edges, anchor with glue dots every 15 cm, and keep the antennas clear.
7. Photograph the completed backbone.

### H5.8 Bench acceptance test

| Check | Instrument / action | Expected |
|---|---|---|
| Continuity, every wire | DMM, both ends | **< 1 Ω** on all six signal wires and the GND bus |
| **No cross-shorts** | DMM, every signal wire to every other signal wire | **Open** (> 1 MΩ) — this is the check that catches a solder bridge before it costs a board |
| Idle levels | DMM, each TX/RX pin to GND | ~3.3 V idle (UART idles high) |
| Heartbeats | Serial console on MAIN | 2 s cadence on both ports |
| Switch open | Flip #1 | Exactly one fault event; L2 silent to the server; L1↔L2 commands still flow |
| Switch closed | Flip back | `segment restored` within 4 s |
| **20 cuts in a row** | Flip #1 twenty times | No missed detections, no crash, no duplicate event storms |
| Strain relief | Tug each wire gently at the board | No movement at the solder joint |
| 30-minute soak | Leave all three hubs running | Zero dropped heartbeats |

### H5.9 Risks & rollback

| Risk | Mitigation |
|---|---|
| A solder bridge between two adjacent header pins | Continuity matrix **before** power, every time. Keep solder wick within reach. |
| Confusing the purple pair's direction | The labels say `TX` and `RX` at both ends. The rule is **always cross TX↔RX**. |
| The cut switch looks like a bug to judges | Rehearse the narration. Show the *dashboard fault banner* as the proof, not the LEDs. |
| The GND bus is broken somewhere | Symptom: random resets and CRC errors on every hub at once. The DMM continuity check from the USB hub's ground to each board's GND finds it in 60 seconds. |

**Rollback:** this phase is additive — unplug the six wires and you are back to a wireless-only system that still demonstrates everything except the ring.

### H5.10 Viva line

> *"Three hubs, six signal wires, one ground. Each hop is full-duplex — two wires per hop, one each direction — which is why a single cut degrades the ring instead of killing it. A one-way circulating ring would die on the first cut; that's why FDDI needed dual counter-rotating rings. We get the same survivability from a triangle."*

---
---

## PHASE H6 — Level Hubs L1 / L2: RFID, LED Strips, Siren and Gate Lights

> **Goal** — Two identical underground hubs, each reading RFID tags, driving two junction strips, sounding a siren and lighting a gate — on pins that will not brick the board.
> **Effort** — 2.0 person-days (1 per hub, mostly soldering time) · **Demo-critical — YES** · **Depends on** — H5 (ring live) or H1 (bench-only bring-up)
> **Demoable result** — swipe a tag: the dashboard's mustering panel updates. Send a command from the server: the junction strips change colour and the siren sounds.

### H6.1 Audit — what exists today

- **Neither level hub exists.** They are the two new ESP32s.
- **HD-2:** the siren is specced on **GPIO12** in the have-vs-buy list — the flash-voltage strapping pin. A 3-pin low-level-trigger module can hold it high at boot and the board will not start.
- **HD-9:** the system map says the siren is on **GPIO25**. Two documents, two pins.
- **HD-12:** no RC522 decoupling specified. This is the number-one cause of a reader that reads one tag then hangs.
- **RC522 is a 3.3 V module** and dies if powered from 5 V.
- The strips in the map are wired as "data + 5V + GND" directly from the hub — **HD-6**.

### H6.2 Target behaviour

1. **Identical hardware on L1 and L2.** Only the `env:` build differs (level number in the heartbeat).
2. **Siren on GPIO14** — resolved, safe, no strapping conflict.
3. **Gate LEDs on GPIO32 (green) and GPIO33 (red)** — free on a hub, and they give you the three indications the demo needs: green = open, red = blocked, **both blinking = amber advisory**.
4. **The RC522 gets 100 nF + 10 µF right at the module.**
5. **The strips are NOT powered from the hub's 5 V pin** — see H8. The hub supplies only the data line.
6. **The 330 Ω resistors are soldered inline, at the hub end.**

### H6.3 Wiring — one level hub (L1 shown; L2 identical)

| # | From (ESP32) | To | Wire | Colour | Length | Termination |
|---|---|---|---|---|---|---|
| 1 | `GPIO17` (UART2 TX) | Ring port A → MAIN `GPIO16` (via W2) | 22 AWG | orange | to panel edge | **SOLDER** |
| 2 | `GPIO16` (UART2 RX) | Ring port A ← MAIN `GPIO17` (via W1) | 22 AWG | orange | to panel edge | **SOLDER** |
| 3 | `GPIO4` (UART1 TX) | Ring port B → L2 `GPIO13` (via W3) | 22 AWG | purple | to panel edge | **SOLDER** |
| 4 | `GPIO13` (UART1 RX) | Ring port B ← L2 `GPIO4` (via W4) | 22 AWG | purple | to panel edge | **SOLDER** |
| 5 | `3V3` | RC522 `3.3V` | 22 AWG | red | 80 mm | **SOLDER** — **3V3 only, never 5 V** |
| 6 | `GND` | RC522 `GND` | 22 AWG | black | 80 mm | **SOLDER** |
| 7 | `GPIO18` | RC522 `SCK` | 22 AWG | yellow | 80 mm | **SOLDER** |
| 8 | `GPIO19` | RC522 `MISO` | 22 AWG | orange | 80 mm | **SOLDER** |
| 9 | `GPIO23` | RC522 `MOSI` | 22 AWG | green | 80 mm | **SOLDER** |
| 10 | `GPIO5` | RC522 `SDA`/`SS` | 22 AWG | white | 80 mm | **SOLDER** |
| 11 | `GPIO27` | RC522 `RST` | 22 AWG | grey | 80 mm | **SOLDER** (or tie RST→3V3, see note) |
| 12 | **Decoupling** | 100 nF ceramic **+** 10 µF electrolytic across RC522 `VCC`/`GND` | — | — | at module | **SOLDER** — HD-12 |
| 13 | `GPIO21` | **330 Ω** → route A strip DIN (J1 on L1 / J3 on L2) | 22 AWG | **blue** | per layout | **SOLDER**, resistor inline at hub end |
| 14 | `GPIO22` | **330 Ω** → route B strip DIN (J2 on L1 / J4 on L2) | 22 AWG | **blue** | per layout | **SOLDER**, resistor inline at hub end |
| 15 | `5V` | Siren **+** lead | 22 AWG | red | 100 mm | **SOLDER** |
| 16 | `GPIO14` | Siren **− / signal** lead | 22 AWG | yellow | 100 mm | **SOLDER** — **LOW = sounds** |
| 17 | `GPIO32` | Gate LED green anode (LED has built-in resistor) | 22 AWG | green | 200 mm | **SOLDER** |
| 18 | `GPIO33` | Gate LED red anode (LED has built-in resistor) | 22 AWG | red | 200 mm | **SOLDER** |
| 19 | LED cathodes | `GND` | 22 AWG | black | 200 mm | **SOLDER** |
| 20 | `GND` | **Common GND bus** | 22 AWG | black | to panel | **SOLDER** |
| 21 | USB port | Powered USB hub | USB cable 1 m | — | 1 m | **USB** |

**The strip's red and black are NOT on rows 13/14.** Only the **blue data wire** comes from the hub. The strip's 5 V and GND come from the LED rail on the service panel (H8). This is HD-6 and it is the difference between a hub that survives a white flash and a hub that resets mid-demo.

### H6.4 The siren, exactly

| Property | Value |
|---|---|
| Type on hand | 2-pin 5 V active buzzer |
| Wiring | one lead to **5 V**, other lead to **GPIO14** |
| Logic | **LOW = sounds** (the GPIO sinks the current) |
| Why GPIO14 | GPIO12 is the flash-voltage strapping pin (HD-2). GPIO25 was the old map's choice and is now reserved for the deferred GPS. |
| **Is it active or passive?** | Test it: apply 5 V across the leads. **A continuous tone = active** (drive it by toggling the pin HIGH/LOW). **A single click = passive** (drive it with a ~2.7 kHz `ledcWriteTone()`). The have-vs-buy note says exactly this — do the test, don't assume. |
| Current | active buzzer ≈ 20–30 mA; a passive piezo ≈ 15 mA and quieter — both fine on a GPIO, but a passive one driven by `ledc` is louder and worth choosing if you have both |
| Bulk decoupling | Optional 100 µF across the buzzer's leads, so the siren's inrush does not glitch the hub |
| Boot behaviour | Verify the buzzer does **not** chirp when the hub powers on. A chirp at boot reads as a false alarm on the panel. |

### H6.5 Gate lights — three states from two LEDs

| Route state | Green (GPIO32) | Red (GPIO33) | Meaning |
|---|---|---|---|
| `OPEN` | **ON** | off | this route is clear |
| `ADVISORY` | blink 1 Hz | blink 1 Hz (offset) | environmental disturbance — no evacuation |
| `BLOCKED` | off | **ON** | do not enter |
| `HOLD` (no safe path) | off | blink 3 Hz | **stand still** — never a green light pointing into danger |

> **Industrial design rule:** advisory and evacuation must never look the same. Amber (alternating green/red) is advisory; fast red is danger. If the LEDs are 5 mm with built-in resistors, they wire straight to the GPIO with no series resistor — verify with the DMM that each draws < 8 mA (ESP32's comfortable per-pin limit is ~12 mA, absolute max 40 mA).

### H6.6 Firmware changes

| Change | Detail |
|---|---|
| `env:lvl1` / `env:lvl2` | Same sketch, `-D LEVEL=1` / `-D LEVEL=2`; the heartbeat carries the level id |
| Ring code | Two UART instances as in H4, plus the hop-2 directional fault report |
| RC522 | `MFRC522` library with explicit RST pin; SPI at 4 MHz max (higher is the usual cause of dropped tags). Debounce: **ignore the same UID within 3 s** to stop one swipe filling the log with ten rows. |
| Strip driver | `FastLED` or `Adafruit_NeoPixel` with **8 LEDs per strip** (per the delivered hardware), on **two independent outputs** |
| Pattern vocabulary | `flow_green`, `advisory_amber`, `hold_amber`, `chase_red`, `strobe_red` — driven by down-link commands from the server. **All decision logic stays on the server.** |
| Siren control | `digitalWrite(14, LOW)` = sound, PLUS an `advisory` state that is a soft pattern (three short beeps) **deliberately different from the continuous critical tone** |
| Command parser | Handle `#CMD` lines only; ignore anything else on that path |
| Failure behaviour | If the ring goes quiet, **hold the last indication** and keep the siren armed. A hub that goes dark in an emergency is worse than a hub showing stale information. |

### H6.7 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| Existing RC522 (was on the hub) | **Moves to L1.** Re-solder to the L1 pin map. | Per the allocation: L1 and L2 gates |
| Old siren pin (GPIO12 and/or 25) | **Abandoned → GPIO14** on both hubs | HD-2, HD-9 |
| Existing 5 V buzzer | Goes to **L1**. The new one goes to L2. | One per level |
| 6 × 5 mm LEDs | 2 per hub (green + red), 2 spares | Gate lights |
| 4 × WS2812B strips | Wired to hub **data** only; power from the LED rail | HD-6 |
| Strip count in firmware | **8**, not 10 | Matches what was delivered |
| Hub 5 V pin | Now powers **nothing** except the siren's + lead | Keeps the hub's USB current well inside one port's budget |

### H6.8 Assembly order (per hub)

1. **Bench the RC522 first**, alone: power over USB, read a tag on the serial console. **Do this before any soldering.**
2. **Add the decoupling caps** and confirm the reader still works (it should now work *better*; swipe 20 times in a row without a hang).
3. Bench the siren: confirm active vs passive, confirm the boot-chirp behaviour.
4. Bench one strip with the 330 Ω on a jumper: confirm the pattern runs. **If the strip flickers or the first LED misbehaves, that is HD-5 — add the 1N4148 series diode now** (see H7.3).
5. **Unplug everything. Solder.** Ring pairs first, then RC522, then strips (data only), then siren, then LEDs, then GND.
6. Continuity matrix: every signal wire against every other — all open.
7. Power, re-verify everything in the same order you benched it.

### H6.9 Bench acceptance test

| Check | Instrument / action | Expected |
|---|---|---|
| Short check before power | DMM, `3V3`→`GND`, `5V`→`GND` | Both **open** |
| Rail voltages | DMM | `3V3` = 3.28–3.33 V, `5V` = 4.90–5.10 V |
| Hub current, idle | DMM in series with USB 5 V | 90–140 mA |
| Hub current, RC522 reading | Same | +40–80 mA peaks, no resets |
| RC522 | 20 consecutive swipes | 20/20 reads, no hang |
| **RC522 VCC** | DMM at the module | 3.28–3.33 V — **never 5 V** |
| Strip data idle | DMM, GPIO21/22 → GND | ~0 V idle, pulses during a pattern |
| **330 Ω in circuit** | DMM, hub pin → strip DIN | 326–334 Ω |
| Strip pattern | Send each of the 5 patterns | All render correctly; no flicker |
| **Siren boot-chirp** | Power-cycle the hub 5 times | **No sound** at boot (HD-2 verification) |
| Siren tone | Command `siren on` | Clear continuous tone, LOW = sounds |
| Advisory vs critical | Command `advisory` then `siren` | **Audibly different** patterns |
| Gate LEDs | Force `OPEN`, `ADVISORY`, `BLOCKED`, `HOLD` | Four distinct, unambiguous indications |
| RFID → backend | Swipe a tag | Row appears in `rfid_evacuation_log`; dashboard mustering updates within 1 s |
| **No 5 V on any GPIO** | DMM, every signal pin to GND, while running | **≤ 3.33 V everywhere.** Any 5 V reading is a stop-work fault. |

### H6.10 Risks & rollback

| Risk | Mitigation |
|---|---|
| RC522 dies from 5 V | **This is the classic.** Label the module `3V3 ONLY` with tape before soldering. |
| RC522 flaky under Wi-Fi/ESP-NOW activity | Decoupling caps + SPI at 4 MHz + keep the module's antenna away from the ESP32's antenna |
| Strip flicker blamed on firmware | It is almost always the **data-level margin** (HD-5) or a **shared 5 V rail dipping** (HD-6). Fix both, then look at firmware. |
| Buzzer draws more than expected | Measure it. If > 20 mA, put an NPN (BC547) or a small MOSFET in front of it rather than loading the GPIO directly. |
| Two hubs behave differently | They should be identical. Differences mean a bad joint or a wrong `LEVEL` flag — check the heartbeat's level id first. |

**Rollback:** each peripheral is independently wired. Unsoldering one strip's data wire returns that junction to "dead strip" without affecting anything else.

### H6.11 Viva line

> *"The level hub is the only processor underground, and it makes no decisions. It reads a tag, drives two LED strips, sounds a siren and lights a gate — all on commands from the server. The reason there's no processor at the junctions is reliability: a passive LED strip can't crash, and we have exactly one MCU per level to maintain instead of five."*

---

## PHASE H7 — Underground Junctions J1–J4: Passive LED Strips

> **Goal** — Four addressable strips that tell a miner which way is safe — with no processor at any of them, and with a data-level margin that actually works.
> **Effort** — 1.0 person-day · **Demo-critical — YES** (it is the only output a person underground can see) · **Depends on** — H6 (hub data lines), H8 (LED rail)
> **Demoable result** — command a route change: **J1 and J2 flip from green chase to red strobe** while J3 and J4 show the alternate exit.

### H7.1 Audit — what exists today

- **4 × WS2812B 8-LED strips** bought (the build originally specified 10-LED; the delivered 8-LED strips are used with the firmware count set to 8).
- **5 × 330 Ω** resistors bought — one per strip plus a spare. OK Confirmed we physically hold five.
- **Glow-in-the-dark arrow stickers** bought — 4, one per junction, showing the **fixed default exit direction** (the no-circuit fallback: if a strip dies, a painted arrow still points somewhere safe).
- **HD-5:** no data-level consideration anywhere. WS2812B is a 5 V part: **V_IH ≈ 0.7 × VDD = 3.5 V**, and an ESP32 drives 3.3 V.
- **HD-6:** the strips have no dedicated supply.
- **Layout decision (stands):** J1/J2 on Tunnel A (**Level 1**), J3/J4 on Tunnel B (**Level 2**), a cross-link J2↔J3, **Exit 2 far east**, trunk routing. **Not reopened.**

### H7.2 Target behaviour

1. **Four strips, four independent data runs**, each with its own 330 Ω. **Not chained** — one dead strip must not take out the next.
2. **Five unambiguous patterns** driven only by commands from the server.
3. **Data-level margin fixed** by the series-diode trick, so the first LED sees a valid HIGH.
4. **Every strip has its own 1000 µF bulk capacitor** at the strip head.
5. **Each junction carries its painted arrow** as the mechanical fallback.

### H7.3 The data-level problem and the free fix (HD-5)

A WS2812B needs its data input above 0.7 × its supply. At 5 V that is **3.5 V**, and the ESP32's output is **3.3 V** — a 200 mV shortfall. In practice this is *marginal*: short wires and a good strip often work, which is exactly why it is dangerous. It fails intermittently: one LED ignoring the first command, a strip that works on the bench and flickers in the diorama, a colour that is always one byte late.

**The fix costs one diode per strip:**

```
   LED RAIL 5.0 V ──┬─────────────────────────────────────► first LED VDD (4.3 V)
                    │
                 [1N4148]
                    │
                 ┌──┴──┐
                 │     │
              [1000µF] │
                 │     │
   GND ──────────┴─────┴──────────────────────────────────► strip GND

   ESP32 GPIO ──[330 Ω]──► first LED DIN        (3.3 V high vs 3.0 V threshold ✓)
```

Dropping the strip's own VDD to ≈ 4.3 V lowers its input threshold to ≈ **3.0 V**, and the ESP32's 3.3 V now has 300 mV of margin instead of −200 mV. The LEDs are imperceptibly dimmer. **This is the standard hobbyist fix and it is free.**

If a strip *still* misbehaves after the diode, the proper fix is a **74AHCT125** level shifter (one channel per strip, 3.3 V in → 5 V out). That part was deliberately not in the BOM; buy it only if the diode has been tried and the strip still glitches.

### H7.4 Wiring — one junction (repeated ×4)

| # | From | To | Wire | Colour | Length | Termination |
|---|---|---|---|---|---|---|
| 1 | Level hub `GPIO21` (J1/J3) or `GPIO22` (J2/J4) | through **330 Ω** → strip `DIN` | 22 AWG solid | **blue** | per layout | **SOLDER** both ends; resistor inline at the hub end |
| 2 | LED rail **5 V** (not the hub's 5 V pin) | through **1N4148** → strip `5V` | 22 AWG solid | **red** | per layout | **SOLDER** both ends |
| 3 | LED rail **GND** | strip `GND` | 22 AWG solid | **black** | per layout | **SOLDER** both ends |
| 4 | 1000 µF capacitor | across strip `5V` (after the diode) and `GND` | — | — | at strip | **SOLDER** — observe polarity (stripe = −) |
| 5 | Strip | Junction mounting | — | — | — | **Double-sided foam tape / glue dot** |
| 6 | Arrow sticker | Junction face | — | — | — | **Adhesive** |

**Run lengths (from the standing layout, unchanged):** J1 ≈ 0.5 m · **J2 trunk ≈ 2.5 m** · J3 ≈ 0.5 m · **J4 trunk ≈ 2.5 m** · plus ≈ 0.5 m of termination slack — **≈ 6.5 m total**, which is why the **second 22 AWG kit** exists.

**One continuous piece per wire. No mid-run splices.** (Standing decision.) If you need to extend, do it at a junction end, not in the middle of a run.

### H7.5 Pattern vocabulary — five states, no ambiguity

| Pattern | Colour behaviour | Meaning | Triggered by |
|---|---|---|---|
| `flow_green` | green chase, 400 ms step | **This way — route open** | route `OPEN` |
| `advisory_amber` | amber breathing, 1.5 s | Environmental disturbance, no evacuation | M1 = ENVIRONMENTAL |
| `hold_amber` | amber solid-fast, 300 ms | **No safe path — do not move** | Dijkstra finds no route |
| `chase_red` | red chase, 120 ms | Route blocked | route `BLOCKED` |
| `strobe_red` | red strobe, 100 ms **+ siren** | **Evacuate now** | Tier-3 critical |

The pattern is sent as a **command from the server**, not decided by the hub. The hub renders intent; the server owns the algorithm.

### H7.6 Firmware changes

| Change | Detail |
|---|---|
| Strip count | `NUM_LEDS = 8` (matches the delivered hardware) |
| Two independent strip objects | One per output pin — **not** a chain, so a single dead strip cannot blank the other |
| Command handler | `{"cmd":"led","junction":"J2","pattern":"chase_red","ms":120}` → pick the segment and render |
| Junction ↔ Level mapping in firmware | L1 drives J1 (GPIO21) and J2 (GPIO22); L2 drives J3 (GPIO21) and J4 (GPIO22) |
| Brightness limit | Cap at ~40 % in firmware. `setBrightness(100)`. It halves the rail current and looks better on camera anyway. This is also the honest reason the 2 A rail has headroom. |
| Hold-on-comm-loss | If commands stop arriving, **hold the last pattern** for 30 s, then default to `hold_amber`. **Never** silently go dark: a dark junction reads as "no hazard" to a person walking past it. |
| Boot default | On power-up, show `hold_amber` until the first command arrives. Safest default, and it doubles as a strip self-test. |

### H7.7 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| Nothing exists yet | This phase builds the junctions | — |
| Strip count | 8 in firmware, not 10 | Matches delivered hardware |
| Strip supply | **Moved off the level hub's 5 V pin onto the LED rail** | HD-6 |
| Data line | Gains a **1N4148** on the strip's VDD and keeps the 330 Ω | HD-5 |
| Junction identity | Physical **labels** (`J1`, `J2`, `J3`, `J4`) on the diorama, matching `tunnel_map.json`'s junction ids exactly | The map, the firmware and the physical prop must use the same four names or the routing demo becomes guesswork |
| Arrows | Fitted to the **default exit** direction, not the current one | The mechanical fallback must be static and always-correct-if-imprecise |

### H7.8 Assembly order

1. **Bench one strip alone.** Use a 5 V supply, a 330 Ω on data, and the ESP32 driving it. Confirm patterns render.
2. **Then add the 1N4148.** Confirm they still render. (If brightness visibly drops in a way you dislike, check whether the strip had been glitching *without* the diode — it almost certainly was.)
3. **Then add the 1000 µF.** Confirm they still render, and confirm the rails do not dip on power-up (watch the 5 V with the DMM while the strip starts).
4. Cut the three wires per junction to length, **label both ends** (`J2-DATA`, `J2-5V`, `J2-GND`).
5. Solder the hub end first; continuity-test; then solder the strip end.
6. Mount: strip at the junction, capacitor tucked behind, arrow sticker on the facing surface.
7. Cable-anchor every 15 cm with glue dots, avoiding the antenna keep-outs on the surface panel.

### H7.9 Bench acceptance test

| Check | Instrument / action | Expected |
|---|---|---|
| **330 Ω in circuit** | DMM, hub pin → strip DIN | 326–334 Ω |
| Strip VDD after the diode | DMM, strip `5V` → strip `GND` | **4.2 – 4.5 V** (never 5 V — that means the diode is bypassed) |
| Rail voltage before the diode | DMM | 4.9 – 5.1 V |
| Capacitor polarity | Visual + DMM in diode mode | Correct orientation; no shorts |
| Strip current at idle (all dark) | DMM in series | ~1 mA per LED standby |
| Strip current, full white, 40 % brightness | DMM in series | **≈ 190 mA per strip** (well inside the rail budget) |
| All five patterns | Command each from the server | Render correctly, no flicker, no colour lag on the first LED |
| Independent failure | Unplug J2's data wire | J1 keeps working (proves they are not chained) |
| Command loss | Stop the server | Strip holds 30 s, then `hold_amber` |
| Power-up default | Power-cycle L1 | Both strips show `hold_amber` within 1 s |
| J4 kill switch (#2) | Flip the service-panel toggle | J4 goes dark; **J3 and the hub are unaffected** |

### H7.10 Risks & rollback

| Risk | Mitigation |
|---|---|
| Strip flickers despite the diode | Verify the diode is actually in series with VDD (not the data line) and that the 330 Ω is on the **data** line. Then check the rail with the DMM during the flicker — a dipping rail is the other cause (HD-6). |
| Long J2/J4 trunks pick up noise | 22 AWG solid on a 2.5 m run at 800 kHz data is fine; if not, shorten and reroute away from the ring bundle. |
| A strip is DOA | You have 4 strips and 4 junctions — **no spare.** Order one spare now (₹70) or accept that a dead strip becomes a card explaining "junction under maintenance" with the arrow sticker carrying the fallback. **Order the spare.** |
| Back-feed through DIN when the rail is switched off | The 330 Ω in series limits it to microamps — safe. This is why switching J4's VDD is a clean demo rather than a data-line cut. |

**Rollback:** each strip is independently wired; pulling one data wire removes that junction from the demo without touching anything else.

### H7.11 Viva line

> *"There's no processor at a junction, on purpose. A passive addressable strip can't crash, can't lose its firmware and can't disagree with the server. The intelligence stays in one place, and the thing underground is as dumb and as reliable as a lamp."*

---

## PHASE H8 — Power Distribution & Grounding: The Two Rails

> **Goal** — One ground, two clean 5 V sources, measured current rather than assumed current, and a rail that survives four strips flashing white.
> **Effort** — 1.0 person-day · **Demo-critical — YES** · **Depends on** — H6
> **Demoable result** — the whole panel runs from one wall socket, the LED rail is independent of the USB hub, and you can state the measured current draw out loud.

### H8.1 Audit — what exists today

- **7-port powered USB hub (5 V / 4 A)** bought — it also creates the shared ground bus.
- **No LED rail.** The strips were implicitly fed from the level hubs' 5 V pins (**HD-6**). Worst case 1.9 A.
- **No current measurement plan** — the inline USB meter was deliberately not bought (**HD-15**), so the power slide currently has no measurement behind it.
- The **4G modem** (deferred) will need a USB port and ~500 mA on its own.

### H8.2 Target behaviour

1. **USB hub = 7 boards.** One port each: MAIN, N1, N2, N3, N4, L1, L2. Nothing else on those ports.
2. **LED rail = the strips.** A separate 5 V / 2 A source (an old phone charger is ideal) feeding all four strips through their diodes and capacitors.
3. **One common ground.** Board grounds, strip grounds and **both** supplies' grounds meet at a single star point at the USB hub.
4. **Measured, not assumed.** Current measured with the DMM in series on (a) the whole board rail and (b) the LED rail.
5. **Every rail joint soldered and taped**, with strain relief.
6. **No two 5 V outputs connected together** — grounds shared, outputs never.

```
                    POWER & GROUND — the whole picture

   wall socket ─► USB hub 5V/4A ─┬─ port1 USB ─► MAIN hub
   (or two)                       ├─ port2 USB ─► N1      (…through port 7 ─► L2)
                                  │
                                  └── GND ──┐
                                            │
   wall socket ─► 5V/2A charger ── 5V ───────┼──┬─[1N4148]─┬─ J1 strip  + [1000µF]
                                             │  ├─[1N4148]─┬─ J2 strip  + [1000µF]
                                             │  ├─[1N4148]─┬─ J3 strip  + [1000µF]
                                             │  └─[1N4148]─┬─ J4 strip  + [1000µF]
                                             │              │
                                             └──── GND ─────┴── ★ STAR POINT
                                       (the ONLY place the two supplies' grounds meet)

   RULE: the two 5 V OUTPUTS are never joined to each other. Only the grounds meet.
```

### H8.3 Wiring

| # | From | To | Wire | Colour | Termination |
|---|---|---|---|---|---|
| 1 | USB hub port 1–7 | MAIN, N1–N4, L1, L2 | 7 × USB cable | — | **USB** |
| 2 | LED rail 5 V | Junction 1 branch (through 1N4148) | 22 AWG | red | **SOLDER** |
| 3 | LED rail 5 V | Junction 2 branch (through 1N4148) | 22 AWG | red | **SOLDER** |
| 4 | LED rail 5 V | Junction 3 branch (through 1N4148) | 22 AWG | red | **SOLDER** |
| 5 | LED rail 5 V | Junction 4 branch (through 1N4148) | 22 AWG | red | **SOLDER** |
| 6 | LED rail GND | Common GND bus (star point) | 22 AWG | black | **SOLDER** |
| 7 | USB hub GND | Common GND bus (star point) | 22 AWG | black | **SOLDER** |
| 8 | Common GND bus | To every board that has a soldered GND wire (MAIN, L1, L2, all strips) | 22 AWG | black | **SOLDER** |
| 9 | J4 branch | **Toggle #2** in series (service panel) | 22 AWG | red | **SOLDER**, service loops |
| 10 | Cable ends | Heatshrink **not used** — electrical tape + glue dots | — | — | **Tape** |

### H8.4 Measuring the current (HD-15 closed, for free)

You do not need the inline USB meter. Use the multimeter in its **10 A DC** range, in **series** with the supply line.

**Procedure — measure the whole board rail:**
1. Power everything down. Unplug the USB hub's adapter from the wall.
2. Set the DMM to **10 A DC** (not mA — boards draw over 1 A and a mA range will blow its fuse).
3. Build a temporary series break: adapter + → DMM red probe; DMM black probe → the adapter's lead that feeds the hub.
4. Power up. Let everything settle and start transmitting (**60 s** so the ESP-NOW traffic and the RC522 are active).
5. Record: idle, all-4-nodes-transmitting, during a full-route LED pattern, and during a siren.
6. **Expected:** 0.75–1.2 A typical; ~1.9 A if every node transmits simultaneously. On a 4 A adapter that is real headroom — say the number out loud.

**Procedure — measure the LED rail:**
1. Repeat with the LED rail's charger in the break, DMM on **10 A DC**.
2. Record: all dark (~5 mA), one strip `chase_red` (~60 mA), **all four at white** (should approach ~0.8 A at 40 % brightness, ~1.9 A at full).
3. Confirm the rail's 5 V at the **far end** of the J2 trunk (the worst-case point) stays **≥ 4.6 V** during a white flash. If it sags below that, the 22 AWG run is too long or too thin for that branch — shorten it or split the branch.

> **Do not exceed 10 A or hold the meter in the circuit for more than ~30 s** — the shunt heats. Two 30-second measurements produce a slide-ready number.

### H8.5 Firmware changes

**None.** Power is passive. (The strip brightness cap from H7.6 is the only firmware-side power decision, and it is made for looks and for rail headroom.)

### H8.6 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| Strip supply | **Moved from the level hub 5 V pin to the LED rail** | HD-6 — the single most demo-critical change in the hardware build |
| Level hub 5 V pin | Now powers **only** the siren's + lead (~25 mA) | Keeps each USB port well inside its budget |
| USB hub | Becomes a **labelled patch panel** — port numbers written on tape next to each board | Debugging a flaky board means knowing which port it is on |
| Grounding | Formalised as a **star point at the USB hub**, with a DMM-verified continuity from every board's GND to that point | One ground, one truth (Rule 3) |
| Power slide | Rebuilt around **measured** numbers | The old slide had deep-sleep figures with nothing measured on this build |

### H8.7 Assembly order

1. **Power down and unplug everything.**
2. Build the **ground bus first** — a single black bus wire with soldered branches to every board and the rail. **Verify continuity to every endpoint with the DMM before connecting any power.**
3. Build the **LED rail** with its four diode branches and the J4 switch. Verify each branch: 5 V before the diode, 4.2–4.5 V after.
4. Connect the **USB hub's ground** to the star point **last** — it is the common reference and you want to have verified everything else first.
5. Power the LED rail alone. Confirm all four strips show `hold_amber`. **No ESP32 is powered yet.**
6. Power the USB hub. Confirm all 7 boards enumerate.
7. Run the current measurements (§H8.4) and write the numbers down where the slide can find them.

### H8.8 Bench acceptance test

| Check | Instrument / action | Expected |
|---|---|---|
| **Ground continuity** | DMM, star point → each board's GND pin | **< 1 Ω** for all 7 boards and all 4 strips |
| **No ground loops** | DMM, between any two boards' GND pins | < 1 Ω (i.e. the same net, not two nets bridged) |
| **5 V outputs isolated** | DMM resistance, USB-hub 5 V → LED-rail 5 V with both unplugged | **Open.** If this reads near 0 Ω, you have tied the two supplies together — **stop and fix.** |
| Rail voltage, both | DMM | 4.90–5.10 V |
| **Far-end strip voltage** | DMM at J2 trunk's far end, during a white flash | **≥ 4.6 V** |
| Flash survivability | Fire all four strips white + all sirens + all 7 boards TX | **No board resets**, no flicker. Watch `/api/telemetry` for a gap. |
| Board rail current | DMM 10 A series | 0.75–1.2 A typical |
| LED rail current | DMM 10 A series | ≤ 0.9 A at 40 % brightness |
| Brownout test | Fire everything, then unplug N4's USB | Everything else unaffected |
| Thermal check | Touch every regulator and the hub's brick after 10 min | Warm is fine; **hot enough to be uncomfortable is a fault** |

### H8.9 Risks & rollback

| Risk | Mitigation |
|---|---|
| Accidentally tying the two 5 V outputs | The isolation test above. This is the one mistake that can damage both supplies. **Do it before powering.** |
| Ground loop causes phantom RFID reads and UART garbage | Star-point grounding, single bus, no daisy chains around the loop |
| Adapter is actually 2 A, not 4 A | The shopping list's note: **verify the label before paying.** If it is 2 A, the boards alone still fit (1.9 A worst case is marginal) — offload the strips first, which is already the design. |
| Long white flashes sag the rail | Brightness cap at 40 %; check the far end; add a second feed point to the far junction if needed |

**Rollback:** revert to per-hub strip power (accepting the brownout risk) by re-soldering one red wire per strip. Not recommended, but it is a one-wire change if the rail fails on demo day.

### H8.10 Viva line

> *"Two 5 V sources, one ground. The boards get a 4-amp hub; the LEDs get their own 2-amp rail, because four strips at full white draw nearly two amps on their own and we are not putting that through a USB board's regulator. We measured it rather than assuming it — the boards draw about 0.9 amps idle with everything transmitting, and the LED rail peaks under an amp at our brightness cap."*

---

## PHASE H9 — Diorama Integration, Service Panel & Pre-Demo Check

> **Goal** — Everything that worked on the bench keeps working inside the diorama, the panel survives being carried into a hall, and there is a five-minute checklist that tells you whether you are ready.
> **Effort** — 1.5 person-days · **Demo-critical — YES** · **Depends on** — all
> **Demoable result** — carried as one piece, plugged into one wall socket, booted in under 60 seconds, every demo beat rehearsed.

### H9.1 Audit — what exists today

- The physical layout is **decided and stands**: 1 tunnel per level (J1/J2 on Tunnel A, J3/J4 on Tunnel B), cross-link J2↔J3, **Exit 2 far east**, trunk routing on the back edges, and the service panel on the front for the two toggles.
- 2 × PVC foam board (4 mm) for the tunnel walls and the service panel.
- 3 miner figurines (RFID-tagged), 1 rescue figurine, acrylic paints, rockfall prop.
- **HD-13:** no strain relief or cable anchoring specified. During handling, solder joints fatigue.

### H9.2 Target behaviour

1. **The whole system lifts as one piece** and sets down without needing rewiring.
2. **One wall socket** powers everything (a single extension board for both supplies).
3. **The service panel** is hinged or clip-mounted, with both toggles labelled, reachable, and with **jumper bypasses** behind them.
4. **Every cable is anchored** every 15 cm and strain-relieved at every board.
5. **Antenna keep-outs are physically built in**, not just written down.
6. **Boot to live dashboard in under 60 seconds**, every time.
7. **A photograph of each board's wiring** is in the repo, in case a joint fails at the venue.

### H9.3 Mounting rules (the ones that get skipped)

| Element | Rule | Why |
|---|---|---|
| All 7 boards | Double-sided foam tape **or** nylon standoffs, antenna edge overhanging the panel edge | §A.3.4 keep-out; a board taped flat under a foam roof loses range |
| Wires | Glue dot every 15 cm; **service loop** (a 5 cm slack loop) at every board and every switch | HD-13 — wire fatigue, not electronics, is what fails after handling |
| Wires across the panel | Route along the **back edges**; never across an antenna; never under a strip's data line | Keep-out + noise |
| Service panel | Hinged with tape or a foam-board pin; the two toggles labelled `RING SEGMENT L1–L2` and `JUNCTION J4 POWER`; **jumper bypass** behind each | The demo actuators must be reachable and repairable in 5 seconds |
| Junction strips | Recessed into the tunnel wall so the light reads as **the tunnel glowing**, not as a strip stuck on a wall | Looks matter to judges |
| Junction labels | `J1`, `J2`, `J3`, `J4` painted or taped on the visible face, matching `tunnel_map.json` | Route demo clarity |
| Arrow stickers | On the **default exit** direction | The static fallback |
| Crack rig (N2, N4) | The gauge strip and the macro lever must be **reachable by a finger** without disturbing the node | The whole point is that a judge can trigger it |
| Miner figurines | Each carries a tag **at a height the RC522 gate can read** (test at the gate before painting) | A tag that reads on the bench but not through the tunnel wall is a dead demo beat |
| RC522 gate | Reader mounted with the tag passing within ~2 cm; no metal within 3 cm of the antenna | Standard MFRICE range reality |
| USB hub + chargers | Off-panel, on the table, **≥ 20 cm** from all antennas | Interference |

### H9.4 Wiring changes

**No new logical connections.** H9 is cable management, strain relief and labelling. Concretely:

| Action | Detail |
|---|---|
| Cable labels | Every wire gets a numbered tape flag at both ends, matching the register in §C.1 |
| Label the panel | A printed card taped to the underside: the pin map (§A.2), the port map, and the MAC table |
| Bundle | Group the ring's 6 signal wires + 1 GND into one bundle on each edge; **do not bundle the strip data wires with the ring** (keep the 800 kHz data away from the UART pairs) |
| USB cable management | Short, tidy runs to the hub; a strain-relief tie at the hub end |
| Power cables | Both supplies routed to the back, with drip loops so a pull cannot reach a solder joint |
| Spares box | Taped inside the diorama's underside: 1 spare ESP32, spare 330 Ω, spare 350 Ω, spare 1N4148, a spare RFID tag, 1 m of 22 AWG, and the **RFID tag frequency note** |

### H9.5 Firmware / system changes

| Change | Detail |
|---|---|
| Boot sequence | All 7 boards power up within ~5 s; the server reaches `ACTIVE` after the 60 s window fills. **Document the exact timeline and rehearse to it.** |
| Startup ordering | If the ESP-NOW hub boots before the nodes, it may miss the first packets. Non-fatal — but confirm the dashboard recovers on its own without a restart. |
| A `preflight` routine | `tools/preflight.py` (from the backend plan, Phase 10) plus a **hardware** half: DMM checks, switch tests, tag-read test |
| Demo rehearsal | Full run of the 7 beats with the timing table from the runbook, on the integrated panel, **twice** |

### H9.6 The pre-demo checklist (print this and pin it inside the panel lid)

**Ten minutes before presenting:**

| # | Check | How | Pass |
|---|---|---|---|
| 1 | All 7 boards enumerate | `ls /dev/cu.*` | 7 devices |
| 2 | Board rail voltage | DMM at the hub's 5 V | 4.9–5.1 V |
| 3 | LED rail voltage | DMM at each strip head | 4.2–4.5 V (after diode) |
| 4 | Ground continuity | DMM, star point → MAIN, L1, L2 GND | < 1 Ω each |
| 5 | Server boots | `python3 app.py` | `[ai] TTF model loaded` |
| 6 | Dashboard live | Open `/` | 4 node cards at 2 Hz |
| 7 | Model status | `curl /api/ai` | `ACTIVE` after 60 s |
| 8 | **Ring intact** | Flip cut switch off/on | Fault banner appears, then clears |
| 9 | **J4 kill switch** | Flip twice | J4 dark, then back |
| 10 | **Siren** | Command `siren on` | Audible |
| 11 | **Junction patterns** | Command all 5 | All render |
| 12 | **RFID** | Swipe each of the 3 miner tags | 3 rows in the mustering panel |
| 13 | **Relay demo** | Foil N2's antenna 10 s | `via relay` badge appears |
| 14 | **Crack demo** | Bend the N2 strip | µε moves first, then mm |
| 15 | Node 1 & Node 2 live hardware | Twist the pots | Gauges move |
| 16 | Spares box present | Visual | Contents intact |
| 17 | Phone hotspot off, laptop on charge | Visual | — |
| 18 | Photography of each board | In the repo folder | Accessible offline |

**If any of 1–7 fails:** you can still present, because the backend's replay mode (backend Phase 0) drives the whole thing from a recorded session. **Rehearse that fallback once so you know the command.**

### H9.7 Changes to the previous prototype

| Component | Change | Why |
|---|---|---|
| Old 3-board prototype | Fully absorbed into the 7-board build. N1 and N2 retain their original MPU + pot wiring; everything else is new. | Nothing from the old build is wasted |
| Single-tunnel diorama | Extended to two levels with a cross-link | Standing layout decision |
| Service panel | **New** — front-mounted, labelled, with bypasses | Demo reliability |
| Cable management | Taped, anchored, labelled | HD-13 |
| Under-panel card | **New** — pin map + port map + MAC table | Any of the 6 team members can debug it |

### H9.8 Bench acceptance test — the "carry it across the room" test

This is the test that matters and it is the one everybody skips.

1. **Boot and verify everything** (the checklist above, items 1–15).
2. **Power down.** Coil the cables as you would for transport.
3. **Lift the whole diorama and carry it across the room.** Set it down. Do this **three times.**
4. **Power up and re-verify items 1–15.**
5. Any item that fails has a fatigue problem — find it, re-solder it, and **give that joint a service loop**.

| Check | Expected after 3 carries |
|---|---|
| All 7 boards enumerate | 7/7 |
| All 4 nodes streaming | 4/4 |
| Ring heartbeats | Both ports, 2 s cadence |
| Cut switch | Clean fault + clean recovery |
| Relay badge | Appears on demand |
| LEDs / siren / RFID | All working |
| Crack channel | Baseline still valid (no drift from the carry) |

### H9.9 Risks & rollback

| Risk | Mitigation |
|---|---|
| A joint fractures in transit | The carry test + service loops + the spares box + the wiring photographs |
| Antennas detuned by final assembly | Re-check packet loss **after** the panel is closed, not before |
| The crack baseline drifts after mounting | **Re-zero the micro baseline as the last step of the build**, and record the new baseline on the node's label |
| Something fails at the venue with no time | Replay mode + the arrow stickers + "degraded mode" is itself a demonstration of the design |
| A judge pulls a wire | The panel's underside card tells you where it goes; the labels tell you which one it is |

### H9.10 Viva line

> *"The panel is designed to be carried, not just to work on the bench. Every board has a service loop, every cable is labelled at both ends and anchored every 15 centimetres, and there's a pin map taped inside the lid. We test it by lifting it and putting it down three times and re-running the whole checklist — that's the difference between a prototype and something you'd hand to a mine."*

---
---

# PART C — APPENDICES

## C.1 Complete cable schedule

One row per physical wire in the build. **Print this, tick it off as you solder.**

### C.1.1 Ring backbone (6 signal + 1 ground)

| ID | From | Pin | To | Pin | Colour | Length | Term. | Done |
|---|---|---|---|---|---|---|---|---|
| W1 | MAIN | 17 | L1 | 16 | orange | hop 1 | SOLDER | ☐ |
| W2 | L1 | 17 | MAIN | 16 | orange | hop 1 | SOLDER | ☐ |
| W3 | L1 | 4 | L2 | 13 | purple | hop 2 | SOLDER | ☐ |
| W4 | L2 | 4 | L1 | 13 | purple | hop 2 | SOLDER + **switch #1** | ☐ |
| W5 | L2 | 17 | MAIN | 13 | teal | hop 3 | SOLDER | ☐ |
| W6 | MAIN | 4 | L2 | 16 | teal | hop 3 | SOLDER | ☐ |
| W7 | MAIN + L1 + L2 + rail | GND | star point at USB hub | GND | black | routed | SOLDER | ☐ |

### C.1.2 Per node (×4)

| ID | From | Pin | To | Colour | Term. | Done |
|---|---|---|---|---|---|---|
| n-01 | ESP32 | 3V3 | GY-521 VCC | red | SOLDER | ☐ |
| n-02 | ESP32 | GND | GY-521 GND | black | SOLDER | ☐ |
| n-03 | ESP32 | 21 | GY-521 SDA | yellow | SOLDER | ☐ |
| n-04 | ESP32 | 22 | GY-521 SCL | green | SOLDER | ☐ |
| n-05 | ESP32 | 32 | disp pot wiper | blue | SOLDER | ☐ |
| n-06 | ESP32 | 3V3 | disp pot end A | red | SOLDER | ☐ |
| n-07 | ESP32 | GND | disp pot end B | black | SOLDER | ☐ |
| n-08 | ESP32 | 34 | SW-420 DO | white | SOLDER | ☐ |
| n-09 | ESP32 | 3V3 | SW-420 VCC | red | SOLDER | ☐ |
| n-10 | ESP32 | GND | SW-420 GND | black | SOLDER | ☐ |

### C.1.3 Per crack channel (N2, N4 only — ×2)

| ID | From | Pin | To | Colour | Term. | Done |
|---|---|---|---|---|---|---|
| c-01 | ESP32 | 3V3 | HX711 VCC + AVCC | red | SOLDER | ☐ |
| c-02 | ESP32 | GND | HX711 GND | black | SOLDER | ☐ |
| c-03 | ESP32 | 18 | HX711 SCK | yellow | SOLDER | ☐ |
| c-04 | ESP32 | 19 | HX711 DT | orange | SOLDER | ☐ |
| c-05 | HX711 | E+ | bridge 3V3 rail (gauge top + R2 top) | red | SOLDER | ☐ |
| c-06 | HX711 | E− | bridge GND rail (R3 bottom + R4 bottom) | black | SOLDER | ☐ |
| c-07 | HX711 | A+ | midpoint (gauge bottom + R3 top) | green | SOLDER | ☐ |
| c-08 | HX711 | A− | midpoint (R2 bottom + R4 top) | white | SOLDER | ☐ |
| c-09 | ESP32 | 33 | macro pot wiper | blue | SOLDER | ☐ |
| c-10 | ESP32 | 3V3 | macro pot end A | red | SOLDER | ☐ |
| c-11 | ESP32 | GND | macro pot end B | black | SOLDER | ☐ |

### C.1.4 Per level hub (×2)

| ID | From | Pin | To | Colour | Term. | Done |
|---|---|---|---|---|---|---|
| l-01..04 | ESP32 | 17/16/4/13 | ring ports A & B | orange / purple | SOLDER | ☐ |
| l-05 | ESP32 | 3V3 | RC522 3.3V | red | SOLDER | ☐ |
| l-06 | ESP32 | GND | RC522 GND | black | SOLDER | ☐ |
| l-07 | ESP32 | 18 | RC522 SCK | yellow | SOLDER | ☐ |
| l-08 | ESP32 | 19 | RC522 MISO | orange | SOLDER | ☐ |
| l-09 | ESP32 | 23 | RC522 MOSI | green | SOLDER | ☐ |
| l-10 | ESP32 | 5 | RC522 SDA/SS | white | SOLDER | ☐ |
| l-11 | ESP32 | 27 | RC522 RST | grey | SOLDER | ☐ |
| l-12 | RC522 VCC | 3V3 | 100 nF + 10 µF to GND | — | SOLDER | ☐ |
| l-13 | ESP32 | 21 | 330 Ω → route A strip DIN | blue | SOLDER | ☐ |
| l-14 | ESP32 | 22 | 330 Ω → route B strip DIN | blue | SOLDER | ☐ |
| l-15 | 5V | — | siren + | red | SOLDER | ☐ |
| l-16 | ESP32 | 14 | siren − | yellow | SOLDER | ☐ |
| l-17 | ESP32 | 32 | gate LED green | green | SOLDER | ☐ |
| l-18 | ESP32 | 33 | gate LED red | red | SOLDER | ☐ |
| l-19 | LED cathodes | GND | common GND | black | SOLDER | ☐ |
| l-20 | ESP32 | GND | common GND | black | SOLDER | ☐ |

### C.1.5 Per junction (×4)

| ID | From | To | Colour | Term. | Done |
|---|---|---|---|---|---|
| j-01 | level hub GPIO (21 or 22) | 330 Ω → strip DIN | blue | SOLDER | ☐ |
| j-02 | LED rail 5 V | 1N4148 → strip 5V | red | SOLDER | ☐ |
| j-03 | LED rail GND | strip GND | black | SOLDER | ☐ |
| j-04 | 1000 µF | strip 5V (after diode) ↔ GND | — | SOLDER | ☐ |

### C.1.6 Power

| ID | From | To | Term. | Done |
|---|---|---|---|---|
| p-01..07 | USB hub ports 1–7 | MAIN, N1, N2, N3, N4, L1, L2 | USB | ☐ |
| p-08 | USB hub GND | star point | SOLDER | ☐ |
| p-09 | LED rail 5 V | J1 branch → 1N4148 | SOLDER | ☐ |
| p-10 | LED rail 5 V | J2 branch → 1N4148 | SOLDER | ☐ |
| p-11 | LED rail 5 V | J3 branch → 1N4148 | SOLDER | ☐ |
| p-12 | LED rail 5 V | J4 branch → 1N4148 + **switch #2** | SOLDER | ☐ |
| p-13 | LED rail GND | star point | SOLDER | ☐ |

**Totals:** 7 ring + 40 node + 22 crack + 40 level-hub + 16 junction + 13 power ≈ **138 wire runs / joints**. At ~2 minutes per joint including the test, that is **~4.5 hours of pure soldering** — budget it, do not discover it.

---

## C.2 Soldering standard operating procedure

Every joint in this build uses the same six steps. It is worth reading once and following always, because intermittent faults from marginal joints cost more time than the discipline does.

1. **Clean and tin.** Wipe the iron tip, apply a little solder, wipe again. Touch the flux pen to the header pin and the pre-tinned wire.
2. **Tin both sides separately.** Pre-tin the wire end, and pre-tin the header pin. Two tinned surfaces join in under a second.
3. **Join with heat, not solder.** Bring the iron to the *joint* (not the solder), hold for 1–2 s, and let the two tins flow together. **Do not feed more solder into a header joint** — it wicks into the neighbouring pin.
4. **Dwell ≤ 3 seconds.** Longer than that and the pad lifts off the board.
5. **Inspect.** A good joint is **shiny, concave and smooth**. A cold joint is dull, blobby and granular — it will pass a continuity test today and fail next week.
6. **Test.** Continuity with the DMM (< 1 Ω), then a **gentle tug** on the wire. A joint that moves is a joint that will fail on stage.

**Additional rules for this build:**
- **Never solder a powered board.** Unplug USB first, every time. (Rule 4 of the workspace, and it is not negotiable.)
- **Soldering to header pins, not to the pad underneath:** the 30-pin DevKit's header pins are the accessible surface. Solder to the pin's exposed length.
- **Keep the iron away from the PCB antenna** — the antenna end has no solderable pins and heat there degrades RF performance.
- **Flux residue:** clean with isopropyl before taping. Left-on flux attracts dust and moisture.
- **Never use hot glue on the BF350**, or anywhere a joint must stay cool and mechanically fixed. Electrical tape and glue dots only, away from the gauge.
- **Label before you solder.** Once the wire is in the board, you cannot tell which end is which.

---

## C.3 GPIO conflict matrix

The whole point of H0's pin freeze. Read across each row: **no pin appears twice on the same board.**

| GPIO | Node (N1–N4) | Main Hub | Level Hub (L1/L2) | Verdict |
|---|---|---|---|---|
| 1, 3 | — | UART0 / USB | — | reserved |
| 2 | onboard LED | onboard LED | onboard LED | safe |
| **4** | spare | **UART1 TX (ring)** | **UART1 TX (ring)** | OK no conflict |
| 5 | spare | spare | RC522 CS | OK |
| 6–11 | ─── never use anywhere (internal SPI flash) ─── | | | NEVER |
| 12 | spare | spare | spare — **never the siren** | NEVER strapping |
| **13** | spare | **UART1 RX (ring)** | **UART1 RX (ring)** | OK |
| **14** | spare | spare | **siren** | OK |
| 16, 17 | spare | **UART2 RX/TX (ring)** | **UART2 RX/TX (ring)** | OK |
| **18** | **HX711 SCK** (N2/N4) | spare | **RC522 SCK** | OK different boards |
| **19** | **HX711 DT** (N2/N4) | spare | **RC522 MISO** | OK different boards |
| **21** | **MPU SDA** | spare | **WS2812 route A** | OK different boards |
| **22** | **MPU SCL** | spare | **WS2812 route B** | OK different boards |
| 23 | spare | spare | RC522 MOSI | OK |
| 25, 26 | spare | *reserved: deferred GPS* | *reserved* | reserved |
| **27** | spare | spare | **RC522 RST** | OK |
| **32** | **disp pot (ADC1)** | spare | **gate LED green** | OK different boards |
| **33** | **macro pot (ADC1, N2/N4)** | spare | **gate LED red** | OK different boards |
| 34 | **SW-420 DO** (input only) | spare | spare | OK |
| 35 | optional SW-420 AO | spare | spare | OK |
| 36, 39 | spare (input only) | spare | spare | available if needed |

**Cross-board verify:** the three pins that appear on multiple boards are 4, 13, 16, 17 (ring — a *deliberate* shared net, matched pairs), and 18/19/21/22/32/33 (different boards, different functions). **There is no accidental reuse anywhere.**

---

## C.4 Pre-power and cold-start checklists

### C.4.1 Before applying power to ANY new assembly

| # | Check | Instrument | Pass |
|---|---|---|---|
| 1 | `5V` → `GND` resistance | DMM ohms | **> 1 kΩ** (a short here means a solder bridge) |
| 2 | `3V3` → `GND` resistance | DMM ohms | **> 1 kΩ** |
| 3 | Every signal wire vs every other signal wire | DMM continuity | **Open** |
| 4 | Each signal wire to its intended destination | DMM continuity | **< 1 Ω** |
| 5 | Polarity of every module | Visual | VCC/GND not swapped |
| 6 | Electrolytic capacitor polarity | Visual | Stripe = negative |
| 7 | Diode orientation | Visual | **Band = cathode = toward the strip's VDD** |
| 8 | No wire on GPIO 6–11, 0, 1, 3, 12 | Visual | Confirmed against §A.2 |
| 9 | No 5 V wire touching any GPIO **other than** the siren's + lead on 5 V (GPIO14 is the sink, not the source) | Visual + DMM | Confirmed |
| 10 | Antenna keep-out | Visual | 15 mm clear |

### C.4.2 Cold start sequence (every rehearsal, in this order)

1. Power the **LED rail first** → all four strips show `hold_amber`.
2. Plug in the **USB hub** → all 7 boards enumerate; node LEDs blink; the hub starts printing to serial.
3. On the Mac: `cd neuralstrata-backend && python3 app.py`
4. Wait for `[ai] TTF model loaded` and `[ai] pattern model loaded`.
5. Open the dashboard; confirm 4 node cards.
6. Mark `t=0`. **`ACTIVE` appears at ≈ t + 65 s.** Rehearse to that number.
7. Run the service-panel switch tests (ring cut, J4 kill).
8. Begin the drill sequence.

---

## C.5 Fault-finding table

| Symptom | Most likely cause | Check | Fix |
|---|---|---|---|
| A board won't boot / boots only sometimes | Wire on GPIO9/10 or GPIO12 | Which pins are used | Move to 4/13 (ring) or 14 (siren) — **HD-1, HD-2** |
| Siren sounds on power-up | GPIO12 pulled high at boot, or a passive buzzer | The buzzer's type | Move to GPIO14; drive a passive one with `ledc` |
| Strip flickers / first LED ignores commands | Data-level margin (3.3 V vs 3.5 V threshold) | Strip VDD value after the diode | Fit the 1N4148 (**HD-5**) |
| Strip flickers only when several are white | Rail sag | DMM at the far end of the J2 trunk | Cap brightness; short the run; split the branch |
| L1/L2 reset when LEDs flash | Strips powered from the hub's 5 V pin | Where the strip's red wire goes | Move to the LED rail (**HD-6**) |
| RC522 reads one tag then hangs | No decoupling; module browning out on transmit | Decoupling caps fitted? | 100 nF + 10 µF at the module (**HD-12**) |
| RC522 reads nothing at all | Powered at 5 V (dead), or SPI speed too high, or tag frequency wrong | Module VCC; SPI clock; tag type | Replace module; drop SPI to 4 MHz; confirm **13.56 MHz** MIFARE tags |
| Crack channel drifts continuously | HX711 warm-up not respected, or a thick glue bond creeping | Serial log over 60 s | 15 s warm-up + `flags bit 2`; re-bond thinner; re-zero after cure |
| Crack channel rails to ±max | Open gauge or broken lead | `flags bit 0` | Re-solder the gauge leads; a railed channel must read as **fault**, not zero |
| Bridge reads a large constant offset | Mismatched completion resistors | DMM on each resistor | Select-match within 0.3 Ω, or null with a 50 Ω trimpot |
| **Any GPIO reads ~5 V** | A 5 V sensor output feeding a 3.3 V pin | DMM on every signal pin | **STOP.** Fix before continuing — **HD-4 class fault** |
| Node data has multi-second gaps | Antenna detuned or a wire across it | Antenna keep-out | Re-route; move the board |
| Ring: one direction dead | A single wire broken, or a switch installed in the wrong place | Continuity of both wires of that hop | Repair; switch belongs on **W4 only** — **HD-7, HD-8** |
| Ring cut demo does nothing | Switch is on the GND wire (shared bus) | Trace the switch | Move it to W4 (**HD-7**) |
| RFID swipes flood the log | No debounce | Count rows per swipe | Ignore the same UID within 3 s |
| Everything works, then stops when the panel is moved | Fatigued solder joint | Wiggle each wire at the board | Re-solder + service loop (**HD-13**) |
| Random CRC/framing errors on all hubs | Broken common GND | DMM star point → each GND | Repair the GND bus |
| Dashboard shows a frozen node value | Node is dead and the UI has no staleness flag | Backend `/api/ai` + health | Backend Phase 2 (`stale` flag, greyed card) |

---

## C.6 Cost summary

| Block | ₹ | Per person (6) |
|---|---|---|
| Core to buy (hub, cables, 22 AWG kit 2, BF350 ×2, HX711 ×2, macro pots ×2, tape) | 1,245 | 208 |
| Corrected quantities (350 Ω ×6, 1N4148 ×8, 1000 µF ×4) | 75 | 13 |
| LED rail charger | 0–150 | 0–25 |
| Multimeter (if not owned) | 600 | 100 |
| Consumables (solder, flux, wick, tape, standoffs, labels) | ~400 | 67 |
| Spare WS2812B strip **(recommended)** | 70 | 12 |
| **Total** | **₹2,390 – ₹2,540** | **₹398 – ₹423** |
| *On hold:* 4G modem + SIM | *1,300* | *217* |
| *Not buying:* GPS, LoRa, solar, terminal blocks | 0 | 0 |

### C.6.1 Cut list if time or money collapses (in this order)

| Cut | Cost of cutting | Demo impact |
|---|---|---|
| 1. **The crack system (H2)** | Loses the "initiation vs progression" story and Model 1's best feature | Significant but survivable — the demo still predicts TTF |
| 2. **The second level (L2 + J3 + J4)** | Loses the ring triangle; becomes a chain | The ring-cut demo degrades to "one level goes dark" |
| 3. **The gate LEDs** | Loses a small visual | Minor |
| **Never cut** | H0, H1, H3, H4, H5, H8 | These are the reliability spine |

---

## C.7 Viva question bank

| Question | Answer lives in |
|---|---|
| "Why this pin for this sensor?" | §A.2 + §A.2.0 — ADC2 unusable with Wi-Fi; input-only pins for sensors; flash and strapping pins avoided |
| "Why three resistors per strain gauge when your BOM listed one?" | §H2.2 — a Wheatstone bridge needs two midpoints; one completion resistor cannot form one (HD-3) |
| "Why is the HX711 on 3.3 V when the datasheet allows 5 V?" | §H2.3 — its DOUT swings to DVDD; 5 V logic into a non-5V-tolerant ESP32 pin (HD-4) |
| "Why does a junction have no processor?" | §H7.11 — passive hardware cannot crash; one MCU per level to maintain |
| "Why not power the LEDs from the ESP32?" | §A.3.2/§H8 — four strips at full white ≈ 1.9 A; boards get their own rail |
| "How is the ring fault detected?" | §H5.5 — 2 s heartbeats per port, edge-triggered fault reporting on a > 4 s gap |
| "What happens if a node is destroyed?" | §H3 — brownout last-gasp packet; the failure becomes data |
| "How do you know it survives handling?" | §H9.8 — the carry-it-across-the-room test, three times, full checklist after each |
| "What's your power budget?" | §A.3.2 + the measured numbers from §H8.4 |
| "Why no GPS?" | It does not measure subsidence; the mine is georeferenced by a fixed baseline + offsets |
| "Why is the LED data pin a problem at all?" | §H7.3 — WS2812B's V_IH is 0.7 × VDD; the diode trick fixes it for ₹5 |
| "How do you protect against a bad solder joint?" | §C.2 — continuity + tug test on every joint, plus the carry test |

---

## C.8 Glossary (plain language)

| Term | Plain meaning |
|---|---|
| **GPIO** | A pin on the microcontroller that can be an input or an output. Some have restrictions (see §A.2.0). |
| **ADC / ADC1 / ADC2** | The chip's analogue-to-digital converter. Analog sensors (pots, strain) need it. **ADC2 stops working when Wi-Fi is on** — which is always, because ESP-NOW is Wi-Fi. |
| **I²C** | A two-wire bus (data + clock) for the MPU-6050. Two pins serve many devices. |
| **SPI** | A four-wire bus for the RFID reader. Faster than I²C; needs one "chip-select" pin per device. |
| **UART** | A two-wire serial link (TX ↔ RX). The ring backbone is three UARTs wired into a triangle. **Always cross TX to RX.** |
| **Strapping pin** | A pin the chip reads *at boot* to configure itself. Get it wrong (GPIO12) and the board may not start. |
| **Input-only pin** | GPIO34–39. Can read, cannot drive. Good for sensors, useless for LEDs. |
| **Full duplex** | Can send and receive at the same time — which is why each ring hop needs two wires. |
| **Ring / triangle** | Three boards, each directly connected to both others. **Any one cut still leaves all three connected.** |
| **Wheatstone bridge** | Four resistors in a diamond; the tiny difference between two midpoints is what the HX711 measures. One arm is the strain gauge. |
| **Strain gauge** | A foil resistor that changes resistance when stretched. Gauge factor ~2.0. Measures micro-strain (µε). |
| **Half-bridge / quarter-bridge** | How many bridge arms are active gauges. This build uses **one active arm + three fixed**. |
| **HX711** | A 24-bit ADC designed to read small bridge outputs. Needs a 15-second warm-up. |
| **Micro-strain (µε)** | The crack *starting*. Comes first. |
| **Macro opening (mm)** | The crack *growing*. Comes second. |
| **WS2812B / NeoPixel** | Addressable RGB strip — every LED is individually controllable over one data wire. It is a **5 V** part; the ESP32 is 3.3 V, which is why the diode trick exists. |
| **V_IH (input high)** | The voltage a chip needs to see as a "1". For a 5 V WS2812B it is 3.5 V — higher than the ESP32's 3.3 V output. |
| **Pull-up / pull-down** | A resistor that holds a wire at a default voltage so it does not float. |
| **Decoupling capacitor** | A small capacitor next to a chip so its own current spikes do not disturb the supply. The RC522 needs one. |
| **Bulk capacitor (1000 µF)** | A big capacitor to absorb the inrush when a strip powers up. |
| **Inrush** | The current surge a device draws in its first milliseconds. |
| **Star point** | The single place where all grounds meet. Prevents ground loops. |
| **Ground loop** | Two ground paths between the same two points. Causes noise and phantom readings. |
| **Service loop** | A deliberate slack loop of wire so pulling on a cable does not pull on the solder joint. |
| **Strain relief** | Anything that keeps mechanical force away from an electrical joint. |
| **Brownout** | The supply dipping so low the chip resets. |
| **Last-gasp packet** | The final message a node sends as it dies. |
| **Keep-out** | Space kept clear around an antenna. Wire and metal nearby reduce range. |
| **Continuity** | A path that conducts. A good joint reads under 1 Ω. |
| **Cold joint** | A dull, poorly-formed solder joint. Passes a test today, fails next week. |
| **Select-matching** | Measuring several parts and picking the ones closest to each other. Free accuracy. |

---

*End of document. Part A is the interface contract (the pin map and the rules), Part B is the build order, Part C is the safety net. If a phase's §5 conflicts with anything in `neural-strata-system-map.html`, `Neural_Strata_Have_vs_Buy.pdf` or `neural-strata-crack-sensor.md`, **this document wins** — the conflicts are enumerated in §A.6 and fixed in Phase H0.*
