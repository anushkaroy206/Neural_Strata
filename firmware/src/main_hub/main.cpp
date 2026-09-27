/*
 * ============================================================================
 * Neural Strata — MAIN HUB firmware · H0 stub + the ring (Phase 9, B6)
 * ============================================================================
 * **What this board is now:** the ring's far end. MAIN terminates two of the
 * three segments (hop 1 to L1, hop 3 to L2), and the third — L1 ↔ L2 — is the one
 * the service-panel switch cuts. §H5.5 puts the fault report at the ends of a
 * segment, so this board is where two of the three possible faults are born and
 * where the third arrives to be forwarded.
 *
 * The framing, the heartbeat cadence, the fault edge detector and the frame
 * builders all live in `lib/ring/` and know nothing about the ESP32. What is
 * *here* is the binding: two `HardwareSerial` instances, the USB console as the
 * server link, and the routing decision of which ring leg a down-link command
 * leaves by. That split is why the same fault machine also runs on the level
 * hubs, where it is the only thing that can see W4.
 *
 * Still H4's (deliberately not stubbed, because a pin defined "for later" is a
 * pin somebody wires): the radio master, the microSD archivist, the modem, and
 * `serial_surface.cpp` — the server-facing protocol proper.
 *
 *     main_hub/main.cpp · ring (Phase 9) + radio master & verbs (Phase 10) → written
 *     main_hub/timekeeper.cpp · archivist.cpp · blackbox.cpp                → Phase 10, written
 *     main_hub/serial_surface.cpp                          → H4 (framing only)
 *     main_hub/protocol_out.cpp                            → Phase 11 (the verbs move there)
 *
 * ── The one line that matters on the bench ──────────────────────────────────
 * Everything a level hub sends arrives here and is printed **verbatim** on the
 * USB console. No rewriting, no re-wrapping, no "helpful" field injection: a
 * relayed frame must be byte-identical to a direct one except for the `via`
 * badge that the originating hub put on it, because that identity is what makes
 * the relay verifiable instead of merely plausible.
 *
 * This is the board on the USB cable. Label it "DO NOT SWAP" before you flash
 * anything: its MAC is compiled into all four nodes (HD-14), so swapping it
 * without reflashing every node breaks the radio link.
 *
 * Deliberately absent: `common/pins.h` (Phase 1) and the SD card pin map
 * (Phase 10 — VSPI CS=5, SCK=18, MOSI=23, MISO=19, 3V3 only).
 * ============================================================================
 */
#ifdef BOARD_MAIN_HUB

#include <Arduino.h>
#include <WiFi.h>
#include <esp_mac.h>
#include <esp_now.h>
#include <math.h>
#include <string.h>

#include "common/pins.h"
#include "common/power.h"
#include "ring.h"

/* ── The ring: two legs, and no more ──────────────────────────────────────────
 *
 * Port 0 is L1 (UART2, 17/16, orange) · port 1 is L2 (UART1, 4/13, teal). The
 * numbers come from pins.h, which took them from §H5.3's wire table — this file
 * never repeats them, so a wiring correction lands in one place.
 */
#define NS_PORT_L1 0
#define NS_PORT_L2 1
#define NS_RING_PORTS 2

static HardwareSerial s_l1(2);           /* UART2 — MAIN's UART2 faces L1 */
static HardwareSerial s_l2(1);           /* UART1 — MAIN's UART1 faces L2 */

static ns_ring_line_t  s_line[NS_RING_PORTS];
static ns_ring_link_t  s_link[NS_RING_PORTS];
static ns_ring_fault_t s_fault[NS_RING_PORTS];

/* Dedicated per-level command buffers so Level 1 (L1: J1/J2) and Level 2 (L2: J3/J4)
 * down-link commands maintain completely isolated state and buffers. */
typedef struct {
    char buf[NS_RING_LINE_MAX];
    bool valid;
    uint32_t last_tx_ms;
} ns_level_cmd_buf_t;

static ns_level_cmd_buf_t s_level_cmd_buf[NS_RING_PORTS];

/*: A segment has one name for the whole system, and this is where two of the
 *  three are named. The third (`L1-L2`) is named by L1, because L1 is its
 *  receiver — W4 carries L2's transmit into L1's receive, and the board that
 *  stops hearing is the board that must complain. */
static const char *s_seg[NS_RING_PORTS] = {"MAIN-L1", "MAIN-L2"};

/*: The direction **this board** has lost, which is the direction the frame is
 *  about. MAIN observes a segment by listening, so what it loses is the level's
 *  transmit: `L1→MAIN`. The level hub at the other end of the same segment loses
 *  the opposite direction and reports *that*. They are both telling the truth —
 *  which is exactly why `dir` is a field on the frame instead of a rule in the
 *  reader, and why a cut's direction is the first thing a judge asks about. */
static const char *s_dir[NS_RING_PORTS] = {"L1\u2192MAIN", "L2\u2192MAIN"};

/*: A MAIN leg cannot be routed around — there is no second wire from this board
 *  to that level — so the honest directive is the hold: keep the last picture and
 *  say the segment is degraded. Claiming a reroute that does not exist would be
 *  worse than saying nothing.
 *
 *  (The cross-link is the opposite case: cutting it *is* survivable, and L1 sends
 *  the `direct-via-MAIN` directive that makes it so. Two segments, two different
 *  truths, which is why the reroute is a field on the frame and not a constant
 *  in the reader.) */
static const char *s_reroute[NS_RING_PORTS] = {"hold-last-indication",
                                               "hold-last-indication"};

static uint32_t s_usb_forwarded = 0, s_usb_dropped = 0, s_cmd_forwarded = 0;
static uint32_t s_hb_forwarded = 0;
static uint32_t s_cmd_refused = 0, s_cmd_rerouted = 0;

static uint32_t s_last_status_ms = 0;

#ifndef NS_SCHEMA_VERSION
#define NS_SCHEMA_VERSION 2
#endif

/* ── Forward declarations ─────────────────────────────────────────────────────
 * One block, at the top, for everything defined further down this file. Phase 9
 * learned this the hard way on the level hub: a prototype next to its definition a
 * hundred lines below the caller compiles only until the caller moves, and then
 * fails with "'x' was not declared in this scope" — which reads like a missing
 * include and is actually a missing *order*. */
static void ns_ring_begin(void);
/* Non-static since Phase 11: `protocol_out.cpp` writes rows through it, so the
 * console has **one** printer and **one** forwarded-line counter rather than two
 * that drift. */
void ns_usb_print(const char *line);
static void ns_route_command(const char *json);
static void ns_pump_usb(void);
static void ns_pump_ring(uint32_t now_ms);

/* Phase 10 — defined under the blackbox section near the bottom of this file,
 * called from `ns_pump_usb()` above it. */
static void ns_on_recv(const uint8_t *mac, const uint8_t *data, int len);
static void ns_on_rollover(uint16_t closed, uint32_t rows, uint32_t drops);
static void ns_radio_begin(void);

/* From protocol_out.cpp (Phase 11) — the wire, both directions. Phase 10 wrote
 * this vocabulary inline here and said the move was provisional; this is it. The
 * board now has exactly one file that knows what the protocol looks like. */
void        ns_out_row_line(const char *node_json, uint16_t chunk_id,
                            uint16_t chunk_seq, uint32_t ts_ms);
void        ns_out_chunk_open(uint16_t chunk_id);
void        ns_out_handle_downlink(const char *line);
uint32_t    ns_out_verbs(void);
uint32_t    ns_out_acks_ok(void);
uint32_t    ns_out_acks_refused(void);
uint32_t    ns_out_replays(void);
uint32_t    ns_out_lines(void);
uint32_t    ns_out_splice_fails(void);

/* ── The ring, as this board plays it ─────────────────────────────────────── */

static void ns_ring_begin(void)
{
    s_l1.begin(115200, SERIAL_8N1, MAIN_RING_L1_RX, MAIN_RING_L1_TX);
    s_l2.begin(115200, SERIAL_8N1, MAIN_RING_L2_RX, MAIN_RING_L2_TX);
    for (int p = 0; p < NS_RING_PORTS; p++) {
        ns_ring_line_init(&s_line[p]);
        ns_ring_link_init(&s_link[p]);
        ns_ring_fault_init(&s_fault[p]);
        s_level_cmd_buf[p].valid = false;
        s_level_cmd_buf[p].buf[0] = '\0';
        s_level_cmd_buf[p].last_tx_ms = 0;
    }
    s_l1.setTimeout(0);
    s_l2.setTimeout(0);
    Serial.printf("[main hub] ring up · L1=UART2(%u/%u) · L2=UART1(%u/%u) · 115200 8N1\n",
                  (unsigned)MAIN_RING_L1_TX, (unsigned)MAIN_RING_L1_RX,
                  (unsigned)MAIN_RING_L2_TX, (unsigned)MAIN_RING_L2_RX);
}

/** Print one line on the USB console — the server's cable.
 *
 *  Printed **as received**. The server's contract is that a relayed frame is
 *  indistinguishable from a direct one except for the origin's badge, so any
 *  transformation here would break the one property the ring is tested on
 *  (`test_a_relayed_level_2_frame_is_identical_to_a_direct_one`). */
void ns_usb_print(const char *line)
{
    if (!line || !*line) return;
    Serial.print(line);
    Serial.print('\n');
    s_usb_forwarded++;
}

/** Decide which leg a down-link command leaves by.
 *
 *  By level, first: `level` if the body carries it, otherwise the junction letter
 *  (J1/J2 are level 1, J3/J4 are level 2 — §A.2.3, the same rule the server's
 *  `HubCommand.level_of()` uses, written twice because the two ends must agree
 *  and neither can ask the other). `pattern` with no junction addresses a whole
 *  level and carries `level` for exactly this reason.
 *
 *  If the intended leg is down, the command goes out the other one and is counted
 *  as a re-route: the triangle's promise is that **L2 keeps receiving alerts even
 *  when its own leg is cut**, and this is the eight lines that keep it. */
static void ns_route_command(const char *json)
{
    long level = 0;
    char junction[8] = {0};

    if (!ns_ring_json_int(json, "level", &level)) {
        if (ns_ring_json_str(json, "junction", junction, sizeof(junction)) &&
            junction[0] == 'J' && junction[1] >= '1' && junction[1] <= '4') {
            level = (junction[1] <= '2') ? 1 : 2;
        }
    }
    if (level != 1 && level != 2) {
        /* Not addressed to a level this board can reach. Refused and *counted*:
         * a command nobody acts on is exactly the thing that must not be silent. */
        s_cmd_refused++;
        Serial.printf("[main hub] down-link refused — no level (%.60s)\n", json);
        return;
    }

    uint32_t now = millis();
    int port = (level == 1) ? NS_PORT_L1 : NS_PORT_L2;

    /* Store command into the dedicated per-level buffer (L1 = port 0, L2 = port 1).
     * Inject "cmd":"led" and "level" if missing so level hub command handlers validate cleanly. */
    long check_lvl = 0;
    char cmd_check[16] = {0};
    bool has_cmd = ns_ring_json_str(json, "cmd", cmd_check, sizeof(cmd_check));

    if ((!ns_ring_json_int(json, "level", &check_lvl) || !has_cmd) && json[0] == '{') {
        if (!has_cmd) {
            snprintf(s_level_cmd_buf[port].buf, sizeof(s_level_cmd_buf[port].buf),
                     "{\"cmd\":\"led\",\"level\":%ld,%s", level, json + 1);
        } else {
            snprintf(s_level_cmd_buf[port].buf, sizeof(s_level_cmd_buf[port].buf),
                     "{\"level\":%ld,%s", level, json + 1);
        }
    } else {
        strncpy(s_level_cmd_buf[port].buf, json, sizeof(s_level_cmd_buf[port].buf) - 1);
        s_level_cmd_buf[port].buf[sizeof(s_level_cmd_buf[port].buf) - 1] = '\0';
    }
    s_level_cmd_buf[port].valid = true;

    /* Rate-limit silent leg logging to once per 5 seconds per port to avoid UART flooding */
    static uint32_t s_last_silent_log_ms[NS_RING_PORTS] = {0, 0};
    if (!ns_ring_link_up(&s_link[port], now)) {
        s_cmd_rerouted++;
        if ((uint32_t)(now - s_last_silent_log_ms[port]) >= 5000) {
            s_last_silent_log_ms[port] = now;
            Serial.printf("[main hub] L%ld leg silent — rerouting via opposite ring port\n",
                          level);
        }
    }

    HardwareSerial *uart = (port == NS_PORT_L1) ? &s_l1 : &s_l2;
    uart->print(NS_RING_CMD_SENTINEL);
    uart->print(s_level_cmd_buf[port].buf);
    uart->print('\n');

    /* Ring reroute fallback: if direct link is down, also send via opposite port */
    int alt_port = (port == NS_PORT_L1) ? NS_PORT_L2 : NS_PORT_L1;
    if (!ns_ring_link_up(&s_link[port], now)) {
        HardwareSerial *alt_uart = (alt_port == NS_PORT_L1) ? &s_l1 : &s_l2;
        alt_uart->print(NS_RING_CMD_SENTINEL);
        alt_uart->print(s_level_cmd_buf[port].buf);
        alt_uart->print('\n');
    }

    s_level_cmd_buf[port].last_tx_ms = now;
    ns_ring_mark_tx(&s_link[port], now);
    s_cmd_forwarded++;
}

/** A line arrived from a level hub. Forward it, or count it. */
static void ns_handle_from_hub(int port, const char *line, uint32_t now_ms)
{
    /* Same clock as the fault check below. millis() here can be a few
     * milliseconds ahead of the loop's `now`, and an unsigned subtract then
     * looks like a multi-day gap — a fault and a restore on every line. */
    ns_ring_mark_rx(&s_link[port], now_ms);

    /* A down-link line arriving *from* a hub means a hub echoed a command back up
     * the ring. Never acted on here — this board is the one that issued it — and
     * counted, because an echo storm is a wiring mistake worth seeing. */
    if (ns_ring_is_downlink(line)) {
        s_usb_dropped++;
        return;
    }

    /* Heartbeats: the level hubs' own, which **are** forwarded.
     *
     * The first version of this swallowed them — "the server does not need 30 a
     * minute" — and that made the server's link display a one-way ratchet: the
     * aging tick could only ever take a link *down*, so a link that came back
     * showed as dead for the rest of the run. The heartbeat is the only frame
     * that can tell the server a level is reachable, so it goes up the cable.
     *
     * It costs ~40 bytes every 2 s per level against an 11 kB/s cable, and it is
     * also what makes a *relayed* level look reachable: L2's beat reaches MAIN
     * directly on hop 3, or through L1 on hop 2 when hop 3 is cut. Either way the
     * server's question — "can I hear that level?" — is answered correctly. */
    if (strstr(line, NS_RING_HB_KEY)) {
        s_hb_forwarded++;
        ns_usb_print(line);
        return;
    }

    /* Everything else — relay envelopes and §H5.5's ring fault frames — goes up
     * the USB cable unchanged. The fault frames are the reason the *server* can
     * show a degraded segment at all: MAIN is the only board wired to it. */
    ns_usb_print(line);
}

/** The USB console is the server. Read it, and act on `#CMD`. */
static void ns_pump_usb(void)
{
    static ns_ring_line_t usb_line;
    static bool started = false;
    if (!started) {                       /* static init is not guaranteed order */
        ns_ring_line_init(&usb_line);
        started = true;
    }

    int budget = 512;
    while (budget-- > 0 && Serial.available() > 0) {
        char c = (char)Serial.read();
        if (ns_ring_line_feed(&usb_line, c)) {
            if (ns_ring_is_downlink(usb_line.buf)) {
                ns_route_command(ns_ring_downlink_json(usb_line.buf));
            } else if (usb_line.buf[0] && usb_line.buf[0] != '{' &&
                       usb_line.buf[0] != '#') {
                /* The blackbox verbs: ACK_DELETE / REPLAY / STATE? / PING. They are
                 * not `#CMD`-prefixed — see the reconciliation at the top of
                 * `protocol_out.cpp`: `#CMD` is the *command* channel, which this
                 * board forwards underground without understanding it, while these
                 * are addressed to this board's own card and are never forwarded. */
                ns_out_handle_downlink(usb_line.buf);
            }
            /* Anything else is another phase's protocol. Ignored rather than
             * refused: a hub that NAKed a line it did not understand would look
             * broken to a server that had simply sent something newer. */
        } else if (usb_line.overflow) {
            usb_line.overflow = false;
            Serial.printf("[main hub] USB line over %d bytes — dropped\n",
                          NS_RING_LINE_MAX);
        }
    }
}

/** Heartbeats out on both legs, every 2 s, and the §H5.5 fault edge. */
static void ns_pump_ring(uint32_t now_ms)
{
    for (int p = 0; p < NS_RING_PORTS; p++) {
        HardwareSerial *uart = (p == NS_PORT_L1) ? &s_l1 : &s_l2;

        int budget = 256;
        while (budget-- > 0 && uart->available() > 0) {
            char c = (char)uart->read();
            if (ns_ring_line_feed(&s_line[p], c)) {
                ns_handle_from_hub(p, s_line[p].buf, now_ms);
            } else if (s_line[p].overflow) {
                s_line[p].overflow = false;
                Serial.printf("[main hub] ring line over %d bytes on %s — dropped\n",
                              NS_RING_LINE_MAX, s_seg[p]);
            }
        }

        if (ns_ring_heartbeat_due(&s_link[p], now_ms)) {
            char hb[192];
            size_t n = ns_ring_build_heartbeat(hb, sizeof(hb), "hub",
                                               ++s_link[p].seq,
                                               (p == NS_PORT_L1) ? "L1" : "L2");
            if (n) {
                uart->print(hb);
                uart->print('\n');
                ns_ring_mark_tx(&s_link[p], now_ms);
            }
        }

        /* Re-transmit active level command every 2.5 seconds to guarantee level hubs retain state */
        if (s_level_cmd_buf[p].valid &&
            (uint32_t)(now_ms - s_level_cmd_buf[p].last_tx_ms) >= 2500) {
            uart->print(NS_RING_CMD_SENTINEL);
            uart->print(s_level_cmd_buf[p].buf);
            uart->print('\n');

            /* If direct leg is down, re-transmit via alternate port as well */
            if (!ns_ring_link_up(&s_link[p], now_ms)) {
                int alt_p = (p == NS_PORT_L1) ? NS_PORT_L2 : NS_PORT_L1;
                HardwareSerial *alt_uart = (alt_p == NS_PORT_L1) ? &s_l1 : &s_l2;
                alt_uart->print(NS_RING_CMD_SENTINEL);
                alt_uart->print(s_level_cmd_buf[p].buf);
                alt_uart->print('\n');
            }

            s_level_cmd_buf[p].last_tx_ms = now_ms;
            ns_ring_mark_tx(&s_link[p], now_ms);
        }

        /* The fault edge. Exactly one report per outage (§H5.5), and the report
         * goes up the USB cable rather than around the ring: a fault that reached
         * the server by the wire it is reporting would be a fault reported by a
         * link that may not exist. */
        int edge = ns_ring_fault_update(&s_fault[p], &s_link[p], now_ms);
        if (edge != 0) {
            char frame[256];
            size_t n;
            if (edge > 0) {
                n = ns_ring_build_fault(frame, sizeof(frame), "hub", s_seg[p],
                                        s_dir[p], s_reroute[p], s_fault[p].faults);
                if (n) {
                    ns_usb_print(frame);
                    Serial.printf("[main hub] RING FAULT %s (%s) → %s · report #%lu\n",
                                  s_seg[p], s_dir[p], s_reroute[p],
                                  (unsigned long)s_fault[p].faults);
                }
            } else {
                n = ns_ring_build_restored(frame, sizeof(frame), "hub", s_seg[p],
                                          s_fault[p].down_ms);
                if (n) {
                    ns_usb_print(frame);
                    Serial.printf("[main hub] segment restored %s after %lu ms\n",
                                  s_seg[p], (unsigned long)s_fault[p].down_ms);
                }
            }
        }
    }
}

/* ══════════════════════════════════════════════════════════════════════════
 * Phase 10 · B13 — the radio master, the timekeeper and the archivist
 * ══════════════════════════════════════════════════════════════════════════
 *
 * H4 left one thing undone on purpose: **the radio master**. This board is the
 * hub four nodes talk to, and until this section existed the blackbox had nothing
 * to archive — a card with no rows on it is a prop, not a blackbox. So the
 * receive path arrives here, and it is deliberately the thinnest one that can
 * work: receive, validate, transcribe, **stamp**, and hand over.
 *
 *     ESP-NOW packet  →  ns_is_node_packet()  →  ns_packet_to_json()
 *                                                 │
 *                              ┌──────────────────┴──────────────────┐
 *                              ▼                                     ▼
 *                    the USB line (stamped)         the Core-0 queue (the same row)
 *
 * ── One stamp, two copies ───────────────────────────────────────────────────
 *
 * Core 1 (`timekeeper.cpp`) owns `row_counter` and `chunk_id`. It stamps the row
 * once and both copies carry that stamp: outward, spliced onto the JSON line the
 * server reads; inward, as the 24-byte struct the archivist writes to the card.
 * The splice is three extra keys and **no key is reused**:
 *
 *     "chunk_id":42,"chunk_seq":17,"ts_ms":142850
 *
 * `ts_ms` is the hub's monotonic clock. It is not `ms` — `ms` is the *node's*
 * uptime, and a hub that overwrote it would destroy the one number that lets the
 * server measure Δt across a node's reboot (defect D9's fix). `chunk_seq` is not
 * `seq` for the same reason: `seq` is the node's own monotonic counter and the gap
 * watchdog in `ingestion/ring.py` counts it. Three new names, no lost fields.
 *
 * ── What the hub still refuses to do ────────────────────────────────────────
 *
 * It does not decide anything. There is no inference, no threshold, no "this row
 * looks like a collapse" — the blackbox is a *recorder*, and the one authority it
 * holds (which chunk may be unlinked) is exercised only against two facts it can
 * see for itself. Everything else is the surface's five-condition gate.
 * ══════════════════════════════════════════════════════════════════════════ */

/* From timekeeper.cpp — Core 1's counter. */
void         ns_tk_begin(void);
uint16_t     ns_tk_chunk_id(void);
uint32_t     ns_tk_row_counter(void);
uint32_t     ns_tk_dropped_rows(void);
uint32_t     ns_tk_chunks_closed(void);
ns_bbx_row_t ns_tk_stamp(uint8_t node, uint8_t level, uint32_t ts_ms,
                         float tilt, float disp_mm, float accel, uint8_t vib,
                         float micro, float macro, uint8_t flags);
bool         ns_tk_push(const ns_bbx_row_t *row);
void         ns_tk_on_rollover(void (*fn)(uint16_t closed, uint32_t rows, uint32_t drops));

/* From archivist.cpp — Core 0's task and the STATE? answer. */
bool         ns_arch_begin(void);
void         ns_arch_report_state(void);
uint32_t     ns_arch_rows_written(void);
uint32_t     ns_arch_flushes(void);
uint32_t     ns_arch_blocked(void);
bool         ns_arch_sd_ok(void);

/* From blackbox.cpp — the file layer, called from the archivist's context. */
uint32_t     ns_bbx_free_mb(void);
const char  *ns_bbx_state(void);
bool         ns_bbx_stream_chunk(uint16_t chunk_id, void (*sink)(const char *line));
bool         ns_bbx_unlink_chunk(uint16_t chunk_id, const char **why);

static uint32_t s_radio_rows = 0, s_radio_not_packets = 0;
/* The wire counters (verbs, acks, replays, lines, splice failures) live in
 * `protocol_out.cpp` behind accessors: they count things that happen on the line,
 * and that module is what owns the line now. */

/* ── The stamped line ──────────────────────────────────────────────────────
 *
 * Moved to `protocol_out.cpp` in Phase 11 (`ns_out_row_line`), together with every
 * other line this board puts on the wire. What is left here is the *decision* to
 * send one, which is the part that needs the radio's context. */

/* ── The chunk boundary, announced on the wire ───────────────────────────── */

/** Core 1 rolled the counter over: announce the **open**, and nothing else.
 *
 *  Phase 10 printed the close from here too, with the timekeeper's counter as the
 *  row count — and that number is not the one the card holds: rows lost to a queue
 *  overflow were counted by the counter and never written to the file. The gate
 *  measures the surface against `expected_rows`, so a close carrying the counter
 *  would have made a chunk look short for a reason nobody could see, on every
 *  rotation, forever.
 *
 *  The close is therefore emitted by the side that owns the file — Core 0, in
 *  `blackbox.cpp`, once the CSV is really closed — and it carries the file's own
 *  row count. Core 1's job is the half it can actually see: a new chunk id exists.
 *  The two lines are allowed to arrive in either order; the surface treats them as
 *  independent facts, which is exactly what they are. */
static void ns_on_rollover(uint16_t closed, uint32_t rows, uint32_t drops)
{
    (void)closed;
    (void)rows;
    (void)drops;
    ns_out_chunk_open(ns_tk_chunk_id());
}

/* ── REPLAY and the down-link verbs ────────────────────────────────────────
 *
 * Both moved to `protocol_out.cpp` in Phase 11 (`ns_out_handle_downlink`, and the
 * CSV→NDJSON sink it drives): `ACK_DELETE` / `REPLAY` / `STATE?` / `PING` are the
 * down-link half of the same protocol whose up-link half is written there, and
 * splitting one vocabulary across two files is how the two ends drift. */

/* ── The radio master ────────────────────────────────────────────────────── */

/** One node packet: validate, transcribe, stamp, and send both copies on.
 *
 *  Runs in the Wi-Fi task's context, which is why it does almost nothing: a
 *  `print` into the USB FIFO and a zero-tick queue send. No SD, no `delay()`, no
 *  file, nothing that can block — the archivist on Core 0 exists precisely so this
 *  callback can stay this short. */
static void ns_on_recv(const uint8_t *mac, const uint8_t *data, int len)
{
    (void)mac;                      /* the node id is inside the packet, and the
                                     * packet is what the server trusts — a MAC
                                     * check here would be a second, weaker
                                     * identity for the same thing */
    if (!ns_is_node_packet(data, len)) {
        s_radio_not_packets++;      /* relay traffic, beacons: counted, ignored */
        return;
    }
    ns_node_packet_t pkt;
    memcpy(&pkt, data, sizeof(pkt));

    char json[256];
    if (ns_packet_to_json(&pkt, json, sizeof(json)) <= 0) return;

    const uint8_t level = (pkt.node <= 2) ? 1 : 2;      /* N1/N2 on L1, N3/N4 on L2 */

    /* A channel the node cannot measure arrives as NaN and the card has no NaN:
     * it is written as 0.0 **and the node's own flags travel with the row**, so a
     * reader can tell "this node has no crack column" (D10) from "the crack is
     * zero". The distinction lives in the flag byte, which is where the node put
     * it, rather than in a sentinel this board invented. */
    float micro = isnan(pkt.micro) ? 0.0f : pkt.micro;
    float macro = isnan(pkt.macro) ? 0.0f : pkt.macro;
    uint8_t vib = (pkt.vib > 255) ? 255 : (uint8_t)pkt.vib;
    uint8_t flags = pkt.flags;
    if (pkt.vib > 255) flags |= NS_BBX_FLAG_VIB_SAT;

    ns_bbx_row_t row = ns_tk_stamp(pkt.node, level, pkt.ms, pkt.tilt, pkt.disp,
                                  pkt.acc, vib, micro, macro, flags);

    /* The row, twice: outward with the stamp spliced on, inward as the 24-byte
     * struct Core 0 writes to the card. **Both carry the same stamp**, taken once,
     * which is the property the whole phase turns on — the surface and the card
     * must be able to be reconciled row for row, and that is only true if one
     * counter named them. */
    ns_out_row_line(json, row.chunk_id, row.seq, row.ts_ms);        /* outward */
    ns_tk_push(&row);                                              /* inward  */
    s_radio_rows++;
}

static void ns_radio_begin(void)
{
    /* The same two lines the nodes do, for the same reason: ESP-NOW rides the
     * Wi-Fi radio, so the mode has to be STA and any earlier association has to be
     * dropped — an ESP32 that has not been told this will accept `esp_now_init()`
     * and then quietly never receive. */
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    if (esp_now_init() != ESP_OK) {
        Serial.println("[main hub] ESP-NOW init FAILED — no telemetry, and the "
                       "blackbox will record nothing (the card still mounts)");
        return;
    }
    esp_now_register_recv_cb(ns_on_recv);
    Serial.printf("[main hub] radio master up · channel %d · listening for 4 nodes "
                  "· no peers registered (a receiver needs none)\n",
                  NS_ESPNOW_CHANNEL);
}

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.printf("\n[main hub] H0 stub — schema v%d\n", NS_SCHEMA_VERSION);
    Serial.printf("[main hub] chip %s · %u KB free heap\n",
                  ESP.getChipModel(), (unsigned)(ESP.getFreeHeap() / 1024));
    Serial.printf("[main hub] no radio, no SD, no timekeeper yet — H4 / Phase 10\n");

    /* ── H3 / §4.1: the brownout detector ───────────────────────────────────
     * This is the one board with a filesystem, so it is the one board where
     * "stop ingest" is an action rather than a flag. Phase 10's archivist must
     * check `ns_power_ingest_allowed()` before every 5 s flush — the gate is
     * fail-closed, so forgetting it is the only way to write to a card on a
     * collapsing rail. */
    if (ns_power_begin()) {
        Serial.printf("[main hub] booted after a BROWNOUT reset — ingest blocked "
                      "this boot; the card may hold a partial chunk\n");
    }
    ns_power_report("main hub");

    /* ── HD-14: the hub's MAC goes into all four nodes ──────────────────────
     * This is the one line of H0 that the rest of the radio build cannot
     * proceed without: the main hub's MAC is compiled into every node's
     * firmware, so if it is wrong, nothing on the bench works and the failure
     * looks like "ESP-NOW is broken" rather than "the wrong address was typed".
     *
     * Both MACs are printed because they are not always the same. ESP-NOW uses
     * the **STA** interface address; the base address from eFuse differs on some
     * modules. Whichever the node ends up compiled with, it must be the STA one,
     * so print it explicitly rather than leaving it to be inferred.
     *
     *   pio run -e main_hub -t upload && pio device monitor -b 115200
     */
    uint8_t sta_mac[6], base_mac[6];
    WiFi.macAddress(sta_mac);
    esp_read_mac(base_mac, ESP_MAC_WIFI_STA);
    Serial.printf("[main hub] STA MAC  %02X:%02X:%02X:%02X:%02X:%02X   "
                  "<- compile THIS into every node (NS_HUB_MAC_*)\n",
                  sta_mac[0], sta_mac[1], sta_mac[2], sta_mac[3], sta_mac[4], sta_mac[5]);
    Serial.printf("[main hub] eFuse MAC %02X:%02X:%02X:%02X:%02X:%02X\n",
                  base_mac[0], base_mac[1], base_mac[2], base_mac[3], base_mac[4], base_mac[5]);
    Serial.printf("[main hub] ** LABEL THIS BOARD \"MAIN HUB — DO NOT SWAP\" **\n");

    ns_ring_begin();
    Serial.printf("[main hub] ready — 2 s heartbeats per leg, fault on a > %u ms gap\n",
                  (unsigned)NS_RING_FAULT_MS);

    /* ── Phase 10: the blackbox, in the order it must start ─────────────────
     *  1. the timekeeper — the queue must exist before a packet can arrive;
     *  2. the archivist   — Core 0's task, which mounts the card and owns every
     *                       write from here on;
     *  3. the radio       — last, because the first packet it delivers will be
     *                       stamped and queued, and the queue has to be there.
     * Starting the radio first would count its first packets as drops, which is a
     * defect that only appears once, on a bench, and looks like a hardware fault. */
    ns_tk_begin();
    ns_tk_on_rollover(ns_on_rollover);
    ns_arch_begin();
    ns_radio_begin();
    Serial.printf("[main hub] blackbox: SD %s · %u MB free · chunk %04u · %lu row(s) so far\n",
                  ns_arch_sd_ok() ? ns_bbx_state() : "NONE",
                  (unsigned)ns_bbx_free_mb(), (unsigned)ns_tk_chunk_id(),
                  (unsigned long)ns_tk_row_counter());
}

void loop() {
    uint32_t now = millis();

    ns_pump_usb();          /* the server's cable: down-link commands          */
    ns_pump_ring(now);      /* the two ring legs: uplink, heartbeats, faults   */

    /* A status line every 10 s. Deliberately chatty for a bench board: this is
     * the console a person reads while flipping the service-panel switch, and it
     * is where "one fault event, not a storm" is observed rather than asserted. */
    if (now - s_last_status_ms >= 10000) {
        s_last_status_ms = now;
        Serial.printf("[main hub] L1 %s (rx %lu tx %lu faults %lu) · "
                      "L2 %s (rx %lu tx %lu faults %lu) · cmd fwd %lu rerouted %lu "
                      "refused %lu · fwd %lu hb %lu\n",
                      ns_ring_link_up(&s_link[NS_PORT_L1], now) ? "up  " : "DOWN",
                      (unsigned long)s_link[NS_PORT_L1].rx_lines,
                      (unsigned long)s_link[NS_PORT_L1].tx_lines,
                      (unsigned long)s_fault[NS_PORT_L1].faults,
                      ns_ring_link_up(&s_link[NS_PORT_L2], now) ? "up  " : "DOWN",
                      (unsigned long)s_link[NS_PORT_L2].rx_lines,
                      (unsigned long)s_link[NS_PORT_L2].tx_lines,
                      (unsigned long)s_fault[NS_PORT_L2].faults,
                      (unsigned long)s_cmd_forwarded, (unsigned long)s_cmd_rerouted,
                      (unsigned long)s_cmd_refused, (unsigned long)s_usb_forwarded,
                      (unsigned long)s_hb_forwarded);
        /* The blackbox's own line, every 10 s, next to the ring's: they are the
         * two halves of "is anything being recorded", and reading them together is
         * how a cut cable and a missing card are told apart on the bench. */
        Serial.printf("[main hub] bbx %s %u MB free · chunk %04u row %lu/%u · "
                      "card rows %lu · flush %lu · drops %lu · blocked %lu · "
                      "radio rows %lu (not-a-packet %lu) · acks %lu/%lu replay %lu\n",
                      ns_bbx_state(), (unsigned)ns_bbx_free_mb(),
                      (unsigned)ns_tk_chunk_id(), (unsigned long)ns_tk_row_counter(),
                      (unsigned)NS_BBX_ROWS_PER_CHUNK,
                      (unsigned long)ns_arch_rows_written(),
                      (unsigned long)ns_arch_flushes(),
                      (unsigned long)ns_tk_dropped_rows(),
                      (unsigned long)ns_arch_blocked(),
                      (unsigned long)s_radio_rows, (unsigned long)s_radio_not_packets,
                      (unsigned long)ns_out_acks_ok(), (unsigned long)ns_out_acks_refused(),
                      (unsigned long)ns_out_replays());
    }
}

#endif  // BOARD_MAIN_HUB
