/*
 * ============================================================================
 * Neural Strata — THE PIN FREEZE.  Source: §A.2.1 of the hardware doc.
 * ============================================================================
 * This file is the one place a GPIO number is written down in this repository.
 * H0's rule 1: "One pin map. The registry in §A.2 is printed, taped above the
 * bench, and every other document is corrected to match it."
 *
 * If a pin moves, it moves here, and the bench photo changes with it. A pin
 * number appearing anywhere else — a sketch, a notebook, a comment — is a bug
 * waiting for the day the two copies disagree.
 *
 * All four nodes are wired identically, **including the two crack pins**. On N1
 * and N3 the HX711 and the macro pot are simply unpopulated; the firmware reads
 * them and transmits 0, so the JSON contract is identical across all four nodes
 * and there is exactly one wiring diagram to build. (H1.2 #2)
 * ============================================================================
 */
#pragma once

#if defined(BOARD_NODE)
/* ═══════════════════════════════════════════════════════════════════════════
 * SENSOR NODE — four boards
 * ═══════════════════════════════════════════════════════════════════════════ */

/* ── Sensor node: MPU-6050 over I²C ────────────────────────────────────────── */
#define PIN_MPU_SDA        21    /* I²C data    · yellow · SOLDER */
#define PIN_MPU_SCL        22    /* I²C clock   · green  · SOLDER */
#define MPU_I2C_ADDR       0x68  /* WHO_AM_I must read 0x68 (H1.7) */

/* ── Sensor node: displacement potentiometer (all nodes) ───────────────────── */
#define PIN_DISP_POT       32    /* ADC1_CH4 · blue */
#define ADC_CH_DISP_POT    4

/* ── Sensor node: SW-420 vibration (all nodes) ─────────────────────────────── */
#define PIN_SW420_DO       34    /* digital in, INPUT ONLY · white */
#define PIN_SW420_AO       35    /* ADC1_CH7, input only — leave open unless diagnosing */

/* ── Sensor node: crack column (N2 and N4 ONLY) ────────────────────────────── */
#define PIN_HX711_DT       19    /* bit-banged input  · orange */
#define PIN_HX711_SCK      18    /* bit-banged output · yellow */
#define PIN_MACRO_POT      33    /* ADC1_CH5 · blue */
#define ADC_CH_MACRO_POT   5

#endif /* BOARD_NODE */

/* ── Onboard status LED (every board class has one, on GPIO 2) ─────────────── */
#define PIN_STATUS_LED      2    /* onboard LED · active HIGH on the DevKit */

/* ── ADC ──────────────────────────────────────────────────────────────────── */
#define ADC_MAX_COUNTS   4095.0f
#define ADC_VREF_V          3.3f

/* ============================================================================
 * Compile-time guards. A wrong pin should fail the build, not the bench.
 * ==========================================================================*/

/* §A.2.0: GPIO 6–11 are the internal SPI flash. Anything here risks a boot
 * failure or a corrupt flash image. The old ring plan's UART1 on 9/10 is
 * exactly the mistake this guard makes impossible (HD-1). */
#define NS_FLASH_PIN(p) ((p) >= 6 && (p) <= 11)

/* ── Shared guard macros ──────────────────────────────────────────────────── */

/* GPIO 34-39 are INPUT ONLY: no output driver, no internal pull-up. Driving one
 * does nothing at all, silently — the peripheral is not connected. */
#define NS_INPUT_ONLY(p) ((p) >= 34 && (p) <= 39)

/* ADC2 is unusable while the radio is on, and ESP-NOW keeps Wi-Fi on for the
 * entire runtime. Every analog read must be an ADC1 pin. */
#define NS_ADC1(p) ((p) >= 32 && (p) <= 39)

#if defined(BOARD_NODE)
#if NS_FLASH_PIN(PIN_MPU_SDA) || NS_FLASH_PIN(PIN_MPU_SCL) || \
    NS_FLASH_PIN(PIN_DISP_POT) || NS_FLASH_PIN(PIN_SW420_DO) || \
    NS_FLASH_PIN(PIN_HX711_DT) || NS_FLASH_PIN(PIN_HX711_SCK) || \
    NS_FLASH_PIN(PIN_MACRO_POT) || NS_FLASH_PIN(PIN_STATUS_LED)
#error "pins.h: a pin on 6-11 is internal SPI flash. Never use. See hardware doc A.2.0."
#endif

/* GPIO 1 and 3 are UART0 — the USB cable. Repurposing either kills the link the
 * whole backend reads. */
#if PIN_MPU_SDA == 1 || PIN_MPU_SCL == 3 || PIN_DISP_POT == 1 || PIN_STATUS_LED == 3
#error "pins.h: GPIO 1 and 3 are UART0 (USB). Reserved. See hardware doc A.2.0."
#endif

/* GPIO 34-39 are INPUT ONLY: no output driver, no internal pull-up. Driving one
 * does nothing at all, silently — the peripheral is not connected. */
#define NS_INPUT_ONLY(p) ((p) >= 34 && (p) <= 39)

#if !NS_INPUT_ONLY(PIN_SW420_DO)
#error "pins.h: SW-420 DO must be an input-only pin (34). It is read, never driven."
#endif

#if NS_INPUT_ONLY(PIN_MPU_SDA) || NS_INPUT_ONLY(PIN_MPU_SCL)
#error "pins.h: I2C needs bidirectional pins. 34-39 have no output driver."
#endif

#if NS_INPUT_ONLY(PIN_HX711_SCK)
#error "pins.h: HX711 SCK is an output. It cannot be on an input-only pin."
#endif

#if NS_INPUT_ONLY(PIN_STATUS_LED)
#error "pins.h: the status LED needs an output driver."
#endif

/* ADC2 is unusable while the radio is on, and ESP-NOW keeps Wi-Fi on for the
 * entire runtime. Every analog read must be an ADC1 pin. */
#define NS_ADC1(p) ((p) >= 32 && (p) <= 39)

#if !NS_ADC1(PIN_DISP_POT)
#error "pins.h: the displacement pot must be on ADC1 (32-39). ADC2 dies with Wi-Fi on."
#endif

#if !NS_ADC1(PIN_MACRO_POT)
#error "pins.h: the macro pot must be on ADC1 (32-39)."
#endif

/* No two signals may share a pin. This catches a copy-paste edit, which is the
 * only way a duplicate realistically appears. */
#if PIN_MPU_SDA == PIN_MPU_SCL
#error "pins.h: SDA and SCL on the same pin."
#endif
#if PIN_HX711_DT == PIN_HX711_SCK
#error "pins.h: HX711 DT and SCK are two wires, not one."
#endif
#endif /* BOARD_NODE */


#if defined(BOARD_LEVEL_HUB)
/* ═══════════════════════════════════════════════════════════════════════════
 * LEVEL HUB — two boards, one per level (Phase 8 · §A.2.3 · §H6.3)
 * ═══════════════════════════════════════════════════════════════════════════
 * Both hubs are wired identically; `-D LEVEL=1|2` selects which junctions this
 * board answers to (J1/J2 + N1/N2, or J3/J4 + N3/N4). That is the whole reason
 * the environment is `env:level_hub` and not `env:lvl1` / `env:lvl2` — one
 * firmware, two boards, one flag to get wrong instead of two files to keep in
 * step (H6.6).
 *
 * Note what is *not* here: no load cell, no MPU, no pot. 18/19 carry RC522 SPI on
 * this board and HX711 on a node — two board classes, two compiles, and the
 * guards below are scoped per board so a shared pin number cannot be mistaken for
 * a shared wire.
 * ------------------------------------------------------------------------- */

/* ── Ring UARTs (§H6.3 rows 1–4) ──────────────────────────────────────────── *
 * Port A → MAIN hub. Port B → the *other* level hub. The pin numbers are the
 * mirror image of the other board's own A port, which is why 4/13 appear on both
 * boards' B ports and are still correct: one board's TX is the other's RX.      */
#define LEVEL_RING_A_TX    17    /* UART2 TX · orange · SOLDER */
#define LEVEL_RING_A_RX    16    /* UART2 RX · orange · SOLDER */
#define LEVEL_RING_B_TX     4    /* UART1 TX · purple · SOLDER */
#define LEVEL_RING_B_RX    13    /* UART1 RX · purple · SOLDER */

/* ── RC522 RFID (§H6.3 rows 5–12) — 3V3 ONLY, never 5 V ───────────────────── */
#define PIN_RC522_SCK      18    /* yellow · SOLDER */
#define PIN_RC522_MISO     19    /* orange · SOLDER */
#define PIN_RC522_MOSI     23    /* green  · SOLDER */
#define PIN_RC522_SDA       5    /* white  · SOLDER — SDA/SS */
#define PIN_RC522_RST      27    /* grey   · SOLDER (or tie to 3V3) */

/* ── Junction strips (§H6.3 rows 13–14) ───────────────────────────────────── *
 * Data only. The strip's red and black go to the LED rail, **not** to the hub's
 * 5 V pin (HD-6): 16 LEDs at full white is ~960 mA, and that is a USB port, not
 * a power supply. The 330 Ω resistors are inline at the hub end.                 */
#define PIN_STRIP_A        21    /* 330 Ω → J1 on L1 / J3 on L2 · blue */
#define PIN_STRIP_B        22    /* 330 Ω → J2 on L1 / J4 on L2 · blue */

/* ── Siren and gate lamps (§H6.3 rows 15–19) ──────────────────────────────── */
#define PIN_SIREN          14    /* **LOW = sounds** (§H6.4). Never GPIO12: the
                                  * flash-voltage strapping pin — a 3-pin module
                                  * holding it high at boot stops the board
                                  * starting (HD-2). GPIO25 is reserved for the
                                  * deferred GPS (HD-9).                        */
#define PIN_GATE_GREEN     32    /* gate LED, green — route open                */
#define PIN_GATE_RED       33    /* gate LED, red — blocked / hold              */

/* ── Guards ───────────────────────────────────────────────────────────────── */

#if NS_FLASH_PIN(PIN_RC522_SCK) || NS_FLASH_PIN(PIN_RC522_MISO) || \
    NS_FLASH_PIN(PIN_RC522_MOSI) || NS_FLASH_PIN(PIN_RC522_SDA) || \
    NS_FLASH_PIN(PIN_RC522_RST) || NS_FLASH_PIN(PIN_STRIP_A) || \
    NS_FLASH_PIN(PIN_STRIP_B) || NS_FLASH_PIN(PIN_SIREN) || \
    NS_FLASH_PIN(PIN_GATE_GREEN) || NS_FLASH_PIN(PIN_GATE_RED) || \
    NS_FLASH_PIN(LEVEL_RING_A_TX) || NS_FLASH_PIN(LEVEL_RING_A_RX) || \
    NS_FLASH_PIN(LEVEL_RING_B_TX) || NS_FLASH_PIN(LEVEL_RING_B_RX)
#error "pins.h: a level-hub pin landed on 6-11 (internal SPI flash). See A.2.0."
#endif

/* GPIO 1 and 3 are UART0 — the USB cable, and the console this board is
 * commissioned through. */
#if LEVEL_RING_A_TX == 1 || LEVEL_RING_A_RX == 3 || \
    LEVEL_RING_B_TX == 1 || LEVEL_RING_B_RX == 3 || PIN_SIREN == 3 || \
    PIN_STRIP_A == 1 || PIN_STRIP_B == 3
#error "pins.h: GPIO 1 and 3 are UART0 (USB). Reserved on every board."
#endif

/* The siren is the one pin with a *stated* reason for its number, so the two
 * historical mistakes are refused here rather than in a code review. */
#if PIN_SIREN == 12
#error "pins.h: GPIO12 is the flash-voltage strapping pin (HD-2). The siren is on 14."
#endif
#if PIN_SIREN == 25
#error "pins.h: GPIO25 is reserved for the deferred GPS (HD-9). The siren is on 14."
#endif

/* Input-only pins cannot drive a strip, a lamp or a siren. */
#if NS_INPUT_ONLY(PIN_STRIP_A) || NS_INPUT_ONLY(PIN_STRIP_B) || \
    NS_INPUT_ONLY(PIN_SIREN) || NS_INPUT_ONLY(PIN_GATE_GREEN) || \
    NS_INPUT_ONLY(PIN_GATE_RED)
#error "pins.h: a level-hub output is on an input-only pin (34-39)."
#endif

/* No two signals on one pin — the L1/L2 set has more pairs to get wrong than the
 * node's, and a swapped transmit/receive is the classic version. */
#if PIN_STRIP_A == PIN_STRIP_B
#error "pins.h: the two junction strips are two data lines, not one."
#endif
#if LEVEL_RING_A_TX == LEVEL_RING_B_TX || LEVEL_RING_A_RX == LEVEL_RING_B_RX
#error "pins.h: the two ring ports must not share a pin."
#endif
#if LEVEL_RING_A_TX == LEVEL_RING_A_RX || LEVEL_RING_B_TX == LEVEL_RING_B_RX
#error "pins.h: a UART's TX and RX are two wires."
#endif
#endif /* BOARD_LEVEL_HUB */

/* ============================================================================
 * MAIN HUB — the ring ports. Phase 9 (B6), §H5.3.
 * ============================================================================
 * Only the ring is here. The SD card (VSPI), the radio master and the modem's
 * UART arrive with `env:main_hub` in Phase 10 / H4, and they are deliberately not
 * stubbed out in advance: a pin defined "for later" is a pin somebody wires.
 *
 * The ring, though, cannot wait for Phase 10, because §H5.5's fault reporting is
 * a *main-hub* behaviour: the board is the far end of two of the three segments,
 * and a segment fault that the middle of the ring cannot report is a fault the
 * dashboard never sees. The numbers are §H5.3's table, verbatim:
 *
 *     W1/W2  UART2  17 TX · 16 RX   MAIN ↔ L1   (hop 1, orange)
 *     W5/W6  UART1   4 TX · 13 RX   MAIN ↔ L2   (hop 3, teal)
 *
 * Note the asymmetry with the level hubs, and that it is correct: MAIN's UART2
 * faces L1 and its UART1 faces L2, while L1's UART2 faces MAIN and its UART1
 * faces L2. Each board spends each of its two UARTs exactly once (§H5.3's table).
 * ========================================================================== */

#if defined(BOARD_MAIN_HUB)

#define MAIN_RING_L1_TX    17    /* UART2 TX · orange · SOLDER — W1 */
#define MAIN_RING_L1_RX    16    /* UART2 RX · orange · SOLDER — W2 */
#define MAIN_RING_L2_TX     4    /* UART1 TX · teal   · SOLDER — W6 */
#define MAIN_RING_L2_RX    13    /* UART1 RX · teal   · SOLDER — W5 */

/* The two pins the whole project reserves on every board. */
#if MAIN_RING_L1_TX == 1 || MAIN_RING_L1_RX == 3 || \
    MAIN_RING_L2_TX == 1 || MAIN_RING_L2_RX == 3
#error "pins.h: GPIO 1 and 3 are UART0 (USB) — the main hub's console. Reserved."
#endif
#if NS_FLASH_PIN(MAIN_RING_L1_TX) || NS_FLASH_PIN(MAIN_RING_L1_RX) || \
    NS_FLASH_PIN(MAIN_RING_L2_TX) || NS_FLASH_PIN(MAIN_RING_L2_RX)
#error "pins.h: a main-hub ring pin landed on 6-11 (internal SPI flash). See A.2.0."
#endif
#if MAIN_RING_L1_TX == MAIN_RING_L2_TX || MAIN_RING_L1_RX == MAIN_RING_L2_RX
#error "pins.h: the two ring legs must not share a pin — MAIN transmits to L1 and L2."
#endif
#if MAIN_RING_L1_TX == MAIN_RING_L1_RX || MAIN_RING_L2_TX == MAIN_RING_L2_RX
#error "pins.h: a UART's TX and RX are two wires."
#endif

/* ── the microSD archivist (Phase 10 · §5.5) ──────────────────────────────────
 *
 * VSPI, and verified free against the §A.2 pin freeze: the RC522 that also wants
 * these four pins lives on **L1/L2, never on MAIN**, so there is no contention
 * anywhere in the system. That is the whole reason the card can be on the same bus
 * a reader would use.
 *
 *      CS    GPIO5    · the only pin that is a *choice* — 5 is a strapping pin and
 *                       is safe here because it is driven after boot, by the SD
 *                       driver, and it was already free in the freeze
 *      SCK   GPIO18   · VSPI CLK
 *      MISO  GPIO19   · VSPI MISO
 *      MOSI  GPIO23   · VSPI MOSI
 *      VCC   3V3      · never 5 V: the breakout has no regulator
 *      GND   star ground
 *
 * §5.5 asks for 100 nF + 10 µF across the breakout. The build has **no capacitors
 * and will not buy any** (user decision, and the same deviation is already
 * recorded for H6.2 row 4). Recorded here so the deviation is visible at the pin
 * table rather than discovered during a Brownout Detector event: the card is
 * mounted at 4 MHz with retries, so a marginal rail shows up as NOSD — a state the
 * gate treats as "refuse every delete" — rather than as silent corruption. */
#define MAIN_SD_CS     5
#define MAIN_SD_SCK   18
#define MAIN_SD_MISO  19
#define MAIN_SD_MOSI  23

#if NS_FLASH_PIN(MAIN_SD_CS) || NS_FLASH_PIN(MAIN_SD_SCK) || \
    NS_FLASH_PIN(MAIN_SD_MISO) || NS_FLASH_PIN(MAIN_SD_MOSI)
#error "pins.h: an SD pin is on the flash bus (GPIO6-11) — the card would fight the boot."
#endif
#if MAIN_SD_CS == MAIN_RING_L1_TX || MAIN_SD_CS == MAIN_RING_L1_RX || \
    MAIN_SD_CS == MAIN_RING_L2_TX || MAIN_SD_CS == MAIN_RING_L2_RX || \
    MAIN_SD_SCK == MAIN_RING_L1_TX || MAIN_SD_SCK == MAIN_RING_L1_RX || \
    MAIN_SD_SCK == MAIN_RING_L2_TX || MAIN_SD_SCK == MAIN_RING_L2_RX || \
    MAIN_SD_MISO == MAIN_RING_L1_TX || MAIN_SD_MISO == MAIN_RING_L1_RX || \
    MAIN_SD_MISO == MAIN_RING_L2_TX || MAIN_SD_MISO == MAIN_RING_L2_RX || \
    MAIN_SD_MOSI == MAIN_RING_L1_TX || MAIN_SD_MOSI == MAIN_RING_L1_RX || \
    MAIN_SD_MOSI == MAIN_RING_L2_TX || MAIN_SD_MOSI == MAIN_RING_L2_RX
#error "pins.h: an SD pin collides with a ring pin — MAIN's ring legs are 17/16 and 4/13."
#endif

#endif /* BOARD_MAIN_HUB */
