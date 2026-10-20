"""Google AI Studio(Gemini) generateContent 어댑터. chat.py 의 내부 메시지 형식(Ollama 식)과 Gemini 형식을 서로 바꾼다."""
import json
import time

from .config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_THINKING, TEMPERATURE

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_think = json.loads(GEMINI_THINKING) if GEMINI_THINKING else None   # 모델이 거부하면 None 으로 바뀐다


def _decl(schema):
    f = schema["function"]
    params = dict(f["parameters"])
    if not params.get("required"):
        params.pop("required", None)            # 빈 required 는 보내지 않는다
    return {"name": f["name"], "description": f["description"], "parameters": params}


def to_request(messages, schemas, temperature):
    system, contents, pending = None, [], []    # pending: 직전 assistant 가 부른 tool 이름 (tool 응답에 이름이 필요하다)
    for m in messages:
        role = m["role"]
        if role == "system":
            system = {"parts": [{"text": m["content"]}]}
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            pending = [c["function"]["name"] for c in calls]
            # 모델이 준 parts 를 그대로 돌려줘야 한다 (thoughtSignature 같은 필드가 함수 호출 이어가기에 필요하다)
            parts = m.get("_gemini_parts") or (
                [{"functionCall": {"name": c["function"]["name"], "args": c["function"]["arguments"]}} for c in calls]
                or [{"text": m.get("content") or ""}])
            contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            try:
                resp = json.loads(m["content"])
            except ValueError:                  # 길이 제한으로 잘려 JSON 이 깨진 경우
                resp = m["content"]
            part = {"functionResponse": {"name": pending.pop(0) if pending else "tool",
                                         "response": resp if isinstance(resp, dict) else {"result": resp}}}
            last = contents[-1] if contents else None
            if last and last["role"] == "user" and all("functionResponse" in p for p in last["parts"]):
                last["parts"].append(part)      # 한 번에 부른 여러 tool 의 응답은 한 묶음으로
            else:
                contents.append({"role": "user", "parts": [part]})
        else:
            contents.append({"role": "user", "parts": [{"text": m["content"]}]})
    body = {"contents": contents, "tools": [{"functionDeclarations": [_decl(s) for s in schemas]}],
            "generationConfig": {"temperature": temperature}}
    if system:
        body["systemInstruction"] = system
    return body


def from_response(data):
    cand = (data.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    return {"role": "assistant",
            "content": "".join(p["text"] for p in parts if "text" in p and not p.get("thought")),
            "tool_calls": [{"function": {"name": p["functionCall"]["name"], "arguments": p["functionCall"].get("args") or {}}}
                           for p in parts if "functionCall" in p],
            "_gemini_parts": parts}


def chat(client, messages, schemas):
    global _think
    body = to_request(messages, schemas, TEMPERATURE)
    send = lambda: client.post(URL.format(model=GEMINI_MODEL), json=body, timeout=60,   # noqa: E731
                               headers={"x-goog-api-key": GEMINI_API_KEY})  # 키는 헤더로만 (URL 에 넣으면 오류 메시지로 새 수 있다)
    if _think:
        body["generationConfig"]["thinkingConfig"] = _think
    t = time.time()
    for wait in (2, 5, None):                      # 503(과부하)은 잠깐 뒤 다시 시도한다 (실측: flash-latest 가 자주 밀렸다)
        r = send()
        if r.status_code != 503 or wait is None:
            break
        print(f"[gemini] 503, {wait}초 뒤 재시도", flush=True)
        time.sleep(wait)
    if r.status_code == 400 and _think:             # 거절 문구에 'thinking' 이 없을 수도 있어서(실측) 400 이면 일단 빼고 다시 보낸다
        print(f"[gemini] 400, thinkingConfig 없이 재시도: {r.text[:200]}", flush=True)
        body["generationConfig"].pop("thinkingConfig")
        r = send()
        if r.status_code < 400:                     # 설정을 뺐더니 성공했다면 이 설정이 원인이다 -> 이후엔 보내지 않는다
            _think = None
    r.raise_for_status()
    data = r.json()
    print(f"[gemini] {time.time() - t:.1f}s model={GEMINI_MODEL} usage={data.get('usageMetadata')}", flush=True)   # 호출별 시간·토큰 (키는 포함되지 않음)
    return from_response(data)
