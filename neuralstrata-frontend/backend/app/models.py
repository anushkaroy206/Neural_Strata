"""
Response shapes for the Neural Strata dashboard API.

These Pydantic models ARE the contract. If a response validates against the
model here, the dashboard can render it. FastAPI also turns them into the
interactive docs at http://127.0.0.1:8000/docs, so you can see and test the
exact JSON the frontend expects without opening the dashboard at all.

Ranges in the Field(...) descriptions are what the frontend clamps to. Sending
values outside them is safe (they get clamped, never crash the UI) but the
reading will display saturated at the limit -- see "What the frontend
tolerates" in README.md.
"""

from typing import Dict, List, Literal, Union

from pydantic import BaseModel, Field

# The three states the dashboard knows how to colour. Anything else is
# treated as NOMINAL by the frontend, so don't invent new ones here --
# map your hardware's states onto these.
Status = Literal["NOMINAL", "WARNING", "CRITICAL"]
RouteStatus = Literal["OPEN", "BLOCKED"]


class NodeReading(BaseModel):
    """One geotechnical sensor node."""

    status: Status = "NOMINAL"
    tilt_x: float = Field(0.0, description="degrees, clamped to +/-45")
    displacement_mm: float = Field(0.0, description="mm, clamped to 0..100")
    acceleration: float = Field(0.0, description="mm/s^2, clamped to -1..50")


class Underground(BaseModel):
    """Escape route availability. Only an explicit BLOCKED closes a route."""

    route_A_status: RouteStatus = "OPEN"
    route_B_status: RouteStatus = "OPEN"


class RfidEntry(BaseModel):
    """One person, last seen. Drives the crew counts and the evac map."""

    id: str
    name: str
    # Unknown zones are placed at 'Headframe' (surface) by the frontend
    # rather than dropped, so a typo hides someone on the surface instead
    # of losing them entirely. Prefer these exact strings.
    zone: str = Field(
        ...,
        description="Tunnel A | Tunnel B | Evacuating A | Evacuating B | Safe Exit | Headframe",
    )
    time: str = Field(..., description="display string, e.g. '14:32:07'")


class Telemetry(BaseModel):
    """
    GET /api/telemetry -- polled every 500 ms. THE ONLY REQUIRED ENDPOINT.

    The dashboard decides it is LIVE purely on this call succeeding.
    """

    system_status: Status = "NOMINAL"
    # "N/A" when there is no meaningful time-to-failure, else seconds 0..86400
    ttf_seconds: Union[int, Literal["N/A"]] = "N/A"
    nodes: Dict[str, NodeReading] = Field(
        default_factory=dict, description="keys 'node_1' and 'node_2'"
    )
    underground: Underground = Field(default_factory=Underground)
    rfid_log: List[RfidEntry] = Field(default_factory=list, description="<= 64")


class NodeExtended(BaseModel):
    velocity: float = Field(0.0, description="mm/s, clamped to +/-100")
    peak_acceleration: float = Field(0.0, description="clamped to -1..999")


class Muster(BaseModel):
    """Headcount. 'underground' + 'safe' should equal 'total'."""

    total: int = 0
    underground: int = 0
    safe: int = 0


class Event(BaseModel):
    time: str
    level: str = Field("info", description="info | warn | crit")
    text: str


class Extended(BaseModel):
    """GET /api/extended -- polled every 1500 ms. Optional."""

    mode: Literal["LIVE", "SIM"] = "LIVE"
    nodes: Dict[str, NodeExtended] = Field(default_factory=dict)
    muster: Muster = Field(default_factory=Muster)
    events: List[Event] = Field(default_factory=list, description="<= 64")


class HistorySample(BaseModel):
    """One past reading, used to back-fill the charts at page load."""

    status: Status = "NOMINAL"
    tilt_x: float = 0.0
    displacement_mm: float = 0.0
    acceleration: float = 0.0


class AiInfo(BaseModel):
    """
    GET /api/ai -- polled every 5 s. Optional, pure metadata for the AI card.

    status == 'ACTIVE'              -> green
    status == 'AWAITING_INTEGRATION'-> amber
    anything else                   -> red
    """

    model: str = "—"
    status: str = "AWAITING_INTEGRATION"
    horizon: str = "—"
    last_train: str = "—"
    confidence: str = "—"
    note: str = ""


class TriggerRequest(BaseModel):
    """POST /api/sim/trigger -- scenario console. Drop it in production."""

    node: str = Field(..., description="'node_1' or 'node_2'")
    severity: str = Field(..., description="'warning' or 'critical'")


class OkResponse(BaseModel):
    ok: bool = True
