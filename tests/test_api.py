from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


class FakeDatabase:
    def __init__(self):
        self.calendars = {}
        self.plans = {}

    def reset(self):
        self.calendars.clear()
        self.plans.clear()


fake_db = FakeDatabase()


@pytest.fixture
def client(monkeypatch):
    fake_db.reset()

    def override_db():
        yield SimpleNamespace()

    app.dependency_overrides[db.get_connection] = override_db

    monkeypatch.setattr(db, "psycopg", SimpleNamespace(connect=lambda url: SimpleNamespace(__enter__=lambda self: self, __exit__=lambda *args: None)))
    monkeypatch.setattr(db, "initialize_database", lambda conn: None)
    monkeypatch.setattr(db, "calendar_exists", lambda conn, calendar_id: calendar_id in fake_db.calendars)
    monkeypatch.setattr(db, "create_calendar", fake_create_calendar)
    monkeypatch.setattr(db, "fetch_calendar", lambda conn, calendar_id: fake_db.calendars.get(calendar_id))
    monkeypatch.setattr(db, "fetch_gate_windows", fake_fetch_windows)
    monkeypatch.setattr(db, "create_plan", fake_create_plan)
    monkeypatch.setattr(db, "fetch_plan", lambda conn, plan_id: fake_db.plans.get(plan_id))

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def fake_create_calendar(conn, calendar_id, gates):
    fake_db.calendars[calendar_id] = {"id": calendar_id, "gates": gates}


def fake_fetch_windows(conn, calendar_id, gate_ids):
    calendar = fake_db.calendars[calendar_id]
    by_id = {gate["gate_id"]: [(w["start"], w["end"]) for w in gate["windows"]] for gate in calendar["gates"]}
    return [by_id.get(gate_id, []) for gate_id in gate_ids]


def fake_create_plan(conn, calendar_id, departure, witnesses, travel_times, max_waits):
    plan_id = f"plan-{len(fake_db.plans) + 1}"
    from app.db import StoredPlan, StoredWitness

    route = []
    for index, witness in enumerate(witnesses):
        route.append(
            StoredWitness(
                gate_id=witness["gate_id"],
                travel_time=travel_times[index] if index < len(travel_times) else 0,
                max_wait_time=max_waits[index],
                arrival=witness["arrival"],
                entry=witness["entry"],
            )
        )
    fake_db.plans[plan_id] = StoredPlan(plan_id, calendar_id, departure, route)
    return plan_id


def calendar_payload(calendar_id="cal-1"):
    return {
        "id": calendar_id,
        "gates": [
            {"gate_id": "A", "windows": [{"start": 10, "end": 20}, {"start": 20, "end": 30}]},
            {"gate_id": "B", "windows": [{"start": 25, "end": 40}]},
        ],
    }


def voyage_payload():
    return {
        "gate_ids": ["A", "B"],
        "travel_times": [5],
        "max_wait_times": [5, 0],
        "search_start": 0,
        "search_end": 30,
    }


def test_publish_calendar_and_search_intervals(client):
    response = client.post("/calendars", json=calendar_payload())
    assert response.status_code == 201
    assert response.json()["id"] == "cal-1"

    response = client.post("/calendars/cal-1/voyages", json=voyage_payload())
    assert response.status_code == 200
    # Departures 20..29 reach B during [25, 40).
    assert response.json()["intervals"] == [{"start": 20, "end": 30}]


def test_invalid_calendar_is_rejected_and_not_persisted(client):
    payload = calendar_payload("bad")
    payload["gates"][0]["windows"][0]["start"] = 25
    response = client.post("/calendars", json=payload)
    assert response.status_code == 400
    assert client.get("/calendars/bad").status_code == 404


def test_end_boundary_is_closed(client):
    client.post("/calendars", json=calendar_payload())
    payload = voyage_payload()
    payload["search_start"] = 25
    payload["search_end"] = 26
    assert client.post("/calendars/cal-1/voyages", json=payload).json() == {"intervals": []}


def test_adopt_and_replay_valid_and_invalid_new_calendar(client):
    client.post("/calendars", json=calendar_payload())
    adopt_response = client.post(
        "/calendars/cal-1/voyages/adopt",
        json={"voyage": voyage_payload(), "departure": 20},
    )
    assert adopt_response.status_code == 201
    plan = adopt_response.json()
    assert [(w["gate_id"], w["arrival"], w["entry"]) for w in plan["witnesses"]] == [
        ("A", 20, 20),
        ("B", 25, 25),
    ]

    replay = client.post(f"/calendars/cal-1/plans/{plan['id']}/replay")
    assert replay.json()["status"] == "STILL_VALID"

    changed = calendar_payload("cal-2")
    changed["gates"][1]["windows"] = [{"start": 30, "end": 40}]
    client.post("/calendars", json=changed)
    failure = client.post(f"/calendars/cal-2/plans/{plan['id']}/replay").json()
    assert failure == {
        "status": "FAILING_GATE",
        "witnesses": None,
        "gate_id": "B",
        "arrival": 25,
        "wait_deadline": 25,
    }


def test_infeasible_adoption_is_rejected(client):
    client.post("/calendars", json=calendar_payload())
    response = client.post(
        "/calendars/cal-1/voyages/adopt",
        json={"voyage": voyage_payload(), "departure": 29},
    )
    assert response.status_code == 400
    assert not fake_db.plans


def test_unknown_gate_in_voyage_is_400(client):
    client.post("/calendars", json=calendar_payload())
    payload = voyage_payload()
    payload["gate_ids"] = ["A", "X"]
    response = client.post("/calendars/cal-1/voyages", json=payload)
    assert response.status_code == 400


def test_boolean_integer_and_extra_fields_are_rejected(client):
    payload = calendar_payload()
    payload["gates"][0]["windows"][0]["start"] = True
    assert client.post("/calendars", json=payload).status_code == 400

    payload = calendar_payload("extra")
    payload["unexpected"] = 1
    assert client.post("/calendars", json=payload).status_code == 400
