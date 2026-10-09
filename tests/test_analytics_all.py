"""
Focused tests for analytics and explain endpoints.
"""
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import mongomock

from app import main
from app.main import app, ensure_indexes

import mongomock.aggregate
if "$dateFromString" not in mongomock.aggregate.date_operators:
    mongomock.aggregate.date_operators.append("$dateFromString")

_orig_handle_date = mongomock.aggregate._Parser._handle_date_operator
def _custom_handle_date(self, operator, values):
    if operator == "$dateFromString":
        if isinstance(values, dict) and "dateString" in values:
            date_str = self.parse(values["dateString"])
            return datetime.fromisoformat(date_str)
        val = self.parse(values)
        if isinstance(val, str):
            return datetime.fromisoformat(val)
        return val
    return _orig_handle_date(self, operator, values)
mongomock.aggregate._Parser._handle_date_operator = _custom_handle_date

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
            "department": "Engineering",
            "joined_on": "2026-01-05",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "created_at": datetime(2026, 1, 5, 4, 30, tzinfo=timezone.utc)
        },
        {
            "emp_code": "EMP0002",
            "name": "Vikram Shah",
            "department": "Engineering",
            "joined_on": "2026-07-13",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "created_at": datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc)
        }
    ])

    test_db.attendance_logs.insert_many([
        {
            "emp_code": "EMP0001",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc),
            "work_hours": 9.12,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False
        }
    ])
    yield test_db

@pytest.fixture
def client():
    return TestClient(app)

def test_department_summary(client):
    res = client.get("/analytics/departments/summary?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["month"] == "2026-07"
    assert len(data["items"]) == 1
    dept = data["items"][0]
    assert dept["department"] == "Engineering"
    assert dept["headcount"] == 2
    assert dept["present_days"] == 1.0
    assert dept["avg_work_hours"] == 9.12

def test_department_trend(client):
    res = client.get("/analytics/departments/Engineering/trend?from=2026-07-06&to=2026-07-08")
    assert res.status_code == 200
    data = res.json()
    assert data["department"] == "Engineering"
    assert len(data["items"]) == 3

    assert data["items"][0]["date"] == "2026-07-06"
    assert data["items"][0]["headcount"] == 1
    assert data["items"][0]["present_count"] == 1.0
    assert data["items"][0]["attendance_rate"] == 1.0
    assert data["items"][0]["moving_avg_7d"] == 1.0

    assert data["items"][1]["date"] == "2026-07-07"
    assert data["items"][1]["present_count"] == 0.0
    assert data["items"][1]["attendance_rate"] == 0.0

def test_department_trend_range_exceeds_92_days_422(client):
    res = client.get("/analytics/departments/Engineering/trend?from=2026-01-01&to=2026-06-01")
    assert res.status_code == 422

def test_department_trend_unknown_dept_404(client):
    res = client.get("/analytics/departments/UnknownDept/trend?from=2026-07-01&to=2026-07-05")
    assert res.status_code == 404

def test_attendance_list(client):
    res = client.get("/attendance?emp_code=EMP0001")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["emp_code"] == "EMP0001"
    assert data["items"][0]["date"] == "2026-07-06"

def test_attendance_list_date_range_invalid_422(client):
    res = client.get("/attendance?date_from=2026-07-10&date_to=2026-07-01")
    assert res.status_code == 422
