/*
 * ============================================================================
 * Neural Strata — status indication.
 * ============================================================================
 * A sensor node has exactly one light: the onboard LED on GPIO 2. It is not
 * decoration — on a bench with four nodes and no screen, the LED is how you tell
 * "this node is fine", "this node is warming up" and "this node cannot see its
 * sensor" apart without opening a serial console.
 *
 * The gate lamps (the WS2812B strips on the junctions) belong to the *level hub*
 * and arrive with `lib/ws2812b` in Phase 8. What is here that they need is the
 * colour vocabulary and the power cap, so a strip driver and a single LED cannot
 * end up describing the same condition in two different colours.
 * ============================================================================
 */
#pragma once

#include <Arduino.h>

#include "config.h"
#include "pins.h"

/* ── The status vocabulary ────────────────────────────────────────────────── */

typedef enum {
    NS_STATUS_BOOT = 0,   /* starting up                                  */
    NS_STATUS_WARMUP,     /* baseline capture in progress (60 s)          */
    NS_STATUS_NOMINAL,    /* sensing, transmitting, nothing wrong         */
    NS_STATUS_WARNING,    /* link degraded or a channel unsettled         */
    NS_STATUS_CRITICAL,   /* cannot transmit — the node is effectively off */
    NS_STATUS_FAULT       /* a sensor is not answering (WHO_AM_I failed)  */
} ns_status_t;

/* One `ns_rgb_t` for the whole project. `lib/ws2812b/` needs the type too and
 * cannot include this header (it is a `src/` file), so the guard macro is the
 * agreement between them; two identical typedefs in one translation unit is a
 * compile error, which is exactly the reminder this needs. */
#ifndef NS_RGB_T_DEFINED
#define NS_RGB_T_DEFINED
typedef struct {
    uint8_t r, g, b;
} ns_rgb_t;
#endif

/* One colour per status, used by the node's LED and — from Phase 8 — by the
 * junction strips, so an operator reads the same colour in both places. */
static inline ns_rgb_t ns_status_colour(ns_status_t s)
{
    switch (s) {
        case NS_STATUS_BOOT:     return (ns_rgb_t){  0,   0, 255};  /* blue   */
        case NS_STATUS_WARMUP:   return (ns_rgb_t){  0, 120, 120};  /* cyan   */
        case NS_STATUS_NOMINAL:  return (ns_rgb_t){  0, 255,   0};  /* green  */
        case NS_STATUS_WARNING:  return (ns_rgb_t){255, 120,   0};  /* amber  */
        case NS_STATUS_CRITICAL: return (ns_rgb_t){255,   0,   0};  /* red    */
        case NS_STATUS_FAULT:    return (ns_rgb_t){255,   0, 255};  /* magenta*/
        default:                 return (ns_rgb_t){  0,   0,   0};
    }
}

/* ============================================================================
 * The strip power cap — mandatory, and stated here once
 * ============================================================================
 * When the WS2812B driver lands (Phase 8), it MUST call
 *
 *     FastLED.setMaxPowerInVoltsAndMilliamps(5, 300);
 *
 * before the first `show()`. A 40-LED strip on full white draws ~2.4 A; the
 * bench supply is a USB port. Without the cap, the first bright frame browns out
 * the ESP32 and everything resets — including, on the main hub, the SD card
 * mid-write.
 *
 * The cap lives here, next to the colours, because the electrical limit and the
 * visual vocabulary are the same design decision: how much light this system is
 * allowed to make.
 * ==========================================================================*/
#define NS_LED_MA_CAP     300
#define NS_LED_VOLTS        5
#define NS_GATE_LAMP_COUNT  4   /* J1..J4, driven by the level hub in Phase 8 */

/* ── The node's single LED ────────────────────────────────────────────────── */

/**
 * Blink patterns, so one LED carries six states distinguishably.
 *
 * The pattern matters more than the brightness: a *steady* light on a node whose
 * radio has died looks identical to a steady light on a healthy node from across
 * the room, and that is the exact confusion this LED exists to prevent.
 *
 *   BOOT      fast blink (5 Hz)          — "I am starting, wait"
 *   WARMUP    slow blink (1 Hz)          — "60 s, do not trust me yet"
 *   NOMINAL   short pulse every 2 s      — "alive", synchronised to the frames
 *   WARNING   double pulse every 2 s     — "alive, but read the dashboard"
 *   CRITICAL  solid on                  — "I cannot hear the hub"
 *   FAULT     triple pulse, 4 Hz        — "my sensor is not answering"
 */
static inline void ns_led_apply(ns_status_t status, uint32_t now_ms)
{
    pinMode(PIN_STATUS_LED, OUTPUT);
    uint32_t t = now_ms % 2000;
    bool on;

    switch (status) {
        case NS_STATUS_BOOT:     on = (now_ms % 200) < 100; break;
        case NS_STATUS_WARMUP:   on = (now_ms % 1000) < 500; break;
        case NS_STATUS_NOMINAL:  on = t < 60; break;
        case NS_STATUS_WARNING:  on = (t < 60) || (t >= 200 && t < 260); break;
        case NS_STATUS_CRITICAL: on = true; break;
        case NS_STATUS_FAULT:    on = (t < 50) || (t >= 250 && t < 300) ||
                                     (t >= 500 && t < 550); break;
        default:                 on = false; break;
    }
    digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW);
}

/* ── The advisory indication (§5.9) ───────────────────────────────────────── *
 * **Advisory and evacuation must never look the same.** That is the whole rule,
 * and it is an industrial-design rule rather than a cosmetic one: a level that
 * breathes gently is telling an operator "the second opinion disagrees, watch
 * this"; a level that strobes is telling them to leave. If the two shared a
 * pattern, the system would either cry wolf on every nuisance event or — far
 * worse — teach people to ignore the one pattern that means *evacuate*.
 *
 * So the advisory is deliberately unlike everything else in this file:
 *
 *   advisory   SLOW amber breathe, 2.4 s period, never fully dark (floor 12/255)
 *   WARNING    amber double-pulse every 2 s      (sharp, two-edged)
 *   CRITICAL   solid red, or the Phase-8 strobe  (hard on/off, no ramp)
 *
 * A sine-squared envelope rather than a linear ramp, because a linear fade looks
 * like a dimming battery indicator; the squared term makes it read as breathing.
 * The floor is not zero so that "advisory" is distinguishable from "off" across a
 * dark drift at 20 m — a breather that reaches zero is a blinker, and a blinker
 * at 2.4 s is too close to WARMUP's 1 Hz blink to trust at a glance.
 * --------------------------------------------------------------------------*/
#define NS_ADVISORY_PERIOD_MS  2400
#define NS_ADVISORY_FLOOR        12     /* 0..255 — visible, never bright */
#define NS_ADVISORY_PEAK         96     /* ~38% of full: soft by construction */

/** 0..255 brightness for the advisory breathe at time `now_ms`. */
static inline uint8_t ns_advisory_level(uint32_t now_ms)
{
    uint32_t phase = now_ms % NS_ADVISORY_PERIOD_MS;
    /* sin^2 without libm: 1 - cos(2x) over 2, evaluated on a fixed-point ramp
     * shaped by the same parabola, which is accurate enough for a lamp and keeps
     * this header dependency-free for every board that includes it. */
    float x = (float)phase / (float)NS_ADVISORY_PERIOD_MS;      /* 0..1 */
    float tri = (x < 0.5f) ? (x * 2.0f) : (2.0f - x * 2.0f);    /* 0..1..0 */
    float env = tri * tri * (3.0f - 2.0f * tri);                /* smoothstep */
    float v = (float)NS_ADVISORY_FLOOR + env * (float)(NS_ADVISORY_PEAK - NS_ADVISORY_FLOOR);
    return (uint8_t)(v + 0.5f);
}

/** The advisory colour: amber, like WARNING but at a third of the drive. Kept
 *  separate from `ns_status_colour` so that a future edit to the warning palette
 *  cannot accidentally make the two indications identical. */
static inline ns_rgb_t ns_advisory_colour(void)
{
    return (ns_rgb_t){255, 120, 0};
}

/** The frame the node is currently able to send, mapped to a status.
 *  Used by `main.cpp` and reused verbatim by the gate lamps (Phase 8). */
static inline ns_status_t ns_status_from_health(bool sensor_ok, bool link_ok,
                                                bool baseline_done)
{
    if (!sensor_ok)     return NS_STATUS_FAULT;
    if (!link_ok)       return NS_STATUS_CRITICAL;
    if (!baseline_done) return NS_STATUS_WARMUP;
    return NS_STATUS_NOMINAL;
}
