# Neural Strata — Crack Detection, two-stage (decided 16 Sep 2026)

## Decision
Two channels per crack node (**N2** = Tunnel A collapse face, **N4** = Tunnel B):

| Channel | Sensor | Catches | Output |
|---|---|---|---|
| Micro-crack (initiation) | **BF350-3AA** 350 Ω foil strain gauge → **HX711** (24-bit, half-bridge) | tiny strain at the crack tip, before visible width | `microstrain` (Δ from baseline) |
| Macro-crack (width/progression) | **10 kΩ rotary pot** + finger/lever across seam | mm-scale opening | `crack` (Δ from baseline, calibrated to mm) |

**Demo story:** seam starts to open → `microstrain` rises first → AI flags *"micro-crack initiated"* (early warning) → pushed further → `crack` width jumps → *"crack propagating"*. This is the initiation → progression pipeline for the anomaly/prediction story.

## Buy (total ≈ ₹245 → ₹41/person of 6)
| Item | Qty | ₹ | Search / source |
|---|---|---|---|
| BF350-3AA 350 Ω foil strain gauge | 2 | 50 | Robu.in SKU 975711 (₹23) / Rees52 RS5503 (₹25) / Techtonics TECH2160 (₹24) |
| HX711 24-bit amplifier module | 2 | 140 | "HX711 load cell amplifier module" |
| 350 Ω 1% metal-film resistor | 2 | 5 | any shop |
| 10 kΩ rotary pot | 2 | 50 | same type as the ones already owned |

## Wiring (identical on N2 & N4)
```
3V3 ── E+ ──┬── A+
            │
         [BF350]
            │
GND ── E- ───┴──[350 Ω]──┬── A-        (junction node = A-)
HX711: SCK → GPIO18, DT → GPIO19, VCC 5 V (AVCC jumpered per module)

Macro pot:  3V3 ── pot end A · GND ── pot end B · wiper → GPIO33
```

### Full pin map (N2 / N4)
| Signal | Pin |
|---|---|
| MPU SDA / SCL | 21 / 22 |
| SW-420 DO (from 3V3) | 34 (AO optional → 35) |
| displacement pot (wiper) | 32 |
| HX711 SCK / DT | 18 / 19 |
| macro-crack pot (wiper) | 33 |

## Mounting rules
- **BF350:** bond onto a thin **rigid** strip (2–3 mm wood/PET) or a real brick/concrete chip spanning the seam, gauge centred on the crack line. Light sand + thin cyano/epoxy. **NEVER on foam** — foam strain exceeds the 2 % limit and glue won't hold.
- **Pot:** body fixed to one seam half; small rigid finger on the other half turns the shaft when the seam opens. Set the lever so the demo's full push ≈ full pot sweep; mark the rest position.

## Bench test (do this before any gluing)
1. Wire gauge + 350 Ω to HX711 as above, power, read on serial.
2. Bend the strip with pliers → output must move steadily, one direction.
3. Hold the bend, watch ~15 s → output settles (warm-up check).
4. No movement / noisy → re-check A+/A-/E+/E- wiring and gauge lead solder joints.

## Firmware (folds into W1 node code)
- HX711 @ 2 Hz: `get_median_reading(N)`. **Ignore the first ~15 s** (warm-up drift), then zero = baseline. `microstrain = raw − baseline`.
- Pot @ 2 Hz: `analogRead(33)`, zero after the mount settles. `crack = raw − baseline`; one-time ruler calibration (full sweep = measured seam travel) gives mm.
- Telemetry (all nodes): `{"node","seq","disp","tilt","acc","microstrain","crack"}` @ 2 Hz (micro/crack = 0 on N1/N3).
- Alerts: `microstrain` slope > θ1 → *"micro-crack initiated"* (early, pre-visible); `crack` Δ > θ2 → *"crack propagating, width rising"*.

## Cost / scope notes
- Everything ≤ ₹80 per part — inside the ₹500/each ceiling.
- FSR408 (₹269) = force sensor, **not** a crack sensor (compression ≠ tension) → skipped; optional impact/pressure channel on the roadmap.
- Plug-in "crack sensor module" units (if bought) = bench spares, unused in the diorama.
