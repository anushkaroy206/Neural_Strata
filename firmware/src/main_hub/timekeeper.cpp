/*
 * ============================================================================
 * Neural Strata — MAIN HUB · timekeeper.cpp   (Phase 10 · B13 · §5.5)
 *   ★ CORE 1 MASTER TIMEKEEPER — owns `row_counter` and `chunk_id`
 * ============================================================================
 * This file exists because of a decision, not a feature: **one core owns the
 * counter.** Every radio packet that arrives is stamped here, and the *same*
 * stamp goes onto both copies of the row —
 *
 *     outward   the NDJSON line on the USB link, read by the surface;
 *     inward    a 24-byte struct pushed to Core 0's queue, written to the card.
 *
 * so the two copies cannot disagree about which chunk a row belongs to or what
 * its position in that chunk is. The alternative — each core counting for itself —
 * is the classic two-writers bug, and its symptom is worse than a crash: the card
 * and the laptop hold rows with subtly different `seq` values, `UNIQUE(chunk_id,
 * seq)` stops matching, and a replay silently duplicates instead of de-duplicating.
 *
 * ── Why the rollover lives here ─────────────────────────────────────────────
 *
 * Core 1 decides that chunk 42 has become chunk 43. Core 0 *discovers* it, from the
 * data on the row it is holding, and rotates the file when the id changes. Core 0
 * never counts, never predicts, never asks. That inversion is what removes the race
 * by construction: there is no moment at which two cores must agree about a
 * boundary, because only one of them knows where it is.
 *
 * ── The 24-byte row ─────────────────────────────────────────────────────────
 *
 * The card's format is CSV (see blackbox.cpp); this struct is the handoff between
 * cores, and it is deliberately narrow because it travels a FreeRTOS queue that has
 * to hold 256 of them. The three quantisations are finer than the sensors' own
 * resolution — 0.01°, 0.1 mm, 0.001 g — so nothing measurable is lost. The one
 * field that can saturate is `micro` at 65.5 mm of crack opening, and it sets
 * `NS_BBX_FLAG_MICRO_SAT` rather than clipping in silence: a saturated reading is
 * a *fact about the instrument*, and a row that hid it would be a lie on the card.
 *
 *      offset  field       unit          range
 *        0     chunk_id    —             1..9999, rolls
 *        2     seq         —             1..NS_BBX_ROWS_PER_CHUNK
 *        4     ts_ms       ms            hub monotonic, wraps at 49 days
 *        8     node        1..4
 *        9     level       1..2
 *       10     tilt_cd     centi-degrees ±327.67°
 *       12     disp_mm10   tenths of mm  0..6553.5 mm
 *       14     accel_mg    milli-g       0..65.5 g
 *       16     vib         count / 2 s
 *       17     micro_um    µm            0..65535 (saturating, flagged)
 *       19     macro_mm10  tenths of mm  0..6553.5 mm
 *       21     flags       NS_FLAG_* bitfield
 *       22     crc         CRC-16/CCITT over bytes 0..21
 * ============================================================================
 */
#ifdef BOARD_MAIN_HUB

#include <Arduino.h>
#include <math.h>
#include <string.h>

#include "common/config.h"
#include "common/pins.h"

/* ── Flags and the 24-byte row ───────────────────────────────────────────────
 * Both live in `common/config.h`, next to the queue depth they are sized against,
 * because `archivist.cpp` includes the same header and the two cores must agree on
 * every offset. The byte map and the static_asserts are there; this file only uses
 * them. (The low flag bits are shared with the node packet's own flags, so a flag
 * set by the node survives to the card unchanged.) */

/* ── Interface (Core 1 → the rest of the board) ──────────────────────────── */

void         ns_tk_begin(void);
uint16_t     ns_tk_chunk_id(void);
uint32_t     ns_tk_row_counter(void);
uint32_t     ns_tk_dropped_rows(void);
uint32_t     ns_tk_chunks_closed(void);
ns_bbx_row_t ns_tk_stamp(uint8_t node, uint8_t level, uint32_t ts_ms,
                         float tilt, float disp_mm, float accel, uint8_t vib,
                         float micro, float macro, uint8_t flags);
bool         ns_tk_push(const ns_bbx_row_t *row);
bool         ns_tk_row_ok(const ns_bbx_row_t *row);
uint32_t     ns_tk_chunk_rows(void);
void         ns_tk_on_rollover(void (*fn)(uint16_t closed, uint32_t rows, uint32_t drops));

/*: The queue Core 0 drains. Created before the radio can deliver anything, because
 *  a queue created lazily is a queue that does not exist during the first packet. */
QueueHandle_t ns_tk_queue(void);

/* ── State ───────────────────────────────────────────────────────────────── */

static QueueHandle_t s_queue = NULL;
static uint16_t      s_chunk_id = 1;
static uint32_t      s_row_counter = 0;      /* rows in the *open* chunk        */
static uint32_t      s_total_rows = 0;       /* since boot — the console figure */
static uint32_t      s_dropped = 0;          /* queue overflows, since boot     */
static uint32_t      s_chunk_drops = 0;      /* overflows inside the open chunk */
static uint32_t      s_chunks_closed = 0;
static uint32_t      s_last_drop_line_ms = 0;

static void (*s_rollover_fn)(uint16_t, uint32_t, uint32_t) = NULL;

/* ── CRC-16/CCITT (polynomial 0x1021) ────────────────────────────────────── */

/** A checksum on a row that is already on its way to a card that will sit in a
 *  tunnel for a week. The card's own filesystem CRC covers the *sector*, which is
 *  exactly the check that fails to notice a queue-written struct that was built
 *  from a partially-updated sensor reading. Sixteen bits over 22 bytes costs
 *  nothing per row and turns "the numbers look odd" into "this row is rejected". */
static uint16_t bbx_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

/* ── Quantisers ──────────────────────────────────────────────────────────── */

static uint16_t q_u16(float v, float scale, float max_v)
{
    if (!isfinite(v) || v <= 0.0f) return 0;
    float scaled = v * scale + 0.5f;
    if (scaled > max_v) scaled = max_v;
    return (uint16_t)scaled;
}

static int16_t q_i16(float v, float scale)
{
    if (!isfinite(v)) return 0;
    float scaled = v * scale;
    if (scaled > 32767.0f) scaled = 32767.0f;
    if (scaled < -32768.0f) scaled = -32768.0f;
    return (int16_t)lroundf(scaled);
}

/** Does this row still look like the row that was stamped?
 *
 *  The CRC travels with the row, so the *reader* can tell a torn queue element from
 *  a real reading without knowing how it was produced. The archivist drops a row
 *  that fails this and counts it — it never repairs one, because a row whose
 *  checksum failed has no defensible correct value in any field. */
bool ns_tk_row_ok(const ns_bbx_row_t *row)
{
    if (!row) return false;
    if (row->node < 1 || row->node > 4) return false;
    if (row->seq == 0) return false;
    uint16_t want = bbx_crc16((const uint8_t *)row, offsetof(ns_bbx_row_t, crc));
    return want == row->crc;
}

/* ── Boot ────────────────────────────────────────────────────────────────── */

void ns_tk_begin(void)
{
    s_queue = xQueueCreate(NS_BBX_QUEUE_DEPTH, sizeof(ns_bbx_row_t));
    /* A zero-tick send needs a queue that exists; if the allocation failed there is
     * no blackbox at all, and saying so once at boot is worth more than counting
     * 8 drops/s into a counter nobody reads. */
    Serial.printf("[timekeeper] core 1 · queue %u × %u B = %u B · chunk %u rows\n",
                  (unsigned)NS_BBX_QUEUE_DEPTH, (unsigned)NS_BBX_ROW_BYTES,
                  (unsigned)(NS_BBX_QUEUE_DEPTH * NS_BBX_ROW_BYTES),
                  (unsigned)NS_BBX_ROWS_PER_CHUNK);
    if (!s_queue) Serial.println("[timekeeper] QUEUE ALLOC FAILED — blackbox is not recording");
    s_chunk_id = 1;
    s_row_counter = 0;
}

QueueHandle_t ns_tk_queue(void)        { return s_queue; }
uint16_t     ns_tk_chunk_id(void)      { return s_chunk_id; }
uint32_t     ns_tk_row_counter(void)   { return s_row_counter; }
uint32_t     ns_tk_dropped_rows(void)  { return s_dropped; }
uint32_t     ns_tk_chunks_closed(void) { return s_chunks_closed; }
uint32_t     ns_tk_chunk_rows(void)    { return s_row_counter; }

void ns_tk_on_rollover(void (*fn)(uint16_t, uint32_t, uint32_t))
{
    s_rollover_fn = fn;
}

/* ── Stamping ────────────────────────────────────────────────────────────── */

/** Assign this row its `chunk_id` and `seq`, rolling the chunk over if the current
 *  one is full. Returns the row; the caller sends **the same struct** to both
 *  destinations.
 *
 *  Call order is the whole design: stamp, then (a) print the NDJSON line, (b) push
 *  to the queue. If the queue is full the row is still printed — the surface's copy
 *  is the one that reaches the cloud, and a full queue must never cost a row the
 *  laptop could have had. That is `ns_tk_push()`'s contract, and it is why the push
 *  is a separate call rather than something this function does. */
ns_bbx_row_t ns_tk_stamp(uint8_t node, uint8_t level, uint32_t ts_ms,
                         float tilt, float disp_mm, float accel, uint8_t vib,
                         float micro, float macro, uint8_t flags)
{
    ns_bbx_row_t row;
    memset(&row, 0, sizeof(row));

    /* The rollover: seq restarts, the chunk id advances, and the caller is told so
     * the wire hears about exactly the same boundary the card does. */
    if (s_row_counter >= NS_BBX_ROWS_PER_CHUNK) {
        uint16_t closed = s_chunk_id;
        uint32_t rows = s_row_counter, drops = s_chunk_drops;
        s_chunks_closed++;
        s_chunk_id = (uint16_t)(s_chunk_id >= NS_BBX_CHUNK_MAX ? 1 : s_chunk_id + 1);
        s_row_counter = 0;
        s_chunk_drops = 0;
        if (s_rollover_fn) s_rollover_fn(closed, rows, drops);
    }

    s_row_counter++;
    s_total_rows++;

    row.chunk_id = s_chunk_id;
    row.seq      = (uint16_t)s_row_counter;
    row.ts_ms    = ts_ms;
    row.node     = node;
    row.level    = level;
    row.tilt_cd  = q_i16(tilt, 100.0f);
    row.disp_mm10 = q_u16(disp_mm, 10.0f, 65535.0f);
    row.accel_mg  = q_u16(accel, 1000.0f, 65535.0f);
    row.vib       = vib;

    /* micro is the one saturating field. 65535 µm = 65.5 mm, and the sensor's rail
     * sits at 90 mm — so a genuinely rail-wide crack is *flagged* rather than
     * clipped, and no downstream reader has to guess which of the two happened. */
    if (isfinite(micro) && micro > 6553.5f * 10.0f) {
        row.micro_um = 65535;
        row.flags |= NS_BBX_FLAG_MICRO_SAT;
    } else if (isfinite(micro) && micro > 0.0f) {
        row.micro_um = (uint16_t)(micro + 0.5f);
    }
    row.macro_mm10 = q_u16(macro, 10.0f, 65535.0f);
    row.flags |= flags;

    row.crc = bbx_crc16((const uint8_t *)&row, offsetof(ns_bbx_row_t, crc));
    return row;
}

/** Hand the stamped row to Core 0. **Zero ticks of patience, and that is correct.**
 *
 *  Blocking here would stall the radio path — the queue exists to absorb an SD
 *  stall, not to make the ESP-NOW receive callback wait for one. A full queue
 *  therefore drops, counts, and tells the truth about it: the row still went up the
 *  USB link, and the chunk it belonged to is pinned on the card forever because it
 *  has drops (see `blackbox.cpp` and the gate's condition 3). */
bool ns_tk_push(const ns_bbx_row_t *row)
{
    if (!s_queue || !row) return false;
    if (xQueueSend(s_queue, row, 0) == pdTRUE) return true;

    s_dropped++;
    s_chunk_drops++;
    /* `DROP:<chunk>,<count>` — the count is *this chunk's* total, not the size of
     * one burst, because the surface accumulates it into the chunk's ledger and a
     * burst count would make the ledger depend on how the drop was reported.
     * Rate-limited to one line per second: a queue that is full stays full for a
     * while, and 8 lines/s of "we are dropping" would bury the rest of the console
     * exactly when someone is reading it. */
    uint32_t now = millis();
    if (now - s_last_drop_line_ms >= 1000) {
        s_last_drop_line_ms = now;
        Serial.printf("DROP:%04u,%u\n", (unsigned)s_chunk_id, (unsigned)s_chunk_drops);
    }
    return false;
}

#endif /* BOARD_MAIN_HUB */
