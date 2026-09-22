"""HTTP API for the tide-gate sailing scheduler."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.errors import UniqueViolation

from . import db, services
from .schemas import (
    AdoptVoyageRequest,
    CalendarCreate,
    CalendarOut,
    IntervalsResponse,
    PlanOut,
    ReplayResponse,
    VoyageRequest,
    WitnessOut,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    with db.psycopg.connect(db.database_url()) as conn:
        db.initialize_database(conn)
    yield


app = FastAPI(
    title="Tide Gate Scheduler",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors(include_url=False)},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/calendars", response_model=CalendarOut, status_code=status.HTTP_201_CREATED)
def publish_calendar(
    payload: CalendarCreate,
    conn=Depends(db.get_connection),
) -> dict[str, Any]:
    calendar_id = payload.id or str(uuid.uuid4())
    gates = [
        {
            "gate_id": gate.gate_id,
            "windows": [window.model_dump() for window in gate.windows],
        }
        for gate in payload.gates
    ]

    if db.calendar_exists(conn, calendar_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="calendar id already exists")

    try:
        db.create_calendar(conn, calendar_id, gates)
    except UniqueViolation:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="calendar id already exists",
        ) from None

    return {"id": calendar_id, "gates": payload.gates}


@app.get("/calendars/{calendar_id}", response_model=CalendarOut)
def get_calendar(calendar_id: str, conn=Depends(db.get_connection)) -> dict[str, Any]:
    calendar = db.fetch_calendar(conn, calendar_id)
    if calendar is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="calendar not found")
    return calendar


@app.post("/calendars/{calendar_id}/voyages", response_model=IntervalsResponse)
def search_voyage(
    calendar_id: str,
    payload: VoyageRequest,
    conn=Depends(db.get_connection),
) -> dict[str, Any]:
    if not db.calendar_exists(conn, calendar_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="calendar not found")

    windows_by_gate = db.fetch_gate_windows(conn, calendar_id, payload.gate_ids)
    try:
        intervals = services.solve_voyage(windows_by_gate, payload)
    except services.SchedulingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"intervals": [{"start": start, "end": end} for start, end in intervals]}


@app.post(
    "/calendars/{calendar_id}/voyages/adopt",
    response_model=PlanOut,
    status_code=status.HTTP_201_CREATED,
)
def adopt_voyage(
    calendar_id: str,
    payload: AdoptVoyageRequest,
    conn=Depends(db.get_connection),
) -> dict[str, Any]:
    if not db.calendar_exists(conn, calendar_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="calendar not found")

    voyage = payload.voyage
    try:
        result = services.adopt_departure(conn, calendar_id, voyage, payload.departure)
    except services.SchedulingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "id": result.plan_id,
        "calendar_id": calendar_id,
        "departure": payload.departure,
        "witnesses": [
            WitnessOut(
                gate_id=witness.gate_id,
                arrival=witness.arrival,
                entry=witness.entry,
                wait=witness.wait,
            )
            for witness in result.witnesses
        ],
    }


@app.post("/calendars/{calendar_id}/plans/{plan_id}/replay", response_model=ReplayResponse)
def replay_plan(
    calendar_id: str,
    plan_id: str,
    conn=Depends(db.get_connection),
) -> dict[str, Any]:
    if not db.calendar_exists(conn, calendar_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="calendar not found")

    plan = db.fetch_plan(conn, plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")

    return services.replay_plan(conn, plan, calendar_id)
