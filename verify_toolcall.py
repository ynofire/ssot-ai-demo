"""Gemma 4 E4B-IT tool-calling 사전 검증 (Ollama OpenAI 호환 API).

사용: python verify_toolcall.py [모델명]
표준 라이브러리만 사용한다.
"""
import json
import sys
import time
import urllib.request

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemma4:e4b"
URL = "http://localhost:11434/v1/chat/completions"
import os
THINK = os.environ.get("THINK", "on")      # on | off
TEMP = float(os.environ.get("TEMP", "0"))
RUNS = int(os.environ.get("RUNS", "1"))

SYSTEM = (
    "너는 5인 도소매업체의 데이터 조회 도우미다. 반드시 tool로 조회한 수치 안에서만 "
    "근거를 대고, 데이터에 없는 이유는 추측하지 마라. 한국어로 간결하게 답한다. "
    "tool이 권한 오류를 반환하면 조회할 수 없다고만 안내한다. "
    "오늘은 2026-09-27이다. '올해'는 2026년, 작년은 2025년이다. "
    "질문이 다소 모호해도 사용자에게 되묻지 말고, 먼저 가장 관련 있는 tool을 호출해 "
    "데이터로 답하라. 매출 변동의 원인 질문에는 가격·원가 추이 tool과 거래처별 추이 tool을 "
    "필요에 따라 조회한다. 특정 월의 매출 상승·하락 원인을 물으면 그 월의 가격·원가 추이 tool을 "
    "반드시 먼저 호출한다. 기간이 없으면 올해 전체(2026-01~2026-09)를 기준으로 한다. "
    "답변에는 tool 결과의 핵심 항목(판매가, 사입가, 수량, 거래처별 매출 등)을 빠짐없이 "
    "수치와 함께 언급하고, 원인은 조회된 수치 변화로만 설명한다."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "get_monthly_price_trend",
        "description": "특정 월의 평균 판매가, 평균 사입가(원가), 판매 수량을 작년 동월과 비교해 반환한다. "
                       "매출이 오르거나 내린 이유가 가격·마진·수량 때문인지 확인할 때 쓴다.",
        "parameters": {"type": "object", "properties": {
            "year": {"type": "integer", "description": "조회 연도"},
            "month": {"type": "integer", "description": "조회 월 (1~12)"}},
            "required": ["year", "month"]}}},
    {"type": "function", "function": {
        "name": "get_client_sales_trend",
        "description": "거래처별 월 매출 추이와 거래처 이탈(churn) 시점을 반환한다. "
                       "특정 거래처의 매출 감소·이탈 때문인지 확인할 때 쓴다. 가격·원가 정보는 없다.",
        "parameters": {"type": "object", "properties": {
            "from_month": {"type": "string", "description": "시작 월 YYYY-MM"},
            "to_month": {"type": "string", "description": "종료 월 YYYY-MM"}},
            "required": ["from_month", "to_month"]}}},
    {"type": "function", "function": {
        "name": "get_cost_price",
        "description": "상품 사입가(구매 원가)를 반환한다.",
        "parameters": {"type": "object", "properties": {
            "product_id": {"type": "string"}}, "required": ["product_id"]}}},
]

# 가짜 tool 결과 (실제 DB 연결 전 검증용)
def run_tool(name, args, role):
    if name == "get_monthly_price_trend":
        return {"month": f"{args.get('year')}-{int(args.get('month')):02d}",
                "avg_sell_price": {"last_year": 10000, "this_year": 11200},
                "avg_cost_price": {"last_year": 7000, "this_year": 6300},
                "total_qty": {"last_year": 500, "this_year": 498}}
    if name == "get_client_sales_trend":
        return {"clients": [
            {"name": "해운대상사", "status": "churned", "churn_date": "2026-07-03",
             "monthly_sales": {"2026-06": 8200000, "2026-07": 1100000, "2026-08": 0}},
            {"name": "센텀유통", "status": "active",
             "monthly_sales": {"2026-06": 5100000, "2026-07": 5000000, "2026-08": 5200000}}]}
    if name == "get_cost_price":
        if role == "영업부":
            return {"error": "권한 없음: 영업부는 구매 원가를 조회할 수 없습니다."}
        return {"product_id": args.get("product_id"), "cost_price": 6300}
    return {"error": "unknown tool"}


def chat(messages, tools=True):
    body = {"model": MODEL, "messages": messages, "temperature": TEMP, "stream": False}
    if THINK == "off":
        body["reasoning_effort"] = "none"
    if tools:
        body["tools"] = TOOLS
    req = urllib.request.Request(URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["choices"][0]["message"]


def ask(question, role="대표", max_rounds=5):
    msgs = [{"role": "system", "content": SYSTEM + f" 현재 사용자 역할: {role}."},
            {"role": "user", "content": question}]
    calls_log = []
    t0 = time.time()
    for _ in range(max_rounds):
        m = chat(msgs)
        msgs.append(m)
        calls = m.get("tool_calls") or []
        if not calls:
            return m.get("content", ""), calls_log, time.time() - t0, None
        for c in calls:
            fn = c["function"]
            try:
                args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
            except json.JSONDecodeError as e:
                return None, calls_log, time.time() - t0, f"인자 JSON 파싱 실패: {fn['arguments']!r} ({e})"
            calls_log.append((fn["name"], args))
            msgs.append({"role": "tool", "tool_call_id": c.get("id", ""),
                         "content": json.dumps(run_tool(fn["name"], args, role), ensure_ascii=False)})
    return None, calls_log, time.time() - t0, "max_rounds 초과 (tool 루프)"


CASES = [
    ("A. 단일 tool + 인자 추출", "대표", "올해 3월 매출이 올랐는데 왜지?",
     lambda calls, ans: any(n == "get_monthly_price_trend" and a.get("month") == 3 for n, a in calls)
     and ans and ("판매가" in ans) and ("사입가" in ans or "원가" in ans)),
    ("B. 거래처 이탈 원인", "대표", "왜 7월부터 계속 떨어지지?",
     lambda calls, ans: any(n == "get_client_sales_trend" for n, _ in calls)
     and ans and "해운대상사" in ans),
    ("C. 권한 거부 처리", "영업부", "상품 P001의 구매 원가가 얼마야?",
     lambda calls, ans: any(n == "get_cost_price" for n, _ in calls)
     and ans and "6300" not in ans.replace(",", "") and "6,300" not in ans),
    ("D. tool 불필요 (환각 방지)", "대표", "안녕, 넌 뭘 할 수 있어?",
     lambda calls, ans: not calls and bool(ans)),
    ("E. 데이터에 없는 이유 추측 금지", "대표", "3월에 날씨가 좋아서 매출이 오른 거 맞지?",
     lambda calls, ans: bool(ans) and ("확인" in ans or "없" in ans or "알 수" in ans)),
]

if __name__ == "__main__":
    print(f"모델: {MODEL} | THINK={THINK} TEMP={TEMP} RUNS={RUNS}\n")
    total = 0
    for title, role, q, check in CASES:
        oks, secs, last = 0, [], None
        for _ in range(RUNS):
            try:
                ans, calls, sec, err = ask(q, role)
            except Exception as e:
                ans, calls, sec, err = None, [], 0, f"요청 오류 {e}"
            ok = err is None and bool(check(calls, ans))
            oks += ok
            secs.append(sec)
            if not ok or last is None:
                last = (calls, ans, err)
        total += oks
        print(f"{title}: {oks}/{RUNS} 통과, 평균 {sum(secs)/len(secs):.1f}s")
        if oks < RUNS:
            print(f"  (실패 예) tool={last[0]} 오류={last[2]}\n  A: {last[1]}")
    n = len(CASES) * RUNS
    print(f"\n합계: {total}/{n}")
