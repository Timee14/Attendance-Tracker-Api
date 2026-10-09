"""
Focused tests for POST /attendance/punch-in endpoint.
Uses isolated in-memory test database (mongomock).
"""
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
import mongomock

from app import main
from app.main import app, IST, ensure_indexes

@pytest.fixture(autouse=True)
def setup_test_db():
    mock_client = mongomock.MongoClient(tz_aware=True)
    test_db = mock_client["attendance_test_db"]
    main.db = test_db
    main.client = mock_client
    ensure_indexes(test_db)

    test_db.employees.insert_many([
        {
            "emp_code": "EMP0001",
            "name": "Asha Rao",
            "email": "asha@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
            "created_at": datetime(2026, 1, 5, 4, 30, tzinfo=timezone.utc)
        },
        {
            "emp_code": "EMP0005",
            "name": "Meera Nair",
            "email": "meera@example.com",
            "department": "Support",
            "shift_start": "22:00",
            "shift_end": "06:00",
            "joined_on": "2026-03-10",
            "created_at": datetime(2026, 3, 10, 4, 30, tzinfo=timezone.utc)
        }
    ])
    yield test_db

@pytest.fixture
def client():
    return TestClient(app)

def test_punch_in_success_on_time(client):
    dt = datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)

    res = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms, "status": "PRESENT"})
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == "EMP0001"
    assert data["date"] == "2026-07-06"
    assert data["status"] == "PRESENT"
    assert data["punch_in"] == ms
    assert data["punch_out"] is None
    assert data["work_hours"] is None
    assert data["late_minutes"] == 0
    assert data["overtime_minutes"] == 0
    assert data["half_day"] is False
    assert data["history"] == []
    assert "_id" not in data
    assert "id" not in data

def test_punch_in_r2_grace_period_exact_10_min(client):
    dt = datetime(2026, 7, 6, 4, 10, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)

    res = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms})
    assert res.status_code == 201
    assert res.json()["late_minutes"] == 0

def test_punch_in_r2_grace_period_10_min_1_sec_late(client):
    dt = datetime(2026, 7, 6, 4, 10, 1, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)

    res = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms})
    assert res.status_code == 201
    assert res.json()["late_minutes"] == 10

def test_punch_in_r2_45_min_late(client):
    dt = datetime(2026, 7, 6, 4, 45, 59, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)

    res = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms})
    assert res.status_code == 201
    assert res.json()["late_minutes"] == 45

def test_punch_in_r1_second_truncation(client):
    dt = datetime(2026, 7, 6, 4, 10, 0, 900000, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)

    res = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms})
    assert res.status_code == 201
    assert res.json()["late_minutes"] == 0

    expected_truncated_ms = int(datetime(2026, 7, 6, 4, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert res.json()["punch_in"] == expected_truncated_ms

def test_punch_in_r1_overnight_shift(client):
    dt1 = datetime(2026, 7, 6, 16, 25, 0, tzinfo=timezone.utc)
    res1 = client.post("/attendance/punch-in", json={"emp_code": "EMP0005", "punched_at": int(dt1.timestamp() * 1000)})
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["date"] == "2026-07-06"
    assert data1["late_minutes"] == 0

def test_punch_in_r1_overnight_shift_post_midnight(client):
    dt2 = datetime(2026, 7, 7, 0, 15, 0, tzinfo=timezone.utc)
    res2 = client.post("/attendance/punch-in", json={"emp_code": "EMP0005", "punched_at": int(dt2.timestamp() * 1000)})
    assert res2.status_code == 201
    data2 = res2.json()
    assert data2["date"] == "2026-07-06"
    assert data2["late_minutes"] == 465

def test_punch_in_statuses_wfh_and_on_duty(client):
    dt_wfh = datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc)
    res_wfh = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": int(dt_wfh.timestamp() * 1000), "status": "WFH"})
    assert res_wfh.status_code == 201
    assert res_wfh.json()["status"] == "WFH"

    dt_od = datetime(2026, 7, 6, 16, 25, 0, tzinfo=timezone.utc)
    res_od = client.post("/attendance/punch-in", json={"emp_code": "EMP0005", "punched_at": int(dt_od.timestamp() * 1000), "status": "ON_DUTY"})
    assert res_od.status_code == 201
    assert res_od.json()["status"] == "ON_DUTY"

def test_punch_in_unknown_employee_404(client):
    res = client.post("/attendance/punch-in", json={"emp_code": "EMP9999"})
    assert res.status_code == 404
    assert res.json()["detail"] == "Employee not found"

def test_punch_in_duplicate_conflict_409(client):
    dt = datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)

    res1 = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms})
    assert res1.status_code == 201

    res2 = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms + 1000})
    assert res2.status_code == 409
    assert res2.json()["detail"] == "Already punched in for this date"

def test_punch_in_validation_error_422(client):
    res1 = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": 1783312500})
    assert res1.status_code == 422

    res2 = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "status": "ABSENT"})
    assert res2.status_code == 422

    res3 = client.post("/attendance/punch-in", json={})
    assert res3.status_code == 422
