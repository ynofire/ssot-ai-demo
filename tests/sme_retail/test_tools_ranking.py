"""상품 순위·운영비 항목별·마지막 주문일 tool 테스트 (LLM 불필요). 정답은 tool 과 독립적인 SQL 로 계산해 비교한다.

사용: python -m pytest tests/sme_retail/test_tools_ranking.py -q
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from verticals.sme_retail import tools  # noqa: E402

DB = Path(__file__).resolve().parents[2] / "data" / "company.db"
call = tools.call_tool


@pytest.fixture(scope="module")
def con():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def truth_margin(con, months=("2026-06", "2026-08"), goods_only=True):
    rows = con.execute(
        "SELECT p.name, 1 - 1.0*SUM(s.qty*s.cost_price)/SUM(s.qty*s.sell_price) m FROM sales s JOIN products p USING(product_id) "
        "WHERE substr(s.date,1,7) BETWEEN ? AND ? " + ("AND p.type='goods' " if goods_only else "") + "GROUP BY p.product_id", months).fetchall()
    return sorted(((r["name"], round(r["m"] * 100, 1)) for r in rows), key=lambda x: x[1])


def test_lowest_margin_matches_independent_sql(con):
    truth = truth_margin(con)
    r = call("대표", "get_product_ranking", {"sort_by": "margin_pct", "order": "asc", "limit": 3})
    assert [(p["product"], p["margin_pct"]) for p in r["products"]] == truth[:3]
    assert r["products"][0]["product"] == truth[0][0] and r["products"][0]["순위"] == 1
    assert truth[0][0] in r["핵심 요약"]


def test_highest_margin_is_reverse_order(con):
    truth = truth_margin(con)
    r = call("영업부", "get_product_ranking", {"sort_by": "margin_pct", "order": "desc", "limit": 3})
    assert [p["product"] for p in r["products"]] == [n for n, _ in reversed(truth)][:3]
    assert "높은" in r["정렬"]


def test_services_excluded_by_default_but_can_be_included(con):
    plain = call("대표", "get_product_ranking", {"sort_by": "margin_pct", "order": "asc", "limit": 14})
    assert all(p["종류"] == "물품" for p in plain["products"]) and plain["전체 상품 수"] == 12
    withsvc = call("대표", "get_product_ranking", {"sort_by": "margin_pct", "order": "asc", "limit": 14, "include_services": True})
    assert withsvc["전체 상품 수"] == 14
    assert withsvc["products"][0]["종류"] == "서비스"                       # 서비스 마진이 더 낮아 순위가 바뀐다
    truth = truth_margin(con, goods_only=False)
    assert withsvc["products"][0]["product"] == truth[0][0]


def test_default_direction_is_never_silently_guessed():
    e = call("대표", "get_product_ranking", {"sort_by": "margin_pct"})
    assert "error" in e and "order" in e["error"]
    for key in ("cost_change_pct", "price_change_pct"):
        assert "error" in call("대표", "get_product_ranking", {"sort_by": key})
    # 매출·수량·이익은 '많은 순'이 기본
    for key in ("qty", "revenue", "gross_profit"):
        r = call("대표", "get_product_ranking", {"sort_by": key, "limit": 14})
        col = {"qty": "qty", "revenue": "revenue_만원", "gross_profit": "gross_profit_만원"}[key]
        vals = [p[col] for p in r["products"]]
        assert vals == sorted(vals, reverse=True)


def test_best_selling_by_qty_matches_sql(con):
    r = call("구매부", "get_product_ranking", {"sort_by": "qty", "order": "desc", "from_month": "2026-01", "to_month": "2026-08", "limit": 1})
    top = con.execute("SELECT p.name, SUM(s.qty) q FROM sales s JOIN products p USING(product_id) WHERE p.type='goods' "
                      "AND substr(s.date,1,7) BETWEEN '2026-01' AND '2026-08' GROUP BY p.product_id ORDER BY q DESC LIMIT 1").fetchone()
    assert (r["products"][0]["product"], r["products"][0]["qty"]) == (top["name"], top["q"])


def test_cost_and_price_change_vs_last_year(con):
    r = call("대표", "get_product_ranking", {"sort_by": "cost_change_pct", "order": "asc", "limit": 14})
    # 공급처 변경(2026-03)으로 모든 물품의 사입가가 전년 동기보다 내렸다
    assert all(p["사입가 변동률_전년동기대비_pct"] < 0 for p in r["products"])
    p0 = r["products"][0]
    cur = con.execute("SELECT 1.0*SUM(s.qty*s.cost_price)/SUM(s.qty) v FROM sales s JOIN products p USING(product_id) WHERE p.name=? AND substr(s.date,1,7) BETWEEN '2026-06' AND '2026-08'", (p0["product"],)).fetchone()["v"]
    old = con.execute("SELECT 1.0*SUM(s.qty*s.cost_price)/SUM(s.qty) v FROM sales s JOIN products p USING(product_id) WHERE p.name=? AND substr(s.date,1,7) BETWEEN '2025-06' AND '2025-08'", (p0["product"],)).fetchone()["v"]
    assert p0["사입가 변동률_전년동기대비_pct"] == round((cur / old - 1) * 100, 1)
    up = call("영업부", "get_product_ranking", {"sort_by": "price_change_pct", "order": "desc", "limit": 14})
    assert all(p["판매가 변동률_전년동기대비_pct"] > 0 for p in up["products"])           # 판매단가 +10% 인상 반영
    assert "가장 많이 오른" in up["핵심 요약"] and "가장 많이 내려간" in r["핵심 요약"]


def test_ranking_argument_validation():
    assert "error" in call("대표", "get_product_ranking", {"sort_by": "foo", "order": "asc"})
    assert "error" in call("대표", "get_product_ranking", {"sort_by": "qty", "order": "sideways"})
    assert len(call("대표", "get_product_ranking", {"sort_by": "qty", "order": "desc", "limit": 999})["products"]) <= 12
    assert "error" in call("대표", "get_product_ranking", {"sort_by": "qty", "order": "desc", "from_month": "2019-01", "to_month": "2019-03"})


def test_expense_breakdown_matches_sql_and_permissions(con):
    r = call("경리", "get_expense_breakdown", {"from_month": "2026-09", "to_month": "2026-09"})
    truth = con.execute("SELECT category, SUM(amount) v FROM expenses WHERE substr(date,1,7)='2026-09' GROUP BY category ORDER BY v DESC").fetchall()
    assert [(c["category"], c["amount_만원"]) for c in r["categories"]] == [(t["category"], round(t["v"] / 10000)) for t in truth]
    assert r["categories"][0]["category"] == "인건비" and "인건비" in r["핵심 요약"]
    assert abs(sum(c["share_pct"] for c in r["categories"]) - 100) < 0.5
    assert "부분 월" in r["note"]
    assert call("대표", "get_expense_breakdown", {})["기간"] == "2026-08"          # 기간이 없으면 마감된 최근 한 달
    for role in ("영업부", "구매부"):                                              # 금전 권한 없음 -> 조회 자체 거부
        e = call(role, "get_expense_breakdown", {})
        assert "권한 없음" in e["error"] and "categories" not in e


def test_client_last_order_date_differs_from_churn_date(con):
    r = call("대표", "get_client_sales_trend", {"client_name": "해운대상사", "from_month": "2026-06", "to_month": "2026-08"})
    c = r["clients"][0]
    last = con.execute("SELECT MAX(date) d FROM sales WHERE client_id='C01'").fetchone()["d"]
    assert c["마지막 주문일"] == last and c["이탈일"] == "2026-07-03" and last < c["이탈일"]
    active = call("대표", "get_client_sales_trend", {"client_name": "센텀유통", "from_month": "2026-06", "to_month": "2026-08"})["clients"][0]
    assert "이탈일" not in active and active["마지막 주문일"] >= "2026-09-01"


def test_new_tools_registered_for_the_llm():
    names = {s["function"]["name"] for s in tools.SCHEMAS}
    assert {"get_product_ranking", "get_expense_breakdown"} <= names
    enum = next(s for s in tools.SCHEMAS if s["function"]["name"] == "get_product_ranking")["function"]["parameters"]["properties"]["sort_by"]["enum"]
    assert set(enum) == set(tools.RANK_KEYS)
    assert not any(w in n for n in names for w in ("create", "update", "delete", "insert"))     # 조회 전용 유지
