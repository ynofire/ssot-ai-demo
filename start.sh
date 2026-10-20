#!/usr/bin/env bash
# SSOT AI 프로토타입 실행 (macOS / Linux)
#   실행:  bash start.sh          (포트 변경: PORT=8010 bash start.sh)
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"

# 1) Python 3.10+ 확인
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 를 찾을 수 없어요. https://www.python.org/downloads/ 에서 Python 3.12 를 설치해 주세요."; exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 이상이 필요해요 (현재: $("$PY" --version)). Python 3.12 를 설치한 뒤 PYTHON=python3.12 bash start.sh 로 실행해 주세요."; exit 1
fi

# 2) 가상환경 + 패키지 (처음 한 번만 오래 걸려요)
if [ ! -d .venv ]; then
  echo "가상환경을 만드는 중..."; "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

# 3) 포트가 이미 사용 중이면 알려주고 멈춘다 (예전에 켜 둔 서버가 남아 있는 경우)
if lsof -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "포트 $PORT 을(를) 이미 다른 프로그램이 쓰고 있어요:"
  lsof -iTCP:"$PORT" -sTCP:LISTEN
  echo "그 프로그램을 끄거나 다른 포트로 실행하세요:  PORT=8010 bash start.sh"; exit 1
fi

# 4) Ollama / 모델 확인 (없어도 대시보드는 뜨지만 AI 채팅은 동작하지 않아요)
if ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "[주의] Ollama 가 실행 중이 아니에요. Ollama 앱을 켠 뒤 다시 실행하면 AI 채팅을 쓸 수 있어요."
elif ! curl -s -m 2 http://localhost:11434/api/tags | grep -q '"gemma4:e4b"'; then
  echo "[주의] 모델이 없어요. 터미널에서  ollama pull gemma4:e4b  (약 9.6GB) 를 먼저 실행하세요."
fi

# 5) 데이터 확인 (없으면 가상 데이터를 새로 만든다). 업종은 config/settings.json 또는 VERTICAL=... 로 고른다
python -m core.seed

echo "서버를 시작합니다:  http://localhost:$PORT   (끄기: Ctrl+C)"
( sleep 2; command -v open >/dev/null 2>&1 && open "http://localhost:$PORT" ) &
exec python -m uvicorn core.main:app --port "$PORT"
