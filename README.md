# Employee Attendance & Analytics API

FastAPI and MongoDB backend service for employee attendance tracking, regularization audit trails, and high-performance aggregation analytics.

---

## 1. Setup & Installation

### Prerequisites
* Python 3.11 or newer
* MongoDB 6.0 or newer (Atlas cluster or local instance)

### Installation
```bash
# 1. Create virtual environment
python -m venv .venv

# 2. Activate virtual environment
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## 2. Configuration

Set the required environment variables or provide a local `.env` file (real environment variables always take precedence):

| Variable | Description | Default |
|---|---|---|
| `MONGO_URI` | MongoDB connection URI string | `mongodb://localhost:27017` |
| `MONGO_DB` | Target MongoDB database name | `attendance_db` |

*Note: Do not commit `.env` or credential files to Git repository.*

---

## 3. Seed Sample Data (Optional)

To load the sample dataset into your configured MongoDB database:
```bash
python sample_seed.py
```

---

## 4. Running the API

Start the FastAPI server via Uvicorn:
```bash
uvicorn app.main:app --port 8000
```
Interactive Swagger documentation will be available at: `http://localhost:8000/docs`.

---

## 5. Running Tests

Run the full automated test suite using pytest against an isolated test database:
```bash
pytest -v
```

---

## 6. Endpoints Overview

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Readiness probe (checks MongoDB ping, returns 200 or 503). |
| `POST` | `/employees` | Create employee (enforces unique `emp_code`). |
| `GET` | `/employees` | Paginated listing of employees sorted by `emp_code` ascending. |
| `POST` | `/attendance/punch-in` | Record punch-in, compute `late_minutes` (R2), handle overnight shifts (R1). |
| `POST` | `/attendance/punch-out` | Record punch-out, compute `work_hours` (R4), `overtime_minutes` (R3), `half_day` (R5). |
| `GET` | `/attendance` | Paginated attendance logs sorted by `date` desc, `emp_code` asc. |
| `PATCH` | `/attendance/{emp_code}/{date}` | Regularize record, recompute derived fields, append to immutable history. |
| `GET` | `/analytics/employees/{emp_code}/monthly` | Monthly employee summary via MongoDB aggregation pipeline. |
| `GET` | `/analytics/departments/summary` | Department summary aggregation across collections. |
| `GET` | `/analytics/leaderboard/late` | Late leaderboard with standard competition ranking (1, 2, 2, 4). |
| `GET` | `/analytics/departments/{department}/trend` | Daily trend series with gap filling and 7-day moving averages. |
| `GET` | `/admin/explain/{endpoint}` | ExecutionStats explain plan for database queries. |

---

## 7. Known Limitations & Design Constraints
* Single-file deployment architecture consolidated in `app/main.py` per assignment specification.
* All indexes are created idempotently at application startup via FastAPI lifespan context.
