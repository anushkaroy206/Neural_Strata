/*
 * ============================================================================
 * Neural Strata — LEVEL HUB strips (SIMPLIFIED)
 * ============================================================================
 * Simplified strips control with essential functionality only
 */

#include <Arduino.h>
#include <string.h>
#include <math.h>

#include "common/pins.h"
#include "common/leds.h"
#include "common/config.h"
#include "ws2812b.h"
#include "strips.h"
#include "ring.h"
#include <math.h>

const ns_pattern_def_t NS_PATTERNS[] = {
    { {0, 255, 0},  800, false }, /* flow_green     — safe        */
    { {255, 120, 0}, 2400, true  }, /* advisory_amber — CREEP: yellow + buzz */
    { {255, 120, 0}, 1000, true  }, /* hold_amber    — CREEP hold + buzz   */
    { {255, 0, 0},   500, true  }, /* chase_red     — FAILURE: red + buzz   */
    { {255, 0, 0},   300, true  }, /* strobe_red    — FAILURE: red strobe + buzz */
    { {255, 120, 0}, 2400, false }, /* NO_COMMAND                      */
    { {0, 0, 0},     1000, false }, /* unknown                         */
};

/* ── SIMPLIFIED STATE ──────────────────────────────────────────────────── */
static bool s_advisory_on = false;
static void ns_gate_from_alarm(uint32_t now_ms);
static void ns_advisory_beep(uint32_t now_ms);
static void ns_restore_normal(void);
static bool s_siren_on = false;
static bool s_hold = false;
static bool s_have_command = false;
static bool s_boot_silent = true;
static uint32_t s_last_cmd_ms = 0;
static uint32_t s_hold_since = 0;

static ns_junction_t s_junction[2];
static const char* s_junction_name[2] = {(LEVEL == 2) ? "J3" : "J1",
                                         (LEVEL == 2) ? "J4" : "J2"};

/* ── SIMPLIFIED RENDERING ──────────────────────────────────────────────── */
void strips_begin(void)
{
    pinMode(PIN_GATE_GREEN, OUTPUT);
    pinMode(PIN_GATE_RED, OUTPUT);
    pinMode(PIN_SIREN, OUTPUT);

    // Safe default: green/flow, no buzz, red off (danger off until server commands it)
    digitalWrite(PIN_GATE_GREEN, HIGH);
    digitalWrite(PIN_GATE_RED, LOW);
    digitalWrite(PIN_SIREN, LOW);         // active-HIGH: LOW = silent

    // Initialize junctions to boot state (amber breathe)
    for (int i = 0; i < 2; i++) {
        s_junction[i].pattern = NS_PAT_FLOW_GREEN;
        s_junction[i].colour = NS_PATTERNS[NS_PAT_FLOW_GREEN].colour;
        s_junction[i].period_ms = NS_PATTERNS[NS_PAT_FLOW_GREEN].period_ms;
        s_junction[i].buzzer = false;
        s_junction[i].changed_ms = millis();
    }

    ns_ws2812b_begin(PIN_STRIP_A, PIN_STRIP_B);
    /* The driver refuses to show anything until the power cap is set (§H6.2).
     * 16 LEDs at full white is ~960 mA; the hub runs from one USB port. */
    ns_ws2812b_set_max_power(5, 300);
}

void strips_apply(uint32_t now_ms)
{
    // Keep the horn in its commanded state. A creep's three beeps are the
    // only other writer, and they must not use delay() — a blocked loop
    // drops the "back to green" line and the horn stays latched.

    // After 30 seconds without a command, freeze the last displayed image.
    // Brief ring flaps (<30 s) are ignored per system design.
    if (s_have_command && !s_hold && s_last_cmd_ms &&
        (uint32_t)(now_ms - s_last_cmd_ms) > 30000) {
        s_hold = true;
        s_hold_since = now_ms;
        Serial.printf("[level %d hub] Holding last image after ring silence\n", LEVEL);
    }

    for (int i = 0; i < 2; i++) {
        ns_simple_render((ns_strip_t)i, &s_junction[i], now_ms);
    }
    ns_ws2812b_show();

    /* Gate lamps follow the alarm the server just sent, not whichever
     * junction happens to sit in slot 0. A green route on one strip must
     * not leave the green lamp lit through a creep or a failure. */
    ns_gate_from_alarm(now_ms);

    ns_advisory_beep(now_ms);
}

/* Three short beeps, then silence, without blocking the ring receiver. */
static uint32_t s_beep_mark = 0;
static void ns_advisory_beep(uint32_t now_ms)
{
    if (s_siren_on) return;
    if (!s_advisory_on) {
        digitalWrite(PIN_SIREN, LOW);
        return;
    }
    uint32_t elapsed = (uint32_t)(now_ms - s_beep_mark);
    if (elapsed >= 4000) {
        s_beep_mark = now_ms;
        elapsed = 0;
    }
    /* 60 ms on, 60 ms off, three times, then low until the next 4 s. */
    if (elapsed < 360) {
        digitalWrite(PIN_SIREN, ((elapsed / 60) % 2) == 0 ? HIGH : LOW);
    } else {
        digitalWrite(PIN_SIREN, LOW);
    }
}

/* ── COMMAND HANDLERS ──────────────────────────────────────────────────── */
void ns_apply_pattern(int slot, const char* pattern_name,
                      const char* colour_name, long period)
{
    if (slot < 0 || slot >= 2) return;

    int idx = ns_pattern_index_simple(pattern_name);
    if (idx < 0) idx = (int)NS_PAT_UNKNOWN;

    ns_junction_t* j = &s_junction[slot];
    j->pattern = (ns_pattern_t)idx;
    j->colour = ns_colour_from_name(colour_name, NS_PATTERNS[idx].colour);
    j->period_ms = (period > 0) ? (uint16_t)period : NS_PATTERNS[idx].period_ms;
    if (j->period_ms == 0) j->period_ms = 1;
    /* The strip colour does not own the pin. Amber is the three-beep advisory
     * and red is the continuous siren, and the server sends those as their own
     * commands so one green junction cannot silence the other level. */
    j->buzzer = false;
    j->changed_ms = millis();
    /* A green lamp is the all-clear. The horn and the red lamp must not
     * survive it, even if the siren-off line was the one the ring dropped. */
    if (j->pattern == NS_PAT_FLOW_GREEN) ns_restore_normal();

    ns_note_command_simple();
}

bool strips_set_junction_downlink(const char* json)
{
    if (!json) return false;
    char junction[8];
    char pattern[24];
    char colour[12];
    long period = 0;

    if (!ns_ring_json_str(json, "junction", junction, sizeof(junction)))
        return false;

    int slot = ns_slot_for_simple(junction);
    if (slot < 0) return false;

    if (!ns_ring_json_str(json, "pattern", pattern, sizeof(pattern)))
        return false;

    const char* colour_p = ns_ring_json_str(json, "colour", colour, sizeof(colour)) ? colour : NULL;
    long period_ms = ns_ring_json_int(json, "period_ms", &period) ? period : 0;

    ns_apply_pattern(slot, pattern, colour_p, period_ms);
    return true;
}

bool strips_set_level_pattern(const char* json)
{
    if (!json) return false;
    char pattern[24];
    char colour[12];
    long period = 0;

    if (!ns_ring_json_str(json, "pattern", pattern, sizeof(pattern)))
        return false;

    const char* colour_p = ns_ring_json_str(json, "colour", colour, sizeof(colour)) ? colour : NULL;
    long period_ms = ns_ring_json_int(json, "period_ms", &period) ? period : 0;

    for (int slot = 0; slot < 2; slot++) {
        ns_apply_pattern(slot, pattern, colour_p, period_ms);
    }

    Serial.printf("[level %d hub] Pattern %s applied to both junctions\n", LEVEL, pattern);
    return true;
}

/* ── BASIC HELPERS ─────────────────────────────────────────────────────── */
static void ns_restore_normal(void)
{
    s_siren_on = false;
    s_advisory_on = false;
    digitalWrite(PIN_SIREN, LOW);
    digitalWrite(PIN_GATE_GREEN, HIGH);
    digitalWrite(PIN_GATE_RED, LOW);
}

void strips_set_advisory(bool on)
{
    if (on) {
        s_siren_on = false;
        digitalWrite(PIN_SIREN, LOW);
        s_advisory_on = true;
        ns_note_command_simple();
        ns_gate_from_alarm(millis());
        return;
    }
    s_advisory_on = false;
    if (!s_siren_on) ns_restore_normal();
    /* Pins move with the command, not on the next strip frame. */
    ns_gate_from_alarm(millis());
}

void strips_set_siren(bool on)
{
    if (!on) {
        ns_restore_normal();
        return;
    }
    s_advisory_on = false;
    ns_siren_simple_set(true);
    ns_note_command_simple();
    ns_gate_from_alarm(millis());
}

void strips_hold_last(void)
{
    if (s_hold) return;
    s_hold = true;
    s_hold_since = millis();
    Serial.printf("[level %d hub] Ring quiet - holding last image\n", LEVEL);
}

bool strips_holding(void) { return s_hold; }
bool strips_have_command(void) { return s_have_command; }

/* ── SIMPLE RENDERING FUNCTIONS ──────────────────────────────────────── */
void ns_simple_render(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms)
{
    switch (j->pattern) {
        case NS_PAT_FLOW_GREEN:
            ns_render_flow_green(strip, j, now_ms);
            break;
        case NS_PAT_CHASE_RED:
            ns_render_chase_red(strip, j, now_ms);
            break;
        case NS_PAT_STROBE_RED:
            ns_render_strobe_red(strip, j, now_ms);
            break;
        case NS_PAT_HOLD_AMBER:
            ns_render_hold_amber(strip, j, now_ms);
            break;
        case NS_PAT_ADVISORY_AMBER:
        case NS_PAT_NO_COMMAND:
        case NS_PAT_UNKNOWN:
            ns_render_advisory_simple(strip, now_ms);
            break;
        default:
            ns_ws2812b_fill(strip, (ns_rgb_t){0, 0, 0});
            break;
    }
}

void ns_note_command_simple(void)
{
    if (s_boot_silent) {
        s_boot_silent = false;
        Serial.printf("[level %d hub] First command received\n", LEVEL);
    }

    if (!s_have_command) {
        s_have_command = true;
        Serial.printf("[level %d hub] Strips now rendering\n", LEVEL);
    }

    s_last_cmd_ms = millis();
    s_hold = false;
}

void ns_siren_simple_set(bool on)
{
    if (on == s_siren_on) return;
    s_siren_on = on;

#ifdef NS_SIREN_PASSIVE
    if (on) ledcWriteTone(0, 2700);
    else ledcWriteTone(0, 0);
#else
    // Active-HIGH wiring (positive to GPIO 14, negative GND): HIGH=sound, LOW=silent
    digitalWrite(PIN_SIREN, on ? HIGH : LOW);
#endif
}

void ns_gate_from_alarm(uint32_t now_ms)
{
    if (s_siren_on) {
        /* Failure: red blinks, green stays off. The siren pin is already held. */
        digitalWrite(PIN_GATE_GREEN, LOW);
        digitalWrite(PIN_GATE_RED, ((now_ms / 200) & 1) ? HIGH : LOW);
        return;
    }
    if (s_advisory_on) {
        /* Creep: red stays on, green stays off. The three beeps are separate. */
        digitalWrite(PIN_GATE_GREEN, LOW);
        digitalWrite(PIN_GATE_RED, HIGH);
        return;
    }
    /* Readings are good again: green on, red off, and the pin is silent. */
    digitalWrite(PIN_GATE_GREEN, HIGH);
    digitalWrite(PIN_GATE_RED, LOW);
}

void ns_simple_gate_apply(ns_pattern_t p, uint32_t now_ms)
{
    bool green_on = false, red_on = false;

    switch (p) {
        case NS_PAT_FLOW_GREEN:
            green_on = true;
            break;
        case NS_PAT_ADVISORY_AMBER:
        case NS_PAT_HOLD_AMBER:
            red_on = true;                 // CREEP = danger indicator (red LED on)
            green_on = false;               // green LED off during danger
            break;
        case NS_PAT_CHASE_RED:
        case NS_PAT_STROBE_RED:
            red_on = ((now_ms / 200) & 1); // FAILURE: red blinks, green stays off
            break;
        default:
            break;
    }

    digitalWrite(PIN_GATE_GREEN, green_on ? HIGH : LOW);
    digitalWrite(PIN_GATE_RED, red_on ? HIGH : LOW);
}

/* ── PATTERN RENDERING FUNCTIONS ────────────────────────────────────── */
void ns_render_flow_green(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms)
{
    uint16_t period = j->period_ms ? j->period_ms : 1;
    uint8_t head = (uint8_t)(fmod((float)now_ms / period, (float)NS_WS2812B_LEDS));
    ns_ws2812b_fill(strip, (ns_rgb_t){0, 0, 0});
    for (uint8_t i = 0; i < NS_WS2812B_LEDS; i++) {
        uint8_t dist = (uint8_t)((i + NS_WS2812B_LEDS - head) % NS_WS2812B_LEDS);
        uint8_t level = (dist == 0) ? 255 : (dist == 1) ? 96 : 24;
        ns_ws2812b_set(strip, i, (ns_rgb_t){
            (uint8_t)(j->colour.r * level / 255),
            (uint8_t)(j->colour.g * level / 255),
            (uint8_t)(j->colour.b * level / 255)
        });
    }
}

void ns_render_chase_red(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms)
{
    uint16_t period = j->period_ms ? j->period_ms : 1;
    uint8_t head = (uint8_t)(fmod((float)now_ms / period, (float)NS_WS2812B_LEDS));
    ns_ws2812b_fill(strip, (ns_rgb_t){0, 0, 0});
    ns_ws2812b_set(strip, head, j->colour);
    ns_ws2812b_set(strip, (uint8_t)((head + NS_WS2812B_LEDS - 1) % NS_WS2812B_LEDS),
                    (ns_rgb_t){96, 0, 0});
}

void ns_render_strobe_red(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms)
{
    uint16_t period = j->period_ms ? j->period_ms : 100;
    bool on = ((now_ms / period) % 2) == 0;
    ns_ws2812b_fill(strip, on ? j->colour : (ns_rgb_t){0, 0, 0});
}

void ns_render_hold_amber(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms)
{
    bool on = ((now_ms / 167) % 2) == 0;
    ns_ws2812b_fill(strip, on ? j->colour : (ns_rgb_t){0, 0, 0});
}

void ns_render_advisory_simple(ns_strip_t strip, uint32_t now_ms)
{
    uint8_t level = ((now_ms / 1000) % 3);
    ns_rgb_t color = (level == 0) ? (ns_rgb_t){255, 120, 0} :
                     (level == 1) ? (ns_rgb_t){127, 60, 0} :
                                    (ns_rgb_t){0, 0, 0};
    ns_ws2812b_fill(strip, color);
}

/* ── HELPER FUNCTIONS ───────────────────────────────────────────────── */
int ns_slot_for_simple(const char* junction)
{
    if (!junction || junction[0] != 'J' || junction[1] < '1' || junction[1] > '4')
        return -1;
    int target = (LEVEL == 2) ? 2 : 1;  /* L2=slot2(J3/J4), L1=slot1(J1/J2) per pins.h */
    if (((junction[1] == '3' || junction[1] == '4') ? 2 : (junction[1] == '1' || junction[1] == '2') ? 1 : -1) != target) return -1;
    if (junction[1] == s_junction_name[0][1]) return 0;  /* GPIO21 -> J1(L1) or J3(L2) */
    if (junction[1] == s_junction_name[1][1]) return 1;  /* GPIO22 -> J2(L1) or J4(L2) */
    return -1;
}

int ns_pattern_index_simple(const char* name)
{
    if (!name) return -1;

    const char* patterns[] = {"flow_green", "advisory_amber", "hold_amber",
                              "chase_red", "strobe_red", "NO_COMMAND", "unknown"};
    int count = sizeof(patterns) / sizeof(patterns[0]);

    for (int i = 0; i < count; i++) {
        if (!strcmp(name, patterns[i])) return i;
    }
    return -1;
}

ns_rgb_t ns_colour_from_name(const char* name, ns_rgb_t fallback)
{
    if (!name) return fallback;
    if (!strcmp(name, "green")) return (ns_rgb_t){0, 255, 0};
    if (!strcmp(name, "amber")) return (ns_rgb_t){255, 120, 0};
    if (!strcmp(name, "red")) return (ns_rgb_t){255, 0, 0};
    if (!strcmp(name, "white")) return (ns_rgb_t){255, 255, 255};
    if (!strcmp(name, "blue")) return (ns_rgb_t){0, 0, 255};
    return fallback;
}
void strips_tag_beep(void)
{
    /* Visible on the gate lamp. The siren stays quiet: that pin is the
     * evacuation horn, and a card swipe is not an evacuation. */
    for (int i = 0; i < 2; i++) {
        digitalWrite(PIN_GATE_GREEN, LOW);
        delay(40);
        digitalWrite(PIN_GATE_GREEN, HIGH);
        delay(40);
    }
}
