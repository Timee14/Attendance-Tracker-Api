"""
Focused tests for PATCH /attendance/{emp_code}/{date} (Regularization and Audit Trail).
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
        "email": "asha@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05",
        "created_at": datetime(2026, 1, 5, 4, 30, tzinfo=timezone.utc)
    })

    test_db.attendance_logs.insert_one({
        "emp_code": "EMP0001",
        "date": "2026-07-07",
        "status": "PRESENT",
        "punch_in": datetime(2026, 7, 7, 4, 35, 0, tzinfo=timezone.utc),
        "punch_out": datetime(2026, 7, 7, 13, 0, 0, tzinfo=timezone.utc),
        "work_hours": 8.42,
        "late_minutes": 35,
        "overtime_minutes": 0,
        "half_day": False,
        "history": []
    })
    yield test_db

@pytest.fixture
def client():
    return TestClient(app)

def test_regularize_punch_in_success_and_audit_history(client):
    new_punch_in_dt = datetime(2026, 7, 7, 3, 58, 0, tzinfo=timezone.utc)
    new_punch_in_ms = int(new_punch_in_dt.timestamp() * 1000)

    payload = {
        "punch_in": new_punch_in_ms,
        "reason": "biometric glitch at the gate",
        "regularized_by": "hr.admin"
    }

    res = client.patch("/attendance/EMP0001/2026-07-07", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["punch_in"] == new_punch_in_ms
    assert data["late_minutes"] == 0
    assert data["work_hours"] == 9.03

    assert len(data["history"]) == 1
    h = data["history"][0]
    assert h["by"] == "hr.admin"
    assert h["reason"] == "biometric glitch at the gate"
    assert isinstance(h["at"], int)

    changes = h["changes"]
    assert "punch_in" in changes
    old_punch_in_ms = int(datetime(2026, 7, 7, 4, 35, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert changes["punch_in"]["from"] == old_punch_in_ms
    assert changes["punch_in"]["to"] == new_punch_in_ms

    assert "late_minutes" in changes
    assert changes["late_minutes"]["from"] == 35
    assert changes["late_minutes"]["to"] == 0

    assert "work_hours" in changes
    assert changes["work_hours"]["from"] == 8.42
    assert changes["work_hours"]["to"] == 9.03

    assert "status" not in changes
    assert "overtime_minutes" not in changes
    assert "half_day" not in changes

def test_regularize_to_leave_clears_punches_and_records_history(client):
    payload = {
        "status": "LEAVE",
        "reason": "applied for emergency sick leave",
        "regularized_by": "manager.john"
    }
    res = client.patch("/attendance/EMP0001/2026-07-07", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "LEAVE"
    assert data["punch_in"] is None
    assert data["punch_out"] is None
    assert data["work_hours"] is None
    assert data["late_minutes"] == 0

    assert len(data["history"]) == 1
    changes = data["history"][0]["changes"]
    assert changes["status"]["from"] == "PRESENT"
    assert changes["status"]["to"] == "LEAVE"

def test_regularize_absent_with_punch_times_fails_422(client):
    dt = datetime(2026, 7, 7, 4, 0, 0, tzinfo=timezone.utc)
    payload = {
        "status": "ABSENT",
        "punch_in": int(dt.timestamp() * 1000),
        "reason": "invalid absent request",
        "regularized_by": "hr.admin"
    }
    res = client.patch("/attendance/EMP0001/2026-07-07", json=payload)
    assert res.status_code == 422

def test_regularize_punch_in_wrong_date_fails_422(client):
    dt = datetime(2026, 7, 8, 4, 0, 0, tzinfo=timezone.utc)
    payload = {
        "punch_in": int(dt.timestamp() * 1000),
        "reason": "wrong date correction",
        "regularized_by": "hr.admin"
    }
    res = client.patch("/attendance/EMP0001/2026-07-07", json=payload)
    assert res.status_code == 422

def test_regularize_punch_out_before_punch_in_fails_422(client):
    dt = datetime(2026, 7, 7, 4, 0, 0, tzinfo=timezone.utc)
    payload = {
        "punch_out": int(dt.timestamp() * 1000),
        "reason": "punch out before in",
        "regularized_by": "hr.admin"
    }
    res = client.patch("/attendance/EMP0001/2026-07-07", json=payload)
    assert res.status_code == 422

def test_regularize_punch_out_over_24h_fails_422(client):
    dt = datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc)
    payload = {
        "punch_out": int(dt.timestamp() * 1000),
        "reason": "punch out over 24h",
        "regularized_by": "hr.admin"
    }
    res = client.patch("/attendance/EMP0001/2026-07-07", json=payload)
    assert res.status_code == 422

def test_regularize_no_changes_fails_422(client):
    payload = {
        "status": "PRESENT",
        "reason": "no actual changes made",
        "regularized_by": "hr.admin"
    }
    res = client.patch("/attendance/EMP0001/2026-07-07", json=payload)
    assert res.status_code == 422

def test_regularize_unknown_employee_404(client):
    payload = {
        "status": "WFH",
        "reason": "worked from home",
        "regularized_by": "hr.admin"
    }
    res = client.patch("/attendance/EMP9999/2026-07-07", json=payload)
    assert res.status_code == 404

def test_regularize_missing_attendance_record_404(client):
    payload = {
        "status": "WFH",
        "reason": "worked from home",
        "regularized_by": "hr.admin"
    }
    res = client.patch("/attendance/EMP0001/2026-07-15", json=payload)
    assert res.status_code == 404

def test_multiple_regularizations_append_immutable_history(client):
    res1 = client.patch("/attendance/EMP0001/2026-07-07", json={
        "status": "WFH",
        "reason": "correction to WFH status",
        "regularized_by": "admin.one"
    })
    assert res1.status_code == 200
    assert len(res1.json()["history"]) == 1

    new_punch_out_dt = datetime(2026, 7, 7, 13, 40, 0, tzinfo=timezone.utc)
    res2 = client.patch("/attendance/EMP0001/2026-07-07", json={
        "punch_out": int(new_punch_out_dt.timestamp() * 1000),
        "reason": "approving overtime worked",
        "regularized_by": "manager.two"
    })
    assert res2.status_code == 200
    data2 = res2.json()
    assert len(data2["history"]) == 2
    assert data2["history"][0]["by"] == "admin.one"
    assert data2["history"][1]["by"] == "manager.two"
    assert data2["overtime_minutes"] == 40
