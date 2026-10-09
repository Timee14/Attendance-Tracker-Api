"""
Focused tests for POST /employees and GET /employees.
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
    yield test_db

@pytest.fixture
def client():
    return TestClient(app)

def test_create_employee_success(client):
    payload = {
        "emp_code": "EMP0100",
        "name": "Jane Doe",
        "email": "jane@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-15"
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == "EMP0100"
    assert data["name"] == "Jane Doe"
    assert isinstance(data["created_at"], int)
    assert "_id" not in data

def test_create_employee_duplicate_409(client):
    payload = {
        "emp_code": "EMP0100",
        "name": "Jane Doe",
        "email": "jane@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-15"
    }
    res1 = client.post("/employees", json=payload)
    assert res1.status_code == 201

    res2 = client.post("/employees", json=payload)
    assert res2.status_code == 409

def test_create_employee_same_shifts_422(client):
    payload = {
        "emp_code": "EMP0101",
        "name": "Same Shift",
        "email": "same@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "09:30",
        "joined_on": "2026-01-15"
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 422

def test_list_employees_pagination_and_filter(client):
    for i in range(1, 4):
        client.post("/employees", json={
            "emp_code": f"EMP000{i}",
            "name": f"Employee {i}",
            "email": f"emp{i}@example.com",
            "department": "Sales" if i == 1 else "Engineering",
            "joined_on": "2026-01-01"
        })

    res = client.get("/employees?page=1&page_size=2")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["items"][0]["emp_code"] == "EMP0001"
    assert data["items"][1]["emp_code"] == "EMP0002"

    res_sales = client.get("/employees?department=Sales")
    assert res_sales.status_code == 200
    data_sales = res_sales.json()
    assert data_sales["total"] == 1
    assert data_sales["items"][0]["emp_code"] == "EMP0001"
