# DECISIONS.md

1. **Indexes.**
   We created unique index `{"emp_code": 1}` and compound index `{"department": 1, "joined_on": 1}` on `employees` to guarantee unique employee creation and accelerate department headcount joins. On `attendance_logs`, we created unique index `{"emp_code": 1, "date": 1}` (guaranteeing one record per employee-day and atomic punch-in conflict prevention), `{"date": -1, "emp_code": 1}` (for paginated listing), and `{"emp_code": 1, "punch_in": -1}` (for punch-out matching). We considered and rejected an index on `{"history.at": 1}` because audit history is embedded and only read with its parent document.

2. **Punch-in race.**
   Both concurrent requests validate employee existence and compute the IST attendance date. Both attempt `insert_one`. The unique compound index on `(emp_code, date)` allows MongoDB to insert the first document and reject the second with a `DuplicateKeyError`. Our code catches this exception and returns `409 Conflict`, guaranteeing exactly one `201` without race windows.

3. **Ties.**
   Our late leaderboard computes standard competition ranking (1, 2, 2, 4) using `$setWindowFields` with `$rank`. The filter `rank <= limit` is applied *after* ranking. When employees tie at the cutoff rank (e.g., two employees sharing rank 10), both are included in the response (returning 11 rows). Ties are ordered deterministically by `emp_code` ascending.

4. **Headcount.**
   The department summary aggregation starts from the `employees` collection (filtering active joiners `joined_on <= end_of_month`) and performs a left outer `$lookup` into `attendance_logs` with `preserveNullAndEmptyArrays: true`. Employees with zero logs are preserved and counted in `headcount`, while contributing 0 to present days and hours.

5. **One thing you would change for 100x data.**
   At 100x scale (~10 million records), we would shard `attendance_logs` on `{"date": 1, "emp_code": 1}` and maintain pre-aggregated monthly rollup documents via MongoDB Change Streams to serve analytics in O(1) time instead of computing multi-stage joins on demand.
