# Gateway API contract

Everything the dashboard needs from the backend ("surface gateway" — the Flask app on the control-room laptop).
`mock/server.js` implements this contract 1:1, so the backend team can diff their responses against it.

## Base URL

| Source | Value |
|---|---|
| `src/js/00-config.js` | `window.NEURAL_STRATA_API` — `''` (default) means *same origin*: the gateway serves `index.html` itself or proxies `/api/*`. |
| Query string | `index.html?api=https://gateway.mine.local:5000` overrides the config at runtime (useful for a static host + remote gateway). |

If the gateway is on another origin it must answer CORS pre-flights (`Access-Control-Allow-Origin`, `GET, POST, OPTIONS`, `Content-Type`).

## Polling cadence and failure behaviour

| Endpoint | Interval | Timeout | On failure |
|---|---|---|---|
| `GET /api/telemetry` | 500 ms | 1.5 s | falls back to the built-in simulator (`SIM`), header shows **Sim mode** |
| `GET /api/extended` | 1.5 s | 2 s | simulator fallback; blackbox panel returns to local simulation after 8 s without data |
| `GET /api/ai` | 5 s | 2 s | shows `OFFLINE — kinematic fallback` |
| `GET /api/history` | once at start-up | 2.5 s | sparklines start empty |

After a failure the dashboard backs off (1 s → ×1.8 → max 15 s) and retries; any success flips the header back to **Online / LIVE**.
Numbers are clamped client-side (see ranges below) and unknown enum values fall back to safe defaults, so a partial payload never breaks the UI — but it will silently mask backend bugs, so send the full shape.

---

## `GET /api/telemetry`

```json
{
  "system_status": "NOMINAL",
  "ttf_seconds": "N/A",
  "nodes": {
    "node_1": { "status": "NOMINAL", "displacement_mm": 5.2, "tilt_x": 1.10, "acceleration": 0.013 },
    "node_2": { "status": "NOMINAL", "displacement_mm": 4.1, "tilt_x": 0.80, "acceleration": -0.011 }
  },
  "underground": { "route_A_status": "OPEN", "route_B_status": "OPEN" },
  "rfid_log": [ { "id": "001", "name": "Deepak", "zone": "Tunnel A", "time": "13:08:04" } ],
  "timestamp": "2026-09-19T07:38:04.000Z"
}
```

| Field | Type / allowed values |
|---|---|
| `system_status` | `NOMINAL` \| `WARNING` \| `CRITICAL` — optional; if absent the worst node status is used |
| `ttf_seconds` | integer seconds `0…86400` or the string `"N/A"` |
| `nodes.node_1`, `nodes.node_2` | the two STM32 monument nodes (mesh cells **E5** over Drift A / **E8** over Drift B). Keys are fixed. |
| `nodes.*.status` | `NOMINAL` \| `WARNING` \| `CRITICAL` |
| `nodes.*.displacement_mm` | number `0…100` (100 mm = terminal threshold) |
| `nodes.*.tilt_x` | number `-45…45` mm/m |
| `nodes.*.acceleration` | number `-1…50` mm/s² (`≥ 3.0` is treated as failure onset) |
| `underground.route_A_status`, `route_B_status` | `OPEN` \| `BLOCKED` |
| `rfid_log[]` | newest first, max 64 kept; `zone` ∈ `Tunnel A`, `Tunnel B`, `Evacuating A`, `Evacuating B`, `Safe Exit`, `Headframe`; `time` = `HH:MM:SS` |
| `timestamp` | ISO-8601, informational |

## `GET /api/extended`

```json
{
  "mode": "LIVE",
  "nodes": {
    "node_1": { "velocity": 0.02, "peak_acceleration": 0.11 },
    "node_2": { "velocity": 0.00, "peak_acceleration": 0.05 }
  },
  "muster": { "total": 8, "underground": 6, "safe": 0 },
  "events": [ { "time": "13:08:04", "level": "NOMINAL", "text": "Shift II tag-in complete." } ],
  "blackbox": {
    "sd_ok": true, "chunk_id": 42, "row": 517, "rows_per_chunk": 1000,
    "free_mb": 3712, "card_mb": 7580, "usb_ok": true, "ram_ring": 0, "power_ok": true,
    "retained": [ { "chunk_id": 41, "rows": 1000 } ]
  },
  "cloud": {
    "online": true, "pending": 163, "sent": 41354, "next_upload_s": 22,
    "last_upload": { "ts": "13:08:04", "rows": 240, "batches": 3, "ok": true },
    "last_ack":    { "chunk_id": 40, "ts": "13:08:04" }
  }
}
```

| Field | Meaning |
|---|---|
| `mode` | `LIVE` when real hardware data is flowing, anything else is shown as `SIM` |
| `nodes.*.velocity` | mm/s, `-100…100` |
| `nodes.*.peak_acceleration` | mm/s² since last re-arm, `-1…999` |
| `muster` | head-count: `underground ≤ total`, `safe ≤ total` |
| `events[]` | newest first, max 64; `level` ∈ `NOMINAL`, `WARNING`, `CRITICAL` |
| `blackbox` | state of the **main hub M-01 MicroSD flight recorder** (addendum §3–§7). Optional block — when present the Blackbox panel, the overview LED and the landing plate switch from local simulation to this feed. |
| `blackbox.sd_ok` | card mounted and writable; `false` → red `BLACKBOX: FAULT / NO SD` |
| `blackbox.chunk_id`, `row`, `rows_per_chunk` | Core 1 master counter: current `chunk_NNNN.csv` and row inside it |
| `blackbox.free_mb`, `card_mb` | card capacity; `free/card ≤ 5 %` arms the emergency circular FIFO indicator |
| `blackbox.usb_ok` | USB serial hub → laptop alive |
| `blackbox.ram_ring` | **optional** — rows held in RAM while no card is present. The gateway omits it: protocol v1 carries no RAM-ring depth on the wire, and the panel keeps its default when the field is absent. |
| `blackbox.power_ok` | `false` = hub in brownout / rebooting |
| `blackbox.retained[]` | closed chunk files still on the card, i.e. awaiting `ACK_DELETE` |
| `cloud.online` | HTTPS link to the Central Mining Cloud is up |
| `cloud.pending`, `sent` | `cloud_outbox` rows by status |
| `cloud.next_upload_s` | seconds until the 30 s upload daemon runs |
| `cloud.last_upload` | last batch result (`ok:false` = HTTPS timeout) |
| `cloud.last_ack` | **optional** — last `ACK_DELETE:NNNN` sent to the hub. Appears only once the gate has actually released a chunk, so it is absent on a fresh gateway rather than null. |

## `GET /api/history`

Start-up backfill for the sparklines — up to 240 samples per node (2 Hz ⇒ the last two minutes), oldest first, same fields as `telemetry.nodes.*`.

```json
{ "node_1": [ { "status": "NOMINAL", "displacement_mm": 5.2, "tilt_x": 1.1, "acceleration": 0.01 } ], "node_2": [] }
```

## `GET /api/ai`

```json
{
  "model": "NS-TTF v1", "status": "ACTIVE", "horizon": "6 h", "last_train": "2026-09-19 04:00",
  "confidence": "0.87", "note": "Free text shown on the AI panel (≤ 180 chars).",
  "forecasts": { "node_1": { }, "node_2": { } }
}
```

`status` is coloured: `ACTIVE` green; `AWAITING_INTEGRATION`, `WARMING UP` (model buffer still filling) and `FALLBACK` (model stale or contradicted by the physics) amber; anything else red. A node present in `forecasts` is tagged **AI FORECAST ACTIVE**, otherwise **KINEMATIC MODE** (the dashboard extrapolates x(t)=x₀+v·t+½a·t² itself). Until the ML service exists, return `AWAITING_INTEGRATION` with an empty `forecasts` object — the mock does this.

## `POST /api/sim/trigger`

Body `{ "node": "node_1" | "node_2", "severity": "warning" | "critical" }`. Sent when an operator arms a scenario from the Scenario Injection console while the gateway is online; the gateway should inject the scenario into its own telemetry stream. Reply `200 {"ok":true}`.

## `POST /api/reset`

Re-arm: clear scenarios, restore crew positions, reset peaks. Reply `200 {"ok":true}`.

## `POST /api/cloud/force_sync`

Safety-officer control from the Offline Outbox Console (operator deck → BLACKBOX tab, **Force Cloud Sync**): run the upload daemon now (all `PENDING` rows in ≤100-row GZIP + HMAC-SHA256 batches), then perform the `COUNT(PENDING)=0 → ACK_DELETE:NNNN` audit. Reply `200 {"ok":true,"uploaded":187,"acked":[41]}` — or `503 {"ok":false}` when the cloud is unreachable (rows stay `PENDING`, nothing is deleted). The dashboard re-polls `/api/extended` immediately after the reply.

## Mock-only: `POST /api/mock/faults`

Body with any of `sd_ok`, `usb_ok`, `online`, `power_ok` (booleans) — simulates the addendum's disaster table on the live feed so the red LED / fault chips can be demonstrated without hardware. Not part of the production contract.

---

## Route table for the Flask gateway

```
GET  /                      → index.html (single file) — or src/index.html + /css, /js when serving the sources
GET  /api/telemetry
GET  /api/extended
GET  /api/history
GET  /api/ai
POST /api/sim/trigger
POST /api/reset
POST /api/cloud/force_sync
```
