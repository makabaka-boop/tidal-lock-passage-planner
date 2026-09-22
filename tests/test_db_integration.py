import os

import psycopg
import pytest

from app import db

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS and DATABASE_URL to run PostgreSQL integration tests",
)


@pytest.fixture
def conn():
    connection = psycopg.connect(db.database_url())
    db.initialize_database(connection)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM plans")
        cursor.execute("DELETE FROM calendars")
    connection.commit()
    yield connection
    connection.close()


def test_persist_and_reload_calendar_and_plan(conn):
    calendar_id = "integration-calendar"
    gates = [
        {"gate_id": "A", "windows": [{"start": 0, "end": 10}]},
        {"gate_id": "B", "windows": [{"start": 5, "end": 20}]},
    ]
    db.create_calendar(conn, calendar_id, gates)

    loaded = db.fetch_calendar(conn, calendar_id)
    assert loaded["gates"][0]["gate_id"] == "A"
    assert loaded["gates"][1]["windows"] == [{"start": 5, "end": 20}]

    witnesses = [
        {"gate_id": "A", "arrival": 0, "entry": 0, "wait": 0},
        {"gate_id": "B", "arrival": 5, "entry": 5, "wait": 0},
    ]
    plan_id = db.create_plan(conn, calendar_id, 0, witnesses, [5], [0, 0])
    plan = db.fetch_plan(conn, plan_id)
    assert plan is not None
    assert [route.gate_id for route in plan.route] == ["A", "B"]
    assert plan.route[0].travel_time == 5
    assert plan.route[1].travel_time == 0
