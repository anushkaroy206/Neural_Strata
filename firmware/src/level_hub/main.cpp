/*
 * ============================================================================
 * Neural Strata — LEVEL HUB firmware (SIMPLIFIED)
 * ============================================================================
 * Simplified level hub with essential functionality
 * Focus on reliability and basic operation
 */
#ifdef BOARD_LEVEL_HUB

#include <Arduino.h>
#include <string.h>
#include <stdio.h>

#include "common/pins.h"
#include "ws2812b.h"
#include "strips.h"
#include "ring.h"

// Level set by build flag: -DLEVEL=1 or -DLEVEL=2
#ifndef LEVEL
#define LEVEL 1
#endif

#define NS_STRIP_HZ 30

/* Functions implemented by rc522.cpp. These are .cpp translation units, so
 * Arduino's automatic .ino prototype generation does not apply. */
void rc522_begin(void);
void rc522_poll(uint32_t now_ms);
bool rc522_take_tag(char *uid, size_t cap);
bool rc522_present(void);
uint32_t rc522_reads(void);
uint8_t rc522_version(void);

/* Ring port functions (implemented in ring_port.cpp) */
void ring_port_begin(void);
void ring_port_pump(uint32_t now_ms);
void ring_port_send_uplink(const char *line);
bool ring_port_uplink_up(void);
void ring_port_status_line(void);

/* ── Basic State ──────────────────────────────────────────────────────── */
static uint32_t s_last_strip_ms = 0;
static uint32_t s_last_status_ms = 0;
static uint32_t s_commands = 0;

/* ── COMMAND HANDLER ──────────────────────────────────────────────────── */
bool level_command(const char* json)
{
    if (!json) return false;

    // Check optional "level" constraint: if present and doesn't match this board, reject
    long level = LEVEL;
    if (ns_ring_json_int(json, "level", &level)) {
        if (level != LEVEL) return false;
    }

    char cmd[16] = {0};
    bool has_cmd = ns_ring_json_str(json, "cmd", cmd, sizeof(cmd));

    // If "cmd" key is absent, infer from presence of "junction" or "pattern"
    if (!has_cmd) {
        char junction[8];
        if (ns_ring_json_str(json, "junction", junction, sizeof(junction))) {
            if (!strips_set_junction_downlink(json))
                return false;
            s_commands++;
            return true;
        }
        char pattern[24];
        if (ns_ring_json_str(json, "pattern", pattern, sizeof(pattern))) {
            if (!strips_set_level_pattern(json))
                return false;
            s_commands++;
            return true;
        }
        return false;
    }

    if (!strcmp(cmd, "led")) {
        if (!strips_set_junction_downlink(json))
            return false;
        s_commands++;
        return true;
    }

    if (!strcmp(cmd, "pattern")) {
        if (!strips_set_level_pattern(json))
            return false;
        s_commands++;
        return true;
    }

    if (!strcmp(cmd, "siren")) {
        bool on = false;
        if (!ns_ring_json_bool(json, "on", &on))
            return false;
        strips_set_siren(on);
        s_commands++;
        return true;
    }

    if (!strcmp(cmd, "advisory")) {
        bool on = false;
        if (!ns_ring_json_bool(json, "on", &on))
            return false;
        strips_set_advisory(on);
        s_commands++;
        return true;
    }

    return false;
}

/* ── RING NOTIFICATION ───────────────────────────────────────────────── */
void level_note_ring_alive(uint32_t now_ms)
{
    // Reserved for ring-state/hold policy; no action on ordinary heartbeats.
    (void)now_ms;
}

/* ── SETUP ───────────────────────────────────────────────────────────── */
void setup()
{
    Serial.begin(115200);
    delay(200);

    Serial.printf("[level %d hub] Basic Level Hub Started\n", LEVEL);

    // Establish safe GPIO states before initializing peripheral modules.
    pinMode(PIN_GATE_GREEN, OUTPUT);
    digitalWrite(PIN_GATE_GREEN, HIGH);   // safe default: green

    pinMode(PIN_GATE_RED, OUTPUT);
    digitalWrite(PIN_GATE_RED, LOW);      // danger off until command

    pinMode(PIN_SIREN, OUTPUT);
    digitalWrite(PIN_SIREN, LOW);         // active-HIGH wiring: LOW = silent

    strips_begin();
    rc522_begin();
    ring_port_begin();

    Serial.printf("[level %d hub] Ready - waiting for commands\n", LEVEL);
}

/* ── LOOP ─────────────────────────────────────────────────────────────── */
void loop()
{
    uint32_t now = millis();

    // Ring communication
    ring_port_pump(now);

    // Basic RFID handling: immediately forward a newly read UID to the ring.
    char uid[64];
    if (rc522_take_tag(uid, sizeof(uid))) {
        char frame[128];
        snprintf(frame, sizeof(frame), "{\"v\":2,\"uid\":\"%s\",\"reader\":\"%s\"}",
                 uid, (LEVEL == 2) ? "lvl2" : "lvl1");
        ring_port_send_uplink(frame);
    }
    rc522_poll(now);

    /* The laptop cannot see the level hub's own USB log. This line is how the
     * muster board tells "reader mute" from "nobody has swiped". */
    static uint32_t s_last_rfid_ms = 0;
    if ((uint32_t)(now - s_last_rfid_ms) >= 5000) {
        s_last_rfid_ms = now;
        char status[96];
        snprintf(status, sizeof(status),
                 "{\"v\":2,\"reader\":\"%s\",\"rc522\":%s,\"reads\":%lu,\"ver\":%u}",
                 (LEVEL == 2) ? "lvl2" : "lvl1",
                 rc522_present() ? "true" : "false",
                 (unsigned long)rc522_reads(),
                 (unsigned)rc522_version());
        ring_port_send_uplink(status);
    }

    // Strip rendering (~30 Hz)
    if ((uint32_t)(now - s_last_strip_ms) >= (1000 / NS_STRIP_HZ)) {
        s_last_strip_ms = now;
        strips_apply(now);
    }

    // Keep siren silent (LOW = silent for active-HIGH).
    if (!strips_have_command()) {
        digitalWrite(PIN_SIREN, LOW);
    }

    // Status reporting every 10 seconds
    if ((uint32_t)(now - s_last_status_ms) >= 10000) {
        s_last_status_ms = now;
        ring_port_status_line();
        Serial.printf("[level %d hub] Commands: %lu\n", LEVEL,
                      (unsigned long)s_commands);
    }
}

#endif // BOARD_LEVEL_HUB
