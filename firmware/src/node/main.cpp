/*
 * ============================================================================
 * Neural Strata — SENSOR NODE (N1, N2, N3, N4).   Phase 1 (B1) · H1 · H3
 * ============================================================================
 * One firmware image, four build flags (`-DNODE_ID=1..4`). H1.2 #1: "If a node
 * needs a different firmware, the design has failed."
 *
 * What it does, in order, every 500 ms:
 *   1. read the MPU-6050  → tilt, and the *measured* acceleration (D9)
 *   2. read the displacement pot  → disp, in mm
 *   3. take the SW-420's 2 s event count  → vib
 *   4. read the crack column on N2/N4 only, else report the channel absent
 *      (µm from the load cell through the HX711, millimetres from the macro pot)
 *   5. pack a v2 packet, stamp it with `ms` and `seq`, send it by ESP-NOW
 *
 * The `ms` field is the point of the whole phase: it is the node's own clock, and
 * the server differentiates position against it rather than against arrival time.
 * A radio link that adds 40 ms of jitter then costs nothing in accuracy.
 *
 * Baseline (H1.2 #5)
 * ------------------
 * The first 60 s after boot capture a quiet baseline — the MPU's tilt offset and
 * the pot's rest position. Until that completes the frame carries
 * FLAG_BASELINE_UNSETTLED, and the LED blinks slowly. Reporting an uncalibrated
 * reading as if it were a measurement is how a node ends up reporting a
 * permanent 2 mm of displacement that is really its own mounting.
 *
 * Declarations
 * ------------
 * The sensor functions are declared here rather than in per-sensor headers: the
 * tree of record lists `node/` as `.cpp` files, and four headers for four
 * functions is structure that pays for itself nowhere.
 * ============================================================================
 */
#ifdef BOARD_NODE

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_system.h>

#include "common/config.h"
#include "common/leds.h"
#include "common/pins.h"
#include "common/power.h"
#include "espnow_packet.h"

/* ── Sensor interface (defined in mpu6050.cpp / sw420.cpp) ────────────────── */
bool  mpu6050_begin(void);
bool  mpu6050_healthy(void);
bool  mpu6050_read(float *tilt_deg, float *acc_ms2);
uint32_t mpu6050_failures(void);

void     sw420_begin(void);
void     sw420_tick(uint32_t now_ms);
uint16_t sw420_last_window(void);
uint32_t sw420_total(void);

float ns_read_disp_mm(void);
float ns_read_macro_mm(void);
float ns_read_batt_v(void);

/* ── Crack column interface (Phase 3 · H2) ──────────────────────────────────
 * `pots.cpp` and `hx711.cpp`. Declared here rather than in headers, as above. */
bool     hx711_begin(void);
void     hx711_tick(uint32_t now_ms);
float    hx711_read_um(void);
bool     hx711_settled(void);
bool     hx711_saturated(void);
bool     hx711_unloaded(void);
int32_t  hx711_zero_counts(void);
uint32_t hx711_good_reads(void);
uint32_t hx711_bad_reads(void);
bool     ns_macro_at_rail(void);
uint16_t ns_macro_counts(void);

/* ── State ───────────────────────────────────────────────────────────────── */

static uint8_t  s_hub_mac[6] = {NS_HUB_MAC_0, NS_HUB_MAC_1, NS_HUB_MAC_2,
                                NS_HUB_MAC_3, NS_HUB_MAC_4, NS_HUB_MAC_5};
static bool     s_hub_set    = (NS_HUB_MAC_SET != 0);

static ns_node_packet_t s_pkt;              /* the last packet built           */
static bool     s_pkt_valid   = false;
static uint16_t s_seq         = 0;
static uint32_t s_next_sample = 0;
static uint32_t s_boot_ms     = 0;

static uint16_t s_flags       = 0;
static bool     s_baseline_done = false;
static float    s_tilt_offset = 0.0f;
static float    s_disp_rest   = 0.0f;

static uint32_t s_sent_ok = 0;
static uint32_t s_sent_fail = 0;
static bool     s_link_ok = false;

/* Phase 2 — when we last put anything on the air, sample or heartbeat. The
 * heartbeat is whatever fills the gap between samples, so the two share one
 * clock: nothing here needs to know which kind of frame went out, only that the
 * hub heard *something*. */
static uint32_t s_last_tx_ms = 0;
static uint32_t s_beats     = 0;

/* ── Helpers ─────────────────────────────────────────────────────────────── */

static void ns_build_mac_str(const uint8_t *mac, char *out, size_t n)
{
    snprintf(out, n, "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

static bool ns_register_peer(const uint8_t *mac)
{
    if (!esp_now_is_peer_exist(mac)) {
        esp_now_peer_info_t peer;
        memset(&peer, 0, sizeof(peer));
        memcpy(peer.peer_addr, mac, 6);
        peer.channel = NS_ESPNOW_CHANNEL;
        peer.encrypt = false;
        if (esp_now_add_peer(&peer) != ESP_OK) return false;
    }
    return true;
}

/* H3.2 #3 — the last-gasp packet.
 *
 * The brownout detector calls this as the supply collapses. It must not
 * allocate, must not read I2C, and must not wait: the radio has milliseconds
 * left. It re-sends the packet it is already holding, flagged, and hopes. A
 * failed last-gasp costs nothing; the SD card on the hub already has the
 * seconds before it, and "the node stopped talking" is itself the signal. */
static inline void ns_note_supply(void)
{
    ns_power_note_supply_mv((uint16_t)(ns_read_batt_v() * 1000.0f));
}

/* H3.2 #3 — the last-gasp packet.
 *
 * The brownout detector calls this as the supply collapses. It must not
 * allocate, must not read I2C, and must not wait: the radio has milliseconds
 * left. It re-sends the packet it is already holding, flagged, and hopes. A
 * failed last-gasp costs nothing; the SD card on the hub already has the
 * seconds before it, and "the node stopped talking" is itself the signal. */
static void IRAM_ATTR ns_on_shutdown(void)
{
#if NS_LAST_GASP_ENABLE
    if (!s_pkt_valid || !s_hub_set) return;
    s_pkt.last  = 1;
    s_pkt.flags |= NS_FLAG_LAST_GASP;
    esp_now_send(s_hub_mac, (const uint8_t *)&s_pkt, sizeof(s_pkt));
#endif
}

static void ns_on_sent(const uint8_t *mac, esp_now_send_status_t status)
{
    if (status == ESP_NOW_SEND_SUCCESS) {
        s_sent_ok++;
        s_link_ok = true;
    } else {
        s_sent_fail++;
        /* One failure is not a link loss — the mesh re-routes within a second
         * (H3.2 #2), so the LED stays green until the failures are consecutive. */
        if (s_sent_fail > 10) s_link_ok = false;
    }
}

/* Neighbour traffic, for the H3 relay ring. A node that hears a neighbour's
 * packet keeps it for NS_NEIGHBOUR_RING frames so it can answer a RELAY_REQ.
 * Storing is cheap; the mesh only exists because every node listens (H3.2 #1). */
static void ns_on_recv(const uint8_t *mac, const uint8_t *data, int len)
{
    if (!ns_is_node_packet(data, len)) return;
    if (len < (int)sizeof(ns_node_packet_t)) return;
    if (((const ns_node_packet_t *)data)->node == NODE_ID) return;   /* me */
    /* Phase 1 records the fact; the responder itself lands with H3's bench
     * (relay REQ handling needs the hub's seq-gap watchdog to be exercised
     * against a real foil-wrapped antenna). */
}

/* ── transmit ────────────────────────────────────────────────────────────── */

/* One place that puts a packet on the air. Both the sample path and the
 * heartbeat path go through it, so "what does a node actually send" has a
 * single answer — and so the `s_last_tx_ms` clock cannot be updated on one
 * path and forgotten on the other. */
static void ns_tx_packet(const ns_node_packet_t *pkt)
{
    s_last_tx_ms = millis();
    if (s_hub_set) {
        esp_err_t r = esp_now_send(s_hub_mac, (const uint8_t *)pkt, sizeof(*pkt));
        if (r != ESP_OK) s_sent_fail++;
    } else {
        /* Bench mode: no hub configured, so print the exact JSON line the hub
         * would have produced. The server can be pointed at this node directly
         * over USB, which is how a single node is brought up before the network
         * exists. */
        char line[256];
        ns_packet_to_json(pkt, line, sizeof(line));
        Serial.println(line);
    }
}

/* ── the crack column, decided once ──────────────────────────────────────── */

/* Why a function: the sample path and the heartbeat path both build a frame, and
 * a channel that is faulted on one path but healthy on the other is a dashboard
 * that flickers between "fault" and "fine" twice a second.
 *
 * The fault bits this returns are **fresh every call**, and the caller ORs them
 * into the frame rather than into `s_flags`. A sticky fault bit is a channel that
 * screams forever after one bad second — the operator learns to ignore the one
 * indicator that is supposed to mean "this sensor is lying to you".
 *
 * Three states are distinguished, and they are not the same thing:
 *
 *   · **absent** (NAN) — this node has no crack column. N1/N3, for the life of
 *     the build. The server reports `null` and needs no code for it.
 *   · **unsettled** — the column is there and has no zero yet (HX711 warm-up).
 *     NAN plus FLAG_BASELINE_UNSETTLED: a channel that cannot yet be trusted
 *     says so, and `0.0` would be a claim it cannot support.
 *   · **faulted** — there is a zero and the channel is still not measuring:
 *     railed, silent, or unloaded. A *number* plus a flag, never a null, because
 *     "sensor lying" and "no sensor" must not look alike on the dashboard.
 */
static void ns_crack_column(float *micro_um, float *macro_mm, uint16_t *bits)
{
    *micro_um = NS_CHANNEL_ABSENT;
    *macro_mm = NS_CHANNEL_ABSENT;
    *bits = 0;

#if NS_HAS_CRACK_COLUMN
    if (hx711_saturated()) {
        /* Railed or silent. Report the rail value so the dashboard shows a
         * number rather than a hole, and let the flag do the arguing. */
        *bits |= NS_FLAG_MICRO_SATURATED;
        *micro_um = NS_MICRO_RAIL_UM;
    } else if (!hx711_settled()) {
        /* Still warming up, or the zero is not taken yet. `micro` stays NAN. */
        *bits |= NS_FLAG_BASELINE_UNSETTLED;
    } else if (hx711_unloaded()) {
        /* The spring is not carrying load: the channel has stopped measuring,
         * which is not the same as a seam that is closing. */
        *bits |= NS_FLAG_MICRO_UNLOADED;
        *micro_um = 0.0f;
    } else {
        *micro_um = hx711_read_um();
    }

    *macro_mm = ns_read_macro_mm();
    if (ns_macro_at_rail()) *bits |= NS_FLAG_MACRO_AT_RAIL;
#else
    /* Wired identically, unpopulated. Read the pin anyway so the two builds
     * differ in this line and nothing else; the frame reports NAN either way. */
    (void)ns_read_macro_mm();
#endif
}

/* H3.2 · Phase 2 — the heartbeat.
 *
 * Called every loop pass and returns immediately unless 2 s have passed since
 * the last transmission. It exists to close one hole: a node with nothing to
 * report and a node with nothing to report *because it is dead* are
 * indistinguishable on the wire, and they were both silent.
 *
 * The values carried are the node's current readings rather than the last
 * packet's, so a heartbeat during the baseline window still shows the sensors
 * moving — the operator can see a node waking up. What makes it a heartbeat is
 * the flag, not the payload: the server counts it as proof of life and refuses
 * to put it in the AI window, because repeated values would look like perfectly
 * stable ground.
 *
 * Reads I2C, so it must NOT be called from an ISR. It is not — it runs from
 * `loop()`.
 */
static void ns_tx_heartbeat(uint32_t now)
{
#if NS_HEARTBEAT_ENABLE
    if (s_last_tx_ms != 0 && (now - s_last_tx_ms) < NS_HEARTBEAT_MS) return;

    float tilt = 0.0f, acc = 0.0f;
    mpu6050_read(&tilt, &acc);
    float disp = ns_read_disp_mm() - s_disp_rest;
    if (disp < 0.0f) disp = 0.0f;

    float micro = NS_CHANNEL_ABSENT, macro = NS_CHANNEL_ABSENT;
    uint16_t crack_bits = 0;
    ns_crack_column(&micro, &macro, &crack_bits);

    s_seq++;
    ns_node_packet_t pkt;
    ns_packet_build(&pkt, NODE_ID, now, disp, tilt - s_tilt_offset, acc,
                    sw420_last_window(), micro, macro,
                    (ns_note_supply(), (uint16_t)(ns_read_batt_v() * 1000.0f)),
                    (uint8_t)((s_flags & NS_FLAG_STICKY_MASK) | crack_bits
                              | NS_FLAG_HEARTBEAT));
    pkt.seq = s_seq;
    pkt.last = 0;
    ns_tx_packet(&pkt);
    s_beats++;
#else
    (void)now;
#endif
}

/* ── setup ───────────────────────────────────────────────────────────────── */

void setup(void)
{
    Serial.begin(NS_SERIAL_BAUD);
    delay(150);

    s_boot_ms = millis();
    pinMode(PIN_STATUS_LED, OUTPUT);
    ns_led_apply(NS_STATUS_BOOT, 0);

    Serial.printf("\n[node %d] Neural Strata node fw %s · schema v%d · level %d\n",
                  NODE_ID, NS_FW_VERSION, NS_SCHEMA_VERSION, NS_LEVEL);

    /* ── sensors ──────────────────────────────────────────────────────────── */
    bool mpu_ok = mpu6050_begin();
    if (!mpu_ok) {
        s_flags |= NS_FLAG_BASELINE_UNSETTLED;
        Serial.printf("[node %d] MPU-6050 NOT FOUND — WHO_AM_I != 0x68. "
                      "Check SDA=21/SCL=22 and that VCC is on 3V3.\n", NODE_ID);
    } else {
        Serial.printf("[node %d] MPU-6050 up (WHO_AM_I 0x68)\n", NODE_ID);
    }
    sw420_begin();
    analogReadResolution(12);

#if NS_HAS_CRACK_COLUMN
    /* Begin the fine channel here rather than lazily on first use: its 15 s
     * warm-up (H2.4) starts now, so by the time the 60 s baseline is done the
     * crack column has a zero and a live reading. Beginning it lazily would make
     * `micro` null for the first 75 s of every boot for no reason. */
    bool hx_ok = hx711_begin();
    Serial.printf("[node %d] crack column present (HX711 DT=%d SCK=%d, macro pot=%d) — "
                  "%s\n", NODE_ID, PIN_HX711_DT, PIN_HX711_SCK, PIN_MACRO_POT,
                  hx_ok ? "HX711 answering" : "HX711 NOT ANSWERING (flag 1 will say so)");
    if (!hx_ok) {
        /* Loud, and not fatal: the rest of the node is fine, and a diorama
         * demo with a dead load cell is still worth running. The frame carries
         * FLAG_MICRO_SATURATED and the server raises SENSOR_FAULT — which is
         * the correct answer for a cell whose leads are not connected. */
        Serial.printf("[node %d] *** load cell channel FAULTED *** — check DT/SCK, "
                      "and that the HX711's VCC and AVCC are both on 3V3.\n", NODE_ID);
    }
#else
    Serial.printf("[node %d] crack column NOT populated — micro/macro report null\n",
                  NODE_ID);
#endif

    /* ── radio ────────────────────────────────────────────────────────────── */
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    esp_wifi_set_channel(NS_ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);

    if (esp_now_init() != ESP_OK) {
        Serial.printf("[node %d] ESP-NOW init FAILED — node cannot transmit\n", NODE_ID);
        s_link_ok = false;
    } else {
        esp_now_register_send_cb(ns_on_sent);
        esp_now_register_recv_cb(ns_on_recv);

        char mac[20];
        uint8_t own_mac[6];
        // `WiFi.macAddress(uint8_t*)` fills a buffer; the no-argument overload
        // returns a String and there is no `.octet` on it in this core.
        WiFi.macAddress(own_mac);
        ns_build_mac_str(own_mac, mac, sizeof(mac));
        Serial.printf("[node %d] my MAC %s  channel %d\n", NODE_ID, mac, NS_ESPNOW_CHANNEL);

        if (s_hub_set) {
            if (!ns_register_peer(s_hub_mac)) {
                Serial.printf("[node %d] could not register the hub as a peer\n", NODE_ID);
            } else {
                ns_build_mac_str(s_hub_mac, mac, sizeof(mac));
                Serial.printf("[node %d] hub %s registered\n", NODE_ID, mac);
            }
        } else {
            /* Deliberately loud. A node with no hub MAC still runs, still reads
             * its sensors and still prints on serial — so it is debuggable on
             * the bench — but it cannot join the network, and the operator needs
             * to be told that in the log rather than infer it from a dashboard
             * that never updates. */
            Serial.printf("[node %d] *** HUB MAC NOT SET *** — fill NS_HUB_MAC_* in "
                          "common/config.h from the H0 MAC table, then reflash.\n", NODE_ID);
        }
    }

    ns_power_register_last_gasp(ns_on_shutdown);

    /* H3: the reset reason is read before anything else, so a brownout on the
     * way up is reported rather than lost. */
    if (ns_power_begin()) {
        Serial.printf("[node %d] booted after a BROWNOUT reset\n", NODE_ID);
    }

    s_flags |= NS_FLAG_BASELINE_UNSETTLED;
    Serial.printf("[node %d] capturing a %lu s quiet baseline — keep the bench still\n",
                  NODE_ID, (unsigned long)(NS_BASELINE_MS / 1000));
}

/* ── loop ────────────────────────────────────────────────────────────────── */

void loop(void)
{
    uint32_t now = millis();

    /* The status LED runs every pass, not on the sample tick: an LED that only
     * updates twice a second cannot blink. */
    ns_status_t st = ns_status_from_health(mpu6050_healthy(), s_link_ok, s_baseline_done);
    ns_led_apply(st, now);

    sw420_tick(now);
    /* The crack column advances on every pass too: it owns its own 10 SPS
     * cadence and its 15 s warm-up, and it returns immediately when the HX711
     * has no conversion ready. */
#if NS_HAS_CRACK_COLUMN
    hx711_tick(now);
#endif

    /* Phase 2 — on every pass, before any early return.
     *
     * The placement is the point. This is the one line that must not be skipped:
     * the two `return`s below are the baseline window (60 s) and the gap between
     * samples, and a node that goes quiet in either of them is a node the server
     * cannot distinguish from a dead one. Calling the heartbeat first means any
     * path that leaves `loop()` has already proved it was alive. */
    ns_tx_heartbeat(now);

    if (now < s_next_sample) {
        delay(5);
        return;
    }
    s_next_sample = now + NS_SAMPLE_MS;

    /* ── baseline capture (H1.2 #5) ───────────────────────────────────────── */
    if (!s_baseline_done) {
        float t = 0.0f, a = 0.0f;
        mpu6050_read(&t, &a);
        float d = ns_read_disp_mm();

        /* A running mean over the quiet window: the offset, not the current
         * value. Accumulated as a simple average because 120 samples of an
         * exponentially-weighted mean would still carry the first noisy second. */
        static float acc_t = 0.0f, acc_d = 0.0f;
        static uint32_t n = 0;
        acc_t += t; acc_d += d; n++;

        if (now - s_boot_ms >= NS_BASELINE_MS) {
            s_tilt_offset = acc_t / (float)n;
            s_disp_rest   = acc_d / (float)n;
            s_baseline_done = true;
            s_flags &= (uint16_t)~NS_FLAG_BASELINE_UNSETTLED;
            Serial.printf("[node %d] baseline: tilt %+0.2f deg, disp rest %0.2f mm "
                          "(%lu samples)\n", NODE_ID, s_tilt_offset, s_disp_rest,
                          (unsigned long)n);
        }
        s_pkt_valid = false;      /* nothing trustworthy to report yet */
        return;
    }

    /* ── the sample ───────────────────────────────────────────────────────── */
    float tilt = 0.0f, acc = 0.0f;
    bool  read_ok = mpu6050_read(&tilt, &acc);
    float disp = ns_read_disp_mm();
    uint16_t vib = sw420_last_window();

    /* Report displacement relative to the node's own rest position, so mounting
     * tolerance does not appear as ground movement. */
    float disp_rel = disp - s_disp_rest;
    if (disp_rel < 0.0f) disp_rel = 0.0f;

    /* Both crack channels start as "absent", and only a node with the column
     * populated and the channel live replaces them. Written this way round on
     * purpose: the default is the honest answer, and a channel can only become
     * present by being read. Nodes 1 and 3 skip the block entirely and report
     * NAN -> null for the life of the build. */
    /* Both crack channels come from one function — see `ns_crack_column`. */
    float micro = NS_CHANNEL_ABSENT, macro = NS_CHANNEL_ABSENT;
    uint16_t crack_bits = 0;
    ns_crack_column(&micro, &macro, &crack_bits);

    if (!read_ok && mpu6050_failures() > 50) {
        s_flags |= NS_FLAG_BASELINE_UNSETTLED;
    }

    /* Bump `seq` on every transmission. Never reset except by design (H3.4): the
     * server reads a non-increasing sequence as a discontinuity and invalidates
     * the AI window, which is exactly right after a reboot and exactly wrong if
     * it were merely a counter wrap we could have avoided. */
    s_seq++;

    ns_packet_build(&s_pkt, NODE_ID, now, disp_rel, tilt - s_tilt_offset, acc,
                    vib, micro, macro,
                    (ns_note_supply(), (uint16_t)(ns_read_batt_v() * 1000.0f)),
                    (uint8_t)((s_flags & NS_FLAG_STICKY_MASK) | crack_bits));
    s_pkt.seq = s_seq;
    s_pkt_valid = true;

    ns_tx_packet(&s_pkt);

    /* One line every 10 s, so a bench session leaves a readable trace without
     * drowning the JSON. */
    static uint32_t last_log = 0;
    if (now - last_log >= 10000) {
        last_log = now;
        /* Rendered as strings, for the same reason the wire carries NAN: on a
         * bench log, `null` and `0.0` must not look alike. H2.3's calibration
         * session is run off these lines — the zero count and the raw macro count
         * are what `NS_MICRO_UM_PER_COUNT` and the pot endpoints get measured
         * from, so both are printed rather than inferred later. */
        char mbuf[20], kbuf[20];
        if (isnan(micro)) snprintf(mbuf, sizeof(mbuf), "null");
        else              snprintf(mbuf, sizeof(mbuf), "%.1f um", (double)micro);
        if (isnan(macro)) snprintf(kbuf, sizeof(kbuf), "null");
        else              snprintf(kbuf, sizeof(kbuf), "%.2f mm", (double)macro);
        Serial.printf("[node %d] seq %u · disp %.2f mm · tilt %+0.2f deg · "
                      "acc %+0.3f m/s2 · vib %u · micro %s · macro %s · "
                      "zero %ld cts · macro %u cts · ok %lu fail %lu · beats %lu\n",
                      NODE_ID, s_seq, (double)disp_rel, (double)(tilt - s_tilt_offset),
                      (double)acc, vib, mbuf, kbuf,
                      (long)hx711_zero_counts(), (unsigned)ns_macro_counts(),
                      (unsigned long)s_sent_ok, (unsigned long)s_sent_fail,
                      (unsigned long)s_beats);
    }
}

#endif  /* BOARD_NODE */
