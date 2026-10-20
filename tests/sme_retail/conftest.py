"""이 폴더의 테스트는 도소매 업종으로 실행한다. core 는 import 시점에 업종을 고르므로 다른 업종 테스트와 한 번에 돌리지 않는다.
사용: python -m pytest tests/sme_retail -q"""
import os

os.environ["VERTICAL"] = "sme_retail"
