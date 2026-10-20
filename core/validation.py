"""입력폼(쓰기) 공용 엔진: 필드 검증 헬퍼, 오류 형식, 변경이력 기록. 업종별 폼 모듈이 가져다 쓴다.

- 서버가 최종 검증한다: 빈 값·음수·소수·날짜 형식·미래 날짜. 오류는 필드별로 모아서 한 번에 반환.
- 저장 + 변경이력 기록을 한 트랜잭션으로 처리한다 (_begin 후 같은 커넥션에서 _log).
"""
import re
from datetime import date, datetime

from .config import TODAY

MIN_DATE = date(2000, 1, 1)
MAX_QTY = 1_000_000
MAX_MONEY = 100_000_000
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ValidationFailed(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(e["message"] for e in errors))
        self.errors = errors


class NotFound(Exception):
    pass


# ------------------------------------------------------------------ 검증 헬퍼
def _err(errors, field, message):
    errors.append({"field": field, "message": message})


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _int(data, field, label, errors, *, required=True, positive=False, maximum=MAX_MONEY):
    v = data.get(field)
    if _blank(v):
        if required:
            _err(errors, field, f"{label}: 값을 입력해 주세요")
        return None
    if isinstance(v, bool):
        _err(errors, field, f"{label}: 정수만 입력할 수 있습니다")
        return None
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not re.fullmatch(r"-?\d+", s):
            _err(errors, field, f"{label}: 정수만 입력할 수 있습니다")
            return None
        v = int(s)
    elif isinstance(v, float):
        if not v.is_integer():
            _err(errors, field, f"{label}: 소수는 입력할 수 없습니다")
            return None
        v = int(v)
    elif not isinstance(v, int):
        _err(errors, field, f"{label}: 정수만 입력할 수 있습니다")
        return None
    if v < 0:
        _err(errors, field, f"{label}: 음수는 입력할 수 없습니다")
        return None
    if positive and v == 0:
        _err(errors, field, f"{label}: 1 이상이어야 합니다")
        return None
    if v > maximum:
        _err(errors, field, f"{label}: 값이 너무 큽니다 (최대 {maximum:,})")
        return None
    return v


def _date(data, field, label, errors, *, required=True):
    v = data.get(field)
    if _blank(v):
        if required:
            _err(errors, field, f"{label}: 값을 입력해 주세요")
        return None
    s = str(v).strip()
    try:
        if not _DATE_RE.match(s):
            raise ValueError
        d = date.fromisoformat(s)
    except ValueError:
        _err(errors, field, f"{label}: YYYY-MM-DD 형식의 실제 날짜여야 합니다")
        return None
    if d > TODAY:
        _err(errors, field, f"{label}: 미래 날짜({TODAY.isoformat()} 이후)는 입력할 수 없습니다")
        return None
    if d < MIN_DATE:
        _err(errors, field, f"{label}: {MIN_DATE.isoformat()} 이후 날짜여야 합니다")
        return None
    return d


def _id(data, field, label, errors, *, required=True):
    v = data.get(field)
    if _blank(v):
        if required:
            _err(errors, field, f"{label}: 값을 입력해 주세요")
        return None
    s = str(v).strip()
    if not _ID_RE.match(s):
        _err(errors, field, f"{label}: 올바른 ID 형식이 아닙니다")
        return None
    return s


def _text(data, field, label, errors, *, required=True, maxlen=50):
    v = data.get(field)
    if _blank(v):
        if required:
            _err(errors, field, f"{label}: 값을 입력해 주세요")
        return None
    s = str(v).strip()
    if len(s) > maxlen or any(ord(ch) < 32 for ch in s):
        _err(errors, field, f"{label}: {maxlen}자 이하의 일반 문자만 입력할 수 있습니다")
        return None
    return s


def _only(data, allowed, errors):
    for k in data:
        if k not in allowed and k != "role":
            _err(errors, k, f"{k}: 수정할 수 없는 항목입니다")


def _now_ts():
    return f"{TODAY.isoformat()} {datetime.now():%H:%M:%S}"


def _log(con, role, table, record_id, field, old, new):
    con.execute(
        "INSERT INTO change_log (timestamp, role, table_name, record_id, field, old_value, new_value) "
        "VALUES (?,?,?,?,?,?,?)",
        (_now_ts(), role, table, str(record_id), field,
         None if old is None else str(old), None if new is None else str(new)))


def _begin(con):
    con.execute("BEGIN IMMEDIATE")
