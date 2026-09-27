/*
 * protocol_out.cpp — the Main Hub's wire, both directions. §5.5 / §5.7 / H4.4.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Phase 10 wrote this vocabulary inside `main.cpp` and said so in a comment: the
 * formats were final, only the file they lived in was provisional. This is the
 * move. Everything that speaks the protocol now lives here, and `main.cpp` is
 * left with the things that are genuinely its job — the radio, the ring, and the
 * `#CMD` router.
 *
 * ── Up: NDJSON ──────────────────────────────────────────────────────────────
 *
 * One row per line, `\n`-terminated, in the shape `lib/espnow_packet` already
 * defines, with Core 1's stamp spliced on:
 *
 *     {"v":2,"node":3,…,"seq":991,"chunk_id":42,"chunk_seq":17,"ts_ms":142850}
 *                                            └──────── the blackbox's three ────┘
 *
 * Three added keys and **no key reused**, which is the part that matters:
 *
 *   `seq` is the *node's* own counter (the ring's gap watchdog counts it, and a
 *   node that reboots restarts it — H3). `ms` is the node's uptime, which is how
 *   the surface tells a reboot from a silence. Neither can be overwritten without
 *   destroying evidence, so the hub's numbers arrive under their own names:
 *   `chunk_seq` (position inside the chunk, the ingest's idempotency key) and
 *   `ts_ms` (the *hub's* monotonic clock). The backend accepts either spelling,
 *   and `tests/test_recovery.py` pins both.
 *
 * ── Down: the repair vocabulary ─────────────────────────────────────────────
 *
 *     ACK_DELETE:<chunk>      unlink a file the surface's gate has released
 *     REPLAY:<first>:<last>   re-stream chunks off the card, as NDJSON
 *     STATE?                  "what have you got?" — §5.7's handshake
 *     PING                    liveness, for an operator with a serial monitor
 *
 * ── The `#CMD` rule, reconciled ─────────────────────────────────────────────
 *
 * H4.4 says every server→hub line begins with `#CMD` and the hub ignores
 * non-`#CMD` lines. The blackbox vocabulary above does not use it, and the reason
 * is structural rather than an oversight: `#CMD`-prefixed JSON is the *command*
 * channel (led · siren · advisory · pattern), which this board **forwards
 * underground** without understanding it. The verbs above are the opposite — they
 * are addressed to this board's own card, they are never forwarded, and they are
 * emitted by the laptop's blackbox gateway (`blackbox/gateway.py`) rather than by
 * the command path.
 *
 * So the rule the board actually implements is: **`#CMD` lines go to the router,
 * bare protocol verbs come here, anything else is ignored.** A hub that replied to
 * a line it did not understand would look alive to a server that had simply sent
 * something newer.
 *
 * ── One writer ──────────────────────────────────────────────────────────────
 *
 * This module is called from Core 1 (a radio row arrives) and from the USB pump,
 * and the `Serial` writes are the only shared state it touches — `Serial` is
 * interrupt-safe for whole-line writes and every line here is written with a
 * single `printf`. It never touches the SD card: `STATE?` delegates to the
 * archivist, which is the only context allowed to open a file.
 */

#include <Arduino.h>
#include <math.h>
#include <string.h>

#include "common/config.h"

/* ── Declared by this file, called from main.cpp ───────────────────────────── */
void        ns_out_row_line(const char *node_json, uint16_t chunk_id,
                            uint16_t chunk_seq, uint32_t ts_ms);
void        ns_out_chunk_open(uint16_t chunk_id);
void        ns_out_chunk_close(uint16_t chunk_id, uint32_t rows, uint32_t drops);
void        ns_out_chunk_file(uint16_t chunk_id, uint32_t bytes, uint32_t drops);
void        ns_out_chunk_state(uint16_t chunk_id, uint32_t rows, uint32_t drops,
                               bool closed);
void        ns_out_chunk_done(uint16_t chunk_id, uint32_t rows, uint32_t drops);
void        ns_out_blackbox_state(const char *state, int32_t free_mb);
void        ns_out_drop(uint16_t chunk_id, uint16_t count);
void        ns_out_handle_downlink(const char *line);
void        ns_out_state_report(void);
uint32_t    ns_out_lines(void);
uint32_t    ns_out_verbs(void);
uint32_t    ns_out_acks_ok(void);
uint32_t    ns_out_acks_refused(void);
uint32_t    ns_out_replays(void);
uint32_t    ns_out_replay_lines(void);
const char *ns_out_last_refusal(void);

/* ── Provided elsewhere, called from here ──────────────────────────────────── */
/* main.cpp — the USB console (counts forwarded lines; prints as received). */
void     ns_usb_print(const char *line);
/* blackbox.cpp — the file layer. `stream_chunk` reads; the caller is Core 1, and
 * blackbox.cpp owns the mutex that keeps a read from racing the archivist's
 * append. */
bool     ns_bbx_stream_chunk(uint16_t chunk_id, void (*sink)(const char *line));
bool     ns_bbx_unlink_chunk(uint16_t chunk_id, const char **why);
/* archivist.cpp — the card view. Only Core 0 may enumerate the card, so the
 * handshake's payload is composed there and only the *conversation* is here. */
void     ns_arch_report_state(void);
/* The archivist's card view, for the state report's payload composition. */
uint16_t ns_arch_open_chunk(void);

static uint32_t s_lines = 0, s_verbs = 0, s_acks_ok = 0, s_acks_refused = 0;
static uint32_t s_replays = 0, s_replay_lines = 0, s_splice_fails = 0;
static char     s_last_refusal[64] = {0};

/* ── Up: the stamped row ──────────────────────────────────────────────────── */

/** Print a node row with Core 1's stamp spliced onto it.
 *
 *  A string splice rather than a second `snprintf` of the whole frame, because
 *  `ns_packet_to_json()` is the *one* definition of the node frame's shape (it
 *  lives in `lib/espnow_packet/` and is shared with every node build). Re-writing
 *  it here to add three keys would be a second definition, and the two would drift
 *  the first time a field was added — the exact class of bug this codebase keeps
 *  digging out of its own history. So: take the canonical bytes, insert before the
 *  closing brace, print.
 *
 *  A frame that does not end in `}` is **printed unstamped rather than dropped**:
 *  a row without a chunk id is still a row, and the surface counts it as unknown
 *  instead of losing it. The alternative — refusing to send what we cannot label —
 *  would make a library change look like a dead node. */
void ns_out_row_line(const char *node_json, uint16_t chunk_id, uint16_t chunk_seq,
                     uint32_t ts_ms)
{
    size_t n = strlen(node_json);
    if (n < 2 || node_json[n - 1] != '}') {
        s_splice_fails++;
        ns_usb_print(node_json);
        return;
    }
    char out[320];
    int head = (int)(n - 1);
    if ((size_t)head > sizeof(out) - 64) {
        s_splice_fails++;
        ns_usb_print(node_json);
        return;
    }
    memcpy(out, node_json, (size_t)head);
    int tail = snprintf(out + head, sizeof(out) - (size_t)head,
                        ",\"chunk_id\":%u,\"chunk_seq\":%u,\"ts_ms\":%lu}",
                        (unsigned)chunk_id, (unsigned)chunk_seq, (unsigned long)ts_ms);
    if (tail <= 0) {
        s_splice_fails++;
        ns_usb_print(node_json);
        return;
    }
    ns_usb_print(out);
    s_lines++;
}

/** The chunk boundary, in the protocol's own words.
 *
 *  `CHUNK_OPEN` is announced by Core 1 the moment the counter rolls, and
 *  `CHUNK_CLOSE` by Core 0 once the file is actually closed and counted. **They are
 *  not the same event and must not be emitted together**, which is the one thing
 *  this pair of functions exists to keep straight:
 *
 *    CHUNK_OPEN   Core 1 knows this immediately. It is a fact about the counter.
 *    CHUNK_CLOSE  carries `expected_rows` — the number the delete gate measures the
 *                 surface against (condition 2) and the number a human will check
 *                 against the file. Only the side that owns the file can count it,
 *                 so this comes from the archivist, a few milliseconds later, with
 *                 the *file's* row count rather than the counter's. The surface needs
 *                 both: a chunk with no close is held forever ("row count unknown"),
 *                 which is safe and useless, and a close with an invented count is
 *                 how a file holding the only copy of some rows gets unlinked.
 */
void ns_out_chunk_open(uint16_t chunk_id)
{
    Serial.printf("CHUNK_OPEN:%04u\n", (unsigned)chunk_id);
}

void ns_out_chunk_close(uint16_t chunk_id, uint32_t rows, uint32_t drops)
{
    Serial.printf("CHUNK_CLOSE:%04u,%lu,%lu\n", (unsigned)chunk_id,
                  (unsigned long)rows, (unsigned long)drops);
}

/** The file that just closed, with its real size. Informational, and parsed as a
 *  message the ingest counts separately from *unknown* — a hub that reports useful
 *  bytes must not look like a hub that is sending garbage. */
void ns_out_chunk_file(uint16_t chunk_id, uint32_t bytes, uint32_t drops)
{
    Serial.printf("CHUNK_FILE:%04u,%lu,%lu\n", (unsigned)chunk_id,
                  (unsigned long)bytes, (unsigned long)drops);
}

/** The two halves of §5.7's handshake answer. */
void ns_out_chunk_state(uint16_t chunk_id, uint32_t rows, uint32_t drops, bool closed)
{
    Serial.printf("CHUNK_STATE:%04u,%lu,%lu%s\n", (unsigned)chunk_id,
                  (unsigned long)rows, (unsigned long)drops,
                  closed ? ",CLOSED" : ",OPEN");
}

void ns_out_chunk_done(uint16_t chunk_id, uint32_t rows, uint32_t drops)
{
    Serial.printf("CHUNK_DONE:%04u,%lu,%lu\n", (unsigned)chunk_id,
                  (unsigned long)rows, (unsigned long)drops);
}

/** `BLACKBOX:OK,3712` · `BLACKBOX:NOSD` · `BLACKBOX:FULL,0` · `BLACKBOX:FAULT`.
 *
 *  `free_mb < 0` omits the number rather than printing a 0 we do not have: an
 *  absent card has no free space to report, and "0 MB free" would read as a full
 *  card on the surface.
 */
void ns_out_blackbox_state(const char *state, int32_t free_mb)
{
    if (free_mb >= 0) Serial.printf("BLACKBOX:%s,%ld\n", state, (long)free_mb);
    else              Serial.printf("BLACKBOX:%s\n", state);
}

/** Rows the timekeeper's queue could not take. Rate-limited at the call site. */
void ns_out_drop(uint16_t chunk_id, uint16_t count)
{
    Serial.printf("DROP:%04u,%u\n", (unsigned)chunk_id, (unsigned)count);
}

/* ── Up: REPLAY, the card re-read onto the same wire ──────────────────────── */

/** One CSV line from the card, rendered as the NDJSON row the ingest expects.
 *
 *  The only place in the firmware where a row is *rendered* rather than copied,
 *  and it has to exist: the card holds CSV (§5.5's schema of record) and the wire
 *  speaks JSON, and §5.7 requires a replayed row to pass through the **same**
 *  ingest path as a live one. Every value is printed at the precision its column
 *  carries — no scaling, no rounding, no unit change — and `source:"replay"` is the
 *  one added field, which is what lets the surface label a back-filled row instead
 *  of double-counting it as live.
 *
 *  A line that does not parse as twelve fields is **skipped, never guessed at**:
 *  a half-written line is what a power cut looks like, and inventing the missing
 *  columns would put fiction into a truth store. */
static void ns_out_replay_sink(const char *line)
{
    unsigned cid = 0, seq = 0, node = 0, level = 0, vib = 0, micro = 0, flags = 0;
    unsigned long ts = 0;
    double tilt = 0, disp = 0, accel = 0, macro = 0;
    if (sscanf(line, "%u,%u,%lu,%u,%u,%lf,%lf,%lf,%u,%u,%lf,%u",
               &cid, &seq, &ts, &node, &level, &tilt, &disp, &accel,
               &vib, &micro, &macro, &flags) != 12) {
        return;
    }
    Serial.printf("{\"chunk_id\":%u,\"seq\":%u,\"ts_ms\":%lu,\"node\":%u,"
                  "\"level\":%u,\"tilt\":%.2f,\"disp_mm\":%.1f,\"accel\":%.3f,"
                  "\"vib\":%u,\"micro\":%u,\"macro\":%.1f,\"flags\":%u,"
                  "\"source\":\"replay\"}\n",
                  cid, seq, ts, node, level, tilt, disp, accel, vib, micro, macro, flags);
    s_replay_lines++;
}

/* ── Down: the verbs ──────────────────────────────────────────────────────── */

/** `ACK_DELETE:0042` · `REPLAY:0042:0045` · `STATE?` · `PING`.
 *
 *  The *server's* verbs: the surface's gate decides, this board acts. `ACK_DELETE`
 *  is the only irreversible one, and it can still be refused here for the two
 *  reasons only this board knows — the chunk is open, or the chunk lost rows to a
 *  drop — and the refusal is reported **with its reason**, because a silent refusal
 *  looks exactly like a slow SD card and would be debugged as one.
 *
 *  `REPLAY` is bounded to 33 chunks per line (`last - first > 32`). Not to hide a
 *  limit: a serial link carrying a live 8 rows/s cannot also stream an unbounded
 *  range without delaying live data, so the burst is bounded here and the surface
 *  asks again for the rest. The bound is in the *protocol*, so both ends can see it.
 */
void ns_out_handle_downlink(const char *line)
{
    if (!line || !*line) return;
    if (!strncmp(line, "#CMD", 4)) return;   /* the command channel, not ours */

    s_verbs++;

    if (!strncmp(line, "ACK_DELETE:", 11)) {
        long cid = strtol(line + 11, NULL, 10);
        const char *why = NULL;
        if (ns_bbx_unlink_chunk((uint16_t)cid, &why)) {
            s_acks_ok++;
            Serial.printf("ACK_OK:%04ld\n", cid);
        } else {
            s_acks_refused++;
            snprintf(s_last_refusal, sizeof(s_last_refusal), "%s", why ? why : "unknown");
            Serial.printf("ACK_REFUSED:%04ld,%s\n", cid, why ? why : "unknown");
        }
        return;
    }

    if (!strncmp(line, "REPLAY:", 7)) {
        long first = strtol(line + 7, NULL, 10);
        const char *colon = strchr(line + 7, ':');
        long last = colon ? strtol(colon + 1, NULL, 10) : first;
        if (last < first) last = first;
        if (last - first > 32) last = first + 32;
        uint32_t served = 0;
        for (long c = first; c <= last; c++) {
            if (ns_bbx_stream_chunk((uint16_t)c, ns_out_replay_sink)) served++;
        }
        s_replays++;
        Serial.printf("REPLAY_DONE:%04ld:%04ld,%lu\n", first, last,
                      (unsigned long)served);
        return;
    }

    if (!strcmp(line, "STATE?")) {
        ns_out_state_report();
        return;
    }

    if (!strcmp(line, "PING")) {
        Serial.printf("PONG:%lu\n", (unsigned long)millis());
        return;
    }

    /* Anything else is not this phase's vocabulary: counted, not answered. */
}

/** The handshake answer, composed where the card can be seen.
 *
 *  Delegated to the archivist on purpose. This module owns the *vocabulary*, and
 *  the archivist owns the *card*: only Core 0 may enumerate it, and a `STATE?`
 *  answered from Core 1 would be reading a filesystem while the writer is appending
 *  to it. The split is the same one that keeps every other write single-threaded.
 *
 *  A hub with no card answers with `BLACKBOX:NOSD` and **no `CHUNK_STATE`**. It
 *  used to answer `CHUNK_STATE:NOSD,0,0`, which the frozen parser reads as "three
 *  numbers expected" and files under *unknown* — so a pulled card would have looked
 *  like a corrupt hub rather than an absent one, on every reconnect, forever.
 */
void ns_out_state_report(void)
{
    ns_arch_report_state();
}

void     ns_out_reset_counters(void) { s_lines = s_verbs = s_acks_ok = 0;
                                       s_acks_refused = s_replays = s_replay_lines = 0; }
uint32_t ns_out_lines(void)          { return s_lines; }
uint32_t ns_out_verbs(void)          { return s_verbs; }
uint32_t ns_out_acks_ok(void)        { return s_acks_ok; }
uint32_t ns_out_acks_refused(void)   { return s_acks_refused; }
uint32_t ns_out_replays(void)        { return s_replays; }
uint32_t ns_out_replay_lines(void)   { return s_replay_lines; }
uint32_t ns_out_splice_fails(void)   { return s_splice_fails; }
const char *ns_out_last_refusal(void) { return s_last_refusal; }
