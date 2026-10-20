"""공개 데모용 변경 테스트: Gemini 어댑터, 읽기 전용 모드, 채팅 횟수 제한. 네트워크·Ollama 없이 돈다.

사용: python -m pytest tests/sme_retail/test_deploy.py -q
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import chat, db as app_db, gemini, main  # noqa: E402
from core.main_helpers import ChatLimiter  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "data" / "company.db"
SALE = {"role": "영업부", "date": "2026-09-26", "client_id": "C02", "product_id": "P001", "qty": 2, "sell_price": 36000}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    shutil.copy(SRC, path)
    monkeypatch.setattr(app_db, "DB_PATH", path)
    return TestClient(main.app)


# ------------------------------------------------------------- Gemini 변환
def test_every_real_tool_schema_converts():
    decls = gemini.to_request([{"role": "user", "content": "x"}], chat.SCHEMAS, 0.3)["tools"][0]["functionDeclarations"]
    assert len(decls) == len(chat.SCHEMAS)
    for d in decls:
        assert d["name"] and d["description"] and d["parameters"]["type"] == "object"
        assert d["parameters"].get("required", ["x"]), f"{d['name']}: 빈 required 가 남음"


def test_conversation_converts_with_tool_names_and_grouped_responses():
    calls = [{"function": {"name": "a", "arguments": {"x": 1}}}, {"function": {"name": "b", "arguments": {}}}]
    body = gemini.to_request([
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "질문1"}, {"role": "assistant", "content": "답1"},
        {"role": "user", "content": "질문2"},
        {"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "tool", "content": json.dumps({"v": 1})},
        {"role": "tool", "content": "{깨진 json...(이하 생략)"},
    ], chat.SCHEMAS, 0.3)
    assert body["systemInstruction"]["parts"][0]["text"] == "SYS"
    roles = [c["role"] for c in body["contents"]]
    assert roles == ["user", "model", "user", "model", "user"]           # system 은 contents 에 없고, 두 tool 응답은 한 묶음
    assert body["contents"][3]["parts"] == [{"functionCall": {"name": "a", "args": {"x": 1}}},
                                            {"functionCall": {"name": "b", "args": {}}}]
    resp = body["contents"][4]["parts"]
    assert [p["functionResponse"]["name"] for p in resp] == ["a", "b"]    # 호출 순서대로 이름이 붙는다
    assert resp[0]["functionResponse"]["response"] == {"v": 1}
    assert resp[1]["functionResponse"]["response"] == {"result": "{깨진 json...(이하 생략)"}   # 잘려 깨진 JSON 도 객체로 감싼다


def test_model_parts_replayed_verbatim_for_thought_signature():
    raw = [{"functionCall": {"name": "a", "args": {}}, "thoughtSignature": "SIG"}]
    out = gemini.from_response({"candidates": [{"content": {"parts": raw}}]})
    assert out["tool_calls"][0]["function"]["name"] == "a"
    body = gemini.to_request([{"role": "user", "content": "q"}, out, {"role": "tool", "content": "{}"}], chat.SCHEMAS, 0.3)
    assert body["contents"][1]["parts"] == raw                           # 서명이 그대로 돌아간다


def test_response_parsing():
    out = gemini.from_response({"candidates": [{"content": {"parts": [
        {"text": "생각", "thought": True}, {"text": "답"}, {"text": "변"}]}}]})
    assert out["content"] == "답변" and out["tool_calls"] == []           # thought 파트는 답에서 빠진다
    blocked = gemini.from_response({"promptFeedback": {"blockReason": "SAFETY"}})
    assert blocked["content"] == "" and blocked["tool_calls"] == []      # 차단·빈 응답은 빈 답 -> chat.py 가 재시도


def test_chat_loop_runs_with_gemini_provider(monkeypatch):
    """tool 호출 -> 조회 -> 최종 답변까지 chat.run_chat 이 Gemini 어댑터와 맞물려 돈다 (네트워크는 가짜)."""
    seen = []
    script = [{"candidates": [{"content": {"parts": [
                  {"functionCall": {"name": "get_monthly_summary", "args": {"from_month": "2026-03", "to_month": "2026-03"}}}]}}]},
              {"candidates": [{"content": {"parts": [{"text": "3월 매출은 9,915만원입니다."}]}}]}]

    def fake_chat(_client, messages, schemas):
        seen.append(gemini.to_request(messages, schemas, 0.3))            # 매 호출의 메시지가 변환 가능한지도 확인
        return gemini.from_response(script[len(seen) - 1])

    monkeypatch.setattr(chat, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(gemini, "chat", fake_chat)
    events = list(chat.run_chat("대표", "3월 매출 알려줘"))
    assert events[-1]["type"] == "answer" and "9,915만원" in events[-1]["text"]
    assert [t["name"] for t in events[-1]["tools"]] == ["get_monthly_summary"]
    fr = seen[1]["contents"][-1]["parts"][0]["functionResponse"]
    assert fr["name"] == "get_monthly_summary" and "months" in fr["response"]   # 실제 조회 결과가 모델에게 돌아갔다


# ------------------------------------------------------------- 읽기 전용
def test_read_only_blocks_every_write_but_not_reads_or_chat(client, monkeypatch):
    monkeypatch.setattr(main, "READ_ONLY", True)
    monkeypatch.setattr(main, "run_chat", lambda *a, **k: iter([{"type": "answer", "text": "ok"}]))
    before = client.get("/api/dashboard", params={"role": "대표"}).json()["snapshots"]
    for method, url, body in [("post", "/api/sales", SALE), ("patch", "/api/sales/1", {"role": "대표", "qty": 3}),
                              ("patch", "/api/inventory/P001", {"role": "대표", "qty": 5}),
                              ("patch", "/api/products/P001", {"role": "대표", "list_price": 1}),
                              ("post", "/api/clients", {"role": "대표", "name": "x", "joined_date": "2026-09-01"}),
                              ("patch", "/api/clients/C02", {"role": "대표", "name": "y"}),
                              ("delete", "/api/sales/1", None)]:
        r = client.request(method, url, json=body)
        assert r.status_code == 403 and "읽기 전용" in r.json()["error"], (method, url, r.status_code)
    assert client.get("/api/dashboard", params={"role": "대표"}).json()["snapshots"] == before   # 아무것도 바뀌지 않았다
    assert client.post("/api/chat", json={"role": "대표", "message": "안녕"}).status_code == 200    # 채팅은 허용
    assert client.get("/api/config").json()["read_only"] is True


def test_writes_still_work_when_not_read_only(client):
    assert client.get("/api/config").json()["read_only"] is False
    assert client.post("/api/sales", json=SALE).status_code == 201


# ------------------------------------------------------------- 횟수 제한
def test_limiter_per_ip_per_minute_and_daily_cap(monkeypatch):
    lim = ChatLimiter(per_min=2, per_day=3)
    assert lim.allow("a") is None and lim.allow("a") is None
    assert "너무 빨라요" in lim.allow("a")                    # 같은 IP 3번째는 분당 제한
    assert lim.allow("b") is None                             # 다른 IP 는 별개
    assert "한도" in lim.allow("c")                           # 전체 일일 한도(3)
    lim.day = lim.day.replace(year=2000)                      # 날짜가 바뀌면 일일 카운트가 초기화된다
    assert lim.allow("c") is None


def test_chat_endpoint_refuses_with_friendly_error_when_limited(client, monkeypatch):
    monkeypatch.setattr(main, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(main, "_limiter", ChatLimiter(per_min=1, per_day=100))
    monkeypatch.setattr(main, "run_chat", lambda *a, **k: iter([{"type": "answer", "text": "정상"}]))
    ask = lambda: client.post("/api/chat", json={"role": "대표", "message": "안녕"}, headers={"x-forwarded-for": "1.2.3.4, 9.9.9.9"}).text  # noqa: E731
    assert "정상" in ask()
    second = ask()
    assert "event: error" in second and "너무 빨라요" in second and "정상" not in second


def test_chat_rejects_oversized_history(client):
    r = client.post("/api/chat", json={"role": "대표", "message": "x", "history": [{"role": "user", "content": "a"}] * 13})
    assert r.status_code == 422


def test_health_does_not_call_external_api_for_gemini(client, monkeypatch):
    monkeypatch.setattr(main, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(main, "GEMINI_API_KEY", "")
    assert client.get("/api/health").json()["ok"] is False           # 키가 없으면 '미연결'
    monkeypatch.setattr(main, "GEMINI_API_KEY", "k")
    assert client.get("/api/health").json()["ok"] is True


# ------------------------------------------------------------- 생각 설정
class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Client:
    def __init__(self, *responses):
        self.responses, self.bodies = list(responses), []

    def post(self, url, json, headers, timeout=None):
        self.bodies.append(json)
        return self.responses.pop(0)


OK = {"candidates": [{"content": {"parts": [{"text": "답"}]}}], "usageMetadata": {"thoughtsTokenCount": 0}}
MSG = [{"role": "user", "content": "q"}]


def test_thinking_config_is_sent_by_default(monkeypatch):
    monkeypatch.setattr(gemini, "_think", {"thinkingBudget": 0})
    c = _Client(_Resp(200, OK))
    assert gemini.chat(c, MSG, chat.SCHEMAS)["content"] == "답"
    assert c.bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_model_that_rejects_thinking_config_gets_retried_without_it(monkeypatch):
    monkeypatch.setattr(gemini, "_think", {"thinkingBudget": 0})
    c = _Client(_Resp(400, {"error": {"message": "Request contains an invalid argument."}}), _Resp(200, OK))   # 실측 문구: 'thinking' 이 없다
    assert gemini.chat(c, MSG, chat.SCHEMAS)["content"] == "답"
    assert "thinkingConfig" not in c.bodies[1]["generationConfig"] and gemini._think is None    # 이후 호출에도 보내지 않는다
    c2 = _Client(_Resp(200, OK))
    gemini.chat(c2, MSG, chat.SCHEMAS)
    assert "thinkingConfig" not in c2.bodies[0]["generationConfig"]


def test_400_that_persists_without_thinking_config_is_raised(monkeypatch):
    monkeypatch.setattr(gemini, "_think", {"thinkingBudget": 0})
    c = _Client(_Resp(400, {"error": {"message": "Invalid JSON payload"}}), _Resp(400, {"error": {"message": "Invalid JSON payload"}}))
    with pytest.raises(RuntimeError):
        gemini.chat(c, MSG, chat.SCHEMAS)
    assert gemini._think == {"thinkingBudget": 0} and len(c.bodies) == 2                       # 한 번 빼고 재시도했지만 또 400 이면 설정 탓이 아니다: 설정은 유지, 예외는 그대로


def test_503_is_retried_then_succeeds_and_persistent_503_raises(monkeypatch):
    monkeypatch.setattr(gemini, "_think", None)
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    c = _Client(_Resp(503, {}), _Resp(503, {}), _Resp(200, OK))
    assert gemini.chat(c, MSG, chat.SCHEMAS)["content"] == "답" and len(c.bodies) == 3
    c = _Client(_Resp(503, {}), _Resp(503, {}), _Resp(503, {}))
    with pytest.raises(RuntimeError):
        gemini.chat(c, MSG, chat.SCHEMAS)
    assert len(c.bodies) == 3                                              # 무한 재시도하지 않는다
