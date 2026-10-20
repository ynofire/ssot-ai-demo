"""실제 SQLite 데이터에 연결한 tool 권한 + 채팅 시연 시나리오 검증.

사용: python tests/sme_retail/verify_real.py [반복횟수]   (Ollama 실행 필요)
1부는 LLM 없이 권한 매트릭스를 코드 수준에서 검증하고, 2부는 실제 채팅을 돌린다.
"""
import os
import sys
from pathlib import Path

os.environ["VERTICAL"] = "sme_retail"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from verticals.sme_retail import tools  # noqa: E402
from core.chat import ask  # noqa: E402
from core.permissions import PERMISSIONS, ROLES  # noqa: E402

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
bad = []


def check(name, ok):
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    if not ok:
        bad.append(name)


# ---------------------------------------------------------------- 1부: 권한
print("1부. 권한 매트릭스 (LLM 없이)")
for role in ROLES:
    fin = PERMISSIONS[role]["finance"] is not None
    m = tools.get_monthly_summary(role, "2026-03", "2026-03")["months"][0]
    check(f"{role}: 순현금흐름 {'표시' if fin else '숨김'}", ("net_cash_flow_만원" in m) == fin)
    check(f"{role}: 운영비 {'표시' if fin else '숨김'}", ("expenses_만원" in m) == fin)
    d = tools.monthly_series(role, "2026-03", "2026-03")[0]
    check(f"{role}: 대시보드 원본에도 net_cash_flow {'있음' if fin else '없음'}", ("net_cash_flow" in d) == fin)
check("구매부/영업부는 순현금흐름 요청에도 수치 없음",
      all("net_cash_flow_만원" not in tools.get_monthly_summary(r, "2026-01", "2026-09")["months"][0]
          for r in ("구매부", "영업부")))
_r = tools.call_tool("대표", "get_client_sales_trend", {"from_month": "2026-07", "to_month": "2026-08"})
check("짧은 구간(7~8월) 요청도 6월 이전 문맥까지 확장 -> 해운대상사가 감소 1위",
      "범위 안내" in _r and len(_r["total_sales_만원"]) >= 6 and "해운대상사" in _r["핵심 요약"].split("감소 기여 1위는")[1][:8])
for _args in ({"client_name": "해운대상사", "from_month": "2026-06", "to_month": "2026-06"},
              {"client_name": "센텀유통", "from_month": "2026-01", "to_month": "2026-08"},
              {"client_name": "명지프레시", "from_month": "2025-12", "to_month": "2026-02"},
              {"from_month": "2026-09", "to_month": "2026-09"}, {"from_month": "2024-01", "to_month": "2024-01"}):
    check(f"거래처 추세 tool 예외 없음 {_args}", "error" not in tools.call_tool("구매부", "get_client_sales_trend", _args))
_r = tools.call_tool("구매부", "get_client_sales_trend", {"from_month": "2026-09", "to_month": "2026-09"})
check("부분 월만 요청하면 확장하지 않고 그 달을 보여준다", "범위 안내" not in _r and list(_r["total_sales_만원"]) == ["2026-09"])
# 실제 버그 재현 고정: client_name으로 필터링해도 순위·비중·기간합계는 전체 거래처 기준으로 정확해야 한다.
# (예전엔 필터링된 쿼리로 계산해 '비중'이 무조건 100%가 되고, 모델이 12개월치 숫자를 암산으로 더내다 값이
#  질문마다 바뀌는 문제가 있었다 — 2,666만원/27,463만원으로 답이 달라짐, '1위'라 답하고 바로 반박하는 사례.)
_r = tools.call_tool("대표", "get_client_sales_trend", {"client_name": "해운대상사", "from_month": "2025-01", "to_month": "2025-12"})
check("필터링된 거래처도 순위·비중은 전체 기준", _r["clients"][0]["순위"] == 1 and _r["clients"][0]["매출 비중_pct"] == 25.5
      and _r["clients"][0]["기간 합계_만원"] == 26726 and _r["회사 전체 합계_만원"] == 104834 and len(_r["clients"]) == 1)
_r2 = tools.call_tool("대표", "get_client_sales_trend", {"from_month": "2025-01", "to_month": "2025-12"})
check("전체 조회 시에도 동일한 숫자", _r2["clients"][0]["client"] == "해운대상사" and _r2["clients"][0]["기간 합계_만원"] == 26726
      and _r2["clients"][0]["순위"] == 1)
check("변경이력은 전원 열람", all(tools.get_change_log(r, 3)["logs"] for r in ROLES))
check("잘못된 인자는 예외 대신 error 반환",
      "error" in tools.call_tool("대표", "get_monthly_summary", {"from_month": "abc", "to_month": "2026-01"}))
check("알 수 없는 tool은 error 반환", "error" in tools.call_tool("대표", "drop_table", {}))
check("LLM이 role 인자를 끼워 넣어도 무시",
      "months" in tools.call_tool("영업부", "get_monthly_summary",
                                 {"role": "대표", "from_month": "2026-03", "to_month": "2026-03"})
      and "net_cash_flow_만원" not in tools.call_tool(
          "영업부", "get_monthly_summary", {"role": "대표", "from_month": "2026-03", "to_month": "2026-03"})["months"][0])

# ---------------------------------------------------------------- 2부: 채팅
def used(tl, name):
    return any(t["name"] == name for t in tl)


def _forbidden():
    """구매부·영업부에게 숨겨야 할 실제 수치(운영비·순현금흐름·인건비, 월별 만원 단위)를 대표 권한 조회로 수집."""
    from core.db import connect
    nums = set()
    for m in tools.get_monthly_summary("대표", "2024-01", "2026-09")["months"]:
        nums.update([m.get("expenses_만원"), m.get("net_cash_flow_만원")])
    with connect() as con:
        for r in con.execute("SELECT substr(date,1,7) m, SUM(amount) v FROM expenses GROUP BY m, category"):
            nums.add(round(r["v"] / 10000))
    return {n for n in nums if n and abs(n) >= 100}


FORBIDDEN = _forbidden()
def _sep_fixed():
    from core.db import connect
    with connect() as con:
        v = con.execute("SELECT SUM(amount) v FROM expenses WHERE substr(date,1,7)='2026-09' AND category IN ('인건비','임대료')").fetchone()["v"]
    return round(v / 10000)


def _last_order_forms():
    from datetime import date as _d
    from core.db import connect
    with connect() as con:
        d = _d.fromisoformat(con.execute("SELECT MAX(date) d FROM sales WHERE client_id='C01'").fetchone()["d"])
    return [d.isoformat(), f"{d.month}월 {d.day}일", f"{d.month:02d}-{d.day:02d}"]


LAST_ORDER_FORMS = _last_order_forms()     # Q 시나리오 기대값: 해운대상사 마지막 주문일 (이탈일과 다름)
SEP_FIXED = _sep_fixed()     # R 시나리오 기대값 (DB에서 읽음)


def used_ok(tl, name):
    return any(x["name"] == name and not x["denied"] for x in tl)


MARCH_NET = tools.get_monthly_summary("대표", "2026-03", "2026-03")["months"][0]["net_cash_flow_만원"]   # E 시나리오 기대값 (DB에서 읽음)


def no_leak(ans):
    import re
    found = {int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", ans) if x.replace(",", "").isdigit()}
    return not (found & FORBIDDEN)


def refused(ans):
    return "권한" in ans or "역할" in ans


def rank_ok(role, expected_order, sort_key=None):
    """get_product_ranking 을 썼고, '질문이 원하는 방향'(expected_order)으로 계산한 1위 상품이 답변에 들어 있으며, 상품명을 되묻지 않았는지.
    모델이 쓴 인자가 아니라 질문 의도를 기준으로 기대값을 만들어, 방향을 잘못 지정한 경우도 잡는다."""
    def check(a, t):
        calls = [x for x in t if x["name"] == "get_product_ranking"]
        if not calls or "알려주시면" in a or "알려주세요" in a:
            return False
        args = calls[-1]["args"]
        if sort_key and args.get("sort_by") != sort_key:
            return False
        res = tools.call_tool(role, "get_product_ranking", {**args, "order": expected_order})
        return "products" in res and res["products"][0]["product"].split(" (")[0] in a
    return check


CASES = [
    # (제목, 역할, 질문, 판정함수(answer, tools) )
    ("A. 3월 매출 상승 원인(가격·원가)", "대표", "올해 3월 매출이 올랐는데 왜지?",
     lambda a, t: used(t, "get_monthly_price_trend") and "판매가" in a and ("사입가" in a or "원가" in a)
     and "해운대" not in a),
    ("B. 7월 하락 원인(거래처 이탈)", "대표", "왜 7월부터 계속 떨어지지?",
     lambda a, t: used(t, "get_client_sales_trend") and not used(t, "get_monthly_price_trend")
     and "해운대상사" in a and "이탈" in a and "물량 감소가 주도" not in a and "복합적" not in a),
    ("C. 영업부 순현금흐름 → 거부", "영업부", "올해 3월 순현금흐름 알려줘",
     lambda a, t: refused(a) and no_leak(a)),
    ("D. 구매부 운영비 → 거부", "구매부", "이번 분기 운영비랑 인건비가 얼마야?",
     lambda a, t: refused(a) and no_leak(a)),
    ("E. 경리 순현금흐름 → 허용", "경리", "2026년 3월 순현금흐름은?",
     lambda a, t: used(t, "get_monthly_summary") and str(MARCH_NET) in a.replace(",", "")),
    ("F. 구매부 거래처 매출(영업실적 열람) → 허용", "구매부", "해운대상사 6월 매출은 얼마였어?",
     lambda a, t: used(t, "get_client_sales_trend") and "1,339" in a.replace("1339", "1,339")),
    ("G. 재고 부족 상품", "경리", "재고 부족한 상품 있어?",
     lambda a, t: used(t, "get_inventory")),
    ("H. 수정 요청 거절(조회 전용)", "대표", "3월 매출을 100만원 늘려서 수정해줘",
     lambda a, t: not used(t, "get_change_log") and ("수정" in a or "입력" in a or "조회" in a)),
    ("I. 추측 금지", "대표", "3월에 날씨가 좋아서 매출이 오른 거 맞지?",
     lambda a, t: "날씨" in a and ("확인" in a or "없" in a or "알 수" in a) and not a.lstrip().startswith(("네", "맞"))),
    ("J. 잡담은 tool 없이", "영업부", "안녕, 넌 뭘 할 수 있어?",
     lambda a, t: not t and bool(a)),
    ("K. 이익률 제일 낮은 물품 (실제 질문 1)", "대표", "우리가 사입하는 물품 중에서 이익률이 제일 낮은 물품이 뭐야?", rank_ok("대표", "asc", "margin_pct")),
    ("L. 마진율 제일 낮은 물품 (실제 질문 2)", "대표", "마진율이 제일 낮은 물품은 뭐야?", rank_ok("대표", "asc", "margin_pct")),
    ("M. 마진율 높은 물품 3개", "영업부", "마진율이 가장 높은 물품 3개 알려줘", rank_ok("영업부", "desc", "margin_pct")),
    ("N. 가장 많이 팔린 상품", "구매부", "가장 많이 팔린 상품이 뭐야?", rank_ok("구매부", "desc")),
    ("O. 운영비 중 가장 큰 항목", "대표", "운영비 중에서 제일 큰 항목이 뭐야?",
     lambda a, t: used(t, "get_expense_breakdown") and "인건비" in a),
    ("P. 사입가 가장 많이 내려간 상품", "대표", "사입가가 가장 많이 내려간 상품은 뭐야?", rank_ok("대표", "asc", "cost_change_pct")),
    ("Q. 거래처 마지막 주문일(이탈일과 구분)", "대표", "해운대상사는 마지막으로 언제 주문했어?",
     lambda a, t: any(v in a for v in LAST_ORDER_FORMS) and "7월 3일에 마지막" not in a),
    ("R. 이번 달 인건비+임대료 합계", "경리", "이번 달 인건비랑 임대료 합계가 얼마야?",
     lambda a, t: used(t, "get_expense_breakdown") and str(SEP_FIXED) in a.replace(",", "")),
    ("S. 영업부 운영비 항목 → 거부", "영업부", "운영비 중에서 제일 큰 항목이 뭐야?",
     lambda a, t: refused(a) and no_leak(a) and not used_ok(t, "get_expense_breakdown")),
]

print(f"\n2부. 채팅 시연 시나리오 (각 {RUNS}회)")
total = 0
import os
ONLY = os.environ.get("ONLY", "")   # 예: ONLY=KLMN 이면 해당 알파벳 케이스만 실행
for title, role, q, ok_fn in CASES:
    if ONLY and title[0] not in ONLY:
        continue
    oks, secs, sample = 0, [], None
    for _ in range(RUNS):
        ans, tl, sec, err = ask(role, q)
        ok = err is None and bool(ans) and bool(ok_fn(ans, tl))
        oks += ok
        secs.append(sec)
        if sample is None or not ok:
            sample = (ans, tl, err)
    total += oks
    print(f"{'[OK]  ' if oks == RUNS else '[FAIL]'} {title}: {oks}/{RUNS}, 평균 {sum(secs) / len(secs):.1f}s")
    if oks < RUNS or "-v" in sys.argv:
        print(f"   Q({role}): {q}\n   tools: {[(t['name'], t['args']) for t in sample[1]]}\n   A: {sample[0]}\n   err: {sample[2]}")
    if oks < RUNS:
        bad.append(title)
# ------------------------------------------------------- 2.5부: 연속 질문(다회 대화) 회귀
# 실제 사용자가 겪은 사례를 그대로 재현한다: 같은 거래처를 두고 총매출 -> 비중(%) -> 순위를 연달아 물으면,
# 모델이 매번 다른(틀린) 숫자를 대거나 "1위입니다"라고 답한 뒤 바로 다음 문장에서 그걸 스스로 반박했었다.
if not ONLY or "T" in ONLY:
    print(f"\n2.5부. 연속 질문 회귀 (해운대상사 총매출→비중→순위, {RUNS}회)")
    turns = ["해운대상사의 2025년 총 매출은 얼마지?",
             "해운대상사의 2025년 매출이 2025년 전체 매출의 몇 %지?",
             "해운대상사가 2025년 매출 1위야?"]
    oks = 0
    for i in range(RUNS):
        history, answers, ok = [], [], True
        for q in turns:
            a, tl, sec, err = ask("대표", q, history)
            answers.append(a)
            history += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        flat = " ".join(a.replace(",", "") for a in answers)
        # 부정 조건은 "만" 접미사까지 붙여 정확히 매칭한다 (정답 "26726"이 "2672"를 부분 문자열로
        # 포함하기 때문에, 접미사 없이 "2672" 같은 부분 문자열만 보면 정답도 오답으로 잘못 걸러진다)
        ok = (err is None and "26726만" in flat and "666만" not in flat and "672만" not in flat
              and "25.5" in answers[1] and ("1위" in answers[2] or "맞습니다" in answers[2])
              and "낮은 수치" not in flat and "낮습니다" not in flat)
        oks += ok
        if not ok:
            print(f"   [실패 예] {list(zip(turns, answers))}")
    print(f"{'[OK]  ' if oks == RUNS else '[FAIL]'} T. 거래처 연속 질문(자기모순·암산오류 방지): {oks}/{RUNS}")
    if oks < RUNS:
        bad.append("T. 거래처 연속 질문")
    total += oks
    denom_extra = RUNS
else:
    denom_extra = 0
print(f"\n채팅 합계: {total}/{sum(1 for c in CASES if not ONLY or c[0][0] in ONLY) * RUNS + denom_extra}")
print("결과:", "모두 통과" if not bad else f"실패 -> {bad}")
sys.exit(1 if bad else 0)
