import time

from app.domain import feasible_departures


def test_two_hundred_thousand_windows_complete_within_three_seconds():
    # 200 gates * 1,000 non-overlapping windows = 200,000 gate windows.
    gate_count = 200
    windows_per_gate = 1000
    horizon = 1_000_000
    step = horizon // windows_per_gate

    windows = [
        [(index * step, index * step + step // 2) for index in range(windows_per_gate)]
        for _ in range(gate_count)
    ]

    started = time.perf_counter()
    result = feasible_departures(
        windows,
        [10**12] * gate_count,
        [0] * (gate_count - 1),
        (0, horizon),
    )
    elapsed = time.perf_counter() - started

    assert result
    assert result[0][0] == 0
    assert elapsed < 3.0
