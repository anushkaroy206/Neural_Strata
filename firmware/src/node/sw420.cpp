/*
 * ============================================================================
 * Neural Strata — SW-420: vibration EVENT COUNTING, not a level.
 * ============================================================================
 * H1.2 #4: "SW-420 event counting, not analog. The firmware counts digital
 * pulses in a 2 s window and transmits the count (`vib`). This is the field the
 * AI's `vib_count_60` / `impulse_periodicity` features are built from."
 *
 * The distinction matters more than it looks. `analogRead(AO)` gives an
 * instantaneous "is it shaking right now" number, and sampled at 2 Hz it aliases:
 * a 12 Hz machine vibration reads as a random walk. A *count* over a window is
 * an integral, so it is immune to when the sample happened to land.
 *
 * The ISR does no work beyond incrementing a counter. It cannot allocate, it
 * cannot print, and it cannot take a lock — an ISR that does any of those on an
 * ESP32 panic-resets the board.
 * ============================================================================
 */
#include <Arduino.h>

#include "common/config.h"
#include "common/pins.h"

/*: Written from an ISR, read from the loop. `volatile` and 32-bit, which the
 *  ESP32 reads and writes atomically — no lock needed, and no lock is allowed
 *  in the ISR anyway. */
static volatile uint32_t s_pulse_count = 0;
static volatile uint32_t s_last_isr_ms = 0;
static uint32_t s_window_start = 0;
static uint16_t s_window_events = 0;
static uint16_t s_last_window_events = 0;
static uint32_t s_total_events = 0;

/*: Debounce. A mechanical switch bounces for ~1-2 ms; at 2 ms two edges are one
 *  tap. Without this a single tap on the bench counts as four or five events,
 *  and every `vib_count_60` the model has ever seen would be inflated. */
#define NS_SW420_DEBOUNCE_MS 2

static void IRAM_ATTR sw420_isr(void)
{
    uint32_t now = millis();
    if ((now - s_last_isr_ms) < NS_SW420_DEBOUNCE_MS) return;
    s_last_isr_ms = now;
    s_pulse_count++;
}

void sw420_begin(void)
{
    /* GPIO34 is input-only: no pull-up is available, so the module must drive
     * the line in both states (it does — the SW-420 board has an LM393
     * comparator with a pull-up on DO). */
    pinMode(PIN_SW420_DO, INPUT);
    attachInterrupt(digitalPinToInterrupt(PIN_SW420_DO), sw420_isr, RISING);
    s_window_start = millis();
    s_pulse_count = 0;
    s_last_isr_ms = millis();
}

/**
 * Advance the window. Call every loop; it returns immediately until
 * `NS_VIB_WINDOW_MS` has elapsed, at which point the count is frozen into
 * `sw420_last_window()` and a new window opens.
 *
 * The count is measured over the window that just ENDED, not the one in
 * progress: a partially-filled window would report a lower `vib` for no physical
 * reason, and the model would read that as the shaking having stopped.
 */
void sw420_tick(uint32_t now_ms)
{
    if (now_ms - s_window_start < NS_VIB_WINDOW_MS) return;

    noInterrupts();
    s_last_window_events = (uint16_t)(s_pulse_count > 0xFFFF ? 0xFFFF : s_pulse_count);
    s_total_events += s_pulse_count;
    s_pulse_count = 0;
    interrupts();

    s_window_events = s_last_window_events;
    s_window_start = now_ms;
}

uint16_t sw420_last_window(void) { return s_last_window_events; }
uint16_t sw420_current_window(void) { return s_window_events; }
uint32_t sw420_total(void) { return s_total_events; }

/* ── The displacement potentiometer ─────────────────────────────────────────
 * The *displacement* pot is on all four nodes (H1.3) and is part of the Phase 1
 * node, so its read lives here. Phase 3's `pots.cpp` owns the macro-crack pot
 * (N2/N4 only) — but the two ADC helpers below are shared, so they live here
 * with the channel that came first and `pots.cpp` declares them. One median
 * implementation, one conversion, two channels: the alternative was two copies
 * that drift apart on the day someone fixes a bug in one of them.
 * -------------------------------------------------------------------------*/

/** Median of 9 samples. A 12-bit ADC on a 3V3 rail with a Wi-Fi radio 5 mm away
 *  produces occasional wild outliers; a median rejects them without the phase lag
 *  a moving average would add to a creep signal.
 *
 *  Shared with `pots.cpp` (the macro-crack channel), hence no `static`. */
float ns_pot_median(uint8_t pin)
{
    uint16_t v[9];
    for (uint8_t i = 0; i < 9; i++) {
        v[i] = analogRead(pin);
        delayMicroseconds(200);
    }
    for (uint8_t i = 1; i < 9; i++) {              /* insertion sort, n=9 */
        uint16_t key = v[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && v[j] > key) { v[j + 1] = v[j]; j--; }
        v[j + 1] = key;
    }
    return (float)v[4];
}

/** Counts → millimetres for any pot on the board. Clamped high and low: a wiper
 *  past its stop must not report a negative displacement, which the server's
 *  contract rejects as a bad frame and the operator would read as the ground
 *  moving backwards. */
float ns_read_pot_mm(uint8_t pin, float mm_min, float mm_max)
{
    float counts = ns_pot_median(pin);
    float frac = counts / ADC_MAX_COUNTS;
    if (frac < 0.0f) frac = 0.0f;
    if (frac > 1.0f) frac = 1.0f;
    return mm_min + frac * (mm_max - mm_min);
}

float ns_read_disp_mm(void)
{
    return ns_read_pot_mm(PIN_DISP_POT, NS_DISP_MM_MIN, NS_DISP_MM_MAX);
}

/* `ns_read_macro_mm()`, its rail test and its raw counts moved to `pots.cpp` in
 * Phase 3 — the crack column's analog half lives with the crack column. */

#if NS_BATT_ENABLE
float ns_read_batt_v(void)
{
    /* Through the DevKit divider; coarse by design. */
    float counts = ns_pot_median(34);
    return (counts / ADC_MAX_COUNTS) * ADC_VREF_V * 2.0f;
}
#else
float ns_read_batt_v(void) { return 0.0f; }
#endif
