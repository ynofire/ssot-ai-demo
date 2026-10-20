"""Google AI Studio(Gemini) generateContent 어댑터. chat.py 의 내부 메시지 형식(Ollama 식)과 Gemini 형식을 서로 바꾼다."""
import json

from .config import GEMINI_API_KEY, GEMINI_MODEL, TEMPERATURE

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


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
    r = client.post(URL.format(model=GEMINI_MODEL), json=to_request(messages, schemas, TEMPERATURE),
                    headers={"x-goog-api-key": GEMINI_API_KEY})   # 키는 헤더로만 보낸다 (URL 에 넣으면 오류 메시지로 새 수 있다)
    r.raise_for_status()
    return from_response(r.json())
