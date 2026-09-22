"""Application services coordinating persistence and scheduling rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import db
from .domain import (
    GateWitness,
    feasible_departures,
    simulate_route,
)
from .schemas import VoyageRequest


class SchedulingError(ValueError):
    """A semantically invalid scheduling request."""


def prepare_voyage(
    windows_by_gate: list[list[tuple[int, int]]],
    request: VoyageRequest,
) -> tuple[list[list[tuple[int, int]]], list[int], list[int], tuple[int, int]]:
    if any(not windows for windows in windows_by_gate):
        missing = request.gate_ids[next(i for i, value in enumerate(windows_by_gate) if not value)]
        raise SchedulingError(f"unknown gate_id in voyage: {missing}")

    return (
        windows_by_gate,
        list(request.max_wait_times),
        list(request.travel_times),
        (request.search_start, request.search_end),
    )


def solve_voyage(
    windows_by_gate: list[list[tuple[int, int]]],
    request: VoyageRequest,
) -> list[tuple[int, int]]:
    prepared = prepare_voyage(windows_by_gate, request)
    gate_windows, max_waits, travel_times, search_window = prepared
    return feasible_departures(gate_windows, max_waits, travel_times, search_window)


@dataclass(frozen=True, slots=True)
class AdoptResult:
    plan_id: str
    witnesses: list[GateWitness]


def adopt_departure(
    conn,
    calendar_id: str,
    request: VoyageRequest,
    departure: int,
) -> AdoptResult:
    windows_by_gate = db.fetch_gate_windows(conn, calendar_id, request.gate_ids)
    prepared = prepare_voyage(windows_by_gate, request)
    gate_windows, max_waits, travel_times, _search_window = prepared
    witnesses, failed_at = simulate_route(
        request.gate_ids,
        gate_windows,
        max_waits,
        travel_times,
        departure,
    )
    if failed_at is not None:
        raise SchedulingError("departure is not feasible")

    witness_payload = [
        {
            "gate_id": witness.gate_id,
            "arrival": witness.arrival,
            "entry": witness.entry,
            "wait": witness.wait,
        }
        for witness in witnesses
    ]
    plan_id = db.create_plan(
        conn,
        calendar_id,
        departure,
        witness_payload,
        travel_times,
        max_waits,
    )
    return AdoptResult(plan_id=plan_id, witnesses=witnesses)


def replay_plan(conn, plan: db.StoredPlan, target_calendar_id: str) -> dict[str, Any]:
    gate_ids = [witness.gate_id for witness in plan.route]
    windows_by_gate = db.fetch_gate_windows(conn, target_calendar_id, gate_ids)
    if any(not windows for windows in windows_by_gate):
        index = next(i for i, value in enumerate(windows_by_gate) if not value)
        stored = plan.route[index]
        return {
            "status": "FAILING_GATE",
            "witnesses": None,
            "gate_id": stored.gate_id,
            "arrival": stored.arrival,
            "wait_deadline": stored.arrival + stored.max_wait_time,
        }

    max_waits = [witness.max_wait_time for witness in plan.route]
    travel_times = [witness.travel_time for witness in plan.route[:-1]]
    witnesses, failed_at = simulate_route(
        gate_ids,
        windows_by_gate,
        max_waits,
        travel_times,
        plan.departure,
    )

    if failed_at is None:
        return {
            "status": "STILL_VALID",
            "witnesses": [
                {
                    "gate_id": witness.gate_id,
                    "arrival": witness.arrival,
                    "entry": witness.entry,
                    "wait": witness.wait,
                }
                for witness in witnesses
            ],
            "gate_id": None,
            "arrival": None,
            "wait_deadline": None,
        }

    index = len(witnesses)
    stored = plan.route[index]
    return {
        "status": "FAILING_GATE",
        "witnesses": None,
        "gate_id": stored.gate_id,
        "arrival": failed_at,
        "wait_deadline": failed_at + stored.max_wait_time,
    }
