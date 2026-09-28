# Neural Strata

Neural Strata is an early-warning bench for an underground coal mine. Surface movement is measured where it starts, decided on a laptop at the pit, and shown underground as junction lights and a horn. A crew standing at a junction can see which pair is still green. An operator looking at the same moment sees the same node, the same severity, and a forecast of how soon the opening may become a failure.

Team Geosentinels built it for the problem *Wireless Surface Mesh Network for Real Time Subsidence Detection*. The people it is written for are the shift operator, the planner who reads the deformation, and a regulator who needs the record. Miners underground do not use the software. They carry a passive RFID fob and follow the lights.

A satellite survey or a walk along the surface finds subsidence after the ground has already moved. Neural Strata keeps four nodes on that surface and asks, several times a second, whether the movement is still ordinary, whether it has become a creep, or whether it has crossed into a failure. The answer is painted on the junctions of a two-level diorama and written down on the laptop, including when the link outward is slow.

The requirements document also describes a later mine installation: solar-powered nodes and a 433 MHz LoRa mesh. This repository is the working prototype. The boards are ESP32. The surface radio is ESP-NOW. The underground path is a wired UART ring. The brain is a Flask server on the laptop beside the bench.

## Who signs in

The dashboard is one page with three desks. The PIN is checked in the browser. Any PIN of four characters or more opens the desk that matches the identity. These identities are for the bench and the demo. They are not a mine login.

| Desk | Identity | What that person is here to do |
|---|---|---|
| Mine operator | `ECL-4471` | Watch the four nodes, acknowledge the live alarm, see the evacuation picture, and read the muster. |
| Mine planner | `GEO-2290` | Read the subsidence view, the time-to-failure projection, and the export prepared for a planning cell. |
| Regulator | `DGMS-1107` | See the overview, the telemetry, and the record. The alarm controls stay with the operator. |

The operator desk is the one used during a physical demo. Planner and regulator desks share the same live feed and hide the controls that are not theirs.

## The bench

The diorama is two levels of tunnel and four junctions. The electronics sit on the surface of that model and in the “underground” of it.

```
  node 1 · node 2          node 3 · node 4
   Level 1 surface          Level 2 surface
        \                      /
         ESP-NOW          ESP-NOW
              \            /
               main hub  ← USB →  laptop
              /        \            Flask · PostgreSQL
        Level 1        Level 2      dashboard · muster
        J1  J2         J3  J4        on-site record
        horn, gate lamps
              \        /
               wired ring
```

**Surface nodes.** There are four, one firmware image, built four times with `NODE_ID` 1 through 4. Every half-second a node reads an MPU-6050 for tilt and measured acceleration, a slide potentiometer for millimetres of opening, and a vibration switch counted over two seconds. Nodes 2 and 4 also carry the crack column: a strain bridge through an HX711, reported as micrometres of crack-mouth opening, plus a second pot for the wider seam. Nodes 1 and 3 report that column as absent. Nodes 1 and 2 belong to Level 1. Nodes 3 and 4 belong to Level 2.

The packet carries the node’s own clock. The laptop differentiates displacement against that clock, so a late radio packet does not look like a sudden jump in the ground. The first minute after power-up is a quiet baseline. Until it finishes, the node marks the frame as unsettled, because a mounting offset must not become a permanent millimetre of “movement.” If the node has been silent for two seconds it also sends a short heartbeat so the dashboard can tell “alive, nothing new” from “cable dead.”

**Main hub.** One ESP32. It hears all four nodes over ESP-NOW, holds a ring port to each level hub, and is the only board the laptop’s USB cable should be plugged into. Every line it accepts is printed, unchanged, to that cable. The laptop is the place where status is decided. The hub forwards.

**Level hubs.** Two more ESP32 boards, the same firmware with `LEVEL` set to 1 or 2. Each one drives the strips, the horn, and the red and green gate lamps for its level, and it polls an RC522 reader on 3.3 V. A new fob beeps on that hub immediately, then the UID is sent up the ring as `lvl1` or `lvl2`. Level 1’s strips are physically junctions 1 and 2. Level 2’s strips are junctions 3 and 4. The colour pairing below is a decision the laptop makes. The hubs execute it.

**Wired ring.** Seven signal-and-ground wires form a triangle: main hub to Level 1, Level 1 to Level 2, Level 2 back to the main hub, each direction its own wire. Heartbeats run on those wires. If a level’s return has been silent for 6 seconds, the laptop marks that leg down, and a command for that level is also offered on the other port so a cut wire does not have to mean a dark junction. The health strip names the two legs `lvl1→hub` and `lvl2→hub`. `J2↔J3` is the surface mesh cross-link; on this bench it stays unknown until that radio relay is in the picture.

**Laptop.** Flask reads the USB stream, updates each node, chooses the lamps and the horn, writes PostgreSQL, and serves the dashboard on port 5000. The dashboard polls about every 200 ms, so a shake on the table and the number on the screen move together.

### How a junction pair is chosen

The horn and the gate lamps are per level, and they follow the worst of the four nodes. The strips are paired across levels, so a problem on one side of the mine lights the matching corners and leaves the other corners green:

| Where the movement is | Strips that leave green | Strips that stay green |
|---|---|---|
| Node 1 or node 2 | Junctions 1 and 3 | Junctions 2 and 4 |
| Node 3 or node 4 | Junctions 2 and 4 | Junctions 1 and 3 |

### How severe the movement is

Acceleration is in m/s². The comparison is signed, so a negative rest value does not trip the horn. Creep has to stay above the line for a short hold, so a single tick does not beep. Failure does not wait.

| Reading | What the bench calls it | That pair | Horn | Gate lamps |
|---|---|---|---|---|
| at or under 0.25 | Nominal | Green flow | Silent | Green on, red off |
| above 0.25, held 0.4 s | Creep | Amber | Three beeps | Red steady, green off |
| at or above 3.0, at once | Failure | Red strobe | Siren held on | Red blinking, green off |

When a failure falls back under 3.0 and is still above 0.25, the bench becomes a creep at once: amber and three beeps, not a silent green gap. When the reading returns under 0.25, the strips go green and the horn stops. A crack stage that the server already calls critical can still lift a quiet node as far as a creep. A node the laptop has not heard for 5 seconds is stale. Stale is a missed packet, not a dead USB port. The port is up when the reader thread still holds it.

## What the laptop keeps besides the lights

**Muster.** Each swipe is stored once per fob: identity, level, tunnel, zone, and time. A second swipe of the same UID inside 3 seconds is the same person still standing there. Level 1 is filed as Tunnel A. Level 2 is filed as Tunnel B. The zone follows whether that level’s route is still open. The dashboard’s muster board is the live list. PostgreSQL table `rfid_evacuation_log` is the longer ledger.

**Texts.** A HIGH or CRITICAL alert can text every number in `backend/assets/locality_phones.txt`. A warning that is still amber does not. If a red text has already gone out, another one inside 60 seconds is not sent. That file stays on the bench laptop. The repository carries only `locality_phones.example.txt`, which shows the shape of a line. On this Mac the working transport is Messages (`NS_SMS_TRANSPORT=messages`). The config default is `hub`, which expects a modem on the hub instead. A demo should show the alert on screen. Firing the full list is a real send.

**The record that stays at the pit.** PostgreSQL on port 5432 is the source of truth for telemetry, RFID, and alert history. Migrations only add tables and columns. `schema.sql` is generated from `backend/persistence/migrations/`. Before a rehearsal, dump the database. `./restore.sh` puts a dump back.

A second copy of the chunk rows can be written to a USB card mounted on the laptop. `backend/blackbox/laptop_card.py` uses the volume named by `NS_BBX_LAPTOP_VOLUME`, or else the first writable volume under `/Volumes` that is not an internal disk. The main hub’s own SPI socket is empty on this bench, so the hub prints `BLACKBOX:NOSD`. That line describes the socket. Eject the card in Finder before you pull it.

**The outward copy.** A local stand-in of the mining cloud listens as PostgreSQL on port 5433 and HTTP on port 5070. The alarm, the strips, and the horn do not wait for that copy. If the stand-in is down, the bench still warns.

## Prediction

The lights are a threshold. The models are a second opinion on the same window of movement, about 60 seconds long and 16 features wide. Three scores are computed. The pattern classifier and the time-to-failure regressor are what the operator is allowed to act on. The novelty score is a note in the margin.

**Pattern.** A random forest, `pattern_model_v1`, labels the window. The class that matters for a structural event is STRUCTURAL. On the synthetic pattern set the held-out recall for that class is 1.000, against a target of at least 0.98. The figure and the split live in `backend/models/pattern_meta.json`.

**Time to failure.** When `ttf_model_v2.json` loads, the dashboard uses the median of its quantile band. That model’s held-out critical MAE is 24.45 seconds and its overall MAE is 477.86 seconds, measured on the synthetic drill set and recorded in `model_meta_v2.json`. If the band cannot be loaded, the server answers with `ttf_model_v1`, the earlier point model. That file’s 0.10 second critical MAE belongs to the 18-run field-drill harvest in `model_meta.json` (9 breach runs, 9 stable runs, 7,870 windows). Quote the file you mean. The two numbers are from two datasets.

**Novelty.** The root-mean-square of the same features, scored with the serving model’s scaler. On the training cloud that value sits near 1. The dashboard index is how far the live window has walked away from 1, clamped between 0 and 1. Under 0.35 the card reads `SEEN`. Otherwise it reads `NOVEL` with the index. It does not raise a tier, close a route, or enter the decision that drives the horn. `NS_NOVELTY_ENABLED=0` removes the annotation and leaves the verdict as it was.

If xgboost or the model files are missing, the server still starts. The forecast then uses the kinematic inverse-velocity estimate, the Fukuzono fallback: time is inferred from how fast the opening is accelerating, without the trained model.

## Run it

You need Python 3.10 or newer, PostgreSQL listening on port 5432, and the packages pinned in `requirements.txt`. The core set is Flask, a PostgreSQL driver, pyserial, and NumPy. xgboost is what makes `/api/ai` active. Without it the page still opens on the kinematic fallback.

```bash
pip install -r requirements.txt
./run.sh --init-db          # apply migrations once, then serve
```

If the script answers “permission denied,” the executable bit was lost in a copy. `bash run.sh` does the same walk.

`./run.sh` prints seven steps and stops at the first failure: interpreter, packages, configuration, database, model files, the contract gates, then the server. Open http://127.0.0.1:5000 and sign in with one of the identities above.

```bash
./run.sh --no-serial        # API and dashboard, no USB hub
./run.sh --check            # run the gates and exit
```

With the boards connected, start the server from `backend/` so the serial port and the text transport are set for this bench:

```bash
cd backend
PYTHONPATH="..:." \
  NS_SERIAL_PORT=/dev/cu.usbserial-0001 \
  NS_SMS_TRANSPORT=messages \
  NS_DB_USER=ns NS_DB_PASS=ns NS_DB_NAME=neural_strata \
  NS_DB_HOST=127.0.0.1 NS_DB_PORT=5432 \
  NS_CLOUD_DB_URL=postgresql://ns:ns@127.0.0.1:5433/neural_strata_cloud \
  NS_CLOUD_HTTP_URL=http://127.0.0.1:5070 \
  python3 app.py --host 127.0.0.1 --port 5000
```

On the demo laptop that Python is `ai_env/bin/python` at the repository root. Use that path if `python3` is not the environment that has the packages. Flask’s reloader is off, so a change under `backend/` is loaded only after the process listening on port 5000 is stopped and started again. A change under `frontend/src/` is loaded only after the bundle is rebuilt, because Flask serves `frontend/index.html`:

```bash
cd frontend
node tools/build-single.js
```

### Bring the hardware up

Power stays off while any wire is being moved. Then:

1. Seat the recording card on the laptop’s USB hub, before the server starts.
2. Power the boards. If the LED strips have their own supply, power that rail before the hub USB.
3. Plug the laptop cable into the main hub only. A cable on a level hub occupies the port the dashboard needs.
4. Confirm PostgreSQL is up, then run the server command above.
5. Open the dashboard and sign in.
6. Wait until all four nodes are fresh and both ring legs read UP. Give the hubs about a minute after they start talking.

The pin freeze, the cable schedule, and the checks to make before the first power-up are in `docs/Neural_Strata_Hardware_Build_Phases.md`, Part C.4. Firmware is PlatformIO. The environments are `main_hub`, `level_hub_l1`, `level_hub_l2`, and one environment per node. Level-hub build notes are in `firmware/src/level_hub/README.md`.

### Record and replay

```bash
cd backend
python3 tools/record.py --seconds 120 --out /tmp/session.csv
python3 tools/replay.py /tmp/session.csv --speed 10 --no-serial
python3 tools/replay.py /tmp/session.csv --speed 0
```

Replay does not have its own pipeline. It replaces the serial thread and calls the same `ingest_line()` the hub cable uses. `--speed 0` prints the session without stepping the clock.

### Gates and the database

```bash
cd backend
python3 tools/check_contracts.py     # the code still matches the frozen API
python3 tools/check_numbers.py       # quoted metrics still match model_meta.json
python3 -m persistence.db --status   # applied migrations, pending migrations, tables
python3 -m persistence.db --init     # apply whatever is pending
```

`check_numbers.py` reads the canonical figures out of `models/model_meta.json`. It does not contain a typed-in MAE. An older document may quote a previous figure when the sentence marks it as historical, as provenance, or as the raw harvest. A bare number that contradicts the artifact fails the gate, and `./run.sh` will refuse to serve.

Take `pg_dump` before a rehearsal. `./restore.sh <dump>` restores it. The database on the demo laptop is the one with the rehearsal rows in it, so a migration that dropped or rewrote rows would delete the record the demo is supposed to show.

## Repository

```
backend/      The edge server: ingest, status, routing, alerts, PostgreSQL, the laptop card copy
firmware/     PlatformIO projects for the main hub, both level hubs, and the four nodes
frontend/     The dashboard. Edit the files in src/. The file Flask serves is the built index.html
ai/           Training scripts and the reports the model files cite. The csv sets stay local.
infra/        A compose file and the schema for the cloud copy
```

The design documents in `docs/`, the csv captures in `ai/datasets/` and `recordings/`, and the sqlite files under `runtime/` and `backend/runtime/` stay on the bench machine. They are listed in `.gitignore` and are not part of the GitHub copy. `docs/Neural_Strata_Directory_Structure.md`, on that machine, is the long map of every file the phases expected.

## Where to read further

| Question | Document |
|---|---|
| What was required, in the SRS sense | `docs/Neural_Strata_SRS_v1.1.md` |
| Which of those lines this tree actually meets | `docs/Neural_Strata_Requirement_Compliance.md` |
| Which pin, which wire, which supply | `docs/Neural_Strata_Hardware_Build_Phases.md` and `docs/Neural_Strata_Wiring_Walkthrough.md` |
| How the server was added, phase by phase | `docs/Neural_Strata_Backend_Build_Phases.md` and `docs/Neural_Strata_Backend_Plan.md` |
| Edge database versus the cloud copy | `docs/Neural_Strata_Dual_Database.md` |
| Dashboard modules and the HTTP contract | `frontend/README.md` and `frontend/docs/API.md` |

When an older slide or plan names a figure, the file to trust is the code on this branch and the matching `backend/models/*_meta.json`. The slides and the long plans live in `docs/` on the team machine.
