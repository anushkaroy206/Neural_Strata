/*
 * ============================================================================
 * Neural Strata — LEVEL HUB · rc522.cpp
 *   tag read → beep → gate lamps → the server
 * ============================================================================
 * The reader exists so that "everyone is accounted for" stops being a sentence
 * someone says and becomes a row in `rfid_evacuation_log` with a timestamp. That
 * is the only reason it is on the paying-for-it list, and it is why the read is
 * sent **immediately** and not batched: a muster board that updates every 30
 * seconds is a muster board nobody trusts.
 *
 * Wiring (§H6.3 rows 5–12) — GPIO18 SCK · 19 MISO · 23 MOSI · 5 SDA/SS · 27 RST,
 * power on **3V3 only**. A 5 V module dies on first contact; a 3.3 V module on
 * 5 V dies on second.
 *
 * Three details that are all "the reader that reads one tag and then hangs":
 *
 *   1. **SPI at 4 MHz or slower.** The usual cause of dropped and duplicated
 *      tags is an SPI clock the module cannot keep up with on 80 mm of 22 AWG.
 *   2. **An explicit RST pin.** A floating reset is a reader that initialises
 *      once, if you are lucky.
 *   3. **A per-UID debounce of 3 s.** One swipe, held near the antenna, produces
 *      a read every ~100 ms; without the debounce the log fills with ten rows for
 *      one miner and the muster count becomes a count of antenna time.
 *
 * The RC522 is fed from the hub's 3V3 pin; the decoupling H6.2 asks for (100 nF
 * + 10 µF at the module) is a soldering instruction, not a firmware one, and it
 * is recorded here because this is the file someone reads while wiring it.
 * ============================================================================
 */
#ifdef BOARD_LEVEL_HUB

#include <Arduino.h>
#include <SPI.h>
#include <string.h>

#include <MFRC522.h>

#include "common/pins.h"

/* Defined in strips.cpp — a short confirmation beep, then the gate lamps blink.
 * Declared here per the interface block at the top of strips.cpp: the §2 tree
 * gives this folder four files and no shared header, so the interface lives in
 * the comment and the declarations live at the point of use. */
void strips_tag_beep(void);

/* ── Reader ───────────────────────────────────────────────────────────────── */

static MFRC522 s_reader(PIN_RC522_SDA, PIN_RC522_RST);

#define NS_RFID_DEBOUNCE_MS   3000
#define NS_RFID_UID_MAX          7      /* MIFARE Classic/Ultralight: 4 or 7 bytes */

static char     s_last_uid[NS_RFID_UID_MAX * 2 + 1];
static uint32_t s_last_uid_ms   = 0;
static char     s_pending_uid[NS_RFID_UID_MAX * 2 + 1];
static bool     s_pending_valid = false;
static uint32_t s_reads = 0, s_debounced = 0;
static bool     s_present = false;
static byte     s_version = 0;

static bool ns_probe_reader(bool announce);

void rc522_begin(void)
{
    SPI.begin(PIN_RC522_SCK, PIN_RC522_MISO, PIN_RC522_MOSI, PIN_RC522_SDA);

    /* `SPI.begin` on the ESP32 sets the bus clock; the module's own limit is
     * 10 MHz at 3.3 V but the harness is 80 mm of 22 AWG, so 4 MHz is the number
     * that works on a bench rather than on a datasheet. */
    SPI.setFrequency(4000000);

    s_reader.PCD_Init();
    delay(50);
    ns_probe_reader(true);
}

/** 0x00 and 0xFF are what a disconnected reader answers. Anything else is a live
 *  SPI link. A single miss at boot used to latch the reader off for the whole
 *  run, so a swipe after that probe never left the board. */
static bool ns_probe_reader(bool announce)
{
    byte version = s_reader.PCD_ReadRegister(MFRC522::VersionReg);
    s_version = version;
    bool live = (version != 0x00 && version != 0xFF);
    if (live) {
        s_reader.PCD_AntennaOn();
        s_reader.PCD_SetAntennaGain(MFRC522::RxGain_max);
    }
    if (announce || live != s_present) {
        Serial.printf("[level %d hub] RC522 %s (version 0x%02X)%s\n",
                      LEVEL, live ? "present" : "NOT RESPONDING", (unsigned)version,
                      live ? "" : " — check 3V3/GND/SPI, and never power it from 5 V");
    }
    s_present = live;
    return live;
}

static void ns_uid_to_hex(const MFRC522::Uid *uid, char *out, size_t cap)
{
    /* Uppercase hex, no separators: the same string the server stores, so the
     * muster log and the label on the fob can be compared by eye. */
    size_t n = uid->size;
    if (n > NS_RFID_UID_MAX) n = NS_RFID_UID_MAX;
    if (cap < n * 2 + 1) { if (cap) out[0] = '\0'; return; }
    for (size_t i = 0; i < n; i++) {
        snprintf(out + i * 2, 3, "%02X", uid->uidByte[i]);
    }
    out[n * 2] = '\0';
}

void rc522_poll(uint32_t now_ms)
{
    /* A failed boot probe is retried. Returning here permanently is how a
     * reader that answers a moment late looks identical to no reader at all. */
    if (!s_present) {
        static uint32_t s_next_probe_ms = 0;
        if (s_next_probe_ms && (uint32_t)(now_ms - s_next_probe_ms) < 2000) return;
        s_next_probe_ms = now_ms ? now_ms : 1;
        s_reader.PCD_Init();
        if (!ns_probe_reader(false)) return;
    }

    /* REQA (PICC_IsNewCardPresent) ignores a card already in HALT. A fob held
     * on the antenna, or one whose previous select failed, then never answers
     * again. WakeupA invites both IDLE and HALT. */
    byte atqa[2];
    byte atqa_len = sizeof(atqa);
    MFRC522::StatusCode woke = s_reader.PICC_WakeupA(atqa, &atqa_len);
    if (woke != MFRC522::STATUS_OK && woke != MFRC522::STATUS_COLLISION) return;

    bool got = s_reader.PICC_ReadCardSerial();
    char uid[NS_RFID_UID_MAX * 2 + 1];
    uid[0] = '\0';
    if (got) ns_uid_to_hex(&s_reader.uid, uid, sizeof(uid));

    /* Always, including a failed select. Leaving the crypto unit on makes every
     * later WakeupA time out — the reader that sees one card and then goes mute. */
    s_reader.PICC_HaltA();
    s_reader.PCD_StopCrypto1();
    if (!got || !uid[0]) return;

    if (s_last_uid[0] && !strcmp(uid, s_last_uid) &&
        (uint32_t)(now_ms - s_last_uid_ms) < NS_RFID_DEBOUNCE_MS) {
        s_debounced++;
        return;                     /* the same fob, still there — not a new read */
    }

    strncpy(s_last_uid, uid, sizeof(s_last_uid) - 1);
    s_last_uid[sizeof(s_last_uid) - 1] = '\0';
    s_last_uid_ms = now_ms ? now_ms : 1;

    strncpy(s_pending_uid, uid, sizeof(s_pending_uid) - 1);
    s_pending_uid[sizeof(s_pending_uid) - 1] = '\0';
    s_pending_valid = true;
    s_reads++;

    /* The miner's own feedback, before the network: the beep is the evidence that
     * the swipe registered, and it must not wait for a round trip. */
    strips_tag_beep();
    Serial.printf("[level %d hub] tag %s (%lu reads, %lu debounced)\n",
                  LEVEL, uid, (unsigned long)s_reads, (unsigned long)s_debounced);
}

/** Hand the newest read to the caller (main.cpp), once. */
bool rc522_take_tag(char *uid, size_t cap)
{
    if (!s_pending_valid || !uid || cap == 0) return false;
    strncpy(uid, s_pending_uid, cap - 1);
    uid[cap - 1] = '\0';
    s_pending_valid = false;
    return true;
}

bool rc522_present(void) { return s_present; }

uint32_t rc522_reads(void) { return s_reads; }

uint8_t rc522_version(void) { return s_version; }

#endif  /* BOARD_LEVEL_HUB */
