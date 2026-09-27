/*
 * ============================================================================
 * Neural Strata — power: the brownout detector, its flag, and the ingest gate.
 * ============================================================================
 * Declared H3 in the tree of record §4.1/§4.2 ("brownout detector: flag + stop
 * ingest"), exercised by the Phase-12 rehearsal's pull-the-plug scenario (H9).
 * The last-gasp send itself lives in `node/main.cpp` — it needs the ESP-NOW peer
 * and the packet, which this header deliberately does not know about. What lives
 * *here* is the state machine every board shares: **did the supply fail, and is
 * it therefore still safe to write?**
 *
 * Two distinct events, and conflating them was the tempting mistake:
 *
 *   LOW SUPPLY   the rail is sagging (below `NS_BATT_LOW_MV`)
 *                → set `NS_FLAG_LOW_BATTERY`, keep working, keep writing.
 *                  Losing the last ten minutes of data because the battery is
 *                  at 3.25 V instead of 3.30 V would be a worse failure than the
 *                  one this detector exists to catch.
 *
 *   BROWNOUT     the rail collapsed.
 *                → set the flag **and stop ingest**. This is the case the tree
 *                  names, and the reason is electrical rather than cautious: an
 *                  SD card mid-write on a collapsing rail is how a FAT partition
 *                  gets corrupted, and a corrupted card loses *everything*, not
 *                  the last ten minutes. The main hub's archivist checks
 *                  `ns_power_ingest_allowed()` before every flush.
 *
 * Sticky by design. A supply that recovered does not un-fail: the operator needs
 * to know it happened, and a flag that cleared itself would make a wobbling
 * supply invisible — the exact class of intermittent fault that ruins a demo.
 * ============================================================================
 */
#pragma once

#include <Arduino.h>
#include <esp_system.h>

#include "common/config.h"
#include "espnow_packet.h"          /* NS_FLAG_LOW_BATTERY, NS_FLAG_LAST_GASP */

/*: Latched power state. `volatile` because the shutdown handler can read it from
 *  the IDF's own context while the loop writes it; plain booleans, so every
 *  access is atomic on the ESP32 and no lock is needed — and none is allowed,
 *  because the last-gasp path runs with milliseconds to live. */
static volatile bool s_power_low = false;
static volatile bool s_power_brownout = false;
static volatile uint16_t s_power_mv = 0;

/**
 * Read the reset reason and latch it. Call once, first thing in `setup()`.
 *
 * `ESP_RST_BROWNOUT` is the detector's own report that it fired — the one event
 * that cannot be inferred from a voltage reading after the fact, because by the
 * time the board is running again the rail is fine. Returns `true` when this boot
 * *is* the recovery from a brownout, so a board can say so on the serial line.
 */
static inline bool ns_power_begin(void)
{
    esp_reset_reason_t why = esp_reset_reason();
    s_power_brownout = (why == ESP_RST_BROWNOUT);
    if (s_power_brownout) {
        s_power_low = true;         /* sticky: it happened, it is still true */
    }
    return s_power_brownout;
}

/**
 * Record one supply measurement in millivolts.
 *
 * **`0` means "not measured", not "0 V".** `NS_BATT_ENABLE 0` is the shipped
 * default and `ns_read_batt_v()` returns 0.0 in that case; treating it as a real
 * reading would latch `LOW_BATTERY` on every board forever and every frame would
 * carry a permanent fault bit — a false alarm that, being permanent, would be
 * ignored within a day. The battery channel has to be switched on for this to
 * have an opinion.
 */
static inline void ns_power_note_supply_mv(uint16_t mv)
{
    if (mv == 0) return;                       /* no instrument, no verdict */
    s_power_mv = mv;
    if (mv < (uint16_t)NS_BATT_LOW_MV) s_power_low = true;
}

/** The supply has failed or is failing. Sticky until the next boot. */
static inline bool ns_power_low(void)      { return s_power_low; }

/** This boot followed a brownout reset. */
static inline bool ns_power_brownout(void) { return s_power_brownout; }

/** Last measured supply, in mV (0 when the channel is off). */
static inline uint16_t ns_power_supply_mv(void) { return s_power_mv; }

/**
 * **The ingest gate.** False once a brownout has been seen this boot.
 *
 * Inverted on purpose, and named for what it permits rather than what it blocks:
 * every writer asks `if (!ns_power_ingest_allowed()) return;` — a fail-closed
 * test. A gate phrased as `ns_power_ok_to_write()` returning `false` on an
 * uninitialised board would be one forgotten call away from writing to a card on
 * a collapsing rail.
 */
static inline bool ns_power_ingest_allowed(void)
{
    return !s_power_brownout;
}

/** The sticky bits a frame should carry, OR-ed in beside the per-frame ones. */
static inline uint16_t ns_power_flags(void)
{
    uint16_t f = 0;
    if (s_power_low)      f |= NS_FLAG_LOW_BATTERY;
    return f;
}

/**
 * Register the board's last-gasp sender (H3.2 #3).
 *
 * Kept here so every board wires it the same way and so the `#if` that can
 * disable it lives in one place. `NS_LAST_GASP_ENABLE 0` silences the call
 * entirely. The handler must not allocate, must not touch I2C and must not wait
 * — the radio has milliseconds left (see `node/main.cpp::ns_on_shutdown`).
 */
static inline void ns_power_register_last_gasp(void (*handler)(void))
{
#if NS_LAST_GASP_ENABLE
    if (handler != nullptr) esp_register_shutdown_handler(handler);
#else
    (void)handler;
#endif
}

/** One line for the serial log, and the fields a health payload would carry. */
static inline void ns_power_report(const char *tag)
{
    Serial.printf("[%s] power: %s · supply %u mV · ingest %s%s\n",
                  tag ? tag : "power",
                  s_power_low ? "LOW/FAILED" : "ok",
                  (unsigned)s_power_mv,
                  ns_power_ingest_allowed() ? "allowed" : "STOPPED",
                  s_power_brownout ? " · brownout reset this boot" : "");
}
