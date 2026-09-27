# Review — Underground Blackbox & Cloud Sync

**Document reviewed:** `Neural_Strata_Addendum_Blackbox_and_Cloud_Sync.pdf` (4 pp, SIH 2026)
**Reviewed by:** engineering review, 20 Sep 2026
**Verdict:** **Adopt, with corrections.** The core idea is right and the Core-1-Master-Timekeeper
argument is the strongest single piece of engineering reasoning in this project. One flaw would
destroy the demo on stage, three would cost data, and seven smaller items need tightening.

---

## 1. Summary of the verdict

| | Count | Meaning |
|---|---|---|
| **Keep unchanged** | 6 | Correct as written. Do not touch. |
| **Must fix** | 7 | Correctness or demo-breaking. Fix before any code is written. |
| **Should fix** | 6 | Defensible but weak. A sharp judge will find these. |
| **Missing — add** | 4 | The features that turn a good design into a winning demo. |

---

## 2. What is genuinely good — keep exactly as written

| # | Element | Why it is right |
|---|---|---|
| G1 | **Core 1 as Master Timekeeper** | The race-condition argument is correct and complete. Core 1 owns the counter because Core 1 is the only thing that sees every packet; Core 0 discovers the rollover *from the data*, not from a counter of its own. This is textbook single-writer design and it is the best viva answer in the project. |
| G2 | **Rows vs chunks, and file-level unlink** | NAND flash cannot erase a line. Rotating into 1,000-row files and unlinking whole files is the correct answer, correctly justified. |
| G3 | **Delete only after the cloud confirms** | Correct instinct, correctly justified: the SD card is the only copy that survives a laptop failure. |
| G4 | **SD on VSPI, pins 5 / 18 / 19 / 23** | **Verified against our A.2 pin freeze: these four pins are genuinely free on the Main Hub.** The Main Hub's only pins are 17/16 (ring A), 4/13 (ring B) and GND. The RC522 — which does use 18/19/23/5 — lives on L1/L2, not on the Main Hub. **No conflict.** This was the riskiest thing in the addendum and it came out correct. |
| G5 | **Two cores, alerting never blocked by the card** | Right split. The SD write is the only slow, blocking operation in the system and it is correctly quarantined on Core 0. |
| G6 | **The arithmetic** | 2 Hz × 4 nodes = 8 rows/s ✓. 1,000 rows ÷ 8 = 125 s ≈ 2 minutes per chunk ✓. |

---

## 3. Must fix — correctness

### M1 · The Main Hub is powered by the cable you are cutting

**This is the one that breaks the demo.** The scenario is "the USB cable to the surface is crushed".
But the Main Hub is an ESP32 **powered from that same USB cable**. Cut it and the hub dies — the
blackbox dies with the cable it was built to survive. The 4-second beat where the dashboard says
"link lost, recording continues" would instead show the hub going dark.

**Fix (no new parts):** the Main Hub must be **locally powered** and the surface link must be
**data-only**.

- Feed the board's `5V` pin from the same local 5 V supply the level hubs use, **or** plug it into
  the mini-hub, which sits *underground* in the model.
- The cable to the laptop is then a **data-only** USB cable — red (+5 V) wire cut inside, D+/D−/GND
  intact. The laptop still enumerates the serial port; the board no longer draws from it.
- **Demo beat becomes true:** sever the surface cable → hub keeps running → Core 0 keeps writing →
  the dashboard shows the blackbox still filling. This is the whole point of the document and it
  only works with local power.

**This must be stated explicitly in the document**, because it is the first thing a judge will ask:
*"If you cut the cable, how does the hub still have power?"*

### M2 · The delete gate is missing the row-count condition

The addendum's gate is a single condition:

> `SELECT COUNT(*) FROM cloud_outbox WHERE chunk_id = 42 AND status = 'PENDING'` → if 0, delete.

**That is not sufficient, and the failure mode is silent and permanent.** If the laptop ever
received *fewer* rows than the hub wrote — a serial glitch, a buffer overrun, a reconnect mid-chunk,
a restart — then those rows were never inserted, so they were never enqueued, so `PENDING` reaches
**0**, and the gate **deletes the SD file that holds the only copy of the missing rows**. That is
exactly the zero-loss failure the document exists to prevent.

**Fix:** the gate requires **five** conditions, all of them:

1. the chunk is **CLOSED** (the hub is no longer writing to it)
2. `received_rows == expected_rows` — the count the hub reported in `CHUNK_CLOSE`
3. `dropped_rows == 0` — the hub did not overflow its queue writing this chunk (see M3)
4. `pending == 0` — **and** that count is derived from the real table, cross-checked
5. the chunk has been closed for a **60-second safety margin**

Every refusal must carry a human-readable reason, because the dashboard shows it
(*"chunk 42: 12 rows would be lost"*) and that reason is what stops someone overriding the gate.

### M3 · Queue overflow is a silent hole in the "zero-loss" claim

The document says incoming cards "safely buffer in RAM" if the SD write stalls. But `xQueueSend`
with a **0-tick wait** — which the document specifies — **drops the record when the queue is full**.
So there is a real loss path: SD stall longer than the queue depth, and rows vanish. The document
never mentions it, and "100% mathematical zero-loss safety" is in the last line.

**Fix (three parts):**

1. Queue depth ≥ **256 records** (24 B each ≈ 6 KB). At 8 rows/s that absorbs a **32-second** stall.
2. On send failure, Core 0 increments a **`dropped` counter** for the current chunk.
3. The hub reports it: `DROP:<chunk>,<count>` and `CHUNK_CLOSE:<chunk>,<rows>,<dropped>`. **Any chunk
   with `dropped > 0` is never deleted** — the file is the only record of those rows — and the
   dashboard shows a `DROPPED` chip so the loss is visible rather than claimable-as-zero.

Also fix the wording: replace "100% mathematical zero-loss safety" with
**"zero loss except hub-queue overflow, which is counted, reported and prevents deletion."**
That sentence is stronger than the absolute claim, because it is true and it shows you measured.

### M4 · A brownout ISR cannot commit an SD sector

> *"Hardware brownout detector fires ISR; commits last open sector to SD before capacitors drain."*

It cannot. An SD write is milliseconds of blocking SPI, and an ISR runs in a few microseconds before
the rails collapse. Writing "the ISR commits the last sector" is the kind of line that loses a viva
to the one judge who has written embedded flash code.

**Fix — say what actually happens, and it is still safe:**

- The chunk file is **append-only** and **flushed every 5 seconds** (`f.flush()` + a short delay, or
  `fdatasync`-equivalent via close/reopen).
- On brownout the detector sets a flag and, if there is time, stops accepting new records.
- Worst case you lose **≤ 5 seconds** of the open chunk — **which the document already says**. The
  mechanism was wrong; the number was right.
- Everything in every **closed** chunk is on the card and intact.

### M5 · A laptop restart mid-chunk loses the count, and can un-block a delete

The document assumes the laptop saw `CHUNK_OPEN` and has been counting since. Restart the laptop
mid-chunk — or unplug and replug the cable — and the laptop has no idea which chunk is open, how
many rows are in it, or which chunks were closed while it was away. Worse, a chunk it never saw
close has `expected_rows = NULL`, which in a naive gate reads as "no constraint" → delete.

**Fix — the reconciliation handshake:**

- On every **reconnect** (and on demand), the laptop sends `STATE?` and the hub **answers**:
  - `CHUNK_STATE:<open_chunk>,<rows_so_far>,<dropped>`
  - `CHUNK_DONE:<chunk>,<rows>,<dropped>` for each recently closed chunk not yet acknowledged
- The laptop **reconciles**: a chunk is only closable from a hub line, never from an assumption.
- `expected_rows = NULL` **blocks** the delete (it is a *constraint*, not an absence of one).
- Counts are **monotonic**: a stale hub count may never lower a number we already hold — a
  reconciliation must not be able to un-close a chunk or erase rows.

### M6 · `200 OK` must mean *committed*, not *accepted*

"If the Central Cloud returns HTTP 200, the data is safe" — but only if the endpoint returns 200
**after the database transaction commits**. An API that accepts the batch and queues it, then
returns 200, gives you a delete authorisation for data that can still be lost.

**Fix — three things:**

1. **Cloud contract:** `200 OK` is returned **only after `COMMIT`**. State it in the document and in
   the code, and put a row in the failure matrix for "cloud returned 200 but never committed"
   (the answer: the chunk is one of the last two uploaded; the 60 s margin and the audit trail make
   it recoverable, and the next batch republishes it).
2. **Idempotency key per batch:** `batch_id = HMAC-or-UUID` sent in a header. The cloud **rejects
   duplicates** with 200 (already stored) rather than inserting twice. Without this, every retry
   duplicates rows — and the outbox *will* retry.
3. **Replay protection on the HMAC:** the signed payload must contain a monotonically increasing
   batch sequence and a timestamp; the server rejects a batch it has already seen. A bare HMAC over
   the body alone is replayable.

### M7 · "Central Mining Cloud" is undefined — and we already chose the database

The document names a cloud that does not exist in this project. We already decided the storage
architecture: **local Postgres is the truth, Neon is the free cloud primary** (auto-suspend, session
pooler on 5432), Supabase is the alternate. The blackbox outbox plugs straight into that decision —
but the document must not invent a third thing.

**Fix:** rename the target to **"the sync target (Neon Postgres)"** and record the one consequence:
**a suspended free-tier database takes seconds to wake**, so the uploader needs generous timeouts
and must not treat a cold start as a failure. A 5-second default HTTP timeout will fail every first
upload of a demo session and the dashboard will sit at `PENDING` while nothing is wrong.

---

## 4. Should fix — tightening

| # | Item | Change |
|---|---|---|
| S1 | **SD row schema ≠ database schema** | The document's row is 6 fields; our telemetry row is 9 (`node, seq, ts_ms, tilt, disp_mm, accel, vib, micro, macro, flags`). **Rule: the SD row and the DB row carry the same fields in the same order**, so a blackbox replay is a plain insert with no translation table. This is what makes the replay feature cost nothing to build. |
| S2 | **"Deleted in 1 millisecond"** | Say *"one FAT directory-entry unlink — no data rewrite, no flash wear — typically a few milliseconds, occasionally tens, depending on the card."* The precision costs nothing and survives questioning. |
| S3 | **"Reducing bandwidth by 85%"** | GZIP on repetitive CSV genuinely does reach ~85–90%, but the document asserts it. **Measure it once, put the real number in, and put raw vs gzipped bytes on the dashboard chip.** A measured number is worth three estimated ones. |
| S4 | **Card size vs the month-long scenario** | At 8 rows/s × ~25 B ≈ **17 MB/day**, a 4 GB card holds **~214 days**, so "month-long isolation → 95 % full" cannot happen on a 4 GB card. The dashboard chip in the document says `3,712 MB free`, i.e. a 4 GB card. **Either** state the card size and use the matching scenario (a 512 MB card fills in ~29 days — that matches), **or** keep the 4 GB card and change the scenario to a capacity warning at 90 %. Pick one and make the numbers agree. |
| S5 | **"Up to 20 MHz bus frequency"** | Start the SPI bus at **4 MHz**, prove it, then raise it in steps. Cheap SD breakouts on hand-soldered wires at 20 MHz is how you get intermittent `card init failed` that looks like a software bug. Target 10–20 MHz, but *start* at 4. |
| S6 | **No decoupling on the SD breakout** | Add **100 nF + 10 µF across the breakout's VCC/GND**, and a first-run check that prints `SD OK / NO SD` at boot. The document's own dashboard design has a `NO SD` state — it needs a way to be reached. |

---

## 5. Missing — add these four

| # | Feature | Why it earns its place |
|---|---|---|
| A1 | **RECOVER, not just "rescue teams retrieve the card"** | The document's answer to `Rockfall Cuts Cable` is that someone walks in and collects the SD card. **You cannot demo that.** Add a `REPLAY:<first>:<last>` command: reconnect the cable, the hub re-streams the chunks it still holds, the laptop ingests them through the same idempotent path, and **the dashboard timeline back-fills the gap**. Nothing new is needed — `UNIQUE(chunk_id, seq)` already makes replay safe. This is the single most persuasive 20 seconds in the whole demonstration: the black line on the graph *fills itself in* from underground. |
| A2 | **A chunk ledger on the dashboard** | A table: `chunk_id · rows · state · synced · dropped · file`. The crowd watches chunk 42 go `CLOSED → SYNCED → ACK → DELETED`. That *is* the seven-step pipeline, made visible, with no narration needed. |
| A3 | **A `DROPPED` chip** | `BLACKBOX: ACTIVE` and `CLOUD: 100% SYNCED` are the document's chips. Add `DROPPED: 0`. A safety system that displays its own loss counter is more credible than one that claims zero. |
| A4 | **Force Sync showing the round trip** | The document already proposes the button. Make it show the **timed round trip**: `350 pending → uploading → gzip 61 KB → HTTP 200 (412 ms) → ACK_DELETE:0042 → file unlinked`. Four visible stages, one button. |

---

## 6. What the firmware must now emit — the contract

The backend can only be zero-loss if the hub tells it the truth. This is the frozen interface.
Anything not on this list, the backend cannot know.

| Line | Direction | Meaning |
|---|---|---|
| `{"chunk_id":42,"seq":450,"ts_ms":142850,"node":2,...}` | hub → laptop | one telemetry row, stamped by Core 1 |
| `CHUNK_OPEN:0043` | hub → laptop | Core 1 advanced the counter |
| `CHUNK_CLOSE:0042,1000,0` | hub → laptop | chunk closed; `rows` and `dropped` are the hub's own counts |
| `CHUNK_STATE:0042,450,0` | hub → laptop | reply to `STATE?`: rows so far in the open chunk |
| `CHUNK_DONE:0042,1000,0` | hub → laptop | a recently closed chunk the laptop has not acknowledged |
| `DROP:0042,3` | hub → laptop | 3 rows were lost to queue overflow |
| `BLACKBOX:OK,3712` / `NOSD` / `FULL` | hub → laptop | card health and free space |
| `ACK_DELETE:0042` | laptop → hub | **only** from a gate pass |
| `REPLAY:0042:0057` | laptop → hub | re-stream those chunks from the SD card |
| `STATE?` | laptop → hub | reconciliate after a reconnect |

**Two rules that make this safe:** (1) a telemetry line always begins with `{` and a control line
never does, so demuxing is one character; (2) the laptop never echoes a line back up the wire.

---

## 7. Changes required in documents that already exist

| Document | Change |
|---|---|
| `Neural_Strata_Backend_Plan.md` (v4.4 lineage) | **Superseded** by `Neural_Strata_Backend_Plan_v5.md`. The v4.4 HTTP surface is preserved. |
| `Neural_Strata_Hardware_Build_Phases.md` | **Add a hardware station for the SD breakout** on the Main Hub: pins 5/18/19/23, 3V3, GND, decoupling, soldered not breadboarded. This is new hardware and it is not in H0–H9. |
| `Neural_Strata_Wiring_Walkthrough.md` | Main Hub goes from **5 wires to 9**. Also record the **local-power / data-only-cable** decision (M1) — it changes how the Main Hub is powered. |
| `Neural_Strata_Frontend_Change_Request.md` | **No change needed.** The v5 backend preserves the v4.4 endpoint contract exactly, so every CR still applies. New blackbox endpoints are additions, not replacements. |
| `Neural_Strata_Requirement_Compliance.md` | Add the blackbox as a new requirement family (B-series) and re-score. |
| `Neural_Strata_Dual_Database.md` | The outbox described here **replaces** §H.6's sync harness. Reconciliation needed. |

---

## 8. The one-paragraph version

Adopt it. The Core-1 timekeeper design is excellent and the SD-on-VSPI pin choice is verified
correct against our frozen hardware map. Fix seven things before writing code: **power the Main Hub
locally** or the demo collapses; **the delete gate needs five conditions, not one**, or it will
silently delete the only copy of rows that never arrived; **count and report queue-overflow drops**
instead of claiming absolute zero loss; **stop saying the brownout ISR flushes the card** — say
append-and-flush-every-5-seconds; **reconcile after a reconnect** with `STATE?`; **make `200 OK` mean
committed** with an idempotency key; and **name the real cloud**. Then add the four missing pieces,
of which `REPLAY` is the one that wins the room.
