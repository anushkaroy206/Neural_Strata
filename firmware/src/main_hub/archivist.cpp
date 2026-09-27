/*
 * ============================================================================
 * Neural Strata — MAIN HUB · archivist.cpp   (Phase 10 · B13 · §5.5)
 *   ★ CORE 0 — queue drain · CSV append · 5 s flush · rotate
 * ============================================================================
 * The other half of the blackbox. Core 1 stamps a row and hands it over; this
 * task is the only thing in the whole system that touches the SD card.
 *
 * ── The rule that removes a race by construction ────────────────────────────
 *
 * **Core 0 never counts.** It cannot know how many rows are in a chunk, and it
 * must not try. It holds one row at a time, looks at that row's `chunk_id`, and if
 * the id is not the one it has open, it rotates the file. The boundary is
 * *discovered from the data* rather than agreed between two cores — which means
 * there is no window in which the two disagree about where chunk 42 ends. Every
 * design that has both cores counting needs a handshake at the boundary; this one
 * needs nothing, and it cannot drift.
 *
 * The same rule decides the CSV: the row that arrives is the row that is written.
 * This task formats bytes, it does not re-derive, round or fuse anything.
 *
 * ── Two clocks, and why the slower one is on the card ───────────────────────
 *
 *   every row     append to the open file (RAM-buffered by the FS layer)
 *   every 5 s     `flush()` — the durability contract in §5.5. A brownout costs at
 *                 most the rows since the last flush, and the surface already has
 *                 all of them, so the *overlap* is what makes the archive
 *                 survivable rather than the archive alone.
 *   every 10 s    remount if the card left, evict old chunks if space is low,
 *                 emit `BLACKBOX:<state>` so the surface's chip tracks reality.
 *
 * ── What happens when the card is gone ──────────────────────────────────────
 *
 * Nothing dramatic, and that is deliberate. Rows keep arriving, the append fails,
 * the failure is counted, and `BLACKBOX:NOSD` goes up the wire. The *surface* is
 * what turns that into "no ACK_DELETE may be sent" — the gate's condition list has
 * no branch for a missing card because the card's absence is expressed by the
 * chunk never being confirmed. The hub's job is to say so, once per 10 s, on the
 * link that is still working.
 * ============================================================================
 */
#ifdef BOARD_MAIN_HUB

#include <Arduino.h>
#include <string.h>

#include "common/config.h"
#include "common/pins.h"
#include "common/power.h"

/* ── Interface ───────────────────────────────────────────────────────────────
 * What Core 1 and main.cpp call. The task itself is started by `ns_arch_begin()`
 * and never returns; everything else here is either a counter or an instruction
 * that is safe to issue from the other core. */
bool     ns_arch_begin(void);
void     ns_arch_report_state(void);
uint32_t ns_arch_rows_written(void);
uint32_t ns_arch_flushes(void);
uint32_t ns_arch_write_fails(void);
uint32_t ns_arch_drops_seen(void);
uint16_t ns_arch_open_chunk(void);
bool     ns_arch_sd_ok(void);
uint32_t ns_arch_blocked(void);

/* From blackbox.cpp — the file layer. Called **only** from this task's context,
 * except where noted. */
bool     ns_bbx_begin(void);
bool     ns_bbx_present(void);
const char *ns_bbx_state(void);
uint32_t ns_bbx_free_mb(void);
uint16_t ns_bbx_chunks_on_card(uint16_t *out, uint16_t max_ids);
bool     ns_bbx_append(uint16_t chunk_id, const char *csv_line);
void     ns_bbx_flush(uint16_t chunk_id);
void     ns_bbx_close_chunk(uint16_t chunk_id, bool report);
void     ns_bbx_remount(void);
void     ns_bbx_fifo_evict(void);
void     ns_bbx_status_line(void);
void     ns_bbx_console(void);
uint32_t ns_bbx_count_rows(uint16_t chunk_id);
uint32_t ns_bbx_drops(uint16_t chunk_id);

/* From protocol_out.cpp — the wire vocabulary. Phase 11 moved every protocol line
 * into that module so there is exactly one file that knows what the protocol
 * looks like; this file decides *when* to say something, not how to spell it. */
void ns_out_chunk_state(uint16_t chunk_id, uint32_t rows, uint32_t drops, bool closed);
void ns_out_chunk_done(uint16_t chunk_id, uint32_t rows, uint32_t drops);
void ns_out_blackbox_state(const char *state, int32_t free_mb);
void ns_out_chunk_close(uint16_t chunk_id, uint32_t rows, uint32_t drops);

/* From timekeeper.cpp — Core 1's counter. Read-only from here. */
QueueHandle_t ns_tk_queue(void);
uint16_t      ns_tk_chunk_id(void);
uint32_t      ns_tk_chunk_rows(void);
uint32_t      ns_tk_row_counter(void);
uint32_t      ns_tk_dropped_rows(void);
bool          ns_tk_row_ok(const ns_bbx_row_t *row);

/* ── Counters ────────────────────────────────────────────────────────────── */

static volatile uint32_t s_rows_written = 0;
static volatile uint32_t s_flushes = 0;
static volatile uint32_t s_write_fails = 0;
static volatile uint32_t s_drops_seen = 0;
static volatile uint16_t s_open_chunk = 0;
static volatile bool     s_sd_ok = false;
static volatile uint32_t s_blocked = 0;      /* rows drained but not written */
static volatile bool     s_blocked_said = false;

bool     ns_arch_sd_ok(void)          { return s_sd_ok; }
uint32_t ns_arch_rows_written(void)   { return s_rows_written; }
uint32_t ns_arch_flushes(void)        { return s_flushes; }
uint32_t ns_arch_write_fails(void)    { return s_write_fails; }
uint32_t ns_arch_drops_seen(void)     { return s_drops_seen; }
uint16_t ns_arch_open_chunk(void)     { return s_open_chunk; }
uint32_t ns_arch_blocked(void)        { return s_blocked; }

/* ── The CSV line ────────────────────────────────────────────────────────── */

/** Format one row exactly as the header promises.
 *
 *  Twelve fields, in the schema's order, with the units the column names carry back
 *  from the struct's quantised integers — and **no re-derivation**: `tilt_cd` is
 *  centi-degrees because the struct stores centi-degrees, and this function's only
 *  judgement is how many decimals to print. §5.5's promise is that a replay is a
 *  straight insert with no translation layer; a formatter that "helpfully" scaled
 *  a value would be that translation layer, and it would be wrong in a way nobody
 *  would see until the crack column drifted on the dashboard. */
static void csv_line(const ns_bbx_row_t *r, char *out, size_t cap)
{
    snprintf(out, cap, "%u,%u,%lu,%u,%u,%.2f,%.1f,%.3f,%u,%u,%.1f,%u",
             (unsigned)r->chunk_id, (unsigned)r->seq, (unsigned long)r->ts_ms,
             (unsigned)r->node, (unsigned)r->level,
             (double)r->tilt_cd / 100.0,
             (double)r->disp_mm10 / 10.0,
             (double)r->accel_mg / 1000.0,
             (unsigned)r->vib,
             (unsigned)r->micro_um,
             (double)r->macro_mm10 / 10.0,
             (unsigned)r->flags);
}

/* ── The task ────────────────────────────────────────────────────────────── */

static void archivist_task(void *arg)
{
    (void)arg;
    uint32_t last_flush = millis();
    uint32_t last_upkeep = last_flush;
    uint32_t last_card_rows = 0;
    char line[128];

    for (;;) {
        /* A 100 ms receive timeout rather than `portMAX_DELAY`: the task has
         * periodic work to do (flush, upkeep) and a queue that is empty for a while
         * — a quiet tunnel at night — must not stop the flush clock. 100 ms is
         * slower than the row rate by 12×, so it costs nothing when rows are
         * flowing. */
        ns_bbx_row_t row;
        if (xQueueReceive(ns_tk_queue(), &row, pdMS_TO_TICKS(100)) == pdTRUE) {
            /* **The brownout gate, fail-closed.** `common/power.h` says it in one
             * line: "every writer asks `if (!ns_power_ingest_allowed()) return;`".
             * A collapsing rail must not be asked to erase a NAND block — that
             * takes milliseconds and turns a survivable reset into a corrupt
             * filesystem. The row is *dropped from the archive*, not lost: Core 1
             * already printed it up the USB cable, and counting it here is what
             * keeps the chunk honest instead of merely incomplete. */
            if (!ns_power_ingest_allowed()) {
                s_blocked++;
                if (!s_blocked_said) {
                    s_blocked_said = true;
                    Serial.println("[archivist] INGEST BLOCKED — brownout this boot; "
                                   "rows are going up the USB link and NOT to the card");
                }
                continue;
            }
            /* Verify the row before it is written: the CRC covers the struct minus
             * itself, so a torn read or a partly-updated sender is caught here
             * rather than discovered by a human reading a CSV. A rejected row is
             * counted, never repaired — we cannot know which field was wrong. */
            if (ns_tk_row_ok(&row)) {
                if (row.chunk_id != s_open_chunk) {
                    /* **The rotation, discovered from the data.** The id on this row
                     * is not the id we have open, so the file changes — and nothing
                     * else has to be true for that to be correct. */
                    if (s_open_chunk) ns_bbx_close_chunk(s_open_chunk, true);
                    s_open_chunk = row.chunk_id;
                    s_sd_ok = ns_bbx_present();
                }
                csv_line(&row, line, sizeof(line));
                if (ns_bbx_append(row.chunk_id, line)) {
                    s_rows_written++;
                } else {
                    s_write_fails++;
                    s_sd_ok = false;
                }
                if (row.flags & NS_BBX_FLAG_HUB_DROP) s_drops_seen++;
            } else {
                s_write_fails++;
            }
        }

        uint32_t now = millis();
        if (s_open_chunk && ns_power_ingest_allowed() && now - last_flush >= NS_BBX_FLUSH_MS) {
            ns_bbx_flush(s_open_chunk);
            s_flushes++;
            last_flush = now;
        }
        if (now - last_upkeep >= NS_BBX_STATUS_MS) {
            last_upkeep = now;
            last_flush = now;
            if (!ns_bbx_present()) ns_bbx_remount();
            s_sd_ok = ns_bbx_present();
            if (s_sd_ok) {
                ns_bbx_fifo_evict();
            }
            ns_bbx_status_line();

            /* One console line per upkeep, and only when the open chunk's row count
             * has moved since last time: this is where a reader sees the timekeeper
             * and the card agree, and a repeating static line would hide the change
             * it exists to show. */
            uint32_t rows = ns_tk_row_counter();
            if (rows != last_card_rows) {
                last_card_rows = rows;
                ns_bbx_console();
            }
        }
    }
}

/* ── Boot ────────────────────────────────────────────────────────────────── */

bool ns_arch_begin(void)
{
    s_sd_ok = ns_bbx_begin();
    s_open_chunk = 0;

    BaseType_t ok = xTaskCreatePinnedToCore(
        archivist_task,      /* the loop above                                   */
        "archivist",         /* name, for `vTaskList` on the bench               */
        4096,                /* stack: a 128-byte line, an SD call, no floats      */
        NULL,                /* no argument                                      */
        1,                   /* priority 1: below the radio, above idle           */
        NULL,
        0);                  /* **Core 0** — Core 1 is the timekeeper and the radio */

    Serial.printf("[archivist] core 0 · %s · rotating on the row's own chunk_id · "
                  "flush %u ms · card %s\n",
                  ok == pdPASS ? "task up" : "TASK CREATE FAILED",
                  (unsigned)NS_BBX_FLUSH_MS, s_sd_ok ? "OK" : "NONE");
    if (!ns_power_ingest_allowed()) {
        Serial.println("[archivist] ingest gate is CLOSED (brownout this boot) — "
                       "the archivist will drain the queue without writing");
    }
    return ok == pdPASS;
}

/* ── Reconciliation support (§5.7, the STATE? answer) ────────────────────── */

/** Print the hub's own view of every chunk it holds.
 *
 *      CHUNK_STATE:<open>,<rows>,<dropped>
 *      CHUNK_DONE:<chunk>,<rows>,<dropped>
 *
 * One STATE line for the chunk being written, one DONE line per chunk on the card.
 * "On the card" is the hub's whole notion of "not yet acknowledged" — an ACK_DELETE
 * unlinks the file, so a chunk that is present is a chunk the surface has not
 * released. That mapping is what lets this be a directory scan instead of a
 * bookkeeping table that can disagree with the card.
 *
 * Counts are read from the files (rows) and from the drop table (drops), never from
 * a running total: the question this answers is "what do you actually hold", and a
 * cached counter answers a different question. */
void ns_arch_report_state(void)
{
    if (!ns_bbx_present()) {
        /* **No card, no chunk state.** This answered `CHUNK_STATE:NOSD,0,0` until
         * Phase 11, and the frozen parser reads that as "three numbers expected" —
         * so an absent card arrived at the surface as an *unknown* line, every
         * reconnect, forever. The card's absence is already said properly by
         * `BLACKBOX:NOSD` (which the ingest parses and the deck renders); the
         * handshake simply has nothing to add. */
        ns_out_blackbox_state("NOSD", -1);
        return;
    }
    ns_out_chunk_state(ns_tk_chunk_id(), ns_tk_row_counter(),
                       ns_bbx_drops(ns_tk_chunk_id()), false);

    uint16_t ids[64];
    uint16_t n = ns_bbx_chunks_on_card(ids, 64);
    for (uint16_t i = 0; i < n; i++) {
        if (ids[i] == s_open_chunk) continue;      /* reported as CHUNK_STATE above */
        ns_out_chunk_done(ids[i], ns_bbx_count_rows(ids[i]), ns_bbx_drops(ids[i]));
    }
}

#endif /* BOARD_MAIN_HUB */
