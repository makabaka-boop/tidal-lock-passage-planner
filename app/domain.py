"""Pure scheduling domain logic.

Times are integer seconds. Every time set is represented by disjoint,
left-closed/right-open integer intervals. A value exactly equal to a window's
``end`` is therefore outside that window.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Sequence

Window = tuple[int, int]


@dataclass(slots=True)
class IntervalSet:
    """Merged disjoint intervals with one deferred additive offset."""

    starts: list[int]
    ends: list[int]
    shift: int = 0

    @classmethod
    def from_intervals(cls, intervals: Sequence[Window], shift: int = 0) -> "IntervalSet":
        starts: list[int] = []
        ends: list[int] = []
        for raw_start, raw_end in sorted(intervals):
            if raw_start >= raw_end:
                continue
            if starts and raw_start <= ends[-1]:
                if raw_end > ends[-1]:
                    ends[-1] = raw_end
            else:
                starts.append(raw_start)
                ends.append(raw_end)
        return cls(starts, ends, shift)

    @classmethod
    def from_windows(cls, windows: Sequence[Window]) -> "IntervalSet":
        return cls.from_intervals(windows)

    @property
    def empty(self) -> bool:
        return not self.starts

    def actual_intervals(self) -> list[Window]:
        shift = self.shift
        return [(start + shift, end + shift) for start, end in zip(self.starts, self.ends)]

    def intersect_actual(self, lower: int, upper: int) -> list[Window]:
        """Return intersection with [lower, upper) in actual coordinates."""

        if lower >= upper or self.empty:
            return []
        raw_lower = lower - self.shift
        raw_upper = upper - self.shift
        i = bisect_right(self.ends, raw_lower)
        result: list[Window] = []
        shift = self.shift
        while i < len(self.starts) and self.starts[i] < raw_upper:
            start = max(self.starts[i], raw_lower) + shift
            end = min(self.ends[i], raw_upper) + shift
            result.append((start, end))
            i += 1
        return result

def gate_preimage(
    entries: IntervalSet,
    window: Window,
    max_wait: int,
    previous_end: int | None = None,
) -> IntervalSet:
    """Map feasible entry times at one gate back to feasible arrival times.

    The arrival-to-entry rule is: enter immediately during the window;
    otherwise, before the window, enter at its start if the wait is allowed.
    """

    if entries.empty:
        return IntervalSet([], [], entries.shift)

    starts = entries.starts
    ends = entries.ends
    shift = entries.shift
    window_start, window_end = window
    raw_start = window_start - shift
    raw_end = window_end - shift
    raw_wait_lower = raw_start - max_wait
    if previous_end is not None:
        raw_wait_lower = max(raw_wait_lower, previous_end - shift)

    # Common, performance-sensitive case for one broad feasible interval.
    # When one source interval contains the whole window, its preimage is a
    # single interval extending back through the permitted wait range.
    if len(starts) == 1 and starts[0] < raw_end and ends[0] > raw_start:
        pre_end = min(ends[0], raw_end)
        pre_start = max(starts[0], raw_start)
        # Every wait arrival maps to the single entry s; only s itself has to
        # be in the source interval. Those arrivals are not bounded by starts[0].
        if starts[0] <= raw_start < ends[0]:
            pre_start = raw_wait_lower
        if pre_start == starts[0] and pre_end == ends[0]:
            return entries
        return IntervalSet([pre_start], [pre_end], shift)

    out_starts: list[int] = []
    out_ends: list[int] = []

    def add(raw_a: int, raw_b: int) -> None:
        if raw_a >= raw_b:
            return
        if out_starts and raw_a <= out_ends[-1]:
            if raw_b > out_ends[-1]:
                out_ends[-1] = raw_b
        else:
            out_starts.append(raw_a)
            out_ends.append(raw_b)

    # Arrivals in the wait range produce the single entry time window_start.
    containing = bisect_right(starts, raw_start) - 1
    if containing >= 0 and starts[containing] <= raw_start < ends[containing]:
        add(raw_wait_lower, raw_start)

    # Arrivals during the window enter at the same time.
    j = bisect_right(ends, raw_start)
    while j < len(starts) and starts[j] < raw_end:
        clipped_start = max(starts[j], raw_start)
        clipped_end = min(ends[j], raw_end)
        if clipped_start < clipped_end:
            add(clipped_start, clipped_end)
        if ends[j] <= raw_end:
            j += 1
        else:
            break

    return IntervalSet(out_starts, out_ends, shift)


def gate_preimage_multi(
    entries: IntervalSet,
    windows: Sequence[Window],
    max_wait: int,
) -> IntervalSet:
    """Map feasible entries back through all sorted windows for one gate."""

    if entries.empty or not windows:
        return IntervalSet([], [], entries.shift)
    if len(windows) == 1:
        return gate_preimage(entries, windows[0], max_wait)

    starts = entries.starts
    ends = entries.ends
    shift = entries.shift
    out_starts: list[int] = []
    out_ends: list[int] = []

    def add(raw_a: int, raw_b: int) -> None:
        if raw_a >= raw_b:
            return
        if out_starts and raw_a <= out_ends[-1]:
            if raw_b > out_ends[-1]:
                out_ends[-1] = raw_b
        else:
            out_starts.append(raw_a)
            out_ends.append(raw_b)

    immediate_index = bisect_right(ends, windows[0][0] - shift)

    for window_index, (actual_window_start, actual_window_end) in enumerate(windows):
        raw_start = actual_window_start - shift
        raw_end = actual_window_end - shift
        raw_wait_lower = raw_start - max_wait
        actual_previous_end = windows[window_index - 1][1] if window_index > 0 else None
        if actual_previous_end is not None:
            raw_wait_lower = max(raw_wait_lower, actual_previous_end - shift)

        # All wait arrivals map to the single entry time raw_start.
        # Membership of that singleton, not overlap of the whole wait range,
        # decides whether the full permitted wait range is feasible.
        containing = bisect_right(starts, raw_start) - 1
        if containing >= 0 and starts[containing] <= raw_start < ends[containing]:
            add(raw_wait_lower, raw_start)

        while immediate_index < len(starts) and ends[immediate_index] <= raw_start:
            immediate_index += 1

        j = immediate_index
        while j < len(starts) and starts[j] < raw_end:
            clipped_start = max(starts[j], raw_start)
            clipped_end = min(ends[j], raw_end)
            if clipped_start < clipped_end:
                add(clipped_start, clipped_end)
            if ends[j] <= raw_end:
                j += 1
            else:
                break
        immediate_index = j

    return IntervalSet(out_starts, out_ends, shift)


def feasible_departures(
    gate_windows: Sequence[Sequence[Window]],
    max_waits: Sequence[int],
    travel_times: Sequence[int],
    search_window: Window,
) -> list[Window]:
    """Return merged feasible first-gate departure times in the search range."""

    if not gate_windows:
        return []

    current = IntervalSet.from_windows(gate_windows[-1])
    for gate_index in range(len(gate_windows) - 1, -1, -1):
        current = gate_preimage_multi(
            current,
            gate_windows[gate_index],
            max_waits[gate_index],
        )
        if current.empty:
            return []
        if gate_index > 0:
            # Entry at this gate becomes arrival at the next gate; reverse
            # the preceding travel segment.
            current.shift -= travel_times[gate_index - 1]

    return current.intersect_actual(*search_window)


@dataclass(frozen=True, slots=True)
class GateWitness:
    gate_id: str
    arrival: int
    entry: int
    wait: int


def earliest_entry(windows: Sequence[Window], arrival: int, max_wait: int) -> int | None:
    starts = [window[0] for window in windows]
    index = bisect_right(starts, arrival) - 1
    if index >= 0 and arrival < windows[index][1]:
        return arrival

    future_index = index + 1
    if future_index < len(windows):
        entry = windows[future_index][0]
        if entry - arrival <= max_wait:
            return entry
    return None


def simulate_route(
    gate_ids: Sequence[str],
    gate_windows: Sequence[Sequence[Window]],
    max_waits: Sequence[int],
    travel_times: Sequence[int],
    departure: int,
) -> tuple[list[GateWitness], int | None]:
    """Simulate using earliest possible entry.

    Returns witnesses and, on failure, the arrival time at the failing gate.
    """

    arrival = departure
    witnesses: list[GateWitness] = []

    for index, gate_id in enumerate(gate_ids):
        entry = earliest_entry(gate_windows[index], arrival, max_waits[index])
        if entry is None:
            return witnesses, arrival
        witnesses.append(
            GateWitness(
                gate_id=gate_id,
                arrival=arrival,
                entry=entry,
                wait=entry - arrival,
            )
        )
        if index < len(gate_ids) - 1:
            arrival = entry + travel_times[index]

    return witnesses, None
