/*
 * ============================================================================
 * Neural Strata — LEVEL HUB · ring_port.cpp
 *   two hardware UARTs on 17/16 and 4/13 · the ring's ESP32 end
 * ============================================================================
 * The framing, the heartbeat cadence and the re-route rule live in `lib/ring/`
 * and know nothing about the ESP32. This file is the binding: two `HardwareSerial`
 * instances, a byte pump, and the decision of which port carries uplink traffic
 * when one of them dies.
 *
 *     port A   UART2   GPIO17 TX · GPIO16 RX   → the MAIN hub
 *     port B   UART1   GPIO4  TX · GPIO13 RX   → the other LEVEL hub
 *
 * ── The triangle, and what each leg is for ──────────────────────────────────
 *
 *        MAIN
 *        /  \            A = both level hubs to MAIN
 *      L1 -- L2          B = the cross-link, L1 ↔ L2
 *
 * Two jobs, and they are different jobs:
 *
 *   1. **Forward.** A frame that arrives on B is traffic from the other level.
 *      It goes out on A, unchanged and still wearing its origin's `via` badge —
 *      a relayed frame must be indistinguishable from a direct one except for the
 *      badge, because the badge is what the deck prints as "via relay" and a
 *      frame that lied about its origin would be a lie in the audit trail.
 *   2. **Survive a cut.** If A is silent, this board's own uplink goes out on B
 *      (MAIN is still reachable through the other level) and traffic arriving on
 *      A is forwarded to B. That is the whole reason the ring is a triangle: one
 *      cut degrades a route, it does not take a level off the air.
 *
 * ── What a hub never does with the ring ─────────────────────────────────────
 * It never *originates* a decision. Down-link lines are commands from the server
 * and they are handed to `level_command()`; anything else on that path is counted
 * and ignored, because an unrecognised line must not be able to change a light.
 * ============================================================================
 */
#ifdef BOARD_LEVEL_HUB

#include <Arduino.h>
#include <string.h>

#include "common/pins.h"
#include "ring.h"

/* Defined in main.cpp — the down-link decoder (see the interface block in
 * strips.cpp for why there is no shared header in this folder). */
bool level_command(const char *json);
/* Defined in main.cpp — "the ring just spoke", which clears a hold. */
void level_note_ring_alive(uint32_t now_ms);

/* ── The two ports ────────────────────────────────────────────────────────── */

#define NS_PORT_A 0
#define NS_PORT_B 1

static HardwareSerial s_uart_a(2);      /* UART2 — 17/16, MAIN connection */
static HardwareSerial s_uart_b(1);      /* UART1 —  4/13, OTHER LEVEL HUB connection */

static ns_ring_line_t s_line[2];
static ns_ring_link_t s_link[2];
static ns_ring_fault_t s_fault[2];        /* §H5.5 — one per segment          */

static const char *s_via = (LEVEL == 2) ? "lvl2" : "lvl1";

/*: The two segments this board can see, named the way the fault frame names
 *  them (§H5.5): the MAIN leg by both ends, the cross-link as L1-L2 regardless
 *  of which end is reporting. A segment has one name, not one per observer —
 *  otherwise the dashboard shows two incidents where there is one. */
static const char *s_seg[2];              /* filled in ring_port_begin()      */

/*: The direction whose loss this board can detect. Port A: MAIN→this board (we
 *  stop hearing MAIN). Port B on L1: L2→L1, which is wire **W4** — the one the
 *  service-panel switch is soldered into (§H5.4). */
static const char *s_dir[2];

/*: What the rest of the system should do about it. A dead cross-link is
 *  survivable by rerouting (that is the triangle's whole point); a dead MAIN leg
 *  is not reroutable, so the honest directive is the hold (§H6.6). */
static const char *s_reroute[2];

/*: Uplink preference. 0 = automatic (A while it is up), 1 = force the direct
 *  MAIN leg, 2 = force the cross-link. Only ever set by a down-link `reroute`
 *  command, so an operator — or Phase 9's server — can pin the path during a
 *  demo instead of hoping the auto rule picks the same one twice. */
static int s_uplink_pref = 0;

static uint32_t s_forwarded = 0, s_ignored = 0, s_overflow = 0, s_uplink_reroutes = 0;

void ring_port_begin(void)
{
    /* Port A → MAIN. 115200 on both ends; the ring is not a bandwidth problem
     * (a 2 Hz frame per node is ~600 B/s against 11 kB/s of headroom). */
    s_uart_a.begin(115200, SERIAL_8N1, LEVEL_RING_A_RX, LEVEL_RING_A_TX);
    /* Port B → the other level hub. The pins are the mirror image of this
     * board's own A port on the *other* board, which is why the same two numbers
     * appear on both boards and are still correct (§H6.3 rows 3–4). */
    s_uart_b.begin(115200, SERIAL_8N1, LEVEL_RING_B_RX, LEVEL_RING_B_TX);

    s_seg[NS_PORT_A] = (LEVEL == 2) ? "MAIN-L2" : "MAIN-L1";
    s_seg[NS_PORT_B] = "L1-L2";
    s_dir[NS_PORT_A] = (LEVEL == 2) ? "MAIN\u2192L2" : "MAIN\u2192L1";
    /* W4 is the L2→L1 transmit wire of hop 2 and the one the switch cuts. Only
     * L1 can detect its loss: the *receiver* of a missing wire is the board that
     * goes quiet, and W4's receiver is L1. */
    s_dir[NS_PORT_B] = "L2\u2192L1";
    s_reroute[NS_PORT_A] = "hold-last-indication";
    s_reroute[NS_PORT_B] = "direct-via-MAIN";

    ns_ring_line_init(&s_line[NS_PORT_A]);
    ns_ring_line_init(&s_line[NS_PORT_B]);
    ns_ring_link_init(&s_link[NS_PORT_A]);
    ns_ring_link_init(&s_link[NS_PORT_B]);
    ns_ring_fault_init(&s_fault[NS_PORT_A]);
    ns_ring_fault_init(&s_fault[NS_PORT_B]);

    /* Outbound only; we do not print to the ring, the ring *is* the link. */
    s_uart_a.setTimeout(0);
    s_uart_b.setTimeout(0);

    Serial.printf("[level %d hub] ring up · A=UART2(%u/%u) B=UART1(%u/%u) via=%s\n",
                  LEVEL, (unsigned)LEVEL_RING_A_TX, (unsigned)LEVEL_RING_A_RX,
                  (unsigned)LEVEL_RING_B_TX, (unsigned)LEVEL_RING_B_RX, s_via);
}

/* ── Uplink ───────────────────────────────────────────────────────────────── */

/** Which of the two live links should carry this board's own uplink?
 *
 *  Preference: A (the direct path to MAIN) while it is up; B when A is silent.
 *  If neither has ever spoken, B is chosen — the port that is *more likely* to
 *  have a live listener at cold boot is the one already wired to MAIN, but a
 *  board that has heard nothing on A is a board whose A link is exactly what is
 *  in doubt, and B is the other way round the triangle. */
static int ns_uplink_port(void)
{
    uint32_t now = millis();

    if (s_uplink_pref == 2) {
        /* Pinned to the cross-link. Honoured even when A looks up: the whole
         * reason to pin it is to *prove* the other path carries traffic, and a
         * "preference" that silently ignored the operator would make that proof
         * impossible. A pin is an instruction, not a hint. */
        if (ns_ring_link_up(&s_link[NS_PORT_B], now)) return NS_PORT_B;
        s_uplink_reroutes++;
        return NS_PORT_A;
    }
    if (s_uplink_pref == 1 && ns_ring_link_up(&s_link[NS_PORT_A], now)) {
        return NS_PORT_A;
    }

    if (ns_ring_link_up(&s_link[NS_PORT_A], now)) return NS_PORT_A;
    if (ns_ring_link_up(&s_link[NS_PORT_B], now)) return NS_PORT_B;
    return NS_PORT_B;
}

/** Pin the uplink path (0 auto · 1 direct-via-MAIN · 2 cross-link). Called from
 *  `level_command()` when the down-link says `{"cmd":"reroute","via":...}`. */
void ring_port_set_uplink_pref(int pref)
{
    if (pref < 0 || pref > 2) return;
    s_uplink_pref = pref;
    s_uplink_reroutes++;
    Serial.printf("[level %d hub] uplink pinned: %s\n", LEVEL,
                  pref == 1 ? "direct-via-MAIN" : pref == 2 ? "cross-link" : "automatic");
}

void ring_port_send_uplink(const char *line)
{
    if (!line || !*line) return;

    int port = ns_uplink_port();
    HardwareSerial *uart = (port == NS_PORT_A) ? &s_uart_a : &s_uart_b;

    if (port == NS_PORT_B && ns_ring_link_up(&s_link[NS_PORT_A], millis())) {
        /* unreachable by construction — kept as a tripwire for a future edit */
        s_uplink_reroutes++;
    } else if (port == NS_PORT_B) {
        s_uplink_reroutes++;
    }

    uart->print(line);
    uart->print('\n');
    ns_ring_mark_tx(&s_link[port], millis());
}

/** Forward an envelope that arrived on `from` out of the other port. */
static void ns_forward(int from, const char *envelope)
{
    int to = (from == NS_PORT_A) ? NS_PORT_B : NS_PORT_A;

    /* Forward to the *direct* path when it is up; otherwise out of the same port
     * it came in on is pointless, so the surviving leg carries it. */
    if (to == NS_PORT_A && !ns_ring_link_up(&s_link[NS_PORT_A], millis()) &&
        ns_ring_link_up(&s_link[NS_PORT_B], millis())) {
        to = NS_PORT_B;
    } else if (to == NS_PORT_B && !ns_ring_link_up(&s_link[NS_PORT_B], millis()) &&
               ns_ring_link_up(&s_link[NS_PORT_A], millis())) {
        to = NS_PORT_A;
    }

    HardwareSerial *uart = (to == NS_PORT_A) ? &s_uart_a : &s_uart_b;
    uart->print(envelope);
    uart->print('\n');
    ns_ring_mark_tx(&s_link[to], millis());
    s_forwarded++;
}

/* ── Receive ──────────────────────────────────────────────────────────────── */

static void ns_handle_line(int port, const char *line, uint32_t now_ms)
{
    /* Same clock the fault edge uses. A later millis() makes last_rx look
     * like it is in the future, so every received line opens a fault. */
    ns_ring_mark_rx(&s_link[port], now_ms);
    level_note_ring_alive(millis());

    if (ns_ring_is_downlink(line)) {
        /* Ours to act on — or, if it addresses the other level, to *forward*.
         * The distinction is made by the decoder, which knows its own junction
         * names; this function only guarantees that a command is never both
         * acted on and forwarded twice. */
        const char *json = ns_ring_downlink_json(line);
        if (json) {
            if (!level_command(json)) {
                ns_forward(port, line);
            }
        }
        return;
    }

    if (line[0] == '{' && (strstr(line, "\"frames\"") || strstr(line, "\"uid\""))) {
        /* A relay envelope, or an RFID swipe, from the other level (or, in a
         * mixed-up harness, from MAIN). Forwarded verbatim: the origin's badge
         * is what the deck prints, and rewriting it here would make the relay
         * unverifiable. An RFID line has no "frames" key, so the old check
         * counted every relayed swipe as noise and the muster board never saw it
         * once that level's direct leg was down. */
        ns_forward(port, line);
        return;
    }

    /* Anything else: counted, never acted on. During bring-up this counter is
     * the number that tells you the baud rate is wrong. */
    s_ignored++;
}

/** Emit the edge-triggered ring events (§H5.5).
 *
 *  Called from the pump, once per port, per pass. Two things happen here and both
 *  are one-shot by construction:
 *
 *    · **onset** — one `{"ring":"fault",…}` frame, upstream, and (for the
 *      L1-L2 segment, from L1, the only board that can see it) one `reroute`
 *      command down W3 telling L2 to send its traffic straight to MAIN. The
 *      controller bit is the whole difference between "the ring notices" and
 *      "the ring acts", and §H5.4's honest demo line is about exactly that.
 *    · **restore** — one `segment restored` frame, and the cross-link is handed
 *      back to the automatic rule.
 *
 *  The fault counter only ever goes up on the *edge*, so a segment cut for five
 *  minutes is one event and not a hundred and fifty. That was the acceptance
 *  criterion; the flap counter is what turns a marginal joint into a diagnosis
 *  instead of a mystery.
 */
static void ns_report_fault(int port, uint32_t now_ms)
{
    int edge = ns_ring_fault_update(&s_fault[port], &s_link[port], now_ms);
    if (edge == 0) return;

    char frame[256];
    if (edge > 0) {
        size_t n = ns_ring_build_fault(frame, sizeof(frame), s_via, s_seg[port],
                                       s_dir[port], s_reroute[port],
                                       s_fault[port].faults);
        if (n) {
            /* Upstream on whichever leg is alive — the fault report must not ride
             * the segment it is reporting. */
            ring_port_send_uplink(frame);
            Serial.printf("[level %d hub] RING FAULT %s (%s) → %s · report #%lu\n",
                          LEVEL, s_seg[port], s_dir[port], s_reroute[port],
                          (unsigned long)s_fault[port].faults);
        }

        /* L1 is the only board that observes W4, so it is the only board that can
         * tell L2 its uplink has gone. `via` names the path L2 must use. */
        if (port == NS_PORT_B && LEVEL == 1 && ns_ring_link_up(&s_link[NS_PORT_B], now_ms)) {
            char cmd[96];
            snprintf(cmd, sizeof(cmd),
                     "#CMD {\"cmd\":\"reroute\",\"via\":\"MAIN\",\"seg\":\"%s\"}",
                     s_seg[port]);
            s_uart_b.print(cmd);
            s_uart_b.print('\n');
            ns_ring_mark_tx(&s_link[NS_PORT_B], now_ms);
        }
    } else {
        size_t n = ns_ring_build_restored(frame, sizeof(frame), s_via, s_seg[port],
                                          s_fault[port].down_ms);
        if (n) {
            ring_port_send_uplink(frame);
            Serial.printf("[level %d hub] segment restored %s after %lu ms\n",
                          LEVEL, s_seg[port], (unsigned long)s_fault[port].down_ms);
        }
        if (port == NS_PORT_B && LEVEL == 1) {
            char cmd[96];
            snprintf(cmd, sizeof(cmd),
                     "#CMD {\"cmd\":\"reroute\",\"via\":\"L1\",\"seg\":\"%s\"}",
                     s_seg[port]);
            s_uart_b.print(cmd);
            s_uart_b.print('\n');
            ns_ring_mark_tx(&s_link[NS_PORT_B], now_ms);
        }
    }
}

void ring_port_pump(uint32_t now_ms)
{
    for (int port = 0; port < 2; port++) {
        HardwareSerial *uart = (port == NS_PORT_A) ? &s_uart_a : &s_uart_b;

        /* Bounded per pass, so a burst on one port cannot starve the other or the
         * strip refresh. 64 bytes at 115200 is ~5.5 ms of traffic per second;
         * draining 256 bytes per loop leaves the bus idle almost always. */
        int budget = 256;
        while (budget-- > 0 && uart->available() > 0) {
            char c = (char)uart->read();
            if (ns_ring_line_feed(&s_line[port], c)) {
                ns_handle_line(port, s_line[port].buf, now_ms);
            } else if (s_line[port].overflow) {
                s_overflow++;
                s_line[port].overflow = false;    /* already dropped by the feeder */
                Serial.printf("[level %d hub] ring line over %d bytes on port %c — dropped\n",
                              LEVEL, NS_RING_LINE_MAX, port == NS_PORT_A ? 'A' : 'B');
            }
        }

        /* The heartbeat. Per link, every 2.0 s, whether or not anything is
         * flowing: a link that is quiet because nothing happened and a link that
         * is dead must not look the same to the far end. */
        if (ns_ring_heartbeat_due(&s_link[port], now_ms)) {
            char hb[192];
            size_t n = ns_ring_build_heartbeat(hb, sizeof(hb), s_via,
                                               ++s_link[port].seq,
                                               port == NS_PORT_A ? "A" : "B");
            if (n) {
                uart->print(hb);
                uart->print('\n');
                ns_ring_mark_tx(&s_link[port], now_ms);
            }
        }

        ns_report_fault(port, now_ms);
    }
}

/** Is the path to MAIN up? Read by main.cpp to decide whether to hold. */
bool ring_port_uplink_up(void)
{
    return ns_ring_link_up(&s_link[NS_PORT_A], millis());
}

void ring_port_status_line(void)
{
    uint32_t now = millis();
    Serial.printf("[level %d hub] ring A %s (rx %lu tx %lu) · B %s (rx %lu tx %lu) · "
                  "fwd %lu ignored %lu overflow %lu reroutes %lu · faults %lu/%lu\n",
                  LEVEL,
                  ns_ring_link_up(&s_link[NS_PORT_A], now) ? "up  " : "DOWN",
                  (unsigned long)s_link[NS_PORT_A].rx_lines,
                  (unsigned long)s_link[NS_PORT_A].tx_lines,
                  ns_ring_link_up(&s_link[NS_PORT_B], now) ? "up  " : "DOWN",
                  (unsigned long)s_link[NS_PORT_B].rx_lines,
                  (unsigned long)s_link[NS_PORT_B].tx_lines,
                  (unsigned long)s_forwarded, (unsigned long)s_ignored,
                  (unsigned long)s_overflow, (unsigned long)s_uplink_reroutes,
                  (unsigned long)s_fault[NS_PORT_A].faults,
                  (unsigned long)s_fault[NS_PORT_B].faults);
}

#endif  /* BOARD_LEVEL_HUB */
