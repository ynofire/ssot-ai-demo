"""채팅 답변에서 내부 함수명이 새지 않는지 (LLM 불필요). _sanitize() 는 모델이 프롬프트를 어겨도 걸러내는 백업 안전장치다.

사용: python -m pytest tests/sme_retail/test_chat_sanitize.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.chat import _sanitize  # noqa: E402
from verticals.sme_retail.tools import FUNCS  # noqa: E402


def test_arrow_parenthetical_removed():
    text = "1. 질문 예시: \"매출 추이를 알려주세요.\" (→ get_monthly_summary 사용)"
    out = _sanitize(text)
    assert "→" not in out and "get_monthly_summary" not in out
    assert "매출 추이를 알려주세요" in out                    # 실제 내용은 남아야 한다


def test_every_known_function_name_stripped():
    for name in FUNCS:
        out = _sanitize(f"이 답변은 {name} 함수를 써서 조회했어요.")
        assert name not in out, name


def test_bare_get_prefixed_identifier_pattern_stripped():
    # 등록되지 않은 이름이라도 known-name 목록에 있으면 걸러진다는 것만 보장 (일반 get_ 패턴 전체를 지우진 않는다,
    # 오탐으로 "getting" 같은 일반 영단어를 지우지 않기 위해서다)
    assert "get_product_ranking" in FUNCS
    out = _sanitize("결과는 get_product_ranking(으)로 확인했어요.")
    assert "get_product_ranking" not in out


def test_normal_text_untouched():
    text = "매출은 9,915만원이고 작년 대비 12.6% 증가했습니다."
    assert _sanitize(text) == text


def test_double_spaces_from_removal_cleaned_up():
    out = _sanitize("판매가가 상승했습니다 (→ get_monthly_price_trend 사용) 그래서 매출이 늘었어요.")
    assert "  " not in out
    assert "get_monthly_price_trend" not in out and "→" not in out
