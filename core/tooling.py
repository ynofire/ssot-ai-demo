"""LLM tool 등록·실행 공용 엔진. 업종별 tools.py 가 스키마와 함수 목록(FUNCS)을 만들어 넘긴다."""
import inspect

from .db import connect
from .permissions import PermissionDenied


def fn(name, description, props, required):
    """Ollama/OpenAI 형식 tool 스키마 한 개."""
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": required}}}


def call_tool(funcs, role, name, args):
    """LLM 이 넘긴 인자를 검증해 실행. 실패는 예외 대신 {'error': ...} 로 돌려 모델이 안내할 수 있게 한다."""
    f = funcs.get(name)
    if f is None:
        return {"error": f"알 수 없는 tool: {name}"}
    params = inspect.signature(f).parameters
    clean = {k: v for k, v in (args or {}).items() if k in params and k != "role"}
    try:
        return f(role, **clean)
    except PermissionDenied as e:
        return {"error": str(e)}
    except (ValueError, TypeError) as e:
        return {"error": f"잘못된 요청: {e}"}


def get_change_log(role, limit=15, table_name=None):
    """변경이력은 역할과 무관하게 전원 열람 가능."""
    limit = max(1, min(int(limit), 50))
    sql = "SELECT timestamp, role, table_name, record_id, field, old_value, new_value FROM change_log "
    args = []
    if table_name:
        sql += "WHERE table_name = ? "
        args.append(table_name)
    with connect() as con:
        rows = con.execute(sql + "ORDER BY timestamp DESC, log_id DESC LIMIT ?", (*args, limit)).fetchall()
    return {"logs": [dict(r) for r in rows]}
