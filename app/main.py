import os
import calendar
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta, time, date as dt_date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional, Dict, List, Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status, Query, Path
from pydantic import BaseModel, Field, EmailStr
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import PyMongoError, DuplicateKeyError
load_dotenv()
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')
MONGO_DB_NAME = os.getenv('MONGO_DB', 'attendance_db')
IST = timezone(timedelta(hours=5, minutes=30))
client: MongoClient = MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=2000)
db = client[MONGO_DB_NAME]

def get_db():
    return db

def ensure_indexes(database=None):
    target_db = database if database is not None else db
    target_db.employees.create_index([('emp_code', ASCENDING)], unique=True, name='idx_employees_emp_code_unique')
    target_db.employees.create_index([('department', ASCENDING), ('emp_code', ASCENDING)], name='idx_employees_dept_emp_code')
    target_db.employees.create_index([('department', ASCENDING), ('joined_on', ASCENDING)], name='idx_employees_dept_joined_on')
    target_db.attendance_logs.create_index([('emp_code', ASCENDING), ('date', ASCENDING)], unique=True, name='idx_attendance_emp_code_date_unique')
    target_db.attendance_logs.create_index([('date', DESCENDING), ('emp_code', ASCENDING)], name='idx_attendance_date_desc_emp_asc')
    target_db.attendance_logs.create_index([('emp_code', ASCENDING), ('punch_in', DESCENDING)], name='idx_attendance_emp_punch_in_desc')
    target_db.attendance_logs.create_index([('date', ASCENDING), ('status', ASCENDING)], name='idx_attendance_date_status')
    target_db.attendance_logs.create_index([('date', ASCENDING), ('late_minutes', ASCENDING), ('emp_code', ASCENDING)], name='idx_attendance_date_late_emp')

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_indexes()
    except Exception:
        pass
    yield
    client.close()
app = FastAPI(title='Employee Attendance & Analytics API', version='2.0.0', lifespan=lifespan)

def round_half_up(value: Optional[float], decimals: int=2) -> Optional[float]:
    if value is None:
        return None
    d = Decimal(str(value))
    target_format = '0.' + '0' * decimals if decimals > 0 else '0'
    return float(d.quantize(Decimal(target_format), rounding=ROUND_HALF_UP))

def truncate_to_seconds(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(microsecond=0)

def epoch_ms_to_datetime(ms: Optional[int]) -> Optional[datetime]:
    if ms is None:
        return None
    seconds = ms // 1000
    return datetime.fromtimestamp(seconds, tz=timezone.utc)

def datetime_to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def resolve_shift_details(punch_in_utc: datetime, shift_start_str: str, shift_end_str: str):
    punch_in_ist = punch_in_utc.astimezone(IST)
    p_date = punch_in_ist.date()
    p_time = punch_in_ist.time()
    sh_h, sh_m = map(int, shift_start_str.split(':'))
    eh_h, eh_m = map(int, shift_end_str.split(':'))
    is_overnight = (eh_h, eh_m) <= (sh_h, sh_m)
    if is_overnight and p_time < time(eh_h, eh_m):
        attendance_date = p_date - timedelta(days=1)
    else:
        attendance_date = p_date
    shift_start_ist = datetime.combine(attendance_date, time(sh_h, sh_m), tzinfo=IST)
    return (attendance_date.isoformat(), shift_start_ist)

def compute_late_minutes_r2(punch_in_utc: datetime, shift_start_ist: datetime) -> int:
    punch_in_ist = punch_in_utc.astimezone(IST)
    elapsed_seconds = (punch_in_ist - shift_start_ist).total_seconds()
    if elapsed_seconds <= 600:
        return 0
    return int(elapsed_seconds // 60)

def compute_work_hours_r4(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    seconds = (punch_out_utc - punch_in_utc).total_seconds()
    raw_hours = seconds / 3600.0
    return round_half_up(raw_hours, 2)

def compute_overtime_r3(punch_out_utc: datetime, shift_start_str: str, shift_end_str: str, attendance_date_str: str) -> int:
    sh_h, sh_m = map(int, shift_start_str.split(':'))
    eh_h, eh_m = map(int, shift_end_str.split(':'))
    is_overnight = (eh_h, eh_m) <= (sh_h, sh_m)
    att_date = dt_date.fromisoformat(attendance_date_str)
    shift_end_date = att_date + timedelta(days=1) if is_overnight else att_date
    shift_end_ist = datetime.combine(shift_end_date, time(eh_h, eh_m), tzinfo=IST)
    punch_out_ist = punch_out_utc.astimezone(IST)
    diff_seconds = (punch_out_ist - shift_end_ist).total_seconds()
    if diff_seconds >= 1800:
        return int(diff_seconds // 60)
    return 0

def compute_working_days(joined_on_str: str, month_str: str) -> int:
    year, month = map(int, month_str.split('-'))
    _, num_days = calendar.monthrange(year, month)
    first_day_of_month = dt_date(year, month, 1)
    last_day_of_month = dt_date(year, month, num_days)
    joined_date = dt_date.fromisoformat(joined_on_str)
    if joined_date > last_day_of_month:
        return 0
    start_date = max(first_day_of_month, joined_date)
    working_days = 0
    curr = start_date
    while curr <= last_day_of_month:
        if curr.weekday() < 5:
            working_days += 1
        curr += timedelta(days=1)
    return working_days

def clean_doc_for_serialization(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    clean = dict(doc)
    clean.pop('_id', None)
    clean.pop('id', None)
    for field in ('created_at', 'punch_in', 'punch_out'):
        if field in clean:
            val = clean[field]
            if isinstance(val, datetime):
                clean[field] = datetime_to_epoch_ms(val)
    if 'history' in clean and isinstance(clean['history'], list):
        serialized_history = []
        for entry in clean['history']:
            h_entry = dict(entry)
            if 'at' in h_entry and isinstance(h_entry['at'], datetime):
                h_entry['at'] = datetime_to_epoch_ms(h_entry['at'])
            if 'changes' in h_entry and isinstance(h_entry['changes'], dict):
                serialized_changes = {}
                for change_key, change_val in h_entry['changes'].items():
                    if isinstance(change_val, dict):
                        c_dict = dict(change_val)
                        for side in ('from', 'to'):
                            if side in c_dict and isinstance(c_dict[side], datetime):
                                c_dict[side] = datetime_to_epoch_ms(c_dict[side])
                        serialized_changes[change_key] = c_dict
                    else:
                        serialized_changes[change_key] = change_val
                h_entry['changes'] = serialized_changes
            serialized_history.append(h_entry)
        clean['history'] = serialized_history
    return clean

def build_employee_monthly_pipeline(emp_code: str, month: str) -> List[Dict[str, Any]]:
    return [{'$match': {'emp_code': emp_code, 'date': {'$regex': f'^{month}'}}}, {'$project': {'date': 1, 'status': 1, 'half_day': 1, 'late_minutes': {'$ifNull': ['$late_minutes', 0]}, 'overtime_minutes': {'$ifNull': ['$overtime_minutes', 0]}, 'day_of_week': {'$dayOfWeek': {'$dateFromString': {'dateString': '$date'}}}, 'presence_weight': {'$cond': [{'$and': [{'$in': ['$status', ['PRESENT', 'WFH', 'ON_DUTY']]}, {'$gte': [{'$dayOfWeek': {'$dateFromString': {'dateString': '$date'}}}, 2]}, {'$lte': [{'$dayOfWeek': {'$dateFromString': {'dateString': '$date'}}}, 6]}]}, {'$cond': [{'$eq': ['$half_day', True]}, 0.5, 1.0]}, 0.0]}}}, {'$group': {'_id': None, 'present_days': {'$sum': '$presence_weight'}, 'leave_days': {'$sum': {'$cond': [{'$eq': ['$status', 'LEAVE']}, 1, 0]}}, 'late_count': {'$sum': {'$cond': [{'$gt': ['$late_minutes', 0]}, 1, 0]}}, 'total_late_minutes': {'$sum': '$late_minutes'}, 'total_overtime_minutes': {'$sum': '$overtime_minutes'}}}]

def build_department_summary_pipeline(month: str, department: Optional[str]=None) -> List[Dict[str, Any]]:
    year, mon = map(int, month.split('-'))
    _, num_days = calendar.monthrange(year, mon)
    last_day_of_month = f'{month}-{num_days:02d}'
    match_emp: Dict[str, Any] = {'joined_on': {'$lte': last_day_of_month}}
    if department:
        match_emp['department'] = department
    return [{'$match': match_emp}, {'$lookup': {'from': 'attendance_logs', 'localField': 'emp_code', 'foreignField': 'emp_code', 'as': 'logs'}}, {'$unwind': {'path': '$logs', 'preserveNullAndEmptyArrays': True}}, {'$project': {'department': 1, 'emp_code': 1, 'is_month_log': {'$cond': [{'$and': [{'$ne': ['$logs', None]}, {'$gte': ['$logs.date', f'{month}-01']}, {'$lte': ['$logs.date', last_day_of_month]}]}, True, False]}, 'status': {'$cond': [{'$and': [{'$ne': ['$logs', None]}, {'$gte': ['$logs.date', f'{month}-01']}, {'$lte': ['$logs.date', last_day_of_month]}]}, '$logs.status', None]}, 'half_day': {'$cond': [{'$and': [{'$ne': ['$logs', None]}, {'$gte': ['$logs.date', f'{month}-01']}, {'$lte': ['$logs.date', last_day_of_month]}]}, '$logs.half_day', False]}, 'work_hours': {'$cond': [{'$and': [{'$ne': ['$logs', None]}, {'$gte': ['$logs.date', f'{month}-01']}, {'$lte': ['$logs.date', last_day_of_month]}]}, '$logs.work_hours', None]}, 'late_minutes': {'$cond': [{'$and': [{'$ne': ['$logs', None]}, {'$gte': ['$logs.date', f'{month}-01']}, {'$lte': ['$logs.date', last_day_of_month]}]}, {'$ifNull': ['$logs.late_minutes', 0]}, 0]}, 'overtime_minutes': {'$cond': [{'$and': [{'$ne': ['$logs', None]}, {'$gte': ['$logs.date', f'{month}-01']}, {'$lte': ['$logs.date', last_day_of_month]}]}, {'$ifNull': ['$logs.overtime_minutes', 0]}, 0]}, 'presence_weight': {'$cond': [{'$and': [{'$ne': ['$logs', None]}, {'$gte': ['$logs.date', f'{month}-01']}, {'$lte': ['$logs.date', last_day_of_month]}, {'$in': ['$logs.status', ['PRESENT', 'WFH', 'ON_DUTY']]}, {'$gte': [{'$dayOfWeek': {'$dateFromString': {'dateString': '$logs.date'}}}, 2]}, {'$lte': [{'$dayOfWeek': {'$dateFromString': {'dateString': '$logs.date'}}}, 6]}]}, {'$cond': [{'$eq': ['$logs.half_day', True]}, 0.5, 1.0]}, 0.0]}, 'is_present_with_hours': {'$cond': [{'$and': [{'$ne': ['$logs', None]}, {'$gte': ['$logs.date', f'{month}-01']}, {'$lte': ['$logs.date', last_day_of_month]}, {'$in': ['$logs.status', ['PRESENT', 'WFH', 'ON_DUTY']]}, {'$ne': ['$logs.work_hours', None]}]}, True, False]}}}, {'$group': {'_id': '$department', 'employees': {'$addToSet': '$emp_code'}, 'present_days': {'$sum': '$presence_weight'}, 'total_work_hours': {'$sum': {'$cond': ['$is_present_with_hours', '$work_hours', 0.0]}}, 'work_hours_count': {'$sum': {'$cond': ['$is_present_with_hours', 1, 0]}}, 'late_count': {'$sum': {'$cond': [{'$gt': ['$late_minutes', 0]}, 1, 0]}}, 'total_late_minutes': {'$sum': '$late_minutes'}, 'leave_count': {'$sum': {'$cond': [{'$eq': ['$status', 'LEAVE']}, 1, 0]}}, 'on_duty_count': {'$sum': {'$cond': [{'$eq': ['$status', 'ON_DUTY']}, 1, 0]}}}}, {'$project': {'department': '$_id', 'headcount': {'$size': '$employees'}, 'present_days': '$present_days', 'avg_work_hours': {'$cond': [{'$gt': ['$work_hours_count', 0]}, {'$divide': ['$total_work_hours', '$work_hours_count']}, None]}, 'late_count': '$late_count', 'total_late_minutes': '$total_late_minutes', 'leave_count': '$leave_count', 'on_duty_count': '$on_duty_count'}}, {'$match': {'headcount': {'$gt': 0}}}, {'$sort': {'department': 1}}]

def build_late_leaderboard_pipeline(month: str, limit: int, department: Optional[str]=None) -> List[Dict[str, Any]]:
    pipeline = [{'$match': {'date': {'$regex': f'^{month}'}, 'late_minutes': {'$gt': 0}}}, {'$group': {'_id': '$emp_code', 'total_late_minutes': {'$sum': '$late_minutes'}, 'late_count': {'$sum': 1}}}, {'$lookup': {'from': 'employees', 'localField': '_id', 'foreignField': 'emp_code', 'as': 'employee'}}, {'$unwind': '$employee'}]
    if department:
        pipeline.append({'$match': {'employee.department': department}})
    pipeline.extend([{'$setWindowFields': {'sortBy': {'total_late_minutes': -1}, 'output': {'rank': {'$rank': {}}}}}, {'$match': {'rank': {'$lte': limit}}}, {'$sort': {'total_late_minutes': -1, 'employee.emp_code': 1}}, {'$project': {'_id': 0, 'rank': '$rank', 'emp_code': '$employee.emp_code', 'name': '$employee.name', 'department': '$employee.department', 'total_late_minutes': '$total_late_minutes', 'late_count': '$late_count'}}])
    return pipeline

class HealthResponse(BaseModel):
    status: str = Field(..., json_schema_extra={'example': 'ok'})

class EmployeeCreate(BaseModel):
    emp_code: str = Field(..., pattern='^EMP\\d{4,6}$', json_schema_extra={'example': 'EMP0001'})
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern='^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$')
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field(default='09:30', pattern='^([01]\\d|2[0-3]):[0-5]\\d$')
    shift_end: str = Field(default='18:30', pattern='^([01]\\d|2[0-3]):[0-5]\\d$')
    joined_on: str = Field(..., pattern='^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$')

class Employee(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str
    shift_end: str
    joined_on: str
    created_at: int = Field(..., ge=100000000000, le=4102444800000)

class EmployeePage(BaseModel):
    items: List[Employee]
    total: int
    page: int
    page_size: int

class HistoryEntry(BaseModel):
    at: int = Field(..., ge=100000000000, le=4102444800000)
    by: str
    reason: str
    changes: Dict[str, Any]

class AttendanceRecord(BaseModel):
    emp_code: str
    date: str
    status: str
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    work_hours: Optional[float] = None
    late_minutes: int
    overtime_minutes: int
    half_day: bool
    history: List[HistoryEntry] = []

class AttendancePage(BaseModel):
    items: List[AttendanceRecord]
    total: int
    page: int
    page_size: int

class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)
    status: Literal['PRESENT', 'WFH', 'ON_DUTY'] = 'PRESENT'

class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)

class RegularizeRequest(BaseModel):
    status: Optional[Literal['PRESENT', 'ABSENT', 'LEAVE', 'WFH', 'ON_DUTY']] = None
    punch_in: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)

class EmployeeMonthly(BaseModel):
    emp_code: str
    month: str
    working_days: int
    present_days: float
    leave_days: int
    late_count: int
    total_late_minutes: int
    total_overtime_minutes: int
    attendance_pct: Optional[float] = None

class DepartmentSummaryItem(BaseModel):
    department: str
    headcount: int
    present_days: float
    avg_work_hours: Optional[float] = None
    late_count: int
    total_late_minutes: int
    leave_count: int
    on_duty_count: int

class DepartmentSummary(BaseModel):
    month: str
    items: List[DepartmentSummaryItem]

class LeaderboardItem(BaseModel):
    rank: int
    emp_code: str
    name: str
    department: str
    total_late_minutes: int
    late_count: int

class Leaderboard(BaseModel):
    month: str
    items: List[LeaderboardItem]

class TrendItem(BaseModel):
    date: str
    is_working_day: bool
    headcount: int
    present_count: float
    late_count: int
    attendance_rate: Optional[float] = None
    moving_avg_7d: Optional[float] = None

class Trend(BaseModel):
    department: str
    items: List[TrendItem]

class ExplainResponse(BaseModel):
    endpoint: str
    collection: str
    explain: Dict[str, Any]

@app.get('/health', response_model=HealthResponse, responses={200: {'description': 'Ready'}, 503: {'description': 'Database unavailable'}}, tags=['system'])
def health():
    try:
        db.command('ping')
        return {'status': 'ok'}
    except (PyMongoError, Exception):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Database unavailable')

@app.post('/employees', response_model=Employee, status_code=status.HTTP_201_CREATED, responses={201: {'description': 'Created'}, 409: {'description': 'Conflict'}, 422: {'description': 'Validation error'}}, tags=['employees'])
def create_employee(body: EmployeeCreate):
    if body.shift_start == body.shift_end:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='shift_start must differ from shift_end')
    doc = body.model_dump()
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    doc['created_at'] = created_at
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='emp_code already exists')
    return clean_doc_for_serialization(doc)

@app.get('/employees', response_model=EmployeePage, status_code=status.HTTP_200_OK, responses={200: {'description': 'A page of employees'}, 422: {'description': 'Validation error'}}, tags=['employees'])
def list_employees(department: Optional[str]=Query(None), page: int=Query(1, ge=1), page_size: int=Query(20, ge=1, le=100)):
    q = {}
    if department:
        q['department'] = department
    total = db.employees.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.employees.find(q).sort('emp_code', ASCENDING).skip(skip).limit(page_size)
    items = [clean_doc_for_serialization(d) for d in cursor]
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size}

@app.post('/attendance/punch-in', response_model=AttendanceRecord, status_code=status.HTTP_201_CREATED, responses={201: {'description': 'Created'}, 404: {'description': 'Unknown employee'}, 409: {'description': 'Already punched in for that date'}, 422: {'description': 'Validation error'}}, tags=['attendance'])
def punch_in(body: PunchInRequest):
    emp = db.employees.find_one({'emp_code': body.emp_code})
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Employee not found')
    if body.punched_at is not None:
        punch_in_utc = epoch_ms_to_datetime(body.punched_at)
    else:
        punch_in_utc = datetime.now(timezone.utc).replace(microsecond=0)
    attendance_date, shift_start_ist = resolve_shift_details(punch_in_utc, emp.get('shift_start', '09:30'), emp.get('shift_end', '18:30'))
    late_minutes = compute_late_minutes_r2(punch_in_utc, shift_start_ist)
    doc = {'emp_code': body.emp_code, 'date': attendance_date, 'status': body.status, 'punch_in': punch_in_utc, 'punch_out': None, 'work_hours': None, 'late_minutes': late_minutes, 'overtime_minutes': 0, 'half_day': False, 'history': []}
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Already punched in for this date')
    return clean_doc_for_serialization(doc)

@app.post('/attendance/punch-out', response_model=AttendanceRecord, status_code=status.HTTP_200_OK, responses={200: {'description': 'Updated record'}, 404: {'description': 'Unknown employee or no punch-in found'}, 409: {'description': 'Already punched out'}, 422: {'description': 'Validation error'}}, tags=['attendance'])
def punch_out(body: PunchOutRequest):
    emp = db.employees.find_one({'emp_code': body.emp_code})
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Employee not found')
    if body.punched_at is not None:
        punch_out_utc = epoch_ms_to_datetime(body.punched_at)
    else:
        punch_out_utc = datetime.now(timezone.utc).replace(microsecond=0)
    record = db.attendance_logs.find_one({'emp_code': body.emp_code, 'punch_in': {'$lte': punch_out_utc}, 'status': {'$in': ['PRESENT', 'WFH', 'ON_DUTY']}}, sort=[('punch_in', DESCENDING)])
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No matching punch-in found')
    if record.get('punch_out') is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Already punched out for this record')
    punch_in_utc = record['punch_in']
    if punch_out_utc <= punch_in_utc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='punched_at must be strictly after punch_in')
    if (punch_out_utc - punch_in_utc).total_seconds() > 86400:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='punched_at cannot be more than 24 hours after punch_in')
    shift_start_str = emp.get('shift_start', '09:30')
    shift_end_str = emp.get('shift_end', '18:30')
    att_date_str = record['date']
    work_hours = compute_work_hours_r4(punch_in_utc, punch_out_utc)
    overtime_minutes = compute_overtime_r3(punch_out_utc, shift_start_str, shift_end_str, att_date_str)
    half_day = bool(work_hours is not None and work_hours < 4.5)
    updated_doc = db.attendance_logs.find_one_and_update({'_id': record['_id'], 'punch_out': None}, {'$set': {'punch_out': punch_out_utc, 'work_hours': work_hours, 'overtime_minutes': overtime_minutes, 'half_day': half_day}}, return_document=ReturnDocument.AFTER)
    if not updated_doc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Already punched out by concurrent request')
    return clean_doc_for_serialization(updated_doc)

@app.get('/attendance', response_model=AttendancePage, status_code=status.HTTP_200_OK, responses={200: {'description': 'A page of records'}, 422: {'description': 'Validation error'}}, tags=['attendance'])
def list_attendance(emp_code: Optional[str]=Query(None), date_from: Optional[str]=Query(None, pattern='^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$'), date_to: Optional[str]=Query(None, pattern='^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$'), status_filter: Optional[str]=Query(None, alias='status'), page: int=Query(1, ge=1), page_size: int=Query(20, ge=1, le=100)):
    if date_from and date_to and (date_from > date_to):
        raise HTTPException(status_code=422, detail='date_from must be on or before date_to')
    q: Dict[str, Any] = {}
    if emp_code:
        q['emp_code'] = emp_code
    if date_from or date_to:
        date_q = {}
        if date_from:
            date_q['$gte'] = date_from
        if date_to:
            date_q['$lte'] = date_to
        q['date'] = date_q
    if status_filter:
        q['status'] = status_filter
    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.attendance_logs.find(q).sort([('date', DESCENDING), ('emp_code', ASCENDING)]).skip(skip).limit(page_size)
    items = [clean_doc_for_serialization(d) for d in cursor]
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size}

@app.patch('/attendance/{emp_code}/{date}', response_model=AttendanceRecord, status_code=status.HTTP_200_OK, responses={200: {'description': 'Updated record'}, 404: {'description': 'Unknown employee or no record for that date'}, 409: {'description': 'Concurrent modification conflict'}, 422: {'description': 'Validation error'}}, tags=['attendance'])
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    emp = db.employees.find_one({'emp_code': emp_code})
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Employee not found')
    old_doc = db.attendance_logs.find_one({'emp_code': emp_code, 'date': date})
    if not old_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Attendance record not found for this date')
    target_status = body.status if body.status is not None else old_doc.get('status', 'PRESENT')
    shift_start_str = emp.get('shift_start', '09:30')
    shift_end_str = emp.get('shift_end', '18:30')
    if target_status in ('ABSENT', 'LEAVE'):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Punch times must not be supplied for ABSENT or LEAVE status')
        new_punch_in = None
        new_punch_out = None
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        if body.punch_in is not None:
            new_punch_in = epoch_ms_to_datetime(body.punch_in)
        else:
            new_punch_in = old_doc.get('punch_in')
        if new_punch_in is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Presence status requires a punch_in instant')
        att_date_calc, shift_start_ist = resolve_shift_details(new_punch_in, shift_start_str, shift_end_str)
        if att_date_calc != date:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="punch_in does not belong to the record's attendance date")
        new_late_minutes = compute_late_minutes_r2(new_punch_in, shift_start_ist)
        if body.punch_out is not None:
            new_punch_out = epoch_ms_to_datetime(body.punch_out)
        else:
            new_punch_out = old_doc.get('punch_out')
        if new_punch_out is not None:
            if new_punch_out <= new_punch_in:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='punch_out must be after punch_in')
            if (new_punch_out - new_punch_in).total_seconds() > 86400:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='punch_out must be within 24 hours of punch_in')
            new_work_hours = compute_work_hours_r4(new_punch_in, new_punch_out)
            new_overtime_minutes = compute_overtime_r3(new_punch_out, shift_start_str, shift_end_str, date)
            new_half_day = bool(new_work_hours is not None and new_work_hours < 4.5)
        else:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False
    field_comparisons = [('status', old_doc.get('status'), target_status), ('punch_in', old_doc.get('punch_in'), new_punch_in), ('punch_out', old_doc.get('punch_out'), new_punch_out), ('work_hours', old_doc.get('work_hours'), new_work_hours), ('late_minutes', old_doc.get('late_minutes', 0), new_late_minutes), ('overtime_minutes', old_doc.get('overtime_minutes', 0), new_overtime_minutes), ('half_day', old_doc.get('half_day', False), new_half_day)]
    changes = {}
    for field_name, old_val, new_val in field_comparisons:
        if old_val != new_val:
            changes[field_name] = {'from': old_val, 'to': new_val}
    if not changes:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Regularization request must change at least one field')
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    history_entry = {'at': now_utc, 'by': body.regularized_by, 'reason': body.reason, 'changes': changes}
    current_history = old_doc.get('history', [])
    updated_doc = db.attendance_logs.find_one_and_update({'emp_code': emp_code, 'date': date, 'history': {'$size': len(current_history)}}, {'$set': {'status': target_status, 'punch_in': new_punch_in, 'punch_out': new_punch_out, 'work_hours': new_work_hours, 'late_minutes': new_late_minutes, 'overtime_minutes': new_overtime_minutes, 'half_day': new_half_day}, '$push': {'history': history_entry}}, return_document=ReturnDocument.AFTER)
    if not updated_doc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Concurrent regularization conflict')
    return clean_doc_for_serialization(updated_doc)

@app.get('/analytics/employees/{emp_code}/monthly', response_model=EmployeeMonthly, status_code=status.HTTP_200_OK, responses={200: {'description': 'Summary'}, 404: {'description': 'Unknown employee'}, 422: {'description': 'Validation error'}}, tags=['analytics'])
def get_employee_monthly(emp_code: str, month: str=Query(..., pattern='^\\d{4}-(0[1-9]|1[0-2])$')):
    emp = db.employees.find_one({'emp_code': emp_code})
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Employee not found')
    working_days = compute_working_days(emp.get('joined_on', '1970-01-01'), month)
    pipeline = build_employee_monthly_pipeline(emp_code, month)
    agg_results = list(db.attendance_logs.aggregate(pipeline))
    if agg_results:
        summary = agg_results[0]
        present_days = float(summary.get('present_days', 0.0))
        leave_days = int(summary.get('leave_days', 0))
        late_count = int(summary.get('late_count', 0))
        total_late_minutes = int(summary.get('total_late_minutes', 0))
        total_overtime_minutes = int(summary.get('total_overtime_minutes', 0))
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0
    if working_days > 0:
        raw_pct = present_days / working_days * 100.0
        attendance_pct = round_half_up(raw_pct, 2)
    else:
        attendance_pct = None
    return {'emp_code': emp_code, 'month': month, 'working_days': working_days, 'present_days': round_half_up(present_days, 2), 'leave_days': leave_days, 'late_count': late_count, 'total_late_minutes': total_late_minutes, 'total_overtime_minutes': total_overtime_minutes, 'attendance_pct': attendance_pct}

@app.get('/analytics/departments/summary', response_model=DepartmentSummary, status_code=status.HTTP_200_OK, responses={200: {'description': 'Summary'}, 422: {'description': 'Validation error'}}, tags=['analytics'])
def get_department_summary(month: str=Query(..., pattern='^\\d{4}-(0[1-9]|1[0-2])$'), department: Optional[str]=Query(None)):
    pipeline = build_department_summary_pipeline(month, department)
    agg_results = list(db.employees.aggregate(pipeline))
    items = []
    for doc in agg_results:
        avg_wh = doc.get('avg_work_hours')
        if avg_wh is not None:
            avg_wh = round_half_up(float(avg_wh), 2)
        items.append({'department': doc['department'], 'headcount': int(doc['headcount']), 'present_days': round_half_up(float(doc['present_days']), 2), 'avg_work_hours': avg_wh, 'late_count': int(doc['late_count']), 'total_late_minutes': int(doc['total_late_minutes']), 'leave_count': int(doc['leave_count']), 'on_duty_count': int(doc['on_duty_count'])})
    return {'month': month, 'items': items}

@app.get('/analytics/leaderboard/late', response_model=Leaderboard, status_code=status.HTTP_200_OK, responses={200: {'description': 'Leaderboard'}, 422: {'description': 'Validation error'}}, tags=['analytics'])
def get_late_leaderboard(month: str=Query(..., pattern='^\\d{4}-(0[1-9]|1[0-2])$'), limit: int=Query(10, ge=1, le=50), department: Optional[str]=Query(None)):
    pipeline = build_late_leaderboard_pipeline(month, limit, department)
    results = list(db.attendance_logs.aggregate(pipeline))
    items = []
    for r in results:
        items.append({'rank': int(r['rank']), 'emp_code': r['emp_code'], 'name': r['name'], 'department': r['department'], 'total_late_minutes': int(r['total_late_minutes']), 'late_count': int(r['late_count'])})
    return {'month': month, 'items': items}

@app.get('/analytics/departments/{department}/trend', response_model=Trend, status_code=status.HTTP_200_OK, responses={200: {'description': 'Trend'}, 404: {'description': 'Unknown department'}, 422: {'description': 'Validation error'}}, tags=['analytics'])
def get_department_trend(department: str, from_date: str=Query(..., alias='from', pattern='^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$'), to_date: str=Query(..., alias='to', pattern='^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$')):
    emp_count = db.employees.count_documents({'department': department})
    if emp_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Department not found')
    d_from = dt_date.fromisoformat(from_date)
    d_to = dt_date.fromisoformat(to_date)
    if d_to < d_from:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='to date must be on or after from date')
    num_days = (d_to - d_from).days + 1
    if num_days > 92:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Date range cannot exceed 92 days')
    dept_employees = list(db.employees.find({'department': department}))
    dept_emp_codes = [e['emp_code'] for e in dept_employees]
    logs = list(db.attendance_logs.find({'emp_code': {'$in': dept_emp_codes}, 'date': {'$gte': from_date, '$lte': to_date}}))
    logs_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for log in logs:
        ldate = log['date']
        logs_by_date.setdefault(ldate, []).append(log)
    items = []
    attendance_rates_history = []
    curr = d_from
    while curr <= d_to:
        curr_str = curr.isoformat()
        is_working_day = curr.weekday() < 5
        headcount = sum((1 for e in dept_employees if e.get('joined_on', '9999-99-99') <= curr_str))
        day_logs = logs_by_date.get(curr_str, [])
        present_count = 0.0
        late_count = 0
        for l in day_logs:
            st = l.get('status')
            if st in ('PRESENT', 'WFH', 'ON_DUTY'):
                if l.get('half_day'):
                    present_count += 0.5
                else:
                    present_count += 1.0
            if (l.get('late_minutes') or 0) > 0:
                late_count += 1
        if is_working_day and headcount > 0:
            rate = round_half_up(present_count / headcount, 4)
        else:
            rate = None
        attendance_rates_history.append(rate)
        window = [r for r in attendance_rates_history[-7:] if r is not None]
        if window:
            moving_avg_7d = round_half_up(sum(window) / len(window), 4)
        else:
            moving_avg_7d = None
        items.append({'date': curr_str, 'is_working_day': is_working_day, 'headcount': headcount, 'present_count': round_half_up(present_count, 2), 'late_count': late_count, 'attendance_rate': rate, 'moving_avg_7d': moving_avg_7d})
        curr += timedelta(days=1)
    return {'department': department, 'items': items}

@app.get('/admin/explain/{endpoint}', response_model=ExplainResponse, status_code=status.HTTP_200_OK, responses={200: {'description': 'Explain output'}, 422: {'description': 'Validation error'}}, tags=['admin'])
def explain_endpoint(endpoint: Literal['attendance_list', 'employee_monthly', 'department_summary', 'late_leaderboard', 'department_trend'], emp_code: Optional[str]=Query(None), month: Optional[str]=Query(None, pattern='^\\d{4}-(0[1-9]|1[0-2])$'), department: Optional[str]=Query(None), limit: int=Query(10, ge=1, le=50), date_from: Optional[str]=Query(None, alias='date_from', pattern='^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$'), date_to: Optional[str]=Query(None, alias='date_to', pattern='^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$'), from_date: Optional[str]=Query(None, alias='from', pattern='^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$'), to_date: Optional[str]=Query(None, alias='to', pattern='^\\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$'), status_filter: Optional[str]=Query(None, alias='status'), page: int=Query(1, ge=1), page_size: int=Query(20, ge=1, le=100)):
    if endpoint == 'attendance_list':
        q: Dict[str, Any] = {}
        if emp_code:
            q['emp_code'] = emp_code
        if date_from or date_to:
            dq = {}
            if date_from:
                dq['$gte'] = date_from
            if date_to:
                dq['$lte'] = date_to
            q['date'] = dq
        if status_filter:
            q['status'] = status_filter
        skip = (page - 1) * page_size
        explain_doc = db.attendance_logs.find(q).sort([('date', DESCENDING), ('emp_code', ASCENDING)]).skip(skip).limit(page_size).explain()
        return {'endpoint': endpoint, 'collection': 'attendance_logs', 'explain': explain_doc}
    elif endpoint == 'employee_monthly':
        if not emp_code or not month:
            raise HTTPException(422, 'emp_code and month are required for employee_monthly explain')
        pipeline = build_employee_monthly_pipeline(emp_code, month)
        explain_doc = db.command('explain', {'aggregate': 'attendance_logs', 'pipeline': pipeline, 'cursor': {}}, verbosity='executionStats')
        return {'endpoint': endpoint, 'collection': 'attendance_logs', 'explain': explain_doc}
    elif endpoint == 'department_summary':
        if not month:
            raise HTTPException(422, 'month is required for department_summary explain')
        pipeline = build_department_summary_pipeline(month, department)
        explain_doc = db.command('explain', {'aggregate': 'employees', 'pipeline': pipeline, 'cursor': {}}, verbosity='executionStats')
        return {'endpoint': endpoint, 'collection': 'employees', 'explain': explain_doc}
    elif endpoint == 'late_leaderboard':
        if not month:
            raise HTTPException(422, 'month is required for late_leaderboard explain')
        pipeline = build_late_leaderboard_pipeline(month, limit, department)
        explain_doc = db.command('explain', {'aggregate': 'attendance_logs', 'pipeline': pipeline, 'cursor': {}}, verbosity='executionStats')
        return {'endpoint': endpoint, 'collection': 'attendance_logs', 'explain': explain_doc}
    elif endpoint == 'department_trend':
        if not department or not from_date or (not to_date):
            raise HTTPException(422, 'department, from, and to are required for department_trend explain')
        explain_doc = db.attendance_logs.find({'date': {'$gte': from_date, '$lte': to_date}}).explain()
        return {'endpoint': endpoint, 'collection': 'attendance_logs', 'explain': explain_doc}
