"""FastAPI 단일 서버: 공용 API(헬스·역할·채팅·변경이력) + 업종 router + 정적 프론트(core 셸 + 업종 스크립트).

실행: uvicorn core.main:app --port 8000   (업종 선택: VERTICAL=sme_retail 또는 config/settings.json)
"""
import importlib
import json
import threading
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chat import run_chat
from .config import MODEL, NUM_CTX, OLLAMA_URL, TODAY, VERTICAL, VERTICAL_PKG, VERTICAL_WEB_DIR, WEB_DIR, vertical
from .db import connect
from .main_helpers import role as _role
from .permissions import PERMISSIONS, ROLES, PermissionDenied
from .validation import NotFound, ValidationFailed


def _warmup():
    """첫 질문이 모델 로딩(수십 초)으로 멈추지 않도록 미리 올려두고 30분 유지.
    실제 채팅(chat.py _llm)과 같은 num_ctx로 올려야 한다 — 다르면 첫 채팅 요청에서 Ollama가
    이미 올라간 모델을 내리고 새 컨텍스트로 다시 로드하면서, 예열을 해도 첫 질문이 여전히 느려진다."""
    try:
        httpx.post(f"{OLLAMA_URL}/api/generate", json={"model": MODEL, "keep_alive": "30m",
                                                        "options": {"num_ctx": NUM_CTX}}, timeout=300)
    except httpx.HTTPError:
        pass


@asynccontextmanager
async def lifespan(_):
    threading.Thread(target=_warmup, daemon=True).start()
    yield


app = FastAPI(title="SSOT AI 컨설팅 프로토타입", lifespan=lifespan)


@app.middleware("http")
async def _revalidate_static(request: Request, call_next):
    """정적 파일은 매번 재검증한다 (ETag 로 304). 업종을 바꿔 같은 포트로 띄우면 /vertical/app.js 주소가 같아서,
    브라우저가 캐시한 이전 업종 스크립트를 그대로 쓰는 문제를 막는다."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


# ---- 오류 응답 형식: 422 {"errors": [{"field", "message"}]}, 403/404 {"error": "..."}
@app.exception_handler(ValidationFailed)
async def _validation_failed(_: Request, exc: ValidationFailed):
    return JSONResponse(status_code=422, content={"errors": exc.errors})


@app.exception_handler(RequestValidationError)
async def _bad_request_shape(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"errors": [
        {"field": "_form", "message": "요청 형식이 올바르지 않습니다"}]})


@app.exception_handler(PermissionDenied)
async def _permission_denied(_: Request, exc: PermissionDenied):
    return JSONResponse(status_code=403, content={"error": str(exc)})


@app.exception_handler(NotFound)
async def _not_found(_: Request, exc: NotFound):
    return JSONResponse(status_code=404, content={"error": str(exc)})


class ChatRequest(BaseModel):
    role: str
    message: str = Field(min_length=1, max_length=1000)
    history: list[dict] = []


@app.get("/api/health")
def health():
    try:
        tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3).json()
        loaded = any(m["name"] == MODEL for m in tags.get("models", []))
        return {"ok": loaded, "model": MODEL, "model_installed": loaded, "today": TODAY.isoformat()}
    except httpx.HTTPError:
        return {"ok": False, "model": MODEL, "model_installed": False, "error": "Ollama 연결 실패"}


@app.get("/api/roles")
def roles():
    return {"roles": ROLES, "permissions": PERMISSIONS}


@app.get("/api/config")
def config():
    """프론트 셸이 그리는 업종 표시 문구."""
    return {"vertical": VERTICAL, "roles": ROLES, **vertical.DISPLAY}


@app.get("/api/change-log")
def change_log(limit: int = Query(50, ge=1, le=200), table_name: str | None = None, by_role: str | None = None,
               from_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
               to_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """변경이력 (전원 열람). 테이블·수정한 역할·기간(YYYY-MM-DD, 양끝 포함)으로 거를 수 있다."""
    conds, args = [], []
    for cond, v in (("table_name = ?", table_name), ("role = ?", by_role), ("substr(timestamp,1,10) >= ?", from_date),
                    ("substr(timestamp,1,10) <= ?", to_date)):
        if v:
            conds.append(cond)
            args.append(v)
    with connect() as con:
        sql = "SELECT * FROM change_log " + ("WHERE " + " AND ".join(conds) + " " if conds else "")
        rows = con.execute(sql + "ORDER BY timestamp DESC, log_id DESC LIMIT ?", (*args, limit)).fetchall()
    return {"logs": [dict(r) for r in rows]}


@app.post("/api/chat")
def chat(req: ChatRequest):
    role = _role(req.role)

    def stream():
        for ev in run_chat(role, req.message, req.history):
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


app.include_router(importlib.import_module(VERTICAL_PKG + ".routes").router)

if VERTICAL_WEB_DIR.exists():
    app.mount("/vertical", StaticFiles(directory=VERTICAL_WEB_DIR), name="vertical")
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
