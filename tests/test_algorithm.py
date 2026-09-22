from app.domain import (
    IntervalSet,
    earliest_entry,
    feasible_departures,
    gate_preimage_multi,
    simulate_route,
)


def iv(result):
    return [(a, b) for a, b in result]


def test_window_end_is_excluded_and_exact_wait_deadline_is_allowed():
    windows = [[(10, 20)]]
    assert feasible_departures(windows, [10], [], (0, 30)) == [(0, 20)]
    assert feasible_departures(windows, [9], [], (0, 30)) == [(1, 20)]
    assert earliest_entry([(10, 20)], 0, 10) == 10
    assert earliest_entry([(10, 20)], 0, 9) is None
    assert earliest_entry([(10, 20)], 20, 0) is None


def test_touching_windows_are_effectively_continuous():
    windows = [[(0, 5), (5, 10)]]
    assert feasible_departures(windows, [0], [], (0, 10)) == [(0, 10)]


def test_wait_then_travel_to_next_gate():
    windows = [[(10, 20)], [(30, 40)]]
    assert feasible_departures(windows, [5, 0], [20], (0, 20)) == [(10, 20)]


def test_gap_can_be_bridged_only_with_enough_wait():
    windows = [[(0, 4), (6, 10)], [(0, 10)]]
    assert feasible_departures(windows, [2, 100], [0], (0, 10)) == [(0, 8)]
    assert feasible_departures(windows, [1, 100], [0], (0, 10)) == [(0, 3), (6, 10)]


def test_interval_set_intersection_handles_shifted_values():
    intervals = IntervalSet.from_intervals([(2, 5), (8, 10)], shift=-3)
    assert intervals.intersect_actual(2, 6) == [(2, 5)]


def test_multi_window_preimage_collects_immediate_and_wait_intervals():
    entries = IntervalSet.from_intervals([(0, 100)])
    result = gate_preimage_multi(entries, [(10, 20), (40, 50)], 5)
    assert result.actual_intervals() == [(5, 20), (35, 50)]


def test_simulation_witnesses_earliest_entries_and_failure_deadline():
    windows = [[(10, 20)], [(30, 35)]]
    witnesses, failed = simulate_route(
        ["a", "b"], windows, [5, 5], [15], 5
    )
    assert failed is None
    assert [(x.arrival, x.entry, x.wait) for x in witnesses] == [(5, 10, 5), (25, 30, 5)]

    witnesses, failed = simulate_route(
        ["a", "b"], [[(10, 20)], [(31, 35)]], [5, 0], [15], 5
    )
    assert witnesses[0].entry == 10
    assert failed == 25


def test_no_solution_returns_empty_list():
    assert feasible_departures([[(10, 20)]], [0], [], (20, 30)) == []


def test_search_window_clips_half_open_result():
    assert feasible_departures([[(0, 100)]], [0], [], (10, 20)) == [(10, 20)]


def test_search_window_does_not_promote_negative_times():
    assert feasible_departures([[(0, 10)]], [100], [], (0, 10)) == [(0, 10)]


def test_search_start_equal_to_window_end_is_empty():
    assert feasible_departures([[(0, 100)]], [0], [], (100, 200)) == []
