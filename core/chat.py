"""읽기 전용 tool-calling 채팅. run_chat() 은 진행 단계 이벤트를 yield 하는 제너레이터.

이벤트: {"type": "stage"|"tool"|"answer"|"error", ...}. pct 는 프로그래스바용(0~100).
"""
import importlib
import json
import re
import time

import httpx

from . import gemini
from .config import (LLM_PROVIDER, LLM_TIMEOUT, MAX_TOOL_RESULT_CHARS, MAX_TOOL_ROUNDS, MODEL, NUM_CTX, OLLAMA_URL,
                     TEMPERATURE, VERTICAL_PKG)
from .permissions import check_role

# 시스템 프롬프트와 tool 은 업종 패키지가 정한다
_prompt = importlib.import_module(VERTICAL_PKG + ".prompt")
_tools = importlib.import_module(VERTICAL_PKG + ".tools")
FUNCS, LABELS, SCHEMAS, call_tool = _tools.FUNCS, _tools.LABELS, _tools.SCHEMAS, _tools.call_tool

MAX_EMPTY_RETRIES = 3

# 프롬프트로도 막지만, 모델이 가끔 "(→ get_xxx 사용)"처럼 내부 함수명을 답변에 흘린다.
# 사용자에게는 구현 세부사항이라 최종 텍스트에서 한 번 더 걸러낸다 (백업 안전장치).
_ARROW_PAREN_RE = re.compile(r"[（(][^()]*→[^()]*[)）]")
_PLACEHOLDER_RE = re.compile(r"[（(][^()]*(?:조회 결과에 따라|수치 제시|수치 입력)[^()]*[)）]"
                             # '~가능한가요?'를 예/아니오로만 받아 '해 드릴 수 있습니다'로 끝내고 조회하지 않는 경우
                             r"|(?:드릴 수 있습니다|드리겠습니다|드릴게요)\.?\s*(?:\n+근거[^\n]*)?\s*$"
                             # 조회도 하지 않고 기간·대상을 되묻는 경우 (기간을 빼면 기본 기간으로 조회되므로 되물을 이유가 없다)
                             r"|지정해 주셔야|드릴까요\?\s*$")
# 단위가 붙은 수치 (데이터 값). '1일당' 같은 지표 이름은 제외한다.
_DATA_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:억|백만|만\s*원|원|%|일(?!당)|명|건|배)")
# 권한 거절 문구. 실제 권한 거절은 tool 이 "권한 없음"을 돌려줄 때만 나와야 한다 (실측: 조회도 안 하고 거절한 사례)
_DENY_RE = re.compile(r"(?:역할로는|권한이 없어)[^.\n]{0,30}(?:볼|조회할) 수 없")
NO_DATA_ANSWER = "데이터를 조회하지 못해 수치로 답할 수 없어요. 질문을 다시 한 번 입력해 주세요."
_TOOL_NAME_RE = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in FUNCS) + r")\b")


def _sanitize(text):
    text = _ARROW_PAREN_RE.sub("", text)
    text = _TOOL_NAME_RE.sub("", text)
    text = re.sub(r"\(\s*\)", "", text)              # 위 치환으로 빈 괄호가 남으면 정리
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()

# 실측: 첫 모델 호출(분석+tool 선택)과 답변 작성이 각각 대기 시간의 약 절반이라 구간을 그렇게 나눈다
_ESTIMATE = {"analyze": 8, "tool_start": 45, "tool_span": 10, "answer": 60}


def _system(role):
    return _prompt.system(role)


def _llm(client, messages):
    if LLM_PROVIDER == "gemini":
        return gemini.chat(client, messages, SCHEMAS)
    # Ollama 고유 API(/api/chat)를 쓴다. OpenAI 호환 경로(/v1/chat/completions)는 options.num_ctx를
    # 조용히 무시하고 Ollama가 하드웨어에 맞춰 컨텍스트 길이를 자체 선택한다 — 개발 PC(8GB GPU)는
    # 32768을 골라 문제가 없었지만, 통합메모리를 쓰는 맥북처럼 더 보수적으로 고르는 환경에서는 작은
    # 값이 선택돼 tool 조회 결과가 긴 답변에서 생성이 중간에 잘렸다 (done_reason=length로 실측 재현).
    # num_ctx를 직접 지정해 기기와 무관하게 동작을 고정한다.
    body = {"model": MODEL, "messages": messages, "tools": SCHEMAS, "stream": False,
            "options": {"temperature": TEMPERATURE, "num_ctx": NUM_CTX}}
    r = client.post(f"{OLLAMA_URL}/api/chat", json=body)
    r.raise_for_status()
    return r.json()["message"]


def run_chat(role, message, history=None):
    check_role(role)
    t0 = time.time()
    messages = [{"role": "system", "content": _system(role)}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])[:2000]})   # 공개 엔드포인트: 이력 하나가 비정상적으로 길어도 잘라낸다
    messages.append({"role": "user", "content": message})

    yield {"type": "stage", "step": "analyze", "label": "질문을 분석하고 있어요", "pct": _ESTIMATE["analyze"]}
    tools_used, got_data = [], False
    try:
        with httpx.Client(timeout=LLM_TIMEOUT) as client:
            for round_no in range(MAX_TOOL_ROUNDS + 1):
                msg = _llm(client, messages)
                calls = msg.get("tool_calls") or []
                # Gemma 가 reasoning 에서 tool 호출을 계획만 하고 실제 호출을 내지 않는 경우가 ~16% 있다
                # (finish=stop, content/tool_calls 모두 빔). 독립 사건이라 재시도로 해결된다.
                # 같은 이유로, 첫 응답에서 조회 없이 빈칸만 채우거나('(조회 결과에 따라 수치 제시)') 조회하겠다는 약속만 하고 끝내기도 한다 -> 빈 답처럼 재시도
                # 조회한 데이터 없이 수치를 말하는 답(실측: '잠시 후 조회 결과가 표시됩니다' 뒤에 지어낸 수치)도 재시도하고,
                # 끝내 그러면 수치 대신 안내문을 보낸다. 프롬프트만으로는 막히지 않아 코드로 강제한다.
                invented = lambda t: not got_data and _DATA_NUM_RE.search(t)  # noqa: E731
                fake_deny = lambda t: not tools_used and _DENY_RE.search(t)  # noqa: E731
                for _ in range(MAX_EMPTY_RETRIES):
                    text = (msg.get("content") or "").strip()
                    if calls or (text and not (round_no == 0 and _PLACEHOLDER_RE.search(text)) and not invented(text) and not fake_deny(text)):
                        break
                    msg = _llm(client, messages)
                    calls = msg.get("tool_calls") or []
                if not calls:
                    text = _sanitize(msg.get("content") or "") or "답변을 만들지 못했어요. 질문을 다시 한 번 입력해 주세요."
                    if invented(text):
                        text = NO_DATA_ANSWER
                    yield {"type": "answer", "text": text,
                           "tools": tools_used, "elapsed": round(time.time() - t0, 1), "pct": 100}
                    return
                messages.append(msg)
                for c in calls:
                    fn = c["function"]
                    raw = fn.get("arguments")
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except json.JSONDecodeError:
                        args = {}
                    pct = min(_ESTIMATE["tool_start"] + round_no * 5, _ESTIMATE["tool_start"] + _ESTIMATE["tool_span"])
                    yield {"type": "tool", "name": fn["name"], "args": args, "pct": pct,
                           "label": LABELS.get(fn["name"], "데이터 조회 중")}
                    result = call_tool(role, fn["name"], args)
                    got_data = got_data or "error" not in result
                    tools_used.append({"name": fn["name"], "args": args, "denied": "권한 없음" in str(result.get("error", ""))})
                    text = json.dumps(result, ensure_ascii=False)
                    if len(text) > MAX_TOOL_RESULT_CHARS:
                        text = text[:MAX_TOOL_RESULT_CHARS] + "...(이하 생략)"
                    messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": text})
                yield {"type": "stage", "step": "write", "label": "답변을 작성하고 있어요", "pct": _ESTIMATE["answer"]}
        yield {"type": "error", "message": "조회가 너무 길어져 중단했어요. 질문을 조금 더 구체적으로 해주세요."}
    except httpx.HTTPStatusError as e:
        print(f"[llm] HTTP {e.response.status_code}: {e.response.text[:300]}")   # 서버 로그에만 남긴다 (키는 헤더라 포함되지 않음)
        yield {"type": "error", "message": "요청이 몰려 AI가 잠시 쉬고 있어요. 잠시 후 다시 시도해 주세요."
               if e.response.status_code in (429, 503) else f"AI 서비스 호출에 실패했어요 (HTTP {e.response.status_code})."}
    except httpx.HTTPError as e:
        yield {"type": "error", "message": f"AI 모델 서버에 연결하지 못했어요 ({type(e).__name__})."
               + (" Ollama가 실행 중인지 확인해 주세요." if LLM_PROVIDER == "ollama" else "")}


def ask(role, message, history=None):
    """테스트용: 이벤트를 모아 (답변, tool 목록, 소요초, 오류) 반환."""
    answer, tools, elapsed, error = "", [], 0, None
    for ev in run_chat(role, message, history):
        if ev["type"] == "answer":
            answer, tools, elapsed = ev["text"], ev["tools"], ev["elapsed"]
        elif ev["type"] == "error":
            error = ev["message"]
    return answer, tools, elapsed, error
