# Audit Notes: Starter Code Defects and Fixes

## Defect Inventory & Audit Log

### 1. `compute_late_minutes` (lines 33–37)
* **Original Code:**
  ```python
  def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
      h, m = map(int, shift_start.split(":"))
      start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
      minutes = int((punch_in - start).total_seconds() / 60)
      return minutes if minutes > 10 else 0
  ```
* **What is Wrong & Trigger Input:**
  * Uses naive UTC datetimes. `punch_in.replace(hour=h, minute=m)` replaces hours/minutes in UTC time instead of IST time, causing negative time differences (e.g. punch-in 03:58 UTC = 09:28 IST compared with 09:30 shift start yields negative interval).
  * Fails to truncate sub-seconds microsecond precision before rule calculation (violates R1).
  * Fails to handle overnight shift boundary attribution (violates R1).
* **Category:** timezone
* **Fix Applied:** Rewrote `compute_late_minutes_r2` and `resolve_shift_details` helpers. Converts UTC instant to IST, truncates microseconds, calculates IST shift start datetime relative to shift date, and applies grace period floor calculation per R1 & R2.

---

### 2. `compute_work_hours` (lines 40–41)
* **Original Code:**
  ```python
  def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
      return round((punch_out - punch_in).total_seconds() / 3600, 2)
  ```
* **What is Wrong & Trigger Input:**
  * Uses Python's built-in `round()`, which applies IEEE 754 round-half-to-even (banker's rounding) instead of exact arithmetic half-up rounding required by R4 & R8. Input `9.125` yields `9.12` instead of `9.13`.
* **Category:** validation
* **Fix Applied:** Implemented `round_half_up` using `decimal.Decimal` with `ROUND_HALF_UP` in `compute_work_hours_r4`.

---

### 3. `compute_overtime` (lines 44–47)
* **Original Code:**
  ```python
  def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
      h, m = map(int, shift_end.split(":"))
      end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
      return max(0, int((punch_out - end).total_seconds() / 60))
  ```
* **What is Wrong & Trigger Input:**
  * Does not enforce the 30-minute minimum overtime threshold (R3). Punching out 10 minutes past shift end yields 10 overtime minutes instead of 0.
  * Fails on overnight shifts (`shift_end <= shift_start`) where `shift_end` falls on the next calendar day.
  * Mixes offset-naive and offset-aware datetimes.
* **Category:** timezone
* **Fix Applied:** Implemented `compute_overtime_r3`. Computes shift end date (next day for overnight shift), converts to IST, calculates floor minutes elapsed, and counts overtime only if `>= 30`.

---

### 4. `health` (lines 72–74)
* **Original Code:**
  ```python
  @app.get("/health")
  def health():
      return {"status": "ok"}
  ```
* **What is Wrong & Trigger Input:**
  * Returns static `{"status": "ok"}` without running a database ping. When MongoDB is offline, it returns 200 OK instead of 503 Service Unavailable as required by contract.
* **Category:** other
* **Fix Applied:** Added `db.command('ping')` inside a try-except block, raising `HTTPException(503, "Database unavailable")` on failure.

---

### 5. `create_employee` (lines 77–85)
* **Original Code:**
  ```python
  @app.post("/employees", status_code=201)
  def create_employee(body: EmployeeIn):
      if db.employees.find_one({"emp_code": body.emp_code}):
          raise HTTPException(409, "emp_code already exists")
      doc = body.model_dump()
      doc["created_at"] = datetime.now()
      db.employees.insert_one(doc)
      doc.pop("_id", None)
      return doc
  ```
* **What is Wrong & Trigger Input:**
  * Check-then-insert race condition. Concurrent requests with the same `emp_code` both pass `find_one` and insert duplicate employee documents.
  * `datetime.now()` returns naive datetime; serializes as string instead of integer epoch milliseconds.
  * Missing validation for `shift_start != shift_end`.
* **Category:** concurrency
* **Fix Applied:** Added unique index `idx_employees_emp_code_unique` on `emp_code`, caught `DuplicateKeyError` to return 409, validated `shift_start != shift_end` (422), and converted `created_at` to integer epoch ms.

---

### 6. `list_employees` (lines 88–96)
* **Original Code:**
  ```python
  @app.get("/employees")
  def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
      q = {}
      if department:
          q["department"] = department
      skip = page * page_size
      total = db.employees.count_documents({})
      items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
      return {"items": items, "total": total, "page": page, "page_size": page_size}
  ```
* **What is Wrong & Trigger Input:**
  * Off-by-one pagination bug: `skip = page * page_size` skips the first page when `page=1` (skips 20 items, returning items 21–40).
  * `count_documents({})` counts the total collection instead of the filtered query `q`.
  * Missing sorting: contract requires items sorted by `emp_code` ascending.
* **Category:** pagination
* **Fix Applied:** Corrected `skip = (page - 1) * page_size`, passed query filter `q` to `count_documents(q)`, and added `.sort("emp_code", ASCENDING)`.

---

### 7. `punch_in` (lines 99–121)
* **Original Code:**
  ```python
  @app.post("/attendance/punch-in", status_code=201)
  def punch_in(body: PunchInIn):
      emp = db.employees.find_one({"emp_code": body.emp_code})
      ts = datetime.fromtimestamp(body.punched_at / 1000) if body.punched_at else datetime.now()
      d = ts.date().isoformat()
      if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
          raise HTTPException(409, "already punched in for this date")
      ...
      doc["id"] = str(res.inserted_id)
      doc.pop("_id", None)
      return doc
  ```
* **What is Wrong & Trigger Input:**
  * Missing employee check: if `emp` is None, throws 500 `TypeError` instead of 404 Not Found.
  * Naive datetime and UTC/IST date mismatch: `d = ts.date().isoformat()` computes UTC date instead of IST date (R1) and ignores overnight shift boundaries.
  * Check-then-insert race condition: concurrent duplicate punch-ins for the same date create multiple records.
  * Schema leak: `doc["id"] = str(...)` leaks internal ID field into response.
* **Category:** concurrency
* **Fix Applied:** Added employee 404 validation check, resolved IST date and overnight shift attribution via `resolve_shift_details`, created unique compound index on `(emp_code, date)` to catch `DuplicateKeyError` (409), validated status enum, and sanitized output using `clean_doc_for_serialization`.

---

### 8. `list_attendance` (lines 124–150)
* **Original Code:**
  ```python
  @app.get("/attendance")
  def list_attendance(...):
      docs = list(db.attendance_logs.find(q))
      docs.sort(key=lambda d: d["date"], reverse=True)
      total = len(docs)
      page_docs = docs[(page - 1) * page_size : page * page_size]
      for d in page_docs:
          d["id"] = str(d.pop("_id"))
      return {"items": page_docs, "total": total, "page": page, "page_size": page_size}
  ```
* **What is Wrong & Trigger Input:**
  * Loads entire collection into Python memory with `list(db.attendance_logs.find(q))` and sorts in Python (memory exhaustion on 100k documents).
  * Missing date validation: does not validate `date_from <= date_to` (must return 422 if `date_from > date_to`).
  * Unstable sort: missing secondary sort key `emp_code ASC`.
  * Schema leak: converts `_id` to `"id"`.
* **Category:** pagination
* **Fix Applied:** Delegated sorting and pagination to database cursor `.sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip(...).limit(...)`, validated date range (422), and removed `id` field.

---

### 9. Application Lifecycle & Indexes (Starter Root)
* **Original Code:** Missing lifespan context manager and index initialization.
* **What is Wrong & Trigger Input:** Application started without database indexes, causing full collection scans (`COLLSCAN`) and failing concurrency constraints.
* **Category:** concurrency
* **Fix Applied:** Created FastAPI `lifespan` handler that calls `ensure_indexes()` at application startup.

---

### 10. Missing Contract Endpoints (lines 153–162)
* **Original Code:** Endpoints marked `# TODO` were unimplemented.
* **What is Wrong & Trigger Input:** Requesting punch-out, regularization, analytics, or explain routes returned 404.
* **Category:** other
* **Fix Applied:** Implemented all 7 contract endpoints: `POST /attendance/punch-out`, `PATCH /attendance/{emp_code}/{date}`, `GET /analytics/employees/{emp_code}/monthly`, `GET /analytics/departments/summary`, `GET /analytics/leaderboard/late`, `GET /analytics/departments/{department}/trend`, and `GET /admin/explain/{endpoint}`.
