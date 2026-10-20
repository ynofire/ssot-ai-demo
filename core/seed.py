"""현재 업종의 DB 가 없으면 가상 데이터를 생성한다 (start 스크립트용).
사용: python -m core.seed          (강제 재생성: python -m core.seed --force)"""
import importlib
import sys

from .config import DB_PATH, VERTICAL_PKG

if __name__ == "__main__":
    if DB_PATH.exists() and "--force" not in sys.argv:
        print(f"데이터 있음: {DB_PATH}")
    else:
        print("가상 데이터를 생성합니다...")
        importlib.import_module(VERTICAL_PKG + ".seed").main()
