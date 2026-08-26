# Neural Strata — Sector Overview

A mining-safety operations dashboard: a live surface plan, a 3D underground
cutaway, a 12-cell LoRa sensor mesh, crew mustering and an evacuation router —
in a single self-contained HTML file, plus a reference backend.

```
.
├── index.html          the whole dashboard (no build step, no CDN, no deps)
├── server/
│   └── app.py          reference backend — stdlib only, serves the API + the page
├── backend/
│   └── app/            FastAPI scaffold, requires fastapi/uvicorn
│       ├── main.py
│       ├── models.py
│       └── sources.py  demo state + sensor reads
├── tests/
│   ├── contract.js     backend robustness: 88 assertions
│   ├── regression.js   headless JSDOM suite (61 assertions)
│   └── package.json
└── .vscode/
    ├── launch.json     F5 launch configs (2 programs)
    ├── tasks.json      Install and test tasks
    ├── settings.json
    └── extensions.json
```

## Run it

**Just the UI** (runs on its own built-in simulation):

```bash
python3 -m http.server 8080     # then open http://localhost:8080
```

**With the backend** — serves the page *and* the API from one process:

```bash
python3 server/app.py           # then open http://localhost:8000
```

The ticker in the bottom-left reads `SIM` when the dashboard is running on its
internal model and `LIVE` once it is talking to a backend. No configuration:
it polls `/api/*`, and falls back to the simulation if nothing answers.

## Tests

```bash
cd tests && npm install

node regression.js              # behaviour: 61 assertions
node regression.js cells        # one suite: boot|cells|keys|detail|scenarios|paint|perf|polling

node contract.js                # backend robustness: 88 assertions
node contract.js fuzz           # one suite: telemetry|extended|history|ai|transport|views|live|fuzz
```

`regression.js` proves the dashboard *behaves* — cells select, keys work, the
reticle paints, the render loop stays cheap.

`contract.js` proves it *survives a real backend*. It boots the page against a
stubbed API and feeds it the payloads production hardware actually emits:
missing fields, `null` mid-reboot, numbers as strings, `NaN` from a firmware
divide-by-zero, values far outside the plotted range, unknown enum values,
HTTP 500, an HTML error page where JSON was expected, and 20+ randomised
payloads per run. It asserts three things after every one of them:

* no uncaught page error,
* no `NaN` / `undefined` / `Infinity` / `[object Object]` in visible text or in
  any inline style (a `width:NaN%` silently collapses a bar),
* no non-finite number ever reaches a canvas 2D call (one `NaN` coordinate
  kills the entire path — the chart goes blank with no error in the console).

Run `contract.js` against your own backend before you trust its output.

---

# Backend API

Everything is JSON over HTTP on the same origin as the page. The frontend polls
`GET` endpoints on a timer and issues `POST`s from the scenario console. If a
request fails or times out the dashboard degrades to its internal simulation and
retries with exponential backoff (1 s → ×1.8 → 15 s cap), so a backend that is
down or restarting is never fatal.

| Method | Path               | Called            | Timeout |
| ------ | ------------------ | ----------------- | ------- |
| `GET`  | `/api/telemetry`   | every 500 ms      | 1500 ms |
| `GET`  | `/api/extended`    | every 1500 ms     | 2000 ms |
| `GET`  | `/api/history`     | once, on load     | 2500 ms |
| `GET`  | `/api/ai`          | every 5000 ms     | 2000 ms |
| `POST` | `/api/sim/trigger` | scenario console  | —       |
| `POST` | `/api/reset`       | "Re-arm system"   | —       |

### `GET /api/telemetry`

The hot path — drives the status banner, the gauges, both canvases, the mesh
cell colours and the RFID board.

```json
{
  "system_status": "NOMINAL",
  "ttf_seconds": "N/A",
  "nodes": {
    "node_1": { "status": "NOMINAL", "tilt_x": 1.52,
                "displacement_mm": 5.2, "acceleration": -0.01 },
    "node_2": { "status": "NOMINAL", "tilt_x": 0.32,
                "displacement_mm": 4.1, "acceleration": 0.015 }
  },
  "underground": { "route_A_status": "OPEN", "route_B_status": "OPEN" },
  "rfid_log": [
    { "id": "001", "name": "Deepak", "zone": "Tunnel A", "time": "12:36:48" }
  ]
}
```

- `system_status` / `status` — `NOMINAL` | `WARNING` | `CRITICAL`. `CRITICAL`
  puts the whole UI into panic styling and arms the siren.
- `ttf_seconds` — integer seconds to predicted failure, or the string `"N/A"`.
- `displacement_mm` — 0–100; 100 is the design limit the AI panel forecasts against.
- `acceleration` — m/s². The dashboard classifies `≥ 3.0` CRITICAL, `> 0.25` WARNING.
- `route_*_status` — `OPEN` | `BLOCKED`. Blocking a route reroutes the evacuation
  map and turns the drift LEDs red in the 3D scene.
- `rfid_log` — most recent 10 badge reads, newest first.

### `GET /api/extended`

```json
{
  "mode": "LIVE",
  "nodes": {
    "node_1": { "velocity": 0.0, "peak_acceleration": 0.02 },
    "node_2": { "velocity": 0.0, "peak_acceleration": 0.02 }
  },
  "muster": { "total": 10, "underground": 6, "safe": 2 },
  "events": [ { "time": "12:36:48", "level": "NOMINAL", "text": "System initialised." } ]
}
```

`mode` is printed verbatim in the ticker. `level` on an event must be one of the
three status strings — it selects the log line's colour.

### `GET /api/history`

Backfills the charts on load. Up to 240 samples per node, oldest first, each
sample shaped exactly like a `nodes.*` entry from `/api/telemetry`.

```json
{ "node_1": [ { "status": "NOMINAL", "tilt_x": 1.1,
                "displacement_mm": 5.2, "acceleration": 0.01 } ],
  "node_2": [] }
```

### `GET /api/ai`

Metadata for the AI panel's header card. Any missing field renders as `—`; the
forecast curves themselves are computed client-side from telemetry.

```json
{ "model": "strata-lstm v2.4", "status": "ONLINE", "horizon": "45 min",
  "last_train": "2026-08-25 12:36", "confidence": "93%", "note": "…" }
```

### `POST /api/sim/trigger`

```json
{ "node": "node_1", "severity": "critical" }
```

`node` is `node_1` or `node_2`; `severity` is `warning` or `critical`. Returns
`{"ok": true}`, or `400` if either value is unrecognised. A `warning` injection
is expected to settle back to `NOMINAL` on its own after ~46 s; a `critical` one
holds until reset.

### `POST /api/reset`

No body. Clears every injection, returns the nodes to baseline and re-seeds the
crew to their starting zones. Returns `{"ok": true}`.

---

## What the frontend tolerates

Every response is passed through a sanitiser (`cleanTelemetry`,
`cleanExtended`, `cleanNode` in `index.html`) before any renderer sees it, so
the UI code downstream can assume finite numbers and known enum values. You do
not have to send a perfect payload — this is what happens if you don't:

| Deviation | Behaviour |
|---|---|
| Field missing / `null` | Falls back to the node's **previous** value, so a chart flat-lines instead of dropping to zero |
| Number sent as a string (`"5.2"`) | Parsed |
| `NaN`, `Infinity`, `""`, object, boolean | Rejected, previous value kept |
| Number outside its range | Clamped: displacement `0…100 mm`, tilt `±45°`, acceleration `-1…50 mm/s²`, TTF `0…86400 s` |
| `system_status` missing or unrecognised | **Derived** from the node statuses — the banner can never read NOMINAL while a node reports CRITICAL |
| Node `status` unrecognised (e.g. `"MAINTENANCE"`) | Treated as `NOMINAL`; renderers never index a colour table with an unknown key |
| `route_*_status` anything but `"BLOCKED"` | Treated as `OPEN` (fail-safe: only an explicit BLOCKED closes a route) |
| `rfid_log` missing / not an array | Treated as empty; crew counts read 0 |
| `rfid_log` entry with an unknown `zone` | Placed in `Headframe` (surface) rather than dropped |
| `rfid_log` longer than 64 entries | Truncated |
| `events` longer than 64 | Truncated |
| `/api/history` longer than 240 samples | Only the most recent 240 are kept |
| Extra nodes (`node_3`…) | Ignored — the UI renders `node_1` and `node_2` |
| HTTP 500, timeout, invalid JSON, HTML error page | Link marked offline, dashboard falls back to the local sim, retry backs off to ~15 s |

Text arriving from the backend (crew names, event text, `/api/ai` fields) is
HTML-escaped before it is written into the DOM, so a malformed or hostile
string cannot inject markup.

**Two consequences worth knowing.** Clamping means a genuinely out-of-range
reading is displayed at the limit rather than rejected — if your sensors can
exceed these ranges, widen `RANGES` in `index.html` rather than letting values
saturate silently. And because a missing field reuses the last good value, a
sensor that stops reporting looks *stable* rather than absent; use
`status` (or drop the node from `nodes` entirely) to signal a dead sensor.

---

## Building the backend in VS Code

A ready-to-run FastAPI scaffold is in `backend/`. Open the `neural-strata`
folder in VS Code, run the *Install backend dependencies* task, and press F5 —
the dashboard comes up at <http://127.0.0.1:8000/> already connected, with
interactive API docs at `/docs`.

You then fill in five functions in `backend/app/sources.py`; only
`read_telemetry()` is required. See **`backend/README.md`** for the full
walkthrough.

Prefer no dependencies? `server/app.py` is stdlib-only and needs no install —
it's the second F5 option.

---

## Connecting the frontend to your backend

### Where the dashboard looks for the API

By default the page calls `/api/...` on **its own origin**, so serving
`index.html` from the same host/port as your API needs no configuration at all.

If your API lives elsewhere, set the base URL — no need to edit call sites:

```bash
# quickest: query string, good for testing
index.html?api=https://mine-api.example.net
```

```html
<!-- permanent: add before the page's own <script> blocks -->
<script>window.NEURAL_STRATA_API = 'https://mine-api.example.net';</script>
```

A trailing slash is stripped for you. **A cross-origin backend must send CORS
headers** or the browser will block every request and the dashboard will sit in
SIM mode — the reference server already sends `Access-Control-Allow-Origin: *`.

### Minimum to get a live dashboard

Only two endpoints are needed for the page to leave SIM mode and show real data:

| Endpoint | Needed for | If absent |
|---|---|---|
| `GET /api/telemetry` | **Required.** Drives status, nodes, charts, routes, crew | Dashboard stays in SIM mode |
| `GET /api/extended` | Velocity, peak accel, muster counts, event log | Those panels stay at defaults |
| `GET /api/history` | Back-fills charts at boot | Charts start empty and fill as data arrives |
| `GET /api/ai` | AI panel metadata | Panel shows `—`, forecasts fall back to kinematic mode |
| `POST /api/sim/trigger`, `POST /api/reset` | Scenario console buttons | Buttons no-op against a live backend |

The dashboard decides it is LIVE purely on `GET /api/telemetry` succeeding. On
any failure it falls back to the built-in simulation and retries with backoff,
so a backend that isn't up yet is a non-event.

### Checking your own backend

`tests/contract.js` is written against the contract, not against the reference
server, so you can point it at your implementation to check conformance before
going live. Start there if the dashboard shows unexpected readings.

---

## Wiring in real hardware

`server/app.py` is a working reference, not a framework. The physics lives in
`MineModel`; the HTTP layer never touches it except through
`telemetry()`, `extended()`, `history_payload()`, `ai()`, `trigger()` and `reset()`.

To go live against real sensors, keep those six return shapes identical and fill
them from your own source — a LoRa gateway, Modbus poller, MQTT subscriber or a
time-series database. Then start with:

```bash
python3 server/app.py --no-sim      # HTTP + API, physics thread disabled
```

If you would rather run the API from your own stack (FastAPI, Express, Flask),
you only need the six endpoints above; serve `index.html` from anywhere on the
same origin, or enable CORS — the reference server already sends
`Access-Control-Allow-Origin: *`.

Ports and binding:

```bash
python3 server/app.py --port 9000 --host 127.0.0.1
```

---

## Using the dashboard

**Mesh cells.** The 12 cells `E1`–`E12` under the status card are the sensor
mesh. Click one and it is pinpointed on whichever model is showing — a reticle
and callout on the surface plan, a beacon planted on that cell's monument in the
3D cutaway, with the camera easing toward it. Click the same cell again (or
double-click, or use the strip's `OPEN E{n} DETAIL ▸`) to open its full readings.
`E5` and `E8` are the instrumented nodes; the other ten are LoRa relays whose
values are interpolated from them.

**Modes.** `SURFACE` is a 2D plan, north up. `UNDERGROUND` is an orbitable 3D
cutaway — drag to orbit, scroll to zoom, double-click to reset the camera.

**Keyboard**

| Key       | Action                          |
| --------- | ------------------------------- |
| `1` – `6` | switch view                     |
| `s` / `u` | surface / underground           |
| `[` `]`   | previous / next mesh cell       |
| `i`       | scenario injections             |
| `r`       | re-arm the system               |
| `?`       | controls reference              |
| `Esc`     | close drawer → view → clear pin |

**Scenario injections.** All four live in one console (`i`): warning and
critical for each node. They post to `/api/sim/trigger` when a backend is
connected, and drive the internal simulation when one is not.
