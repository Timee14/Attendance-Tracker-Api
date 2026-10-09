"""
Focused tests for POST /attendance/punch-out.
Uses isolated in-memory test database (mongomock).
"""
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import mongomock

from app import main
from app.main import app, ensure_indexes

@pytest.fixture(autouse=True)
def setup_test_db():
    mock_client = mongomock.MongoClient(tz_aware=True)
    test_db = mock_client["attendance_test_db"]
    main.db = test_db
    main.client = mock_client
    ensure_indexes(test_db)

    test_db.employees.insert_one({
        "emp_code": "EMP0001",
        "name": "Asha Rao",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05",
        "created_at": datetime(2026, 1, 5, 4, 30, tzinfo=timezone.utc)
    })
    yield test_db

@pytest.fixture
def client():
    return TestClient(app)

def test_punch_out_success_standard_day(client):
    in_dt = datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc)
    res_in = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": int(in_dt.timestamp() * 1000)})
    assert res_in.status_code == 201

    out_dt = datetime(2026, 7, 6, 13, 5, 0, tzinfo=timezone.utc)
    res_out = client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": int(out_dt.timestamp() * 1000)})
    assert res_out.status_code == 200
    data = res_out.json()
    assert data["work_hours"] == 9.12
    assert data["overtime_minutes"] == 0
    assert data["half_day"] is False
    assert data["punch_out"] == int(out_dt.timestamp() * 1000)

def test_punch_out_half_day_and_overtime(client):
    in_dt = datetime(2026, 7, 7, 4, 0, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": int(in_dt.timestamp() * 1000)})

    out_dt = datetime(2026, 7, 7, 7, 30, 0, tzinfo=timezone.utc)
    res_out = client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": int(out_dt.timestamp() * 1000)})
    assert res_out.status_code == 200
    data = res_out.json()
    assert data["work_hours"] == 3.5
    assert data["half_day"] is True

def test_punch_out_duplicate_409(client):
    in_dt = datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": int(in_dt.timestamp() * 1000)})

    out_dt = datetime(2026, 7, 6, 13, 5, 0, tzinfo=timezone.utc)
    res1 = client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": int(out_dt.timestamp() * 1000)})
    assert res1.status_code == 200

    res2 = client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": int(out_dt.timestamp() * 1000) + 1000})
    assert res2.status_code == 409

def test_punch_out_before_punch_in_422(client):
    in_dt = datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": int(in_dt.timestamp() * 1000)})

    out_dt = datetime(2026, 7, 6, 3, 0, 0, tzinfo=timezone.utc)
    res = client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": int(out_dt.timestamp() * 1000)})
    assert res.status_code == 404 or res.status_code == 422
