/*
 * ============================================================================
 * Neural Strata — the potentiometers, including the macro-crack channel (N2/N4).
 * ============================================================================
 * H1.3 puts a displacement pot on all four nodes; H2 puts a second pot — a
 * 10 kΩ rotary with a lever across the seam — on N2 and N4 only. This file owns
 * the ADC side of both, and it was split out of `sw420.cpp` in Phase 3 because
 * `node/pots.cpp` is the tree of record's home for the crack column's analog
 * half, and because two of the three things here are crack-column-only.
 *
 * The macro channel is the *coarse* half of the two-stage story. That is not a
 * shortcoming to be apologised for — it is the mechanism. A 12-bit ADC over
 * 25 mm resolves 6.1 µm per count and is noisy to ~25 µm; the load cell next to
 * it sees a couple of micrometres. So the pot is what says *how far the seam has
 * already opened* (millimetres, the number that decides whether to pull the crew
 * out), and the cell is what says *it has started* (micrometres, minutes
 * earlier). Collapsing them into one instrument would lose the lead time that
 * justifies the whole column.
 *
 * Rails: at either end of its travel the wiper sits against a stop, so the
 * reading stops tracking the seam. The frame says so (bit 1) rather than
 * reporting a confident 25.0 mm — a rail on this channel looks exactly like the
 * seam opening at full speed if only the number is read.
 * ============================================================================
 */
#ifdef BOARD_NODE

#include <Arduino.h>

#include "common/config.h"
#include "common/pins.h"

/* The median filter is shared with the displacement channel, which lives with
 * the SW-420 in the Phase 1 file. Declared here rather than in a header because
 * the tree of record gives `node/` no headers — the same reason `main.cpp`
 * declares its sensor functions itself. */
float ns_pot_median(uint8_t pin);
float ns_read_pot_mm(uint8_t pin, float mm_min, float mm_max);

/* ── the macro-crack pot, N2/N4 ──────────────────────────────────────────── */

/* (`ns_read_disp_mm()` itself stays in `sw420.cpp` with the Phase 1 node; this
 * file borrows the two shared ADC helpers it declares above.) */

#if NS_HAS_CRACK_COLUMN

/** The last raw ADC count of the macro pot, for the rail test and the bench
 *  log. Kept as counts rather than millimetres because "is it against a stop"
 *  is a question about counts. */
static uint16_t s_macro_counts = 0;

float ns_read_macro_mm(void)
{
    /* Read the counts once and derive both the millimetres and the rail test
     * from the same sample. Reading the pin twice gives two different numbers
     * and two different opinions about whether the channel is railed. */
    float counts = ns_pot_median(PIN_MACRO_POT);
    s_macro_counts = (uint16_t)(counts + 0.5f);
    float frac = counts / ADC_MAX_COUNTS;
    if (frac < 0.0f) frac = 0.0f;
    if (frac > 1.0f) frac = 1.0f;
    return NS_MACRO_MM_MIN + frac * (NS_MACRO_MM_MAX - NS_MACRO_MM_MIN);
}

/** Is the wiper against the **top** stop?
 *
 *  Only the top stop is a rail in the sense that matters. The bottom stop is the
 *  seam-closed rest position — the lever sits there all day on a healthy seam, so
 *  treating it as a fault would make every quiet node report a broken macrosensor.
 *  At the *top* stop the lever has run out of travel: the seam may still be
 *  opening and the channel has stopped following it, which is a channel that is
 *  lying by omission. `NS_MACRO_RAIL_ADC_LO` is kept and printed for the bench,
 *  where "is it sitting on the low stop" is a useful thing to see. */
bool ns_macro_at_rail(void)
{
    return s_macro_counts >= NS_MACRO_RAIL_ADC_HI;
}

uint16_t ns_macro_counts(void) { return s_macro_counts; }

/** True while the wiper is at the **low** stop. Not a fault — it is where a
 *  closed seam rests — but H2.7's pot sweep needs to see it. */
bool ns_macro_at_low_stop(void) { return s_macro_counts <= NS_MACRO_RAIL_ADC_LO; }

#else  /* N1/N3 — wired identically, unpopulated */

float ns_read_macro_mm(void)
{
    /* Read the pin anyway so the two builds differ in this line and nothing
     * else, then report 0: the channel is absent, and `main.cpp` sends NAN for
     * it regardless. */
    (void)analogRead(PIN_MACRO_POT);
    return 0.0f;
}

bool ns_macro_at_rail(void) { return false; }
bool ns_macro_at_low_stop(void) { return false; }
uint16_t ns_macro_counts(void) { return 0; }

#endif  /* NS_HAS_CRACK_COLUMN */

#endif  /* BOARD_NODE */
