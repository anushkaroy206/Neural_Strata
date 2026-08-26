# Backend — build it in VS Code

A FastAPI scaffold that already serves the dashboard and answers all six
endpoints with placeholder data. You replace the placeholders with real sensor
reads.

## Start in 3 steps

1. **Open the `neural-strata` folder in VS Code** (File → Open Folder). Open
   the folder itself, not a single file, or F5 won't find the config.
2. **Install dependencies** — Terminal → Run Task → *Install backend
   dependencies*, or:
   ```bash
   pip install -r backend/requirements.txt
   ```
3. **Press F5**, choosing *Backend: FastAPI (reload)*.

Then open <http://127.0.0.1:8000/> — the dashboard, already connected and
showing LIVE. Interactive API docs are at <http://127.0.0.1:8000/docs>.

No dependencies installed yet? Press F5 and pick *Backend: reference server
(no dependencies)* instead — `server/app.py` is stdlib-only and needs nothing.

## The only file you edit

```
backend/app/sources.py     <-- your sensor code goes here (5 TODOs)
backend/app/models.py          response shapes = the contract
backend/app/main.py            routing; you rarely touch this
```

`sources.py` has five functions. Only the first is required:

| Function | Endpoint | If you skip it |
|---|---|---|
| `read_telemetry()` | `/api/telemetry` | **Required** — dashboard stays in SIM |
| `read_extended()` | `/api/extended` | Those panels stay at defaults |
| `read_history()` | `/api/history` | Charts start empty, fill as data arrives |
| `read_ai()` | `/api/ai` | AI card shows `—` |
| `trigger()` / `reset()` | test hooks | Scenario buttons no-op |

Uvicorn runs with `--reload`, so saving `sources.py` restarts the server and
the dashboard picks up the change within about half a second. Breakpoints in
`sources.py` hit on every poll.

## Three things that will save you time

**Don't raise from `sources.py`.** Return the last known good value instead.
The frontend reuses a node's previous sample when a field is missing, so a
brief gap flat-lines a chart rather than dropping it to zero. The flip side:
a sensor that silently stops reporting looks *stable*, not absent — signal a
dead sensor with `status` or by dropping the node from `nodes` entirely.

**Derive `system_status`, don't hardcode it.** `worst_status()` is provided.
The frontend re-derives it anyway as a safety net, so the banner can never
read NOMINAL while a node reports CRITICAL — but keep them in agreement.

**Don't block.** These functions are called on a 500 ms poll. If your source
is slow (a database query, a radio round-trip), cache it in a background
thread and return the cached value here.

## Demo state to delete

So the scenario buttons do something before hardware exists, `sources.py`
keeps an `_INJECTED` dict that fakes a fault. Delete `_INJECTED`, `_BASELINE`,
`_SEVERITY`, `trigger()` and `reset()` once you're reading real sensors.

## Checking your work

```bash
cd tests && npm install
node contract.js        # 88 assertions
```

The contract suite tests against the *contract*, not the reference server, so
it works as a conformance check on whatever you build. Run it if the dashboard
shows readings you don't expect.

## Using a different stack

Nothing ties the dashboard to Python. Any server answering these six endpoints
works — the shapes are in `models.py` and in the main README's API section. If
your backend runs on a different host or port, point the dashboard at it:

```
index.html?api=http://localhost:5000
```

Cross-origin backends must send CORS headers, or the browser blocks every
request and the dashboard sits silently in SIM mode showing plausible fake
data. That's the failure worth watching for, because it doesn't look like one.
