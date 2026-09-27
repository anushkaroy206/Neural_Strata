/*
 * ============================================================================
 * Neural Strata — HX711: the fine channel of the crack column (N2/N4).
 * ============================================================================
 * H2.1–H2.4. A YZC-131 1 kg load cell bridges the seam through a pen spring; the
 * cell's own output goes to an HX711, and the seam opening in micrometres is
 * what this file produces.
 *
 *     µ = (counts − zero_counts) × NS_MICRO_UM_PER_COUNT
 *
 * Four decisions are in this file, and each one exists because of a specific way
 * a strain channel lies:
 *
 * 1 · **10 SPS, not 80.** The chip offers both; 80 SPS trades noise for speed
 *     nobody uses, because the frame rate on the wire is 2 Hz. A noisier number
 *     sampled faster is still a noisier number.
 *
 * 2 · **A median, then a mean.** The median (of NS_HX711_MEDIAN_N) removes
 *     *spikes* — a conversion disturbed by the Wi-Fi radio 5 mm away, which a
 *     mean would smear across the next samples of a creep signal. The mean is
 *     used once, for the baseline, where the quantity being removed is white
 *     noise and only the mean is unbiased.
 *
 * 3 · **15 s of warm-up before the zero.** The HX711's output drifts for the
 *     first seconds after power-up. Averaging a baseline inside that drift
 *     contaminates the zero, and a contaminated zero does not look like a
 *     calibration error — it looks like a crack that opens a little and never
 *     closes.
 *
 * 4 · **A rail is a number AND a flag.** A broken wire and an unpowered bridge
 *     both settle at full scale. §3.1: an open gauge must read as *fault*, never
 *     as zero strain; the two are identical in the data otherwise. So the frame
 *     carries NS_MICRO_RAIL_UM (a recognisable number, never a plausible one)
 *     and sets FLAG_MICRO_SATURATED — and if the chip stops answering entirely,
 *     the same flag is set, because a silent bridge is a faulted channel, not an
 *     absent one.
 *
 * Non-blocking by construction: `hx711_tick()` costs nothing unless the chip
 * says a conversion is ready (`DT` low), and one conversion is 26 clock pulses
 * at ~2 µs each. Nothing here waits 100 ms, because the node's `loop()` also
 * owes the radio a heartbeat every 2 s and the sampler a reading every 500 ms.
 *
 * The driver is in-tree rather than `bogde/HX711` (the plan's suggestion, left
 * commented in platformio.ini). The tree of record names `node/hx711.cpp` as the
 * driver; the protocol is 26 pulses and a two's-complement widening; and an
 * in-tree driver keeps the build offline-reproducible and the sign convention
 * (below) visible in the code that a bench session debugs. Swapping the library
 * back in is a 20-line change confined to this file.
 * ============================================================================
 */
#ifdef BOARD_NODE

#include <Arduino.h>

#include "common/config.h"
#include "common/pins.h"

/* ── state ───────────────────────────────────────────────────────────────── */

static bool     s_begun        = false;   /* begin() ran (wiring is set up)    */
static bool     s_live         = false;   /* the chip has answered recently    */
static bool     s_settled      = false;   /* the 15 s window + baseline is done */
static bool     s_saturated    = false;   /* railed, or the chip went silent   */
static bool     s_unloaded     = false;   /* the spring has gone slack         */

static uint32_t s_boot_ms      = 0;
static uint32_t s_last_good_ms = 0;
static uint32_t s_last_read_ms = 0;
static uint32_t s_good_reads   = 0;
static uint32_t s_bad_reads    = 0;

/* The median window: the last NS_HX711_MEDIAN_N *medians* of raw counts. */
static int32_t  s_win[NS_HX711_MEDIAN_N];
static uint8_t  s_win_n = 0;
static uint8_t  s_win_i = 0;

/* The zero. Accumulated as a sum while unsettled, frozen afterwards — never
 * re-averaged. A baseline that keeps moving is a high-pass filter, and it would
 * filter away the creep this channel exists to see. */
static int32_t  s_zero_sum = 0;
static uint8_t  s_zero_n   = 0;
static int32_t  s_zero_counts = 0;
static float    s_opening_um  = 0.0f;
static uint16_t s_unload_run  = 0;

/* ── the wire protocol ───────────────────────────────────────────────────── */

/**
 * Clock out one 24-bit conversion, if the chip has one ready.
 *
 * `DT` goes low when a conversion is available and stays high otherwise, so the
 * first line of this function is also the whole non-blocking strategy: if the
 * chip is not ready, we return immediately and `loop()` carries on.
 *
 * 25 pulses, not 24: the extra pulse selects channel A at gain 128 for the
 * *next* conversion. Omitting it leaves the chip on whatever it was set to
 * (channel B, gain 32), which does not fail — it reads a plausible number that
 * is wrong by a factor of 64, and the bench session that discovers this loses a
 * day. Hence the explicit final pulse and this comment.
 */
static bool hx711_read_raw(int32_t *out)
{
    if (digitalRead(PIN_HX711_DT) != LOW) return false;

    uint32_t v = 0;
    for (uint8_t i = 0; i < 24; i++) {
        digitalWrite(PIN_HX711_SCK, HIGH);
        delayMicroseconds(1);
        v = (v << 1) | (uint32_t)(digitalRead(PIN_HX711_DT) == HIGH ? 1 : 0);
        digitalWrite(PIN_HX711_SCK, LOW);
        delayMicroseconds(1);
    }
    /* Select channel A / gain 128 for the next conversion. */
    digitalWrite(PIN_HX711_SCK, HIGH);
    delayMicroseconds(1);
    digitalWrite(PIN_HX711_SCK, LOW);
    delayMicroseconds(1);

    /* Two's complement over 24 bits: the top bit is the sign. The chip's counts
     * are signed and sit near zero at rest, so sign-extending into 32 bits is
     * the only correct widening — reading the raw 24 bits as unsigned turns a
     * small negative offset into a number 16 million counts high. */
    if (v & 0x800000UL) v |= 0xFF000000UL;
    *out = (int32_t)v;
    return true;
}

/** Median of the window, by insertion sort. `NS_HX711_MEDIAN_N` is 2, and the
 *  sort is here rather than a one-line special case so that raising it to 3 or 5
 *  on a noisy bench does not silently turn "median" into "midrange" — which is
 *  exactly the bug a `min`/`max` shortcut introduces, and it would read as a
 *  channel that rejected spikes while quietly rejecting the signal too. */
static int32_t hx711_window_median(void)
{
    if (s_win_n == 0) return 0;
    int32_t v[NS_HX711_MEDIAN_N];
    const uint8_t n = s_win_n;
    for (uint8_t i = 0; i < n; i++) v[i] = s_win[i];
    for (uint8_t i = 1; i < n; i++) {
        int32_t key = v[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && v[j] > key) { v[j + 1] = v[j]; j--; }
        v[j + 1] = key;
    }
    if (n % 2 == 1) return v[n / 2];
    /* Even count: the plan's "median of 2". The midpoint of the middle pair is
     * the standard definition, and for n = 2 it is the pair's mean. */
    return (int32_t)(((int64_t)v[n / 2 - 1] + (int64_t)v[n / 2]) / 2);
}

/* ── public interface ────────────────────────────────────────────────────── */

/**
 * Prepare the channel. Returns false when the chip never answers, which the
 * caller reports as a fault (bit 0), not as an absent channel: "no crack column"
 * is a property of N1/N3, and a bridge that is wired but silent is a fault.
 */
bool hx711_begin(void)
{
    pinMode(PIN_HX711_SCK, OUTPUT);
    digitalWrite(PIN_HX711_SCK, LOW);
    /* DT is a plain input: the module drives it. No pull-up — the HX711's own
     * output stage holds it, and a pull-up on a 3V3 line the chip drives low
     * wastes current and confuses the ready test. */
    pinMode(PIN_HX711_DT, INPUT);

    s_begun = true;
    s_boot_ms = millis();
    s_last_good_ms = s_boot_ms;

    /* Probe. A live chip pulls DT low within ~100 ms of power-up; if it is not
     * low yet that is normal (the first conversion takes time), so the probe
     * waits briefly rather than declaring the cell dead at t=0. */
    uint32_t t0 = millis();
    while (millis() - t0 < 400) {
        int32_t raw;
        if (hx711_read_raw(&raw)) {
            s_last_good_ms = millis();
            s_good_reads++;
            return true;
        }
        delay(2);
    }
    s_bad_reads++;
    s_live = false;
    return false;
}

/**
 * Advance the channel. Call every `loop()`; it does nothing until the chip says
 * a conversion is ready, and it owns both of the channel's clocks:
 *
 *   · the **conversion** cadence (10 SPS, i.e. one usable reading per call at
 *     most), and
 *   · the **warm-up** — nothing is treated as a measurement until
 *     NS_HX711_SETTLE_MS have passed AND NS_HX711_BASELINE_N readings have been
 *     averaged into the zero.
 */
void hx711_tick(uint32_t now_ms)
{
    if (!s_begun) return;

    /* Silence is a fault, not a rest state. Checked on every pass, because the
     * failure this catches — a cell whose leads came off — produces exactly the
     * frame a perfectly quiet seam would produce. */
    if (s_live && (now_ms - s_last_good_ms) > NS_HX711_DEAD_MS) {
        s_live = false;
        s_saturated = true;
    }

    /* One conversion per NS_HX711_READ_MS at most: the chip's data-ready line
     * already gates this, and the extra gate stops a 10 SPS chip being clocked
     * at loop() speed and producing a mixture of fresh and repeated samples. */
    if (now_ms - s_last_read_ms < NS_HX711_READ_MS) return;

    int32_t raw;
    if (!hx711_read_raw(&raw)) return;

    s_last_read_ms = now_ms;
    s_last_good_ms = now_ms;
    s_good_reads++;
    s_live = true;

    const bool railed = (raw >= NS_HX711_RAIL_COUNTS) || (raw <= -NS_HX711_RAIL_COUNTS);
    if (railed) {
        /* Do not feed a rail into the median or the zero: it is not a reading,
         * it is the absence of one, and letting it through would drag the
         * baseline toward full scale for two seconds after the wire was fixed. */
        s_saturated = true;
        s_bad_reads++;
        return;
    }
    s_saturated = false;

    s_win[s_win_i] = raw;
    s_win_i = (uint8_t)((s_win_i + 1) % NS_HX711_MEDIAN_N);
    if (s_win_n < NS_HX711_MEDIAN_N) s_win_n++;
    int32_t med = hx711_window_median();

    if (!s_settled) {
        const bool warm = (now_ms - s_boot_ms) >= NS_HX711_SETTLE_MS;
        if (warm) {
            s_zero_sum += med;
            s_zero_n++;
            if (s_zero_n >= NS_HX711_BASELINE_N) {
                s_zero_counts = s_zero_sum / (int32_t)s_zero_n;
                s_settled = true;
            }
        }
        /* While unsettled the channel publishes nothing at all — see
         * hx711_read_um(). Reporting 0.0 here would be a claim ("the seam is
         * closed") made from a number that has not been zeroed yet. */
        return;
    }

    /* The opening, in µm. Tension rises with the seam — see the sign convention
     * in config.h; a rising count is a rising gap. */
    const float um = (float)(med - s_zero_counts) * NS_MICRO_UM_PER_COUNT;
    s_opening_um = um;

    /* Slack-spring detection: sustained negative opening. One sample below the
     * line is noise or a bump; NS_MICRO_UNLOAD_N consecutive ones is a spring
     * that is no longer carrying load. */
    if (um <= NS_MICRO_UNLOAD_UM) {
        if (s_unload_run < 0xFFFF) s_unload_run++;
        if (s_unload_run >= NS_MICRO_UNLOAD_N) s_unloaded = true;
    } else {
        s_unload_run = 0;
        s_unloaded = false;
    }
}

/** µm of crack-mouth opening, or NS_MICRO_RAIL_UM when the channel is faulted.
 *  Returns 0.0 while the channel is unsettled — the caller reports the channel
 *  as absent (NAN) in that window, so this value is never published. */
float hx711_read_um(void)
{
    if (s_saturated || !s_live) return NS_MICRO_RAIL_UM;
    return s_opening_um;
}

/** True once the warm-up and the zero are done and the chip is answering. Until
 *  then the frame carries FLAG_BASELINE_UNSETTLED and `micro: null`. */
bool hx711_settled(void) { return s_settled && s_live && !s_saturated; }

/** Railed, or the chip stopped answering. Either way the number is not a
 *  measurement — §3.1: fault, never a calm zero. */
bool hx711_saturated(void) { return s_saturated || !s_live; }

/** The spring is no longer carrying load; the channel has stopped measuring. */
bool hx711_unloaded(void) { return s_unloaded; }

bool     hx711_live(void)     { return s_live; }
int32_t  hx711_zero_counts(void) { return s_zero_counts; }
uint32_t hx711_good_reads(void)  { return s_good_reads; }
uint32_t hx711_bad_reads(void)   { return s_bad_reads; }

#endif  /* BOARD_NODE */
