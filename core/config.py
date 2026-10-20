import importlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
WEB_DIR = ROOT / "core" / "web"

# 업종 선택: 환경변수 VERTICAL 이 config/settings.json 의 "vertical" 보다 우선한다.
VERTICAL = os.environ.get("VERTICAL") or json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))["vertical"]
VERTICAL_PKG = f"verticals.{VERTICAL}"
vertical = importlib.import_module(VERTICAL_PKG)   # 업종 패키지 __init__ 은 상수만 두고 core 를 import 하지 않는다
VERTICAL_WEB_DIR = ROOT / "verticals" / VERTICAL / "web"

# 환경변수 SSOT_DB 로 DB 를 바꿀 수 있다 (UI 테스트 시 임시 복사본을 쓰기 위함)
DB_PATH = Path(os.environ.get("SSOT_DB", ROOT / "data" / vertical.DB_FILE))

# 시연용 고정 '오늘' (업종별 설정). 실제 서비스에서는 date.today() 로 교체.
TODAY = vertical.TODAY

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("MODEL", "gemma4:e4b")
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.3"))
# Ollama가 기기에 따라 컨텍스트 길이를 자체 선택하면 통합메모리 환경(맥북 등)에서 너무 작게 잡혀
# 답변이 중간에 잘릴 수 있다 (실측 재현: num_ctx=4096에서 done_reason=length). 실측 최대 사용량(약
# 5600 토큰, 대화 이력 포함)의 약 3배 여유를 두고 16384로 고정한다. 메모리 비용은 미미하다(가중치가
# 대부분이고 KV 캐시 증가분은 4096->16384 구간에서 실측상 거의 없었다).
NUM_CTX = int(os.environ.get("NUM_CTX", "16384"))
# thinking 을 끄면 tool 호출 정확도가 급락한다 (검증 결과 7/15). 반드시 켠 상태로 유지.
LLM_TIMEOUT = 300
MAX_TOOL_ROUNDS = 4
MAX_TOOL_RESULT_CHARS = 6000

# ---- 공개 데모용 (모두 환경변수. 기본값은 로컬 Ollama 그대로라 기존 동작은 바뀌지 않는다)
# LLM_PROVIDER=gemini 이면 Google AI Studio(Gemini API)를 쓴다. 키는 서버 환경변수로만 받는다 (저장소·프론트에 두지 않는다).
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")   # flash-latest 는 응답이 120초 넘게 걸렸고 lite 는 1.1초였다 (실측)
# 답하기 전에 "생각"하는 시간을 조절하는 설정 (JSON, 예: '{"thinkingLevel": "low"}'). 기본은 보내지 않는다 —
# {"thinkingBudget": 0} 은 flash-lite 에서 400 "invalid argument" 였고(실측), lite 는 설정 없이도 1초대였다.
# 설정했는데 모델이 400 으로 거부하면 자동으로 빼고 다시 보낸다.
GEMINI_THINKING = os.environ.get("GEMINI_THINKING", "")
# READ_ONLY=1 이면 /api/chat 외의 쓰기 요청(POST·PATCH 등)을 전부 막고 프론트의 입력 버튼을 숨긴다.
READ_ONLY = os.environ.get("READ_ONLY", "") == "1"
# 공개 링크에서 Gemini 무료 할당량이 바닥나지 않도록 채팅 횟수를 제한한다 (질문 1개 = 모델 호출 2~3회).
CHAT_PER_IP_PER_MIN = int(os.environ.get("CHAT_PER_IP_PER_MIN", "5"))
CHAT_PER_DAY = int(os.environ.get("CHAT_PER_DAY", "150"))
