/*
 * ============================================================================
 * Neural Strata — LEVEL HUB strips (simplified)
 * ============================================================================
 * Simplified strips control for Level Hub
 */

#ifndef STRIPS_H
#define STRIPS_H

#ifdef BOARD_LEVEL_HUB

#include "common/leds.h"
#include "ws2812b.h"

/* Structure for junction state */
typedef struct {
    int pattern;
    ns_rgb_t colour;
    uint16_t period_ms;
    bool buzzer;
    uint32_t changed_ms;
} ns_junction_t;

/* Pattern enum and constants (simplified) */
enum { NS_PAT_FLOW_GREEN, NS_PAT_ADVISORY_AMBER, NS_PAT_HOLD_AMBER,
       NS_PAT_CHASE_RED, NS_PAT_STROBE_RED, NS_PAT_NO_COMMAND, NS_PAT_UNKNOWN };

typedef int ns_pattern_t;

/* The default colour and period for each pattern, in enum order. */
typedef struct {
    ns_rgb_t colour;
    uint16_t period_ms;
    bool buzzer;
} ns_pattern_def_t;

extern const ns_pattern_def_t NS_PATTERNS[];

void strips_begin(void);
void strips_apply(uint32_t now_ms);
bool strips_set_junction_downlink(const char* json);
bool strips_set_level_pattern(const char* json);
void strips_set_advisory(bool on);
void strips_set_siren(bool on);
void strips_hold_last(void);
bool strips_holding(void);
bool strips_have_command(void);

/* Simplified rendering functions */
void ns_simple_render(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms);
void ns_simple_gate_apply(ns_pattern_t p, uint32_t now_ms);
void ns_render_flow_green(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms);
void ns_render_chase_red(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms);
void ns_render_strobe_red(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms);
void ns_render_hold_amber(ns_strip_t strip, const ns_junction_t* j, uint32_t now_ms);
void ns_render_advisory_simple(ns_strip_t strip, uint32_t now_ms);
void ns_siren_simple_set(bool on);
void ns_note_command_simple(void);
int ns_slot_for_simple(const char* junction);
int ns_pattern_index_simple(const char* name);
ns_rgb_t ns_colour_from_name(const char* name, ns_rgb_t fallback);
void ns_apply_pattern(int slot, const char* pattern_name,
                      const char* colour_name, long period);

#endif

#endif // STRIPS_H
