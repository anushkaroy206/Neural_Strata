# Neural Strata — Wiring Walkthrough

## The build, in the order your hands will do it · Station 0 → Station 8

| | |
|---|---|
| **Document** | `Neural_Strata_Wiring_Walkthrough.md` · v1.0 |
| **Relationship to** | A **re-sequencing**, not a replacement, of `Neural_Strata_Hardware_Build_Phases.md`. Same pins, same colours, same termination choices — reorganised into one continuous bench order so you do not have to jump between H1 and H8 with a soldering iron in your hand. **If the two ever disagree, the phase document wins.** |
| **Covers** | 7 boards · 4 sensor nodes · 2 level hubs · 1 main hub · 4 junctions · 7 ring wires · 3 power rails |
| **You will need** | DMM (non-negotiable), iron + fine tip, 0.8 mm solder, flux pen, wick, side cutters, wire strippers, tweezers, isopropyl, masking tape + marker, helping hands |
| **Golden rule** | **Never solder a powered board. Unplug the USB first, every time.** |

> **How to use this.** Do the stations in order. Each one has *Before you start*, the connection tables, *After you finish* (the checks that must pass before you move on), and *Pitfalls*. Do not skip a check to save five minutes — every one of them exists because it caught a real failure.

---

# Before Station 0 · Read once, apply forever

## The three rails and one ground

| Rail | Where it lives | Feeds | **Never connect to** |
|---|---|---|---|
| **5 V** | USB hub adapter, and the separate LED-rail charger | WS2812B strips, siren's `+` lead, each board's `5V` pin (regulator input) | any GPIO, the HX711, the MPU-6050, the SW-420 |
| **3.3 V** | each board's onboard regulator, `3V3` pin | MPU-6050, SW-420, both pots' end A, HX711 (**VCC and AVCC**), RC522 | the siren's 5 V lead |
| **GND** | **one common bus** across all 7 boards, all 4 strips and **both** power sources | everything's return path | — |

**Two separate 5 V sources sharing one ground is correct and intended.** The USB hub and the LED-rail charger must share **GND only** — never tie their 5 V outputs together.

## The GPIO rules that decide whether this build works

| GPIO | Rule | Why it matters here |
|---|---|---|
| **6, 7, 8, 9, 10, 11** | **NEVER USE** | wired to the internal SPI flash. Anything here risks boot failure or a corrupted flash. *This is why the ring is remapped off 9/10.* |
| **34, 35, 36, 39** | **input only** — no output driver, no pull-up | fine for the SW-420 `DO`; cannot drive an LED |
| **32, 33, 34, 35, 36, 39** | **ADC1** — use for every analog read | ADC2 is unusable while Wi-Fi is on, and **ESP-NOW is Wi-Fi** |
| **12** | strapping pin (MTDI) — sets flash regulator voltage | never put a siren or a pull-up-heavy module here |
| **0, 2, 4, 5, 15** | strapping pins — usable, but have a required level at boot | avoid for anything that must be predictable from millisecond one |
| **1, 3** | UART0 / USB | reserved, never repurpose |
| **25, 26** | reserved for the deferred GPS | do not use, even though they look free |

**UART budget:** the ESP32 has exactly **three** hardware UARTs (0, 1, 2). USB takes UART0, the ring takes UART1 and UART2. There is **no UART3** and no UART left for GPS. Also: **no ESP32 pin is 5 V tolerant. Ever.**

## Wire colour legend — use it, it is not decoration

| Colour | Carries |
|---|---|
| **red** | 3.3 V power to a module — *and* the 5 V rail wire at the strips |
| **black** | GND, always |
| **yellow** | I²C SDA (node) · HX711 SCK (crack) · RC522 SCK · siren drive |
| **green** | I²C SCL (node) · RC522 MOSI · gate LED green · bridge `A+` |
| **blue** | pot wipers · **strip data to every junction** |
| **orange** | SW-420 `DO` · HX711 `DT` · RC522 MISO · **ring port A (hop 1)** |
| **purple** | **ring hop 2** (L1 ↔ L2) — the leg that carries the cut switch |
| **teal** | **ring hop 3** (MAIN ↔ L2) |
| **white** | SW-420 body · RC522 CS · bridge `A−` |

**Label both ends of every wire with tape + marker while the wire is still in your hand.** The 7-wire backbone is unbuildable without it.

## ESD and handling

- Touch the laptop's metal chassis before handling the **BF350** or the **RC522**.
- Keep the BF350's leads **shorted with a scrap of foil** until you solder them.
- Never hot-plug the ring — power down MAIN, L1 and L2 before changing any backbone wire.
- Both ESD-sensitive parts stay in their bags until their station.

---

# STATION 0 · Bench truth — measurements before anything

**Effort 0.5 day. Do not skip. This station has saved more builds than any other.**

### 0.1 Sanity-check the meter first

Put the DMM in continuity mode and touch the probes together.

| Reading | Meaning |
|---|---|
| **0.1 – 0.5 Ω** | meter is healthy, proceed |
| **2 – 3 Ω** | **the meter's battery is dying.** Stop. Every "good joint" test you run afterwards will be wrong. Change the battery. |

### 0.2 Reconcile the old documents (40 minutes)

This build has three earlier documents that disagree about pins. **This walkthrough and the phase document are the corrected versions**; the old pages are wrong in these specific places:

| Old document says | Truth | Why |
|---|---|---|
| Ring port on **GPIO 9 / 10** | **4 / 13** | 9 and 10 are flash pins |
| Siren on **GPIO 12** | **GPIO 14** | GPIO12 is the flash-voltage strapping pin — a low-level-trigger module can hold it high and the board will not boot |
| Siren on **GPIO 25** | **GPIO 14** | 25 is reserved for the deferred GPS |
| Gate LEDs on **25 / 26** | **32 / 33** | 25/26 are GPS-reserved; 32/33 are free on a level hub |
| Ring port on **16 / 17 vs 25 / 26** | **16 / 17 (UART2)** | consistent with the rest of the build |
| GPS on **"UART3", 5 V-tolerant** | **no UART3; no pin is 5 V tolerant** | both claims are false |
| W6 = **MAIN GPIO17 → L2 GPIO9** | **MAIN GPIO4 → L2 GPIO16** | GPIO17 is already committed to hop 1, and GPIO9 is a flash pin |

**Write the corrected pins on a card and tape it to the bench.** You will look at it fifty times.

### 0.3 Photograph and bin

- Photograph every existing board from above, with wires labelled.
- Two bins: **verified** and **unknown**. Anything whose continuity has not been measured goes in *unknown*.
- **Do not begin Station 1 until the card is written and the meter checks out.**

---

# STATION 1 · The standard sensor node — N1, N2, N3, N4

**Effort 0.5 day for the first node, then ~30 minutes each. All four are identical.**

### 1.1 Before you start

- One node on the bench at a time. **Never two partially wired boards powered at once** — you will cross-feed and misdiagnose.
- Bench-first rule: **breadboard it with jumpers, flash the firmware, confirm all three channels move on the dashboard.** Then power down and solder. Yes, this feels like doing it twice. It is why the soldered version works.

### 1.2 The connections — 10 wires per node

| # | From (ESP32) | To (sensor) | Colour | Length | Termination |
|---|---|---|---|---|---|
| 1 | `3V3` | GY-521 `VCC` | red | 70 mm | **SOLDER** |
| 2 | `GND` | GY-521 `GND` | black | 70 mm | **SOLDER** |
| 3 | `GPIO21` | GY-521 `SDA` | yellow | 70 mm | **SOLDER** |
| 4 | `GPIO22` | GY-521 `SCL` | green | 70 mm | **SOLDER** |
| 5 | `GPIO32` | displacement pot **wiper** (middle pin) | blue | 120 mm | **SOLDER** |
| 6 | `3V3` | displacement pot **end A** | red | 120 mm | **SOLDER** |
| 7 | `GND` | displacement pot **end B** | black | 120 mm | **SOLDER** |
| 8 | `GPIO34` | SW-420 `DO` | white | 120 mm | **SOLDER** |
| 9 | `3V3` | SW-420 `VCC` | red | 120 mm | **SOLDER — 3V3, never 5 V** |
| 10 | `GND` | SW-420 `GND` | black | 120 mm | **SOLDER** |
| — | USB port | powered USB hub | — | 1 m | **USB cable** |

`GPIO35` (SW-420 `AO`) is **left open** unless you are diagnosing.

```
        SENSOR NODE (N1–N4) — all four identical
        ┌───────────────────────────────────────────────────┐
   USB ─┤ 5V  ●                                        ● 3V3 ├──┬── MPU-6050 VCC    (red)
        │                                                      ├── SW-420 VCC      (red)
        │   ESP32 DevKit (30-pin, ESP32-WROOM-32)              └── disp pot end A  (red)
        │                                                         (+ macro pot on N2/N4)
        │ GND ●                                        ● GND  ├──┬── MPU / SW-420 / pot ends (black)
        │ 21  ● SDA ──────────────────────────── MPU-6050 SDA     (yellow)
        │ 22  ● SCL ──────────────────────────── MPU-6050 SCL     (green)
        │ 32  ● ──────────────────────────────── disp pot wiper   (blue)
        │ 34  ● ◄─────────────────────────────── SW-420  DO       (white)
        │  2  ● onboard LED
        └───────────────────────────────────────────────────┘
```

### 1.3 Which node gets what

| Node | Level | Sensors fitted at this station |
|---|---|---|
| **N1** | 1 | MPU-6050 + displacement pot + SW-420 |
| **N2** | 1 | as N1, **plus the full crack channel (Station 2)** |
| **N3** | 2 | as N1 |
| **N4** | 2 | as N1, **plus the full crack channel (Station 2)** |

**N1 and N3 transmit `micro = 0` and `macro = 0`** so the JSON contract is identical across all four nodes. That is deliberate — one firmware, four instances, no drift.

### 1.4 Assembly order, per node

1. Breadboard, flash, confirm the dashboard shows tilt, displacement and vibration.
2. **Power down.** Unplug USB.
3. Cut and pre-tin 10 wires to length. **Label both ends.**
4. Solder to the **ESP32 header pins first**, one wire at a time, **tug-testing each joint** as it cools.
5. Solder the sensor ends. Keep the SW-420's three wires side by side so they cannot swap.
6. **Continuity check before power:** `3V3` ↔ `GND` must read **open**, not 0 Ω. A bridge here destroys the board on the next step.
7. Power up and re-verify all three channels.
8. **Photograph the finished node from above.** This photo is your wiring diagram at 11 p.m.

### 1.5 After you finish — Station 1 checks

| Check | How | Expected |
|---|---|---|
| No power-to-ground bridge | DMM continuity, `3V3` ↔ `GND` | open circuit |
| Every joint is mechanical | gentle tug on each of the 10 wires | nothing moves |
| MPU-6050 alive | tilt on the dashboard moves when you tilt the board | smooth, no `NaN` |
| Pot spans its range | move the slide through its full travel | ~0 → ~100 mm |
| SW-420 counts | tap the sensor | event count increases |
| Current draw | DMM in **10 A DC**, in series with the 5 V USB line | 80–120 mA idle, 180–280 mA peaks while transmitting |

### 1.6 Pitfalls

- **A joint that moves is a joint that fails on stage.** 22 AWG solid in a 2.54 mm header feels like it works. It does not survive handling.
- **Strip ≤ 8 mm** of insulation. Longer and you will bridge the neighbouring pin.
- Check adjacent pins for bridges **before** applying power, every time.

---

# STATION 2 · The two-stage crack channel — N2 and N4 only

**Effort 2.0 days. The most delicate work in the build.**

### 2.1 The physics in one paragraph, so the wiring makes sense

A foil strain gauge (BF350) is one arm of a **Wheatstone bridge**. When the seam opens, the gauge stretches, its resistance rises, and the bridge goes out of balance — the HX711 reads that imbalance as **microstrain rate**, which is your *initiation* signal. A separate rotary pot across the same seam measures **macro opening in millimetres**, which is your *propagation* signal. Two sensors, two physical principles, one report: initiation then propagation. That is what separates this from a vibration threshold wearing a crack-sensor label.

### 2.2 The bridge, before any wire goes near the board

The BF350 is **one arm**. It needs **three fixed resistors** to complete the bridge.

- **Select-match R2, R3, R4:** measure each with the DMM and pick the three closest to each other — **within 0.3 Ω**. This is free with the meter you already have, and it removes most of the offset that would otherwise eat your ADC range.
- Solder the network **on a small piece of perfboard or directly across the HX711 terminals**. Do **not** leave it floating in air where one wire can touch the next.
- The gauge sits as the **top-left arm**.

```
              THE BRIDGE (drawn flat)

     3V3 ──┬───────────────┬
           │               │
        BF350            R2          <- top-left arm = the gauge
         (E+)             │              (the only arm that changes)
           │               │
           ├──── A+        ├──── A−     <- the two midpoints the HX711 reads
           │               │
          R3              R4
           │               │
     GND ──┴───────────────┴
           (E−)

     E+  = 3V3 rail of the bridge        A+ = midpoint, gauge side
     E−  = GND rail of the bridge        A− = midpoint, R2/R4 side
```

### 2.3 The connections — 15 lines, repeated identically on N2 and N4

| # | From | To | Wire | Colour | Termination |
|---|---|---|---|---|---|
| 1 | ESP32 `3V3` | HX711 `VCC` **and** `AVCC` (jumper them together) | 22 AWG | red | **SOLDER** |
| 2 | ESP32 `GND` | HX711 `GND` | 22 AWG | black | **SOLDER** |
| 3 | ESP32 `GPIO18` | HX711 `SCK` | 22 AWG | yellow | **SOLDER** |
| 4 | ESP32 `GPIO19` | HX711 `DT` | 22 AWG | orange | **SOLDER** |
| 5 | HX711 `E+` | bridge node: gauge top **and** R2 top | 22 AWG | red | **SOLDER** |
| 6 | HX711 `E−` | bridge node: R3 bottom **and** R4 bottom | 22 AWG | black | **SOLDER** |
| 7 | HX711 `A+` | bridge midpoint: gauge bottom **and** R3 top | 22 AWG | green | **SOLDER** |
| 8 | HX711 `A−` | bridge midpoint: R2 bottom **and** R4 top | 22 AWG | white | **SOLDER** |
| 9 | BF350 leads | into the bridge as the top-left arm | gauge leads | — | **SOLDER to the supplied leads only — never to the gauge itself** |
| 10 | BF350 body | rigid strip / concrete chip **spanning the seam** | — | — | **BOND: thin cyanoacrylate or 2-part epoxy** |
| 11 | ESP32 `GPIO33` | macro pot **wiper** | 22 AWG | blue | **SOLDER** |
| 12 | ESP32 `3V3` | macro pot **end A** | 22 AWG | red | **SOLDER** |
| 13 | ESP32 `GND` | macro pot **end B** | 22 AWG | black | **SOLDER** |
| 14 | macro pot body | fixed to **one** seam half | — | — | **BOND (glue dot)** |
| 15 | macro pot shaft | small rigid finger on the **moving** seam half | — | — | **BOND / mechanical** |

**Line 1 is a safety decision, not a preference.** The HX711's `DOUT` swings to whatever its supply is. At 5 V, `GPIO19` would see 5 V logic into a pin that is **not 5 V tolerant**. Running the HX711 at **3.3 V on both VCC and AVCC** keeps every logic level safe with no divider and no level shifter. The cost is half the excitation voltage — irrelevant, because the HX711 has 24 bits and you subtract the baseline in firmware anyway.

### 2.4 The bond — the one joint whose *quality* is the measurement

| Step | Detail |
|---|---|
| Surface prep | light sand with 240 grit, wipe with isopropyl, **let it dry fully** |
| Glue | a **thin** layer of cyanoacrylate or 2-part epoxy |
| Placement | gauge **centred on the crack line**, leads dressed so they cannot pull on it |
| Cure | cyanoacrylate: minutes. Epoxy: the **full** cure time on the pack — do not test early |
| Baseline | zero the baseline in firmware **only after** full cure |

> **Never hot glue. Never foam.** A thick bond is a compliant layer that absorbs the strain you are trying to measure — it can halve your sensitivity. Foam strain also exceeds the gauge's 2% limit, and its creep reads exactly like a slowly cracking seam: the worst possible false positive.

### 2.5 After you finish — Station 2 checks

| Check | How | Expected |
|---|---|---|
| Bridge is not shorted | DMM across `E+`/`E−`, then `A+`/`A−` | open, no dead short, before power |
| HX711 alive | read raw counts over serial | stable numbers, not `0` and not railed |
| **Warm-up honoured** | watch the first 30 s of readings | first ~15 s drift, then settle. Firmware ignores the first 15 s and reports `micro = 0` with the settling flag set |
| Gauge responds | press **very gently** on the strip next to the gauge | counts move, then return |
| Macro pot responds | move the seam finger through its travel | 0 → full-scale mm, clamped at 0 on backlash |
| Open-gauge behaviour | unplug one gauge lead | `flags bit 0` sets — an open gauge must read as **fault**, never as *zero strain* |

### 2.6 Pitfalls

- **A thick glue layer is the most common way this sensor is wasted.** Thin.
- The gauge's supplied leads are solderable; **the gauge itself is not**. Never re-solder to the body.
- Do **not** power the HX711 at 5 V "just to test". That is how `GPIO19` dies.
- `RATE` pin: **leave it low (10 SPS)** for lowest noise, then take a median of 4 readings at 2 Hz.

---

# STATION 3 · Main hub

**Effort 1.0 day (includes the placement work). Only 5 wires leave this board — everything else is wireless.**

### 3.1 Before you start

- The main hub is a **bare ESP32**: no breadboard, no shield.
- It is the only board with two ring ports *and* the USB uplink.

### 3.2 The connections

| # | From (Main Hub) | To | Colour | Length | Termination |
|---|---|---|---|---|---|
| 1 | `GPIO17` (UART2 TX) | ring port A → **L1 `GPIO16`** (via W1) | orange | ~1 m | **SOLDER** both ends |
| 2 | `GPIO16` (UART2 RX) | ring port A ← **L1 `GPIO17`** (via W2) | orange | ~1 m | **SOLDER** both ends |
| 3 | `GPIO4` (UART1 remap TX) | ring port B → **L2 `GPIO13`** (via W6) | teal | ~1 m | **SOLDER** both ends |
| 4 | `GPIO13` (UART1 remap RX) | ring port B ← **L2 `GPIO4`** (via W5) | teal | ~1 m | **SOLDER** both ends |
| 5 | `GND` | common GND bus | black | ~1 m | **SOLDER** |
| 6 | USB port | powered USB hub, **port 1** | — | 1 m | **USB cable** |
| — | *deferred* GPS TX | *`GPIO25`, software serial RX, receive-only* | — | — | *not this build* |

```
        MAIN HUB — the only board with two ring ports + USB

        17 TX ──┐
        16 RX ──┘──► hop 1 ──► L1   (orange pair)
         4 TX ──┐
        13 RX ──┘──► hop 3 ──► L2   (teal pair)
        GND ───────► common GND bus (black, shared by all 7 boards)
        USB ───────► laptop  (JSON telemetry up, commands down)
```

### 3.3 Firmware note that is really a wiring note

`GPIO9`/`GPIO10` are flash pins. The remap costs exactly one line:

```cpp
Serial1.begin(115200, SERIAL_8N1, 13, 4);   // UART1: RX=13, TX=4
```

### 3.4 After you finish — Station 3 checks

| Check | How | Expected |
|---|---|---|
| UART2 alive | USB serial monitor | boots, prints telemetry lines |
| No flash-pin contact | continuity from `GPIO9`/`GPIO10` to anything else | open |
| Antenna clear | 15 mm of clear space around the PCB antenna end | no wires, no foil, no board |
| Board is not hot | touch test after 5 minutes | warm, not hot |

---

# STATION 4 · Level hub — L1 and L2

**Effort 1.5 days each. This is the busiest board: ring, RFID, two LED strips, siren and gates.**

### 4.1 Before you start

- L1 and L2 are **identical hardware**. The only difference is the firmware environment (`lvl1` / `lvl2`) and which junctions they drive.
- **RC522 is 3.3 V only.** Feeding it 5 V is the fastest way to destroy one.

### 4.2 The connections — 21 rows

| # | From (ESP32) | To | Colour | Length | Termination |
|---|---|---|---|---|---|
| 1 | `GPIO17` (UART2 TX) | ring port A → MAIN `GPIO16` (via W2) | orange | to panel edge | **SOLDER** |
| 2 | `GPIO16` (UART2 RX) | ring port A ← MAIN `GPIO17` (via W1) | orange | to panel edge | **SOLDER** |
| 3 | `GPIO4` (UART1 TX) | ring port B → L2 `GPIO13` (via W3) | purple | to panel edge | **SOLDER** |
| 4 | `GPIO13` (UART1 RX) | ring port B ← L2 `GPIO4` (via W4) | purple | to panel edge | **SOLDER** |
| 5 | `3V3` | RC522 `3.3V` | red | 80 mm | **SOLDER — 3V3 only, never 5 V** |
| 6 | `GND` | RC522 `GND` | black | 80 mm | **SOLDER** |
| 7 | `GPIO18` | RC522 `SCK` | yellow | 80 mm | **SOLDER** |
| 8 | `GPIO19` | RC522 `MISO` | orange | 80 mm | **SOLDER** |
| 9 | `GPIO23` | RC522 `MOSI` | green | 80 mm | **SOLDER** |
| 10 | `GPIO5` | RC522 `SDA` / `SS` | white | 80 mm | **SOLDER** |
| 11 | `GPIO27` | RC522 `RST` | grey | 80 mm | **SOLDER** (or tie `RST` → 3V3 and free GPIO27) |
| 12 | — | **100 nF ceramic + 10 µF electrolytic** across RC522 `VCC`/`GND`, **at the module** | — | at module | **SOLDER** |
| 13 | `GPIO21` | **330 Ω** inline → route A strip `DIN` (J1 on L1, J3 on L2) | blue | per layout | **SOLDER**, resistor at the hub end |
| 14 | `GPIO22` | **330 Ω** inline → route B strip `DIN` (J2 on L1, J4 on L2) | blue | per layout | **SOLDER**, resistor at the hub end |
| 15 | `5V` | siren **+** lead | red | 100 mm | **SOLDER** |
| 16 | `GPIO14` | siren **−** / signal lead | yellow | 100 mm | **SOLDER — LOW = sounds** |
| 17 | `GPIO32` | gate LED **green** anode (built-in resistor) | green | 200 mm | **SOLDER** |
| 18 | `GPIO33` | gate LED **red** anode | red | 200 mm | **SOLDER** |
| 19 | both LED cathodes | `GND` | black | 200 mm | **SOLDER** |
| 20 | `GND` | common GND bus | black | to panel | **SOLDER** |
| 21 | USB port | powered USB hub | — | 1 m | **USB cable** |

```
        LEVEL HUB (L1 or L2)
        ┌───────────────────────────────────────────────────────────┐
   USB ─┤ ● 17 TX ┐                                                │
        │ ● 16 RX ┘ UART2 ──► ring port A                           │
        │ ●  4 TX ┐                                                │
        │ ● 13 RX ┘ UART1 ──► ring port B                           │
        │                                                           │
        │ ● 18 SCK  ┐                                               │
        │ ● 19 MISO │ RC522  (SPI @ 3V3 + 100 nF + 10 µF)           │
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

**Rows 13 and 14 are data only.** The strip's **red and black are NOT here** — they come from the LED rail on the service panel (Station 6). Only the **blue data wire** comes from the hub. Getting this wrong is what resets a hub mid-demo when a strip flashes white.

### 4.3 The siren, exactly

| Property | Value |
|---|---|
| Type on hand | 2-pin 5 V buzzer |
| Wiring | one lead to **5 V**, the other to **GPIO14** |
| Logic | **LOW = sounds** (the GPIO sinks the current) |
| Test before you trust it | apply 5 V across the leads. **Continuous tone = active** (toggle the pin HIGH/LOW). **Single click = passive** (drive it with ~2.7 kHz `ledcWriteTone()`). Test, do not assume. |
| Pin choice | GPIO14 — because GPIO12 is the flash-voltage strapping pin and GPIO25 is GPS-reserved |

### 4.4 Why the RC522 decoupling matters

The RC522 browns out when its antenna transmits. Without the **100 nF + 10 µF** right at the module, the classic symptom is *"it reads one tag, then hangs."* The capacitors are two components and five minutes; debugging the failure without them is an evening.

### 4.5 After you finish — Station 4 checks

| Check | How | Expected |
|---|---|---|
| RC522 reads | tap a tag | reads, then reads again — not once |
| RC522 supply | DMM at the module | 3.3 V, never 5 V |
| Strip data is isolated | DMM from `GPIO21` to the strip's 5 V | **no continuity** |
| 330 Ω fitted | DMM across the inline resistor | ~330 Ω |
| Siren | trigger from firmware | sounds on LOW |
| Gate LEDs | toggle each | green and red light independently |
| Hub current | DMM in series with 5 V USB | 90–140 mA idle, peaks during RFID + strip activity |

---

# STATION 5 · The ring backbone — seven wires and the cut switch

**Effort 1.5 days. This is the demo.**

### 5.1 Why a triangle

Three boards, each with exactly **two** hardware UARTs, wired as a full-duplex triangle. **Every board uses both UARTs, once each. Every hop is a matched pair. Nothing is shared and no flash pin is touched.**

| Board | UART2 (17 TX / 16 RX) | UART1 (4 TX / 13 RX) |
|---|---|---|
| **MAIN** | → **L1** (W1, W2) | → **L2** (W5, W6) |
| **L1** | → **MAIN** (W1, W2) | → **L2** (W3, W4) |
| **L2** | → **MAIN** (W5, W6) | → **L1** (W3, W4) |

### 5.2 The seven wires

| Wire | From | To | Colour | Termination |
|---|---|---|---|---|
| **W1** | MAIN `GPIO17` (UART2 TX) | L1 `GPIO16` (UART2 RX) | **orange** | **SOLDER** both ends |
| **W2** | L1 `GPIO17` (UART2 TX) | MAIN `GPIO16` (UART2 RX) | **orange** | **SOLDER** both ends |
| **W3** | L1 `GPIO4` (UART1 TX) | L2 `GPIO13` (UART1 RX) | **purple** | **SOLDER** both ends |
| **W4** | L2 `GPIO4` (UART1 TX) | L1 `GPIO13` (UART1 RX) | **purple** | **SOLDER** both ends — **the cut switch goes here** |
| **W5** | L2 `GPIO17` (UART2 TX) | MAIN `GPIO13` (UART1 RX) | **teal** | **SOLDER** both ends |
| **W6** | MAIN `GPIO4` (UART1 TX) | L2 `GPIO16` (UART2 RX) | **teal** | **SOLDER** both ends |
| **W7** | **common GND** — MAIN + L1 + L2 + all nodes' hub GND | one black bus | **black** | **SOLDER** at a star point |

```
                    THE RING (triangle, full duplex)

                          ┌──────────────┐
                          │   MAIN HUB   │
                          │ UART2 ─ UART1│
                          └───┬──────┬───┘
                     W1,W2    │      │    W5,W6
                    (orange)  │      │    (teal)
                     hop 1    │      │    hop 3
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

**One continuous piece per wire. No mid-run splices.** If you must extend, do it at a hub end, never in the middle of a run.

### 5.3 The cut switch — put it on W4

**Wire the switch into W4 only** — the L2 → L1 transmit line of hop 2.

| Choice | Verdict |
|---|---|
| **W4** | **Correct.** Carries L2's *outgoing* traffic — its telemetry and its RFID swipes. Cutting it means the server loses sight of L2 while L2 keeps working and keeps obeying commands down W3. A genuine, visible degradation with the ring still partly alive. |
| W3 instead | Works electrically, but cuts L2's *inbound* commands — less informative |
| A hop's GND | **Does nothing.** With one shared ground bus, the return current simply takes the other two legs. The demo silently fails and nobody can explain why. |

**Demo line:** *"We cut the spine. L2 keeps receiving alerts on the other leg, but we've lost its telemetry — so the system marks that segment degraded and re-routes the data path. What it doesn't do is pretend everything is fine."*

**Switch mounting:** through the **removable PVC foam service panel**, with a **5 cm service loop** on both sides of the switch and a glue dot anchoring each wire 2 cm from the tab. These get flipped dozens of times in rehearsal.

### 5.4 The four joints that decide whether this build works — joint 4 is here

| Joint | Rule |
|---|---|
| **Cut-switch joints** | service loop both sides, glue-dot anchor 2 cm from each tab, verify continuity in **both** switch positions before the first rehearsal |

### 5.5 After you finish — Station 5 checks

| Check | How | Expected |
|---|---|---|
| Backbone continuity | DMM end to end on each of W1–W6 | < 1 Ω |
| W7 bus continuity | DMM from each board's GND to the star point | < 1 Ω, one bus only |
| No short between legs | DMM between any two signal wires | open |
| Cut switch, both positions | DMM across the switch | closed / open, cleanly |
| Ring alive | run the system, all three boards reporting | full telemetry from both levels |
| Cut the spine | flip the switch | L1 ↔ L2 segment goes red, route recomputes via MAIN, **L2's junction LEDs keep flashing alerts** |

---

# STATION 6 · The four junctions and the LED rail

**Effort 1.0 day for junctions, folded into the power station.**

### 6.1 How a junction is driven

| Junction | Level | Route | Driven by | Strip `DIN` from | LEDs |
|---|---|---|---|---|---|
| **J1** | 1 | A | L1 | L1 **`GPIO21`** via 330 Ω | 8 |
| **J2** | 1 | B | L1 | L1 **`GPIO22`** via 330 Ω | 8 |
| **J3** | 2 | A | L2 | L2 **`GPIO21`** via 330 Ω | 8 |
| **J4** | 2 | B | L2 | L2 **`GPIO22`** via 330 Ω | 8 |

**There is no MCU at a junction.** They are passive WS2812B strips the level hub addresses by segment. That is a deliberate decision — one board per level, and no junction board to power, flash or debug.

### 6.2 The connections — three wires per junction, repeated ×4

| # | From | To | Colour | Termination |
|---|---|---|---|---|
| 1 | level hub `GPIO21` (J1/J3) or `GPIO22` (J2/J4) | through **330 Ω** → strip `DIN` | **blue** | **SOLDER** both ends, resistor inline **at the hub end** |
| 2 | **LED rail 5 V** (not the hub's 5 V pin) | through **1N4148** → strip `5V` | **red** | **SOLDER** both ends |
| 3 | **LED rail GND** | strip `GND` | **black** | **SOLDER** both ends |
| 4 | **1000 µF** electrolytic | across strip `5V` (after the diode) and `GND`, **at the strip** | — | **SOLDER** — observe polarity (stripe = −) |
| 5 | strip | junction mounting | — | foam tape / glue dot |
| 6 | arrow sticker | junction face | — | adhesive |

```
        ONE JUNCTION (repeat ×4)

   hub GPIO21/22 ──[330 Ω]──┐
                            │  blue
   LED rail 5V ──[1N4148]───┼──► strip 5V     red     (+ 1000 µF here)
                            │
   LED rail GND ────────────┴──► strip GND    black
```

Run lengths: J1 ≈ 0.5 m · **J2 trunk ≈ 2.5 m** · J3 ≈ 0.5 m · **J4 trunk ≈ 2.5 m** · plus ≈ 0.5 m slack — **≈ 6.5 m total**, which is why a second 22 AWG kit is on the list.

### 6.3 The two components that make it reliable

| Component | Why |
|---|---|
| **1N4148 in series with the strip's VDD** | the strip expects a data HIGH close to its own VDD. Dropping VDD by ≈0.6 V makes a 3.3 V data signal read as a solid HIGH — a hardware fix that costs one diode and prevents the classic "first LED flickers, rest are fine" |
| **1000 µF across the strip's 5 V/GND** | WS2812B draws an inrush on power-up. Without it, the rail dips and other strips misbehave |

### 6.4 Pattern vocabulary — the firmware side of the wiring

| Pattern | Behaviour | Means |
|---|---|---|
| `flow_green` | green chase, 400 ms | route open — this way |
| `advisory_amber` | amber breathing, 1.5 s | environmental disturbance, no evacuation |
| `hold_amber` | amber solid-fast, 300 ms | **no safe path — do not move** |
| `chase_red` | red chase, 120 ms | route blocked |
| `strobe_red` | red strobe, 100 ms **+ siren** | **evacuate now** |

`NUM_LEDS = 8`. **Brightness capped at ~40 %** — halves the rail current and looks better on camera. **On command loss, hold the last pattern 30 s then default to `hold_amber`.** Never go dark: a dark junction reads as "no hazard" to someone walking past it.

### 6.5 After you finish — Station 6 checks

| Check | How | Expected |
|---|---|---|
| Each strip lights | boot default `hold_amber` on power-up | all four strips amber |
| Diode polarity | DMM across the 1N4148, forward and reverse | conducts one way only |
| Capacitor polarity | visual | stripe on the GND side |
| Strips are not chained | tug each strip's data line | the other three keep working |
| White flash test | drive all four to white briefly | no board resets, no flicker |
| Far-end voltage | DMM at the **end of the J2 trunk** during a white flash | **≥ 4.6 V** |

---

# STATION 7 · Power and ground — the decision that saves the demo

**Effort 1.0 day. No firmware.**

### 7.1 The architecture

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

### 7.2 The current budget and why there are two supplies

| Load | Idle | Peak |
|---|---|---|
| ESP32 board (Wi-Fi on, ESP-NOW 2 Hz) | ~90 mA | **~250 mA** |
| MPU-6050 + SW-420 + pots (per node) | ~5 mA | ~8 mA |
| HX711 (N2, N4) | ~1.5 mA | ~1.5 mA |
| RC522 (per level hub) | ~25 mA | ~100 mA |
| **7 boards subtotal** | **~0.75 A** | **~1.9 A** |
| **one WS2812B strip, 8 LEDs, full white** | — | **480 mA** |
| **4 strips, worst case** | — | **1.92 A** |

The 4 A adapter runs the 7 boards with ≈2× headroom. **The strips get their own 5 V / 2 A source** — because a worst case that browns out the main hub mid-demo is exactly the failure you cannot explain on stage.

### 7.3 The connections

| # | From | To | Colour | Termination |
|---|---|---|---|---|
| 1 | USB hub ports 1–7 | MAIN, N1–N4, L1, L2 | — | **USB cables** |
| 2–5 | LED rail 5 V | each junction branch (through 1N4148) | red | **SOLDER** |
| 6 | LED rail GND | common GND bus (star point) | black | **SOLDER** |
| 7 | USB hub GND | common GND bus (star point) | black | **SOLDER** |
| 8 | common GND bus | every board and strip that has a soldered GND | black | **SOLDER** |
| 9 | J4 branch | **toggle #2** in series, on the service panel | red | **SOLDER with service loops** |
| 10 | cable ends | **no heatshrink** — electrical tape + glue dots | — | **tape** |

### 7.4 Measure the current — you have the meter, use it

You do not need an inline USB meter. Use the DMM in its **10 A DC** range, **in series** with the supply.

**Board rail:**
1. Power everything down; unplug the hub's adapter from the wall.
2. DMM to **10 A DC** — not mA. Boards draw over 1 A and a mA range will blow its fuse.
3. Break the circuit: adapter `+` → red probe; black probe → the lead feeding the hub.
4. Power up, let it settle **60 s** so ESP-NOW and the RC522 are active.
5. Record: idle · all four nodes transmitting · during a full-route LED pattern · during a siren.
6. **Expected 0.75–1.2 A typical, ~1.9 A if all four nodes transmit at once.** On a 4 A adapter that is real headroom — say the number out loud in the viva.

**LED rail:**
1. Repeat with the LED charger in the break.
2. Record: all dark (~5 mA) · one strip `chase_red` (~60 mA) · **all four at white** (~0.8 A at the 40 % cap).
3. Confirm the rail's 5 V at the **far end of the J2 trunk** stays **≥ 4.6 V** during a white flash. Below that, shorten or split the branch.

> **Do not exceed 10 A or leave the meter in circuit more than ~30 s** — the shunt heats. Two 30-second measurements give you a slide-ready number.

### 7.5 After you finish — Station 7 checks

| Check | How | Expected |
|---|---|---|
| One ground only | DMM from each board's GND to the star point | < 1 Ω, and **no** second path |
| 5 V outputs never tied | DMM between the hub's 5 V and the rail's 5 V | **open** |
| Rail voltage under load | DMM at the far end during a white flash | ≥ 4.6 V |
| No board resets during LED patterns | run the worst pattern | nothing reboots |
| Current figures recorded | the procedure above | written down and photographed |

---

# STATION 8 · Diorama integration

**Effort 0.5 day. Do this last — flux spatter and dropped solder destroy paint and foam.**

### 8.1 Mounting rules

| Item | Method | Rule |
|---|---|---|
| Boards → diorama | double-sided foam tape or nylon standoffs | **no drilling through boards** |
| Cables → diorama | glue dots + electrical tape | anchor every **15 cm**; **never across an antenna keep-out** |
| Antenna orientation | PCB antenna end (opposite USB) faces open air | not toward another board or a wire bundle |
| Node spacing | 30–90 cm apart on the surface panel | at this size, interference and detuning matter, not range |
| USB hub brick | ≥ 20 cm from every antenna | switching noise + metal |
| RFID tags | ≥ 10 cm from the RC522 when not being read | passive tags cause ghost reads |
| Service panel | hinged, all switches on it, **labelled** | judges flip a labelled switch and the wiring stays hidden |
| Junction labels | `J1` `J2` `J3` `J4` physically on the diorama | must match `tunnel_map.json` exactly, or the routing demo becomes guesswork |
| Arrow stickers | fitted to the **default exit** direction | the mechanical fallback must be static and always-correct-if-imprecise |

### 8.2 The demo beats to rehearse here

| Beat | Action | Must show |
|---|---|---|
| **Relay heal** | wrap N2's antenna in foil for 10 s | `via relay (N1)` badge appears within 2 s, **data continuity maintained**, returns to direct within 2 s |
| **Cut the spine** | flip the W4 switch | L1 ↔ L2 goes red, route recomputes via MAIN, **L2's junction LEDs keep flashing** |
| **Node death** | flip N3's power switch | `OFFLINE · last-gasp received` |
| **Machinery vs crack** | inject `machinery_pass` then `warning_creep` | amber advisory with no SMS, then structural with countdown |

Practise the foil beat until it is boring. Nothing metallic may sit within 15 mm of N2 or it will not work.

### 8.3 Final photographs

Photograph, from above, with wires labelled: each node · each level hub · the main hub · the bridge network on N2 and N4 · the ring routing · the LED rail and ground star point · the closed service panel. **These photographs are your wiring diagram when something fails at 11 p.m.**

---

# Appendix · Master pin card — tape this to the bench

### NODES (N1–N4) — 10 wires each
```
21 SDA (yellow)   22 SCL (green)     32 disp pot wiper (blue)
34 SW-420 DO (white)                 3V3 + GND to every module (red/black)
N2 + N4 ONLY:  18 HX711 SCK (yellow)  19 HX711 DT (orange)
               33 macro pot wiper (blue)   HX711 VCC+AVCC on 3V3
```

### MAIN HUB — 5 wires leave the board
```
17 TX  16 RX   -> L1   (orange, hop 1)
 4 TX  13 RX   -> L2   (teal, hop 3)
GND            -> common bus
USB            -> laptop
```

### LEVEL HUBS (L1, L2) — identical
```
17/16 -> ring A (orange)   4/13 -> ring B (purple)
RC522: 18 SCK · 19 MISO · 23 MOSI · 5 CS · 27 RST · 3V3 + 100nF + 10µF
Route A strip: GPIO21 --[330R]--> DIN      Route B strip: GPIO22 --[330R]--> DIN
Siren: 5V to + , GPIO14 to -  (LOW = sound)
Gate LEDs: GPIO32 green, GPIO33 red  (cathodes to GND)
```

### RING — 7 wires
```
W1 MAIN17 -> L1-16   (orange)
W2 L1-17  -> MAIN16  (orange)
W3 L1-4   -> L2-13   (purple)
W4 L2-4   -> L1-13   (purple)  <-- CUT SWITCH HERE
W5 L2-17  -> MAIN13  (teal)
W6 MAIN4  -> L2-16   (teal)
W7 GND    -> one black bus, star at the USB hub
```

### JUNCTIONS — 3 wires + 2 components each
```
data: hub GPIO21 (J1/J3) or GPIO22 (J2/J4) --[330R]--> DIN   blue
power: LED RAIL 5V --[1N4148]--> strip 5V                    red   (+1000µF)
gnd:   LED RAIL GND ----------> strip GND                    black
```

### THE FOUR COMMANDMENTS
```
1. NEVER use GPIO 6,7,8,9,10,11  (flash pins)
2. NEVER put 5 V on a GPIO or on the RC522 / HX711 / MPU / SW-420
3. NO pin is 5 V tolerant
4. ONE ground bus, TWO separate 5 V sources, never joined
```

---

*Sequenced from `Neural_Strata_Hardware_Build_Phases.md` (H0–H9, 2,141 lines). Where this walkthrough and the phase document disagree, the phase document — and the A.2 pin freeze within it — is authoritative. Stations map to phases as: 0→H0 · 1→H1 · 2→H2 · 3→H4 · 4→H6 · 5→H5 · 6→H7 · 7→H8 · 8→H9.*
