"""Request and response schemas."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

TIME_LIMIT = 10**12

BoundedInt = Annotated[int, Field(strict=True, ge=0, le=TIME_LIMIT)]
LargeInt = Annotated[int, Field(strict=True, ge=-(10**18), le=10**18)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WindowIn(StrictModel):
    start: BoundedInt
    end: BoundedInt


class GateIn(StrictModel):
    gate_id: StrictStr
    windows: list[WindowIn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_gate(self) -> Self:
        if not self.gate_id or not self.gate_id.isascii():
            raise ValueError("gate_id must be a non-empty ASCII string")

        previous_end = -1
        for window in self.windows:
            if window.start >= window.end:
                raise ValueError("each window must satisfy start < end")
            if window.start < previous_end:
                raise ValueError("windows must be sorted by start and must not overlap")
            previous_end = window.end
        return self


class CalendarCreate(StrictModel):
    id: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    gates: list[GateIn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_calendar(self) -> Self:
        seen: set[str] = set()
        for gate in self.gates:
            if gate.gate_id in seen:
                raise ValueError("gate_id values must be unique within a calendar")
            seen.add(gate.gate_id)
        return self


class WindowOut(StrictModel):
    start: int
    end: int


class GateOut(StrictModel):
    gate_id: str
    windows: list[WindowOut]


class CalendarOut(StrictModel):
    id: str
    gates: list[GateOut]


class VoyageRequest(StrictModel):
    gate_ids: list[StrictStr] = Field(min_length=1, max_length=200)
    travel_times: list[BoundedInt]
    max_wait_times: list[BoundedInt]
    search_start: BoundedInt
    search_end: BoundedInt

    @model_validator(mode="after")
    def validate_voyage(self) -> Self:
        if len(set(self.gate_ids)) != len(self.gate_ids):
            raise ValueError("voyage gates must be distinct")
        if any(not gate_id or not gate_id.isascii() for gate_id in self.gate_ids):
            raise ValueError("gate_id must be a non-empty ASCII string")
        if len(self.travel_times) != len(self.gate_ids) - 1:
            raise ValueError("travel_times length must be one less than gate_ids")
        if len(self.max_wait_times) != len(self.gate_ids):
            raise ValueError("max_wait_times length must equal gate_ids")
        if self.search_start >= self.search_end:
            raise ValueError("search window must satisfy start < end")
        return self


class IntervalsResponse(StrictModel):
    intervals: list[WindowOut]


class WitnessOut(StrictModel):
    gate_id: str
    arrival: LargeInt
    entry: LargeInt
    wait: LargeInt


class AdoptVoyageRequest(StrictModel):
    voyage: VoyageRequest
    departure: BoundedInt


class PlanOut(StrictModel):
    id: str
    calendar_id: str
    departure: int
    witnesses: list[WitnessOut]


class ReplayResponse(StrictModel):
    status: str
    witnesses: list[WitnessOut] | None = None
    gate_id: str | None = None
    arrival: LargeInt | None = None
    wait_deadline: LargeInt | None = None
