import random

import pytest

from app.domain import feasible_departures, simulate_route


def merge_points(points, lower, upper):
    selected = sorted(point for point in points if lower <= point < upper)
    if not selected:
        return []
    intervals = []
    start = previous = selected[0]
    for point in selected[1:]:
        if point == previous + 1:
            previous = point
        else:
            intervals.append((start, previous + 1))
            start = previous = point
    intervals.append((start, previous + 1))
    return intervals


def brute_force(gate_windows, max_waits, travel_times, search_window):
    start, end = search_window
    feasible = []
    for departure in range(start, end):
        _witnesses, failed_at = simulate_route(
            [str(index) for index in range(len(gate_windows))],
            gate_windows,
            max_waits,
            travel_times,
            departure,
        )
        if failed_at is None:
            feasible.append(departure)
    return merge_points(feasible, start, end)


def random_windows(rng, start=0, end=80, max_count=5):
    count = rng.randint(1, max_count)
    boundaries = sorted(rng.sample(range(start, end + 1), k=min(count * 2, end - start + 1)))
    windows = []
    for index in range(0, len(boundaries) - 1, 2):
        if boundaries[index] < boundaries[index + 1]:
            windows.append((boundaries[index], boundaries[index + 1]))
    return windows or [(0, 1)]


@pytest.mark.parametrize("seed", range(80))
def test_matches_second_by_second_enumeration(seed):
    rng = random.Random(seed)
    gate_count = rng.randint(1, 5)
    gate_windows = [random_windows(rng) for _ in range(gate_count)]
    travel_times = [rng.randint(0, 15) for _ in range(gate_count - 1)]
    max_waits = [rng.randint(0, 15) for _ in range(gate_count)]
    search_window = (0, 80)

    expected = brute_force(gate_windows, max_waits, travel_times, search_window)
    actual = feasible_departures(gate_windows, max_waits, travel_times, search_window)
    assert actual == expected


def test_extreme_boundaries_match_enumeration():
    cases = [
        ([[(0, 1)]], [0], [], (0, 2)),
        ([[(10, 11)], [(10, 11)]], [1, 1], [0], (0, 13)),
        ([[(0, 5), (5, 10)]], [1], [], (0, 12)),
        ([[(5, 10)], [(0, 20)]], [5, 20], [0], (0, 11)),
    ]
    for gate_windows, waits, travel, search in cases:
        expected = brute_force(gate_windows, waits, travel, search)
        assert feasible_departures(gate_windows, waits, travel, search) == expected
