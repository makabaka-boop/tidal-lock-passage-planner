"""PostgreSQL persistence for immutable calendars and adopted plans."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg

TIME_LIMIT = 10**12
BIGINT_LIMIT = 9_223_372_036_854_775_807

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS calendars (
    id text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gates (
    calendar_id text NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
    position integer NOT NULL,
    gate_id text NOT NULL,
    PRIMARY KEY (calendar_id, gate_id),
    UNIQUE (calendar_id, position)
);

CREATE TABLE IF NOT EXISTS gate_windows (
    calendar_id text NOT NULL,
    gate_id text NOT NULL,
    position integer NOT NULL,
    start_time bigint NOT NULL CHECK (start_time >= 0 AND start_time <= %s),
    end_time bigint NOT NULL CHECK (end_time > start_time AND end_time <= %s),
    PRIMARY KEY (calendar_id, gate_id, position),
    FOREIGN KEY (calendar_id, gate_id)
        REFERENCES gates(calendar_id, gate_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plans (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    calendar_id text NOT NULL REFERENCES calendars(id),
    departure bigint NOT NULL CHECK (departure >= 0 AND departure <= %s),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plan_route (
    plan_id uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    position integer NOT NULL,
    gate_id text NOT NULL,
    travel_time bigint NOT NULL CHECK (travel_time >= 0 AND travel_time <= %s),
    max_wait_time bigint NOT NULL CHECK (max_wait_time >= 0 AND max_wait_time <= %s),
    arrival_time bigint NOT NULL CHECK (arrival_time >= 0 AND arrival_time <= %s),
    entry_time bigint NOT NULL CHECK (
        entry_time >= arrival_time
        AND entry_time >= 0
        AND entry_time <= %s
    ),
    PRIMARY KEY (plan_id, position)
);

CREATE INDEX IF NOT EXISTS idx_gate_windows_lookup
    ON gate_windows (calendar_id, gate_id, position);
"""


def database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://tide:tide@localhost:5432/tide_scheduler",
    )


def get_connection() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(database_url()) as conn:
        yield conn


def initialize_database(conn: psycopg.Connection[Any]) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            SCHEMA_SQL,
            (
                TIME_LIMIT,
                TIME_LIMIT,
                TIME_LIMIT,
                TIME_LIMIT,
                TIME_LIMIT,
                BIGINT_LIMIT,
                BIGINT_LIMIT,
            ),
        )
    conn.commit()


def calendar_exists(conn: psycopg.Connection[Any], calendar_id: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1 FROM calendars WHERE id = %s", (calendar_id,))
        return cursor.fetchone() is not None


def create_calendar(
    conn: psycopg.Connection[Any],
    calendar_id: str,
    gates: list[dict[str, Any]],
) -> None:
    """Insert a validated calendar in one transaction."""

    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO calendars (id) VALUES (%s)", (calendar_id,))
        cursor.executemany(
            "INSERT INTO gates (calendar_id, position, gate_id) VALUES (%s, %s, %s)",
            [
                (calendar_id, position, gate["gate_id"])
                for position, gate in enumerate(gates)
            ],
        )
        with cursor.copy(
            """
            COPY gate_windows
                (calendar_id, gate_id, position, start_time, end_time)
            FROM STDIN
            """
        ) as copy:
            for gate_position, gate in enumerate(gates):
                for window_position, window in enumerate(gate["windows"]):
                    copy.write_row(
                        (
                            calendar_id,
                            gate["gate_id"],
                            window_position,
                            window["start"],
                            window["end"],
                        )
                    )
    conn.commit()


def fetch_calendar(
    conn: psycopg.Connection[Any], calendar_id: str
) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT g.position, g.gate_id, w.position, w.start_time, w.end_time
            FROM gates g
            JOIN gate_windows w
              ON w.calendar_id = g.calendar_id AND w.gate_id = g.gate_id
            WHERE g.calendar_id = %s
            ORDER BY g.position, w.position
            """,
            (calendar_id,),
        )
        rows = cursor.fetchall()

    if not rows:
        return None if not calendar_exists(conn, calendar_id) else {"id": calendar_id, "gates": []}

    gates: dict[int, dict[str, Any]] = {}
    for gate_position, gate_id, _window_position, start, end in rows:
        gate = gates.setdefault(gate_position, {"gate_id": gate_id, "windows": []})
        gate["windows"].append({"start": start, "end": end})

    return {"id": calendar_id, "gates": [gates[index] for index in sorted(gates)]}


def fetch_gate_windows(
    conn: psycopg.Connection[Any], calendar_id: str, gate_ids: list[str]
) -> list[list[tuple[int, int]]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT gate_id, start_time, end_time
            FROM gate_windows
            WHERE calendar_id = %s AND gate_id = ANY(%s)
            ORDER BY gate_id, position
            """,
            (calendar_id, gate_ids),
        )
        rows = cursor.fetchall()

    grouped: dict[str, list[tuple[int, int]]] = {gate_id: [] for gate_id in gate_ids}
    for gate_id, start, end in rows:
        grouped[gate_id].append((start, end))

    return [grouped[gate_id] for gate_id in gate_ids]


@dataclass(frozen=True, slots=True)
class StoredWitness:
    gate_id: str
    travel_time: int
    max_wait_time: int
    arrival: int
    entry: int


@dataclass(frozen=True, slots=True)
class StoredPlan:
    id: str
    calendar_id: str
    departure: int
    route: list[StoredWitness]


def create_plan(
    conn: psycopg.Connection[Any],
    calendar_id: str,
    departure: int,
    witnesses: list[dict[str, int]],
    travel_times: list[int],
    max_wait_times: list[int],
) -> str:
    with conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO plans (calendar_id, departure)
                VALUES (%s, %s)
                RETURNING id
                """,
                (calendar_id, departure),
            )
            plan_id = cursor.fetchone()[0]
            cursor.executemany(
                """
                INSERT INTO plan_route
                    (plan_id, position, gate_id, travel_time, max_wait_time,
                     arrival_time, entry_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        plan_id,
                        position,
                        witness["gate_id"],
                        travel_times[position] if position < len(travel_times) else 0,
                        max_wait_times[position],
                        witness["arrival"],
                        witness["entry"],
                    )
                    for position, witness in enumerate(witnesses)
                ],
            )
    return str(plan_id)


def fetch_plan(conn: psycopg.Connection[Any], plan_id: str) -> StoredPlan | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.id, p.calendar_id, p.departure,
                   r.gate_id, r.travel_time, r.max_wait_time,
                   r.arrival_time, r.entry_time
            FROM plans p
            JOIN plan_route r ON r.plan_id = p.id
            WHERE p.id = %s
            ORDER BY r.position
            """,
            (plan_id,),
        )
        rows = cursor.fetchall()

    if not rows:
        return None

    plan_id_value, calendar_id, departure = rows[0][0], rows[0][1], rows[0][2]
    route = [
        StoredWitness(
            gate_id=row[3],
            travel_time=row[4],
            max_wait_time=row[5],
            arrival=row[6],
            entry=row[7],
        )
        for row in rows
    ]
    return StoredPlan(str(plan_id_value), calendar_id, departure, route)
