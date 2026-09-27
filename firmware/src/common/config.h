/*
 * ============================================================================
 * Neural Strata — firmware configuration.
 * ============================================================================
 * Node IDs, cadence, queue depth, chunk size (tree of record §2). Every tunable
 * the firmware has, in one place — the same rule the backend follows in
 * `backend/config.py`, for the same reason: a magic number in a sketch is a
 * number nobody can find when the bench disagrees with the field.
 *
 * The values here are the ones the documents specify. Where a value must be
 * *measured* rather than chosen (the pot calibration, the MAC table), it is a
 * named placeholder with a comment saying where the real number comes from —
 * never a made-up default that silently behaves.
 * ============================================================================
 */
#pragma once

#include <stdint.h>

/* ============================================================================
 * 1. Identity — one firmware, four boards
 * ==========================================================================*/

/** Set per board: `-DNODE_ID=1..4` in platformio.ini. H1.2 #1: "One firmware
 *  image, four build flags. If a node needs a different firmware, the design has
 *  failed." */
#ifndef NODE_ID
#define NODE_ID 1
#endif

#if NODE_ID < 1 || NODE_ID > 4
#error "config.h: NODE_ID must be 1..4. Four nodes, two per level."
#endif

/** Nodes 2 and 4 carry the crack column (BF350 + HX711 + macro pot). Nodes 1 and
 *  3 are wired identically but unpopulated, and transmit 0 on those channels so
 *  the packet shape never changes. §A.2.1 */
#define NS_HAS_CRACK_COLUMN (NODE_ID == 2 || NODE_ID == 4)

/** Which level this node reports to. N1/N2 → L1 (Tunnel A), N3/N4 → L2. */
#define NS_LEVEL ((NODE_ID <= 2) ? 1 : 2)

#define NS_FW_VERSION "1.0.0-p1"

/* ============================================================================
 * 2. Cadence
 * ==========================================================================*/

/** 2 Hz, the training cadence. The model was trained on 121 samples = 60 s + 1
 *  at exactly this rate; `config.MODEL_HZ` on the server is the same number from
 *  the other end of the wire. */
#define NS_SAMPLE_HZ        2
#define NS_SAMPLE_MS        (1000 / NS_SAMPLE_HZ)

/* ── Phase 2 · heartbeat ───────────────────────────────────────────────────
 * A liveness frame every 2 s regardless of what the node has to say. The
 * server's STALE_AFTER_S is 5.0 s, i.e. two missed heartbeats — long enough
 * not to flap on a lossy radio, short enough to notice during a demo.
 *
 * This is the single most important firmware change in Phase 2, and it exists
 * because of a specific hole: a node inside its 60 s baseline window used to
 * transmit nothing, and a node with a dead radio also transmits nothing. The
 * server could not tell them apart, so a node that had never booted correctly
 * looked exactly like one that was warming up. Both were "fine". */
#define NS_HEARTBEAT_ENABLE 1
#define NS_HEARTBEAT_MS     2000

/** SW-420 event window. H1.2 #4: "counts digital pulses in a 2 s window and
 *  transmits the count". The AI's `vib_count_60` is three of these. */
#define NS_VIB_WINDOW_MS    2000

/** H1.2 #5: a 60 s quiet baseline at boot, during which the MPU's tilt offset
 *  and the pot's rest position are recorded. Until it completes, the frame
 *  carries FLAG_BASELINE_UNSETTLED and the server refuses to trust the channel. */
#define NS_BASELINE_MS      60000

/** Serial console. 115200 is what the backend's SERIAL_PORT opens at. */
#define NS_SERIAL_BAUD      115200

/* ============================================================================
 * 3. Radio
 * ==========================================================================*/

/** ESP-NOW channel. All seven boards must agree; a mismatch produces a link that
 *  looks dead for no visible reason, which is a bad afternoon on a bench. */
#define NS_ESPNOW_CHANNEL   1

/** Long-range mode. Off by default — it trades bandwidth for range, and the
 *  diorama is 2 m across. Turn on only if H3's range-margin test fails. */
#define NS_ESPNOW_LR        0

/* ── The underground blackbox (Phase 10 · B13/B14 · §5.5) ─────────────────────
 *
 * The numbers below are the *contract* between the two cores, so they live in one
 * file the way the pin freeze does. Three of them are load-bearing arguments, not
 * tunables:
 *
 *  · `NS_BBX_ROWS_PER_CHUNK 1000` — a row is 8 rows/s (2 Hz × 4 nodes), so a chunk
 *    is ~2 minutes of the tunnel. That is the unit the delete gate reasons about:
 *    small enough that losing the open chunk costs seconds, large enough that the
 *    card holds hours.
 *  · `NS_BBX_QUEUE_DEPTH 256` — 24 B × 256 = 6 KB, about **32 s of stall**. Below
 *    ~100 records a single SD garbage-collection pause would start dropping rows
 *    that the card could have taken; above ~512 the queue's RAM starts competing
 *    with the radio stack for no additional protection.
 *  · `NS_BBX_FLUSH_MS 5000` — NAND flash cannot erase a line, and a flush per row
 *    wears the card out for nothing. Five seconds is the worst-case window that
 *    can be lost to a brownout, and it is the number §5.5 quotes.
 */
#define NS_BBX_ROWS_PER_CHUNK 1000
#define NS_BBX_QUEUE_DEPTH    256
#define NS_BBX_FLUSH_MS       5000
#define NS_BBX_ROW_BYTES      24     /* sizeof(ns_bbx_row_t) — static_asserted */
#define NS_BBX_ROW_HEADER     1      /* `chunk_id` is the first CSV column       */

/*: FIFO eviction. The card is a *ring*, not a warehouse: when free space falls
 *  below the low-water mark the oldest **closed** chunks go, newest-first is kept.
 *  Two chunks are always held back deliberately:
 *   · the open one, which the hub is writing to;
 *   · any chunk with drops, forever — it holds the only copy of rows that the
 *     radio lost, so it is evidence, not storage. The delete gate refuses those
 *     too; the two refusals are the same rule stated twice, which is what you
 *     want for a rule about destroying data. */
#define NS_BBX_FIFO_FREE_MB   64
#define NS_BBX_FIFO_KEEP      6

/*: 4 MHz, deliberately slow. §5.5 says start here and raise it after the bus has
 *  proved itself — a marginal SD link that corrupts one row in ten thousand is far
 *  worse than a card that writes 30 % slower, because the failure shows up in the
 *  archive weeks later. */
#define NS_BBX_SPI_HZ         4000000
#define NS_BBX_STATUS_MS      10000

/*: A chunk id is a uint16 that rolls. 9999 is the last id an operator can read
 *  out loud without ambiguity (`chunk_0042.csv`); after that the counter wraps to
 *  1 and the *oldest* file is the one being re-created, which the eviction pass
 *  has already dealt with. */
#define NS_BBX_CHUNK_MAX      9999

/* ── The row Core 1 hands Core 0 (Phase 10 · §5.5) ───────────────────────────
 *
 * Defined here rather than in either .cpp because **two translation units must
 * agree on it byte-for-byte** and this project has no headers inside `src/` — the
 * interface blocks are the headers. Two copies of this struct in two files would
 * be the same class of bug the server side keeps flushing out: two definitions of
 * one thing, drifting.
 *
 * The card's on-disk format is CSV (blackbox.cpp); this is the in-RAM handoff, and
 * it is narrow because the queue has to hold 256 of them. The quantisations are
 * finer than the sensors' own resolution (0.01°, 0.1 mm, 0.001 g), so nothing
 * measurable is lost; `micro` saturates at 65.5 mm and sets a flag rather than
 * clipping in silence.
 *
 *      offset  field       unit            range
 *        0     chunk_id    —               1..NS_BBX_CHUNK_MAX, rolls
 *        2     seq         —               1..NS_BBX_ROWS_PER_CHUNK
 *        4     ts_ms       ms              hub monotonic
 *        8     node        1..4
 *        9     level       1..2
 *       10     tilt_cd     centi-degrees   ±327.67°
 *       12     disp_mm10   tenths of mm    0..6553.5 mm
 *       14     accel_mg    milli-g         0..65.5 g
 *       16     vib         count / 2 s
 *       17     micro_um    µm              0..65535 (saturating, flagged)
 *       19     macro_mm10  tenths of mm    0..6553.5 mm
 *       21     flags       NS_FLAG_* / NS_BBX_FLAG_*
 *       22     crc         CRC-16/CCITT over bytes 0..21
 */
#define NS_BBX_FLAG_MICRO_SAT  0x40   /* micro hit the 65.5 mm ceiling           */
#define NS_BBX_FLAG_VIB_SAT    0x20   /* the 2 s vibration count passed 255      */
#define NS_BBX_FLAG_HUB_DROP   0x80   /* the row's chunk lost rows to a full queue */

#if defined(__cplusplus)
#include <stddef.h>
typedef struct __attribute__((packed)) {
    uint16_t chunk_id;
    uint16_t seq;
    uint32_t ts_ms;
    uint8_t  node;
    uint8_t  level;
    int16_t  tilt_cd;
    uint16_t disp_mm10;
    uint16_t accel_mg;
    uint8_t  vib;
    uint16_t micro_um;
    uint16_t macro_mm10;
    uint8_t  flags;
    uint16_t crc;
} ns_bbx_row_t;

static_assert(sizeof(ns_bbx_row_t) == NS_BBX_ROW_BYTES,
              "ns_bbx_row_t changed size — Core 1 and Core 0 would disagree about "
              "the queue's element size, and the failure is silent row corruption.");
static_assert(offsetof(ns_bbx_row_t, chunk_id) == 0,  "byte map: chunk_id");
static_assert(offsetof(ns_bbx_row_t, seq)      == 2,  "byte map: seq");
static_assert(offsetof(ns_bbx_row_t, ts_ms)    == 4,  "byte map: ts_ms");
static_assert(offsetof(ns_bbx_row_t, node)     == 8,  "byte map: node");
static_assert(offsetof(ns_bbx_row_t, level)    == 9,  "byte map: level");
static_assert(offsetof(ns_bbx_row_t, tilt_cd)  == 10, "byte map: tilt_cd");
static_assert(offsetof(ns_bbx_row_t, disp_mm10) == 12, "byte map: disp_mm10");
static_assert(offsetof(ns_bbx_row_t, accel_mg) == 14, "byte map: accel_mg");
static_assert(offsetof(ns_bbx_row_t, vib)      == 16, "byte map: vib");
static_assert(offsetof(ns_bbx_row_t, micro_um) == 17, "byte map: micro_um");
static_assert(offsetof(ns_bbx_row_t, macro_mm10) == 19, "byte map: macro_mm10");
static_assert(offsetof(ns_bbx_row_t, flags)    == 21, "byte map: flags");
static_assert(offsetof(ns_bbx_row_t, crc)      == 22, "byte map: crc");
#endif

/** The main hub's MAC.
 *
 *  ⚠ FILL THIS IN FROM THE H0 MAC TABLE BEFORE FLASHING. The hub's MAC is
 *  compiled into all four nodes (HD-14), so this constant is what makes the hub
 *  swappable-or-not: change it and every node must be reflashed.
 *
 *  Label the hub "DO NOT SWAP" once it is filled in.
 *  H0.3 #3 records the value; H3.2 #5 requires it be documented. */
#define NS_HUB_MAC_0  0x28
#define NS_HUB_MAC_1  0x05
#define NS_HUB_MAC_2  0xa5
#define NS_HUB_MAC_3  0xe2
#define NS_HUB_MAC_4  0x61
#define NS_HUB_MAC_5  0x20
#define NS_HUB_MAC_SET 1   /* ✅ ENABLED — New MAC: 28:05:a5:e2:61:20 */

/** Neighbour MACs, for the H3 relay ring. A node hears its two neighbours and
 *  keeps their last NS_NEIGHBOUR_RING packets so it can answer a RELAY_REQ.
 *  Filled from the same H0 table. */
#define NS_NEIGHBOURS_SET 0

/* ============================================================================
 * 4. Sensor scaling — measured, not guessed
 * ==========================================================================*/

/** Displacement pot travel. The bench procedure (H1.7) is: slide the pot end to
 *  end and confirm the reading sweeps 0 → 100 mm monotonically. These are the
 *  numbers that make that true; if the sweep is short, it is these two that get
 *  corrected, from the measurement, not by scaling the output to look right. */
#define NS_DISP_MM_MIN      0.0f
#define NS_DISP_MM_MAX      100.0f

/** Macro-crack pot travel, N2/N4. A seam opening, so a much smaller range. */
#define NS_MACRO_MM_MIN     0.0f
#define NS_MACRO_MM_MAX     25.0f

/* ── Phase 3 · the crack column, N2/N4 only (H2) ───────────────────────────
 *
 * Two instruments measure the same seam at two different scales, and the whole
 * phase depends on that difference being real: a load cell sees the first
 * micrometres of movement, a 10 kΩ pot answers to millimetres. The events they
 * raise are therefore tens of seconds apart, which is what makes "initiation"
 * and "propagation" two different things rather than one thing said twice.
 *
 * The unit is **µm of crack-mouth opening**, not micro-strain. The build has a
 * YZC-131 load cell and an HX711, not a foil gauge, so what is physically
 * measured is elongation across the seam — millimetres and micrometres are the
 * units that rig can report honestly. The server's contract says the same thing
 * (`backend/contracts.py`, `micro` = µm; `backend/config.py`,
 * `MICRO_PLAUSIBLE_UM`). The plan's original `15 µε/s` threshold is the same
 * physics: 15 µε/s × the plan's 100 mm crack-mouth gauge = 1.5 µm/s.
 *
 * Sign convention, fixed by the mounting (load-cell mounting note): the cell is
 * pre-tensioned and the seam opens in **tension**, so a rising count means a
 * rising opening. A build where the spring went slack instead reads as
 * FLAG_MICRO_UNLOADED, never as a closing crack.
 * -------------------------------------------------------------------------*/

/** HX711 conversion rate. The chip offers 10 or 80 SPS; 10 is the noise-quiet
 *  one and 2 Hz is all the wire carries, so there is nothing to gain from 80. */
#define NS_HX711_RATE_SPS     10
#define NS_HX711_READ_MS      (1000 / NS_HX711_RATE_SPS)

/** Median of the last N conversions before the value is used.
 *
 *  ⚠ The two documents disagree and this is the reconciliation: H2.4 specifies
 *  `read_median(4)` at 2 Hz, while the backend build doc's `node.cpp` line says
 *  "median of 2 readings". Four is the stricter reading and it is free — at
 *  10 SPS a 2 Hz publish period contains five conversions, so a median of four
 *  throws away nothing but spikes. The *reason* for a median over a mean is the
 *  noise that matters on this channel: a mean spreads one disturbed conversion
 *  across the next samples of a creep signal, which is precisely the signal the
 *  engine differentiates.
 *
 *  Raising this past 4 would start to lag the reading by more than one publish
 *  period: at 10 SPS, N gives N × 100 ms of latency, and the rate window is only
 *  5 s wide. */
#define NS_HX711_MEDIAN_N     4

/** Warm-up (H2.4). The HX711's own output drifts for the first seconds after
 *  power-up; averaging inside that drift contaminates the zero, and a
 *  contaminated zero is a crack that never goes away. So: 15 s of *clock*, then
 *  the baseline. */
#define NS_HX711_SETTLE_MS    15000

/** Readings averaged for the zero, taken AFTER the warm-up. 20 readings at
 *  10 SPS is 2 s — long enough to average out the white noise, short enough that
 *  the first real reading is only ~17 s after boot. */
#define NS_HX711_BASELINE_N   20

/** µm of crack-mouth opening per HX711 count — **measured on the bench, not
 *  chosen** (H2.6 #8, H2.7).
 *
 *  Bench procedure, and the numbers it fills in:
 *
 *    1. mount the load cell and take the zero (the 15 s warm-up below);
 *    2. displace the seam by a known 1000 µm — a feeler gauge, or the macro pot
 *       whose mm-per-count was just calibrated;
 *    3. read the raw counts at both ends: `c0` at rest, `c1` at 1000 µm.
 *
 *        NS_MICRO_UM_PER_COUNT = 1000.0f / (c1 − c0)
 *
 *  The placeholder below is a *predicted* value, not a calibrated one, and the
 *  difference matters: an uncalibrated channel still detects initiation (the
 *  rate is linear in counts) but every absolute µm figure it reports is wrong by
 *  whatever the prediction missed. Predicted from the parts on hand: a 1 kg
 *  YZC-131 declares 1.0 mV/V rated output, which at the HX711's 3.3 V excitation
 *  and ±20 mV / 128-gain input puts 1 kg at ≈1.38 M counts, i.e. ≈1.41 × 10⁵
 *  counts per newton; with the k ≈ 0.9 N/mm pen spring that is ≈1.27 × 10⁵ counts
 *  per millimetre. */
/* The two calibration counts are **integers**, because that is what an ADC
 * reading is and because the preprocessor cannot compare floating constants —
 * `#if (0.0f - 0.0f) <= 0.0f` is an error, not a guard. Keeping them integral
 * lets the guard below exist at all, and a count with a decimal point would be
 * a lie about the precision of the measurement anyway. */
#define NS_MICRO_CAL_C0       0        /* counts at rest, from the bench       */
#define NS_MICRO_CAL_C1       127000   /* counts at +1000 µm, from the bench   */
#if (NS_MICRO_CAL_C1 - NS_MICRO_CAL_C0) <= 0
#error "config.h: the micro calibration needs c1 > c0. Measure both (H2.6 #8)."
#endif
#define NS_MICRO_UM_PER_COUNT (1000.0f / (float)(NS_MICRO_CAL_C1 - NS_MICRO_CAL_C0))

/** Absolute counts that mean the input has railed. 2^23 is full scale; a few
 *  thousand short of it is where a broken wire or an unpowered bridge settles.
 *  Rails set FLAG_MICRO_SATURATED and the frame carries NS_MICRO_RAIL_UM — a
 *  number, because a null would read as "no sensor" rather than "sensor lying". */
#define NS_HX711_RAIL_COUNTS  8300000L

/** What a railed channel reports, in µm. Matches the server's `SIM_MICRO_RAIL_UM`
 *  and sits below `MICRO_PLAUSIBLE_UM`'s ceiling, so a real rail is never
 *  rejected as a bad frame — the flag is the authority, the number is for the
 *  dashboard. */
#define NS_MICRO_RAIL_UM      90000.0f

/** Slack-spring detection. If the subtracted opening falls this far below the
 *  zero for NS_MICRO_UNLOAD_N consecutive readings, the spring that crosses the
 *  seam is no longer carrying load: the channel has stopped measuring. Reported
 *  as FLAG_MICRO_UNLOADED with a 0.0 reading. −250 µm is well outside the
 *  thermal and noise wander of a pre-tensioned cell (≈±5 µm), and well inside
 *  what a released spring produces. */
#define NS_MICRO_UNLOAD_UM    (-250.0f)
#define NS_MICRO_UNLOAD_N     5

/** How long the chip may be silent before the channel counts as faulted rather
 *  than merely idle. At 10 SPS a live chip answers every 100 ms; 1.5 s is 15
 *  missed conversions — far beyond scheduling jitter, far short of a demo. */
#define NS_HX711_DEAD_MS      1500

/** The macro pot's **rail window**, in ADC counts — where the wiper is close
 *  enough to a stop that the reading has stopped meaning anything. The frame
 *  says so (bit 1) and the server treats the channel exactly as it does a railed
 *  HX711.
 *
 *  ⚠ Not to be confused with the server's `MACRO_ADC_LO/HI` (`config.py`), which
 *  are the *calibration span*: 0 counts = 0 mm and 4095 counts = 25 mm. Two
 *  different quantities, so two different names — the firmware's are the
 *  *rail window* and they sit strictly inside the calibration span. Sharing the
 *  name across the wire, which an earlier draft of this file did, is how one end
 *  ends up clamping where the other end is still measuring. */
#define NS_MACRO_RAIL_ADC_LO  20
#define NS_MACRO_RAIL_ADC_HI  4075

/** MPU-6050. ±2 g full scale keeps the sensitivity high, which matters because
 *  ACC_WARNING is 0.25 m/s² — an 8 g range would quantise that to nothing. */
#define NS_MPU_AFS_SEL      0        /* 0 = ±2 g  */
#define NS_MPU_GYRO_FS_SEL  0        /* 0 = ±250 °/s, unused, kept for completeness */
#define NS_G_PER_LSB        (2.0f / 32768.0f)
#define NS_MS2_PER_G        9.80665f

/** Supply measurement. The DevKit's 3V3 rail through the internal ADC is not a
 *  calibrated reference — this is a coarse "is it dying" indication, which is
 *  all FLAG_LOW_BATTERY needs to be. 0 disables it and the frame reports 0. */
#define NS_BATT_ENABLE      0
#define NS_BATT_LOW_MV      3300

/* ============================================================================
 * 5. Failure behaviour
 * ==========================================================================*/

/** H3.2 #3: the brownout detector fires one final packet before the radio dies.
 *  "Within ~2 ms" is the budget; the handler does no allocation and no I2C — it
 *  re-sends the last packet it already holds, flagged NS_FLAG_LAST_GASP. */
#define NS_LAST_GASP_ENABLE 1

/** H3.4: "never reset `seq` on a soft restart except by design; a non-increasing
 *  seq is a discontinuity the backend uses to invalidate the AI window." The
 *  counter lives in RTC memory so a warm reset does not silently restart it at
 *  zero — a restart that looks like a gap is information, not a bug. */
#define NS_SEQ_IN_RTC       1
