"""
Neural Strata backend -- FastAPI application.

You normally do NOT need to edit this file. It wires the six endpoints to the
functions in sources.py, which is where your sensor code goes.

Run it:
    uvicorn app.main:app --reload --port 8000
or just press F5 in VS Code.

Then open:
    http://127.0.0.1:8000/          -> the dashboard, already connected
    http://127.0.0.1:8000/docs      -> interactive API docs, try every endpoint
"""

import logging
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import sources
from .models import (
    AiInfo,
    Extended,
    HistorySample,
    OkResponse,
    Telemetry,
    TriggerRequest,
)

log = logging.getLogger("neural-strata")

app = FastAPI(
    title="Neural Strata API",
    version="1.0.0",
    description=(
        "Backend for the Neural Strata mining-safety dashboard.\n\n"
        "`GET /api/telemetry` is the only required endpoint -- the dashboard "
        "decides it is LIVE purely on that call succeeding. The rest degrade "
        "gracefully."
    ),
)

# Allows the dashboard to be served from a different origin than this API.
# Fine for local development; narrow allow_origins to your real host in
# production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# API. Each route is a thin wrapper: it never contains sensor logic, and it
# never lets an exception reach the dashboard -- a 500 would knock the page
# back into SIM mode. On error we log and return a safe default.
# --------------------------------------------------------------------------
@app.get("/api/telemetry", response_model=Telemetry, tags=["telemetry"])
def get_telemetry() -> Telemetry:
    """Node readings, routes and crew. Polled every 500 ms."""
    try:
        return sources.read_telemetry()
    except Exception:
        log.exception("read_telemetry failed")
        return Telemetry()


@app.get("/api/extended", response_model=Extended, tags=["telemetry"])
def get_extended() -> Extended:
    """Velocities, muster counts and the event log. Polled every 1500 ms."""
    try:
        return sources.read_extended()
    except Exception:
        log.exception("read_extended failed")
        return Extended()


@app.get(
    "/api/history",
    response_model=Dict[str, List[HistorySample]],
    tags=["telemetry"],
)
def get_history() -> Dict[str, List[HistorySample]]:
    """Chart back-fill, requested once at page load. Up to 240 samples/node."""
    try:
        return sources.read_history()
    except Exception:
        log.exception("read_history failed")
        return {node_id: [] for node_id in sources.NODE_IDS}


@app.get("/api/ai", response_model=AiInfo, tags=["ai"])
def get_ai() -> AiInfo:
    """Model metadata for the AI card. Polled every 5 s."""
    try:
        return sources.read_ai()
    except Exception:
        log.exception("read_ai failed")
        return AiInfo()


@app.post("/api/sim/trigger", response_model=OkResponse, tags=["test hooks"])
def post_trigger(req: TriggerRequest, response: Response) -> OkResponse:
    """Inject a fault, for testing alarm paths. Remove in production."""
    ok = sources.trigger(req.node, req.severity)
    if not ok:
        response.status_code = 400
    return OkResponse(ok=ok)


@app.post("/api/reset", response_model=OkResponse, tags=["test hooks"])
def post_reset() -> OkResponse:
    """Clear injected faults and return to baseline. Remove in production."""
    sources.reset()
    return OkResponse(ok=True)


# --------------------------------------------------------------------------
# Serve the dashboard from this same server, so there is no CORS to think
# about and no second process to start. index.html is expected one directory
# above backend/ -- the layout the ZIP ships with.
# --------------------------------------------------------------------------
INDEX = Path(__file__).resolve().parents[2] / "index.html"


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(INDEX)
