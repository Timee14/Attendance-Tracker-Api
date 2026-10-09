"""
Focused tests for GET /analytics/employees/{emp_code}/monthly.
Uses isolated in-memory test database (mongomock).
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
        },
        {
            "emp_code": "EMP0003",
            "name": "Future Joiner",
            "department": "Sales",
            "joined_on": "2026-08-01",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "created_at": datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
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
        },

        {
            "emp_code": "EMP0001",
            "date": "2026-07-07",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 7, 4, 35, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 7, 13, 40, tzinfo=timezone.utc),
            "work_hours": 9.08,
            "late_minutes": 35,
            "overtime_minutes": 40,
            "half_day": False
        },

        {
            "emp_code": "EMP0001",
            "date": "2026-07-08",
            "status": "WFH",
            "punch_in": datetime(2026, 7, 8, 4, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 8, 7, 30, tzinfo=timezone.utc),
            "work_hours": 3.5,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": True
        },

        {
            "emp_code": "EMP0001",
            "date": "2026-07-09",
            "status": "LEAVE",
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False
        },

        {
            "emp_code": "EMP0001",
            "date": "2026-07-11",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 11, 4, 15, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 11, 13, 50, tzinfo=timezone.utc),
            "work_hours": 9.58,
            "late_minutes": 15,
            "overtime_minutes": 50,
            "half_day": False
        }
    ])
    yield test_db

@pytest.fixture
def client():
    return TestClient(app)

def test_employee_monthly_summary_success(client):
    res = client.get("/analytics/employees/EMP0001/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()

    assert data["emp_code"] == "EMP0001"
    assert data["month"] == "2026-07"

    assert data["working_days"] == 23

    assert data["present_days"] == 2.5
    assert data["leave_days"] == 1

    assert data["late_count"] == 2

    assert data["total_late_minutes"] == 50

    assert data["total_overtime_minutes"] == 90

    assert data["attendance_pct"] == 10.87

def test_mid_month_joiner_working_days(client):
    res = client.get("/analytics/employees/EMP0002/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 15
    assert data["present_days"] == 0.0
    assert data["attendance_pct"] == 0.0

def test_future_joiner_working_days_zero(client):
    res = client.get("/analytics/employees/EMP0003/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 0
    assert data["attendance_pct"] is None

def test_employee_monthly_unknown_employee_404(client):
    res = client.get("/analytics/employees/EMP9999/monthly?month=2026-07")
    assert res.status_code == 404
    assert res.json()["detail"] == "Employee not found"

def test_employee_monthly_invalid_month_format_422(client):
    res1 = client.get("/analytics/employees/EMP0001/monthly?month=2026-13")
    assert res1.status_code == 422

    res2 = client.get("/analytics/employees/EMP0001/monthly?month=2026/07")
    assert res2.status_code == 422
