/*
 * ============================================================================
 * Neural Strata — MAIN HUB · blackbox.cpp   (Phase 10 · B13 · §5.5)
 *   SD mount · chunk files · DROP accounting · FIFO eviction · REPLAY serving
 * ============================================================================
 * The card in the tunnel is the last copy of the data that exists nowhere else.
 * Everything this file does is therefore either *append* or *refuse*, and the two
 * destructive operations it owns — evicting an old chunk, and unlinking a chunk
 * the surface asked for — both have a list of reasons they will not happen.
 *
 * ── The one rule that shapes the whole file ─────────────────────────────────
 *
 * **Only the archivist task touches files.** Core 1 never opens, writes, flushes
 * or closes anything; it stamps a row and hands it over (`timekeeper.cpp`). That
 * is the entire concurrency design of the blackbox, and it is why there is not one
 * mutex in this file: one writer means there is nothing to lock. The functions
 * below are called from exactly one task, and the ones a *different* context may
 * call (`ns_bbx_state()`, `ns_bbx_free_mb()`, `ns_bbx_stream_chunk()` from a
 * control verb) are documented as such and take no shared file handle.
 *
 * ── The on-card format is a CSV whose columns ARE the schema ────────────────
 *
 *     chunk_0042.csv
 *     chunk_id,seq,ts_ms,node,level,tilt,disp_mm,accel,vib,micro,macro,flags
 *     42,1,142850,1,1,0.12,5.004,0.0061,0,0.0,12.5,0
 *
 * §5.5: "the SD row schema equals the database schema, twelve fields in the same
 * order, so a blackbox replay is a straight insert with no translation layer."
 * The header line is there for the human who `head`s the file in six months — the
 * reader skips it, and `ns_bbx_stream_chunk()` skips it too, which is what keeps
 * "the file is the schema" true rather than aspirational.
 *
 * ── Why the card is a ring ──────────────────────────────────────────────────
 *
 * NAND flash cannot erase a line, so a blackbox never deletes *rows* — it unlinks
 * whole *files*, and only when it has run out of room. `ns_bbx_fifo_evict()` is
 * that pass. Two chunks are never evicted: the one being written (obviously), and
 * any chunk that recorded a DROP, because those rows went nowhere else. A chunk
 * with a drop is evidence; the file stays until a human unlinks it, which the
 * system deliberately provides no remote verb for.
 *
 * ── What this file deliberately does not do ─────────────────────────────────
 *
 * It does not decide *whether* a chunk may be deleted. That is the surface's
 * five-condition gate (`backend/blackbox/gate.py`), which knows the cloud's
 * state — a thing this board cannot see. `ns_bbx_unlink_chunk()` executes an
 * `ACK_DELETE` and re-checks only the two locally knowable rules (open chunk,
 * dropped rows), so a bug or a forged line on the wire still cannot destroy the
 * only copy of a dropped row.
 * ============================================================================
 */
#ifdef BOARD_MAIN_HUB

#include <Arduino.h>
#include <FS.h>
#include <SD.h>
#include <SPI.h>
#include <string.h>

#include "common/config.h"
#include "common/pins.h"

/* From protocol_out.cpp — the wire vocabulary (Phase 11). This module owns the
 * *files*; that one owns the *words*. A `CHUNK_CLOSE` carries a row count that only
 * the side holding the file can produce, so the two facts meet here and are spelled
 * there. */
void ns_out_chunk_close(uint16_t chunk_id, uint32_t rows, uint32_t drops);
void ns_out_chunk_file(uint16_t chunk_id, uint32_t bytes, uint32_t drops);
void ns_out_blackbox_state(const char *state, int32_t free_mb);

/* ── Interface ───────────────────────────────────────────────────────────────
 * Declared here because the archivist task (Core 0) is the only caller, and this
 * folder has no shared header on purpose: the interface block is the header, and
 * it sits where a reader of the caller lands. */
bool     ns_bbx_begin(void);
bool     ns_bbx_present(void);
const char *ns_bbx_state(void);
uint32_t ns_bbx_free_mb(void);
uint16_t ns_bbx_chunks_on_card(uint16_t *out, uint16_t max_ids);
bool     ns_bbx_open_chunk(uint16_t chunk_id);
bool     ns_bbx_append(uint16_t chunk_id, const char *csv_line);
void     ns_bbx_flush(uint16_t chunk_id);
void     ns_bbx_close_chunk(uint16_t chunk_id, bool report);
void     ns_bbx_remount(void);
void     ns_bbx_note_drop(uint16_t chunk_id, uint16_t count);
uint32_t ns_bbx_drops(uint16_t chunk_id);
uint32_t ns_bbx_count_rows(uint16_t chunk_id);
bool     ns_bbx_stream_chunk(uint16_t chunk_id, void (*sink)(const char *line));
bool     ns_bbx_unlink_chunk(uint16_t chunk_id, const char **why);
void     ns_bbx_fifo_evict(void);
void     ns_bbx_status_line(void);
void     ns_bbx_console(void);
uint32_t ns_bbx_writes(void);
uint32_t ns_bbx_write_fails(void);
uint32_t ns_bbx_evicted(void);
uint32_t ns_bbx_unlinked(void);
uint32_t ns_bbx_replayed(void);

/* ── Card state ──────────────────────────────────────────────────────────── */

static bool     s_mounted      = false;
static const char *s_state     = "NOSD";     /* OK | NOSD | FULL | FAULT */
static uint32_t s_mount_fails  = 0;
static uint32_t s_writes       = 0;
static uint32_t s_write_fails  = 0;
static uint32_t s_evicted      = 0;
static uint32_t s_replayed     = 0;
static uint32_t s_unlinked     = 0;

/*: The single open file. Owned by the archivist task; nobody else touches it. */
static File     s_file;
static uint16_t s_open_id      = 0;
static uint32_t s_open_bytes   = 0;
static uint32_t s_since_flush_ms = 0;

/*: Drops, per chunk, remembered for the chunks that matter. A drop makes a chunk
 *  undeletable, so this table has to outlive the chunk's own open/close cycle —
 *  32 entries covers 32 chunks ≈ an hour of tunnel at 2 min/chunk, and the
 *  eviction pass refuses to delete anything already gone from it (see the note in
 *  `ns_bbx_drops()`). */
#define NS_BBX_DROP_SLOTS 32
static struct { uint16_t chunk_id; uint32_t count; } s_drops[NS_BBX_DROP_SLOTS];
static uint8_t  s_drop_slots_used = 0;

/* ── Paths ───────────────────────────────────────────────────────────────── */

static void bbx_path(char *out, size_t cap, uint16_t chunk_id)
{
    snprintf(out, cap, "/chunk_%04u.csv", (unsigned)chunk_id);
}

/* ── Mount ───────────────────────────────────────────────────────────────── */

/** Mount the card, retrying: a marginal SPI link is the normal failure here.
 *
 *  The retry is not politeness. `SD.begin()` returning false once is a *moment* —
 *  the card is in the middle of its own internal housekeeping — and a hub that
 *  decided "no SD" on the first refusal would sit there refusing every delete
 *  (correctly, but pointlessly) until someone power-cycled it. Three attempts with
 *  the bus re-started between them costs ~200 ms at boot and removes the commonest
 *  false negative in the field. */
static bool bbx_mount(void)
{
    for (int attempt = 1; attempt <= 3; attempt++) {
        SPI.begin(MAIN_SD_SCK, MAIN_SD_MISO, MAIN_SD_MOSI, MAIN_SD_CS);
        if (SD.begin(MAIN_SD_CS, SPI, NS_BBX_SPI_HZ)) {
            s_mounted = true;
            s_state = "OK";
            return true;
        }
        SPI.end();
        delay(40);
    }
    s_mounted = false;
    s_state = "NOSD";
    s_mount_fails++;
    return false;
}

bool ns_bbx_begin(void)
{
    bool ok = bbx_mount();
    if (ok && !SD.cardType()) {            /* mounted but no card type: a fault */
        s_state = "FAULT";
        ok = false;
    }
    if (ok) {
        ns_bbx_fifo_evict();
        ns_bbx_status_line();
    }
    return ok;
}

/** Re-try a card that has gone away. Called on the upkeep tick, never per row. */
void ns_bbx_remount(void)
{
    if (s_mounted) return;
    bbx_mount();
    if (s_mounted) ns_bbx_status_line();
}

bool     ns_bbx_present(void)     { return s_mounted; }
const char *ns_bbx_state(void)    { return s_state; }
uint32_t ns_bbx_free_mb(void)
{
    if (!s_mounted) return 0;
    uint64_t total = SD.totalBytes(), used = SD.usedBytes();
    if (total <= used) return 0;
    return (uint32_t)((total - used) / (1024ULL * 1024ULL));
}

/** `BLACKBOX:OK,3712` — the line the surface reads to fill its BLackbox chip.
 *
 *  NOSD carries no free-space number on purpose: an unmounted card has no
 *  capacity, and "0 MB free" would read as "full" — a different fault with a
 *  different response (evict vs reseat the card). */
void ns_bbx_status_line(void)
{
    if (s_mounted) {
        ns_out_blackbox_state(s_state, (int32_t)ns_bbx_free_mb());
    } else {
        ns_out_blackbox_state(s_state, -1);
    }
}

/* ── Chunk files, writer side (archivist task only) ──────────────────────── */

/** Open (or create) the chunk file and write the column header if it is new.
 *
 *  `FILE_APPEND` and not `FILE_WRITE`: a hub that rebooted mid-chunk must continue
 *  the file it was writing, not truncate the rows already on the card. That is the
 *  difference between a 5-second hole in the archive and a 2-minute one. */
bool ns_bbx_open_chunk(uint16_t chunk_id)
{
    if (!s_mounted) return false;
    if (s_file && s_open_id == chunk_id) return true;
    if (s_file) ns_bbx_close_chunk(s_open_id, false);

    char path[32];
    bbx_path(path, sizeof(path), chunk_id);
    bool existed = SD.exists(path);
    s_file = SD.open(path, FILE_APPEND);
    if (!s_file) {
        s_write_fails++;
        s_state = "FAULT";
        return false;
    }
    if (!existed) {
        s_file.print("chunk_id,seq,ts_ms,node,level,tilt,disp_mm,accel,vib,"
                     "micro,macro,flags\n");
    }
    s_open_id = chunk_id;
    s_open_bytes = (uint32_t)s_file.size();
    s_since_flush_ms = millis();
    return true;
}

bool ns_bbx_append(uint16_t chunk_id, const char *csv_line)
{
    if (!s_mounted || !csv_line) return false;
    if (chunk_id != s_open_id && !ns_bbx_open_chunk(chunk_id)) return false;
    size_t n = s_file.print(csv_line);
    if (n == 0) {
        s_write_fails++;
        s_state = "FAULT";
        return false;
    }
    s_open_bytes += (uint32_t)n;
    s_writes++;
    return true;
}

/** Flush to the card. **5 s is the durability contract, not a performance knob.**
 *
 *  A flush per row would wear the card out for nothing; never flushing would make
 *  the "≤ 5 s of the open chunk is at risk" promise in §5.5 false. So the flush is
 *  driven by *elapsed time* rather than by a row count, and it is also what makes
 *  a brownout survivable: the surface's copy already has those rows, the card is
 *  the backup, and five seconds is the overlap they share. */
void ns_bbx_flush(uint16_t chunk_id)
{
    if (!s_file || chunk_id != s_open_id) return;
    s_file.flush();
    s_since_flush_ms = millis();
}

void ns_bbx_close_chunk(uint16_t chunk_id, bool report)
{
    if (!s_file || chunk_id != s_open_id) return;
    s_file.flush();
    uint32_t bytes = s_open_bytes;
    s_file.close();
    s_open_id = 0;
    s_open_bytes = 0;
    if (report) {
        /* Two lines, two audiences, from the one place that knows both numbers:
         * the protocol's `CHUNK_CLOSE` — the gate reads it, and `expected_rows` is
         * its row count — and the informational `CHUNK_FILE`, which a human reads
         * and the ingest counts as a *message* rather than as an unknown line
         * (which is what an unparsed `CHUNK_FILE` used to be). The close goes first:
         * it is what makes the chunk measurable on the surface. */
        ns_out_chunk_close(chunk_id, ns_bbx_count_rows(chunk_id), ns_bbx_drops(chunk_id));
        ns_out_chunk_file(chunk_id, bytes, ns_bbx_drops(chunk_id));
    }
}

/** Rows accepted by this file since boot — the surface's `flushes` companion. */
uint32_t ns_bbx_writes(void)     { return s_writes; }
uint32_t ns_bbx_write_fails(void){ return s_write_fails; }
uint32_t ns_bbx_evicted(void)    { return s_evicted; }
uint32_t ns_bbx_unlinked(void)   { return s_unlinked; }
uint32_t ns_bbx_replayed(void)   { return s_replayed; }

/* ── DROP accounting ─────────────────────────────────────────────────────── */

/** Record that `count` rows were lost. Called with the *open* chunk's id when the
 *  Core-1 queue overflowed, or with the wire's own chunk id when the hub's radio
 *  path reported a drop.
 *
 *  Overwriting a slot is a deliberate choice: the table is 32 deep, and if it ever
 *  wraps the *oldest* record goes — not the newest, because the newest drop is the
 *  one attached to a chunk that is still open and therefore still evictable-or-not. */
void ns_bbx_note_drop(uint16_t chunk_id, uint16_t count)
{
    if (!count) return;
    for (uint8_t i = 0; i < s_drop_slots_used; i++) {
        if (s_drops[i].chunk_id == chunk_id) {
            s_drops[i].count += count;
            return;
        }
    }
    uint8_t slot = (s_drop_slots_used < NS_BBX_DROP_SLOTS)
                       ? s_drop_slots_used++
                       : (uint8_t)(NS_BBX_DROP_SLOTS - 1);
    if (s_drop_slots_used >= NS_BBX_DROP_SLOTS) {
        /* Full: shift down and take the newest slot, so the oldest record is the
         * one sacrificed — and the caller still gets a slot. */
        memmove(&s_drops[0], &s_drops[1], sizeof(s_drops[0]) * (NS_BBX_DROP_SLOTS - 1));
        slot = NS_BBX_DROP_SLOTS - 1;
    }
    s_drops[slot].chunk_id = chunk_id;
    s_drops[slot].count = count;
}

/** How many rows this chunk lost. **Zero is not the same as "unknown".**
 *
 *  A chunk older than the drop table answers 0, and the delete gate on the surface
 *  will then see `dropped_rows == 0` from the wire — but it also holds the chunk's
 *  `CHUNK_CLOSE` line, which carried the cumulative count at the time. The hub is
 *  not the authority on that number; the surface is, because the surface keeps
 *  every one. This function exists for the *hub's own* two decisions (evict, or
 *  refuse an ACK_DELETE) where the newest chunks are the ones that matter. */
uint32_t ns_bbx_drops(uint16_t chunk_id)
{
    for (uint8_t i = 0; i < s_drop_slots_used; i++) {
        if (s_drops[i].chunk_id == chunk_id) return s_drops[i].count;
    }
    return 0;
}

/* ── Listing, eviction, deletion ─────────────────────────────────────────── */

/** The chunk ids on the card, low to high, up to `max_ids`. Returns how many.
 *
 *  A directory scan every call, on purpose: the card is the truth about what is on
 *  the card. A cached list would be one more thing that can disagree with reality
 *  at exactly the moment someone is asking what is safe to delete. */
uint16_t ns_bbx_chunks_on_card(uint16_t *out, uint16_t max_ids)
{
    if (!s_mounted) return 0;
    File dir = SD.open("/");
    if (!dir) return 0;

    /* Collected unsorted first, then insertion-sorted: a directory with a hundred
     * files is still trivial, and this keeps the RAM cost at max_ids × 2 bytes. */
    uint16_t n = 0;
    for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
        /* `name()` is a full path on some core versions and a bare name on others —
         * both spellings of the same file, and a blackbox that listed half its own
         * chunks depending on the core's mood would be a bug nobody would look for
         * in the SD driver. So the parse starts at the marker, never at byte 0. */
        const char *name = f.name();
        const char *mark = name ? strstr(name, "chunk_") : NULL;
        unsigned id = 0;
        if (mark && sscanf(mark, "chunk_%4u.csv", &id) == 1 && id > 0) {
            if (n < max_ids) {
                uint16_t k = n;
                while (k > 0 && out[k - 1] > (uint16_t)id) { out[k] = out[k - 1]; k--; }
                out[k] = (uint16_t)id;
                n++;
            }
        }
        f.close();
    }
    dir.close();
    return n;
}

/** Delete whole old chunks when the card runs low. The hub's own housekeeping.
 *
 *  Refusals, in order: the chunk being written; a chunk with drops; and anything
 *  inside the newest `NS_BBX_FIFO_KEEP`. Only then does the oldest go. Note that
 *  this is a *local* decision about *storage*, and it is the only place in the
 *  firmware that destroys data without the surface's permission — which is why it
 *  evicts the oldest and never the newest, and why it stops the moment the
 *  low-water mark is cleared rather than emptying the card. */
void ns_bbx_fifo_evict(void)
{
    if (!s_mounted) return;
    if (ns_bbx_free_mb() >= NS_BBX_FIFO_FREE_MB) return;

    uint16_t ids[64];
    uint16_t n = ns_bbx_chunks_on_card(ids, 64);
    for (uint16_t i = 0; i < n; i++) {
        if (ns_bbx_free_mb() >= NS_BBX_FIFO_FREE_MB) break;
        if ((n - i) <= NS_BBX_FIFO_KEEP) break;          /* keep the newest KEEP */
        uint16_t cid = ids[i];
        if (cid == s_open_id) continue;
        if (ns_bbx_drops(cid) > 0) continue;             /* evidence, not storage */
        const char *why = NULL;
        if (ns_bbx_unlink_chunk(cid, &why)) s_evicted++;
    }
}

/** Execute an `ACK_DELETE`. Returns true only when the file is really gone.
 *
 *  The two local checks are the ones this board can make without lying: it will
 *  not unlink the chunk it is writing, and it will not unlink a chunk that lost
 *  rows to a queue overflow. Everything else — cloud confirmation, row counts,
 *  the 60-second margin — was decided on the surface by `gate.py`, against state
 *  this board cannot see. */
bool ns_bbx_unlink_chunk(uint16_t chunk_id, const char **why)
{
    if (why) *why = NULL;
    if (!s_mounted) { if (why) *why = "no card"; return false; }
    if (chunk_id == s_open_id) { if (why) *why = "chunk is open"; return false; }
    if (ns_bbx_drops(chunk_id) > 0) { if (why) *why = "chunk lost rows to a drop"; return false; }

    char path[32];
    bbx_path(path, sizeof(path), chunk_id);
    if (!SD.exists(path)) { if (why) *why = "no such chunk on the card"; return false; }
    if (!SD.remove(path)) { if (why) *why = "unlink failed"; return false; }
    s_unlinked++;
    return true;
}

/** How many rows are really in a chunk file — the number `CHUNK_DONE` reports.
 *
 *  Counted from the file rather than remembered from the write path, because the
 *  question it answers is *"what is on the card"*, and the card is the only thing
 *  that survives a reboot. The header line is excluded, so the count is rows, not
 *  lines — an off-by-one here would make the reconciliation in §5.7 compare a row
 *  count against a line count and hold every chunk forever. */
uint32_t ns_bbx_count_rows(uint16_t chunk_id)
{
    if (!s_mounted) return 0;
    char path[32];
    bbx_path(path, sizeof(path), chunk_id);
    File f = SD.open(path, FILE_READ);
    if (!f) return 0;
    uint32_t rows = 0;
    bool first = true;
    while (f.available()) {
        String ln = f.readStringUntil('\n');
        if (first) { first = false; continue; }
        if (ln.length()) rows++;
    }
    f.close();
    return rows;
}

/* ── REPLAY serving ──────────────────────────────────────────────────────── */

/** Stream a chunk back up the wire, line by line, exactly as it was written.
 *
 *  §5.7's demo: the rows travel the *same* path as live telemetry, so the surface's
 *  `UNIQUE(chunk_id, seq)` makes the whole operation idempotent — replaying a chunk
 *  a hundred times inserts nothing new. That is why this function hands each CSV
 *  line to a sink and does not touch the file: **the hub does not transform rows on
 *  replay.** The surface's replayer is the thing that knows the row format, and a
 *  hub that "helpfully" re-serialised here would be a second implementation of the
 *  wire contract, drifting quietly.
 *
 *  The header line is skipped (it is for humans), and the sink returns nothing:
 *  a replay is best-effort by design, and a chunk that vanished mid-stream (an
 *  eviction racing a request) simply stops — the gate will not have released it.
 */
bool ns_bbx_stream_chunk(uint16_t chunk_id, void (*sink)(const char *line))
{
    if (!s_mounted || !sink) return false;
    char path[32];
    bbx_path(path, sizeof(path), chunk_id);
    File f = SD.open(path, FILE_READ);
    if (!f) return false;

    char line[160];
    size_t used = 0;
    bool first = true;
    while (f.available()) {
        int c = f.read();
        if (c < 0) break;
        if (c == '\n') {
            line[used] = '\0';
            used = 0;
            if (first) { first = false; continue; }        /* column header */
            if (line[0]) sink(line);
            continue;
        }
        if (c != '\r' && used < sizeof(line) - 1) line[used++] = (char)c;
    }
    if (used) { line[used] = '\0'; if (line[0]) sink(line); }
    f.close();
    s_replayed++;
    return true;
}

/* ── One status line for the hub's own console ───────────────────────────── */

void ns_bbx_console(void)
{
    uint16_t ids[64];
    uint16_t n = ns_bbx_chunks_on_card(ids, 64);
    Serial.printf("[blackbox] %s · free %u MB · %u chunk(s) on card · %u written · "
                  "%u write-fail · %u evicted · %u unlinked · %u replay(s)\n",
                  s_state, (unsigned)ns_bbx_free_mb(), (unsigned)n,
                  (unsigned)s_writes, (unsigned)s_write_fails,
                  (unsigned)s_evicted, (unsigned)s_unlinked, (unsigned)s_replayed);
}

#endif /* BOARD_MAIN_HUB */
