"""채팅(LLM) tool 함수 + 대시보드가 공유하는 조회 로직. 전부 읽기 전용.

- 모든 함수는 첫 인자로 role 을 받고, 함수 안에서 권한을 검사한다 (LLM 이 아니라 코드가 강제).
- LLM 에 돌려주는 금액은 '만원' 단위 정수로 변환한다 (큰 원 단위 숫자를 소형 모델이 자주 틀림).
- 대시보드용 원 단위 원본은 monthly_series() 가 반환한다.
"""
import calendar
import re
from datetime import date, timedelta

from core.config import TODAY
from core.db import connect
from core.permissions import can_read, require_read
from core.tooling import call_tool as _call_tool
from core.tooling import get_change_log
from core.tooling import fn as _fn

MAX_MONTHS = 36
RANK_KEYS = {"margin_pct": "마진율(매출총이익률)", "gross_profit": "매출총이익", "revenue": "매출", "qty": "판매수량",
             "cost_change_pct": "사입가 변동률", "price_change_pct": "판매가 변동률"}
CHANGE_KEYS = ("cost_change_pct", "price_change_pct")
MIN_TREND_MONTHS = 6   # 거래처 추세는 최소 이 기간의 문맥이 있어야 원인(급락 시점)을 판단할 수 있다
_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


# ------------------------------------------------------------------ 헬퍼
def _month(value, name):
    m = _MONTH_RE.match(str(value).strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"{name}은(는) 'YYYY-MM' 형식이어야 합니다: {value!r}")
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"


def _month_range(frm, to):
    frm, to = _month(frm, "from_month"), _month(to, "to_month")
    if frm > to:
        frm, to = to, frm
    out, (y, m) = [], (int(frm[:4]), int(frm[5:]))
    while f"{y:04d}-{m:02d}" <= to:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if len(out) > MAX_MONTHS:
        raise ValueError(f"조회 기간은 최대 {MAX_MONTHS}개월입니다.")
    return out


def _is_partial(month):
    """당월은 마감 전이라 항상 부분 월로 취급한다 (다른 달과 직접 비교하지 않는다)."""
    return month == TODAY.strftime("%Y-%m")


def _man(won):
    return round(won / 10000)


def _won_str(v):
    return f"{round(v):,}원"


def _pct(a, b):
    return round((a / b - 1) * 100, 1) if b else None


# --------------------------------------------------- 월별 시계열 (대시보드 공용)
def monthly_series(role, frm, to):
    """월별 [매출, (매입), (운영비·순현금흐름)] 원 단위 원본. 권한 없는 필드는 아예 포함하지 않는다."""
    require_read(role, "sales")
    months = _month_range(frm, to)
    with connect() as con:
        sales = {r["m"]: r for r in con.execute(
            "SELECT substr(date,1,7) m, SUM(qty*sell_price) rev, SUM(qty*cost_price) cogs "
            "FROM sales WHERE substr(date,1,7) BETWEEN ? AND ? GROUP BY m", (months[0], months[-1]))}
        purchases = {r["m"]: r["v"] for r in con.execute(
            "SELECT substr(date,1,7) m, SUM(qty*unit_cost) v FROM purchases "
            "WHERE substr(date,1,7) BETWEEN ? AND ? GROUP BY m", (months[0], months[-1]))}
        expenses = {r["m"]: r["v"] for r in con.execute(
            "SELECT substr(date,1,7) m, SUM(amount) v FROM expenses "
            "WHERE substr(date,1,7) BETWEEN ? AND ? GROUP BY m", (months[0], months[-1]))}
    see_purchase, see_finance = can_read(role, "purchase"), can_read(role, "finance")
    out = []
    for mo in months:
        s = sales.get(mo)
        row = {"month": mo, "partial": _is_partial(mo), "revenue": s["rev"] if s else 0}
        if see_purchase:
            row["purchases"] = purchases.get(mo, 0)
            if s:
                row["gross_margin_pct"] = round((1 - s["cogs"] / s["rev"]) * 100, 1)
        if see_finance and see_purchase:
            row["expenses"] = expenses.get(mo, 0)
            row["net_cash_flow"] = row["revenue"] - row["purchases"] - row["expenses"]
        out.append(row)
    return out


# ------------------------------------------------ 전월(마감) / 당월(진행 중) 스냅샷
def _same_day_last_year(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:                       # 윤년 2/29 -> 전년 2/28
        return d.replace(year=d.year - 1, day=28)


def _period_metrics(role, d_from, d_to):
    """[d_from, d_to] (포함) 기간의 매출·매입·총이익률·운영비·순현금흐름(원). 권한 없는 필드는 포함하지 않는다."""
    require_read(role, "sales")
    a, b = d_from.isoformat(), d_to.isoformat()
    with connect() as con:
        s = con.execute("SELECT COALESCE(SUM(qty*sell_price),0) rev, COALESCE(SUM(qty*cost_price),0) cogs "
                        "FROM sales WHERE date BETWEEN ? AND ?", (a, b)).fetchone()
        pur = con.execute("SELECT COALESCE(SUM(qty*unit_cost),0) v FROM purchases WHERE date BETWEEN ? AND ?", (a, b)).fetchone()["v"]
        exp = con.execute("SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE date BETWEEN ? AND ?", (a, b)).fetchone()["v"]
    out = {"revenue": s["rev"]}
    if can_read(role, "purchase"):
        out["purchases"] = pur
        if s["rev"]:
            out["gross_margin_pct"] = round((1 - s["cogs"] / s["rev"]) * 100, 1)
        if can_read(role, "finance"):
            out["expenses"] = exp
            out["net_cash_flow"] = s["rev"] - pur - exp
    return out


def month_snapshots(role):
    """대시보드 상단 카드용. 전월은 마감된 한 달 전체를 전년 동월과, 당월은 마감 전이므로 1일~오늘을 전년 동기간과 비교한다.
    매 요청마다 DB 에서 다시 계산하므로 당월은 입력폼으로 저장하면 즉시 반영된다."""
    require_read(role, "sales")
    first = TODAY.replace(day=1)
    prev_last = first - timedelta(days=1)
    prev_first = prev_last.replace(day=1)
    ly_prev_first = _same_day_last_year(prev_first)
    ly_prev_last = ly_prev_first.replace(day=calendar.monthrange(ly_prev_first.year, ly_prev_first.month)[1])
    ly_first, ly_today = _same_day_last_year(first), _same_day_last_year(TODAY)
    return {
        "prev_month": {"month": prev_first.strftime("%Y-%m"), "closed": True,
                       "values": _period_metrics(role, prev_first, prev_last),
                       "last_year": _period_metrics(role, ly_prev_first, ly_prev_last),
                       "last_year_label": f"{ly_prev_first:%Y-%m}"},
        "current_month": {"month": first.strftime("%Y-%m"), "closed": False, "through_day": TODAY.day,
                          "values": _period_metrics(role, first, TODAY),
                          "last_year": _period_metrics(role, ly_first, ly_today),
                          "last_year_label": f"{ly_first:%Y-%m} (1~{ly_today.day}일)"},
    }


# ============================================================== LLM tool 함수
def get_monthly_summary(role, from_month, to_month):
    rows = monthly_series(role, from_month, to_month)
    months = []
    for r in rows:
        m = {"month": r["month"], "revenue_만원": _man(r["revenue"])}
        if "purchases" in r:
            m["purchases_만원"] = _man(r["purchases"])
        if "gross_margin_pct" in r:
            m["gross_margin_pct"] = r["gross_margin_pct"]
        if "expenses" in r:
            m["expenses_만원"] = _man(r["expenses"])
            m["net_cash_flow_만원"] = _man(r["net_cash_flow"])
        if r["partial"]:
            m["note"] = f"{TODAY.day}일까지의 부분 월 (다른 달과 직접 비교 불가)"
        months.append(m)
    omitted = []
    if not can_read(role, "purchase"):
        omitted.append("매입·매출총이익률")
    if not can_read(role, "finance"):
        omitted.append("운영비·순현금흐름")
    result = {"months": months}
    if omitted:
        result["권한 안내"] = f"현재 역할({role})로는 {', '.join(omitted)} 정보를 볼 수 없어 제외했습니다."
    return result


def get_monthly_price_trend(role, year, month):
    require_read(role, "sales")
    see_cost = can_read(role, "purchase")
    y, m = int(year), int(month)
    if not 1 <= m <= 12:
        raise ValueError("month는 1~12여야 합니다.")
    this, last = f"{y:04d}-{m:02d}", f"{y - 1:04d}-{m:02d}"
    with connect() as con:
        rows = con.execute(
            "SELECT substr(s.date,1,7) mo, s.product_id pid, p.name, SUM(s.qty) q, "
            "SUM(s.qty*s.sell_price) rev, SUM(s.qty*s.cost_price) cost "
            "FROM sales s JOIN products p USING(product_id) "
            "WHERE substr(s.date,1,7) IN (?,?) GROUP BY mo, s.product_id", (this, last)).fetchall()
    by = {}
    for r in rows:
        by.setdefault(r["pid"], {})[r["mo"]] = r
    rev = {this: 0, last: 0}
    cost = {this: 0, last: 0}
    for r in rows:
        rev[r["mo"]] += r["rev"]
        cost[r["mo"]] += r["cost"]
    if not rev[this] or not rev[last]:
        return {"error": f"{this} 또는 {last} 매출 데이터가 없어 비교할 수 없습니다."}

    price_eff = vol_eff = sell_base = cost_base = cost_eff = 0.0
    products = []
    for pid, v in by.items():
        a, b = v.get(last), v.get(this)
        if not (a and b):
            continue
        a_ps, b_ps = a["rev"] / a["q"], b["rev"] / b["q"]
        a_pc, b_pc = a["cost"] / a["q"], b["cost"] / b["q"]
        price_eff += b["q"] * (b_ps - a_ps)
        vol_eff += (b["q"] - a["q"]) * a_ps
        sell_base += b["q"] * a_ps
        cost_base += b["q"] * a_pc
        cost_eff += b["q"] * (b_pc - a_pc)
        item = {"product": a["name"], "revenue_만원": {"this": _man(b["rev"]), "last": _man(a["rev"])},
                "qty": {"this": b["q"], "last": a["q"]},
                "avg_sell_price": {"this": _won_str(b_ps), "last": _won_str(a_ps)}}
        if see_cost:
            item["avg_cost_price"] = {"this": _won_str(b_pc), "last": _won_str(a_pc)}
        products.append((b["rev"], item))
    products.sort(key=lambda t: -t[0])

    result = {
        "month": this, "compare_to": last,
        "revenue_만원": {"this": _man(rev[this]), "last": _man(rev[last]),
                        "change_pct": _pct(rev[this], rev[last])},
        "매출 변화 분해": {
            "판매가 변화 효과_만원": _man(price_eff),
            "물량 변화 효과_만원": _man(vol_eff),
            "평균 판매가 변화율_pct": round(price_eff / sell_base * 100, 1) if sell_base else None,
        },
        "top_products": [p for _, p in products[:6]],
    }
    if abs(vol_eff) * 3 < abs(price_eff):
        result["매출 변화 분해"]["주요 요인"] = "판매가 변화가 주도 (물량 변화는 미미하므로 원인이 아님)"
    elif abs(price_eff) * 3 < abs(vol_eff):
        result["매출 변화 분해"]["주요 요인"] = "물량 변화가 주도 (판매가 변화는 미미)"
    else:
        result["매출 변화 분해"]["주요 요인"] = "판매가와 물량 변화가 함께 작용"
    if see_cost:
        result["gross_margin_pct"] = {"this": round((1 - cost[this] / rev[this]) * 100, 1),
                                      "last": round((1 - cost[last] / rev[last]) * 100, 1)}
        result["평균 사입가 변화율_pct"] = round(cost_eff / cost_base * 100, 1) if cost_base else None
    else:
        result["권한 안내"] = f"현재 역할({role})로는 사입가·원가 정보를 볼 수 없어 제외했습니다."
    if _is_partial(this):
        result["note"] = f"{this}은(는) {TODAY.day}일까지의 부분 월입니다."
    r = result
    dec = r["매출 변화 분해"]
    causes = [f"판매가: 평균 판매가 {dec['평균 판매가 변화율_pct']:+.1f}% (매출 효과 {dec['판매가 변화 효과_만원']:+,}만원)"]
    if see_cost:
        causes.append(f"사입가(원가): 평균 사입가 {r['평균 사입가 변화율_pct']:+.1f}% → "
                      f"매출총이익률 {r['gross_margin_pct']['last']}% → {r['gross_margin_pct']['this']}%")
    causes.append(f"물량: 매출 효과 {dec['물량 변화 효과_만원']:+,}만원 ({dec['주요 요인']})")
    summary = (f"매출 {r['revenue_만원']['this']:,}만원 (작년 동월 {r['revenue_만원']['last']:,}만원, "
               f"{r['revenue_만원']['change_pct']:+.1f}%)")
    return {"핵심 요약": summary, "원인 정리": causes, **result}


def get_client_sales_trend(role, from_month, to_month, client_name=None):
    """거래처별 매출. 기간 합계·전체 대비 비중·순위는 여기서 직접 계산해 돌려준다 — 소형 모델이
    월별 숫자 12개를 암산으로 더하다 틀리는 것(실측: 같은 질문에 2,666만원/27,463만원으로 답이 바뀜)과,
    '1위입니다'라고 답하고 바로 다음 문장에서 그걸 스스로 반박하는 것(실측 재현)을 막기 위함이다.
    client_name 필터는 화면에 보여줄 목록만 좁히고, 순위·비중·회사 전체 합계는 항상 전체 거래처 기준으로 계산한다
    (필터링된 채로 계산하면 '전체 대비 비중'이 무조건 100%가 되는 오류가 있었다)."""
    require_read(role, "sales")
    months = _month_range(from_month, to_month)
    # 부분 월(이번 달)만 콕 집어 물은 경우는 그 달 데이터를 그대로 보여준다
    widened = len(months) < MIN_TREND_MONTHS and not all(_is_partial(mo) for mo in months)
    if widened:   # 모델이 "7월부터"를 그대로 7~8월로 좁혀 요청해도, 직전 월(급락 시점)이 보이도록 넓힌다
        idx = int(months[-1][:4]) * 12 + int(months[-1][5:]) - 1 - (MIN_TREND_MONTHS - 1)
        months = _month_range(f"{idx // 12:04d}-{idx % 12 + 1:02d}", months[-1])
    with connect() as con:
        rows = con.execute(
            "SELECT c.client_id cid, c.name, c.status, c.churn_date, substr(s.date,1,7) mo, "
            "SUM(s.qty*s.sell_price) v FROM sales s JOIN clients c USING(client_id) "
            "WHERE substr(s.date,1,7) BETWEEN ? AND ? GROUP BY c.client_id, mo", (months[0], months[-1])).fetchall()
        last_orders = {r["client_id"]: r["d"] for r in con.execute("SELECT client_id, MAX(date) d FROM sales GROUP BY client_id")}
    if client_name and not any(client_name.lower() in r["name"].lower() for r in rows):
        return {"error": f"'{client_name}' 거래처의 해당 기간 매출이 없습니다."}

    clients = {}
    for r in rows:
        c = clients.setdefault(r["cid"], {"cid": r["cid"], "name": r["name"], "status": r["status"],
                                          "churn_date": r["churn_date"], "monthly": {}})
        c["monthly"][r["mo"]] = r["v"]
    # 부분 월은 추세 근거로 오용되므로 시계열에서 제외한다 (부분 월만 요청한 경우에만 표시)
    shown = [mo for mo in months if not _is_partial(mo)] or months
    skipped_partial = [mo for mo in months if _is_partial(mo) and mo not in shown]
    first, last = (shown[0], shown[-1]) if len(shown) >= 2 else (None, None)

    out, total = [], {mo: 0 for mo in shown}
    for c in clients.values():
        for mo in shown:
            total[mo] += c["monthly"].get(mo, 0)
        period_total = sum(c["monthly"].get(mo, 0) for mo in shown)
        item = {"client": c["name"], "status": "이탈" if c["status"] == "churned" else "거래중",
                "기간 합계_만원": _man(period_total),  # 월별 숫자를 모델이 직접 더하지 않도록 미리 계산해 준다
                "monthly_sales_만원": {mo: _man(c["monthly"].get(mo, 0)) for mo in shown}}
        item["마지막 주문일"] = last_orders.get(c["cid"])          # 이탈일과 다를 수 있다 (이탈일은 거래 종료로 기록한 날)
        if c["churn_date"]:
            item["이탈일"] = c["churn_date"]
        if first:
            item["change_만원"] = _man(c["monthly"].get(last, 0) - c["monthly"].get(first, 0))
        out.append((period_total, item))
    out.sort(key=lambda t: -t[0])
    company_total = sum(v for v, _ in out)
    for rank, (v, item) in enumerate(out, 1):
        item["순위"] = rank                                        # 전체 거래처 기준 순위 (필터 여부와 무관)
        item["매출 비중_pct"] = round(v / company_total * 100, 1) if company_total else None

    shown_out = [i for v, i in out if not client_name or client_name.lower() in i["client"].lower()]
    result = {"clients": shown_out[:15], "전체 거래처 수": len(out),
              "회사 전체 합계_만원": _man(company_total),
              "total_sales_만원": {mo: _man(v) for mo, v in total.items()}}
    if len(shown_out) == 1:
        result["핵심 요약"] = (f"{shown_out[0]['client']}의 {months[0]}~{months[-1]} 매출 합계는 "
                            f"{shown_out[0]['기간 합계_만원']:,}만원이고, 전체 거래처 중 {shown_out[0]['순위']}위"
                            f"({shown_out[0]['매출 비중_pct']}%)입니다.")
    if first and not client_name:   # 회사 전체 추세 조회일 때만 (특정 거래처만 봤다면 '감소 기여' 설명이 맞지 않는다)
        change = total[last] - total[first]
        result["비교 구간"] = f"{first} → {last}"
        result["전체 매출 변화_만원"] = _man(change)
        if change < 0:
            drops = sorted((c["monthly"].get(last, 0) - c["monthly"].get(first, 0), c["name"])
                           for c in clients.values())
            drops = [(d, n) for d, n in drops if d < 0]
            drop_sum = sum(d for d, _ in drops)
            result["감소 기여 상위 거래처"] = [
                {"client": n, "change_만원": _man(d), "share_of_decliners_pct": round(d / drop_sum * 100)}
                for d, n in drops[:3]]
            result["설명"] = "share_of_decliners_pct는 매출이 줄어든 거래처들의 감소액 합계 중 비중이다."
            top_d, top = drops[0]
            top_c = next(c for c in clients.values() if c["name"] == top)
            churn_txt = f", {top_c['churn_date']} 이탈" if top_c["churn_date"] else ""
            o_first = total[first] - top_c["monthly"].get(first, 0)
            o_last = total[last] - top_c["monthly"].get(last, 0)
            summary = f"{first}→{last} 전체 매출 {_man(change):+,}만원. 감소 기여 1위는 {top}({_man(top_d):+,}만원{churn_txt})."
            if o_first > 0:   # 거래처 하나만 조회했다면 '나머지'는 없다
                summary += f" {top} 제외 나머지 거래처 합계는 {_man(o_first):,}→{_man(o_last):,}만원({_pct(o_last, o_first):+.1f}%)."
            result = {"핵심 요약": summary, **result}
    if widened:
        result["범위 안내"] = f"추세 판단을 위해 조회 구간을 {months[0]}~{months[-1]}로 넓혀 조회했습니다."
    if skipped_partial:
        result["note"] = (f"{', '.join(skipped_partial)}은(는) {TODAY.day}일까지의 부분 월이라 추세 비교에서 제외했습니다.")
    return result


def get_inventory(role, low_only=False):
    require_read(role, "purchase")
    if isinstance(low_only, str):
        low_only = low_only.lower() in ("true", "1", "yes")
    with connect() as con:
        rows = con.execute(
            "SELECT p.name, p.unit, i.qty, i.reorder_level FROM inventory i "
            "JOIN products p USING(product_id) ORDER BY p.product_id").fetchall()
    items = [{"product": r["name"], "unit": r["unit"], "qty": r["qty"], "reorder_level": r["reorder_level"],
              "status": "부족" if r["qty"] <= r["reorder_level"] else "정상"} for r in rows]
    if low_only:
        items = [i for i in items if i["status"] == "부족"]
    return {"as_of": TODAY.isoformat(), "items": items,
            "low_stock_count": sum(1 for i in items if i["status"] == "부족")}


def get_product_price(role, product):
    require_read(role, "sales")
    like = f"%{product}%"
    with connect() as con:
        rows = con.execute(
            "SELECT product_id, name, unit, list_price, cost_price FROM products "
            "WHERE product_id = ? OR name LIKE ? LIMIT 5", (str(product).upper(), like)).fetchall()
    if not rows:
        return {"error": f"'{product}' 상품을 찾을 수 없습니다."}
    see_cost = can_read(role, "purchase")
    items = []
    for r in rows:
        item = {"product_id": r["product_id"], "product": r["name"], "unit": r["unit"],
                "list_price": _won_str(r["list_price"])}
        if see_cost:
            item["cost_price"] = _won_str(r["cost_price"])
            item["margin_pct"] = round((1 - r["cost_price"] / r["list_price"]) * 100, 1)
        items.append(item)
    result = {"as_of": TODAY.isoformat(), "items": items}
    if not see_cost:
        result["권한 안내"] = f"현재 역할({role})로는 사입가(원가) 정보를 볼 수 없어 제외했습니다."
    return result


def _shift_months(months, n):
    out = []
    for m in months:
        idx = int(m[:4]) * 12 + int(m[5:]) - 1 + n
        out.append(f"{idx // 12:04d}-{idx % 12 + 1:02d}")
    return out


def _default_closed_period(from_month, to_month):
    """기간이 없으면 마감된 최근 한 달(from 만 있으면 그 달 하나)을 쓴다."""
    if not to_month:
        to_month = (TODAY.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        from_month = from_month or to_month
    elif not from_month:
        from_month = to_month
    return _month_range(from_month, to_month)


def get_product_ranking(role, sort_by="margin_pct", order=None, limit=5, from_month=None, to_month=None, include_services=False):
    """상품별 순위: 마진율·매출총이익·매출·판매수량, 그리고 사입가·판매가 변동률(전년 동기 대비)로 여러 상품을 비교한다.
    기본: 사입하는 물품(서비스 제외), 최근 마감 3개월."""
    require_read(role, "sales")
    if sort_by not in RANK_KEYS:
        return {"error": f"sort_by는 {', '.join(RANK_KEYS)} 중 하나여야 합니다."}
    see_cost = can_read(role, "purchase")
    if sort_by in ("margin_pct", "gross_profit", "cost_change_pct"):
        require_read(role, "purchase")            # 마진·이익·사입가는 원가(상품구매) 정보가 필요하다
    if order is None:                              # 방향을 조용히 추측하지 않는다 (틀린 순위를 '1위'로 답하게 됨)
        if sort_by == "margin_pct" or sort_by in CHANGE_KEYS:
            return {"error": "order를 지정해 주세요: asc(낮은 순 · 변동률은 가장 많이 내려간 순) 또는 desc(높은 순 · 변동률은 가장 많이 오른 순)."}
        order = "desc"                             # 매출·이익·수량은 '많은 순'이 기본
    if order not in ("asc", "desc"):
        return {"error": "order는 asc(낮은 순) 또는 desc(높은 순)이어야 합니다."}
    limit = max(1, min(int(limit), 14))
    if isinstance(include_services, str):
        include_services = include_services.lower() in ("true", "1", "yes")

    if not to_month:                                # 기본 기간: 마감된 최근 3개월
        last_closed = TODAY.replace(day=1) - timedelta(days=1)
        to_month = last_closed.strftime("%Y-%m")
        idx = last_closed.year * 12 + last_closed.month - 1 - 2
        from_month = from_month or f"{idx // 12:04d}-{idx % 12 + 1:02d}"
    elif not from_month:
        from_month = to_month
    months = _month_range(from_month, to_month)
    prior = _shift_months(months, -12)

    def load(ms):
        with connect() as con:
            rows = con.execute(
                "SELECT p.product_id pid, p.name, p.type, p.unit, p.list_price, p.cost_price, SUM(s.qty) q, "
                "SUM(s.qty*s.sell_price) rev, SUM(s.qty*s.cost_price) cogs "
                "FROM sales s JOIN products p USING(product_id) WHERE substr(s.date,1,7) BETWEEN ? AND ? "
                + ("" if include_services else "AND p.type='goods' ") + "GROUP BY p.product_id",
                (ms[0], ms[-1])).fetchall()
        return {r["pid"]: r for r in rows}

    cur, prev = load(months), load(prior)
    if not cur:
        return {"error": f"{months[0]}~{months[-1]} 기간에 판매된 상품이 없습니다."}

    items = []
    for pid, r in cur.items():
        it = {"product": r["name"], "종류": "물품" if r["type"] == "goods" else "서비스",
              "revenue_만원": _man(r["rev"]), "qty": r["q"], "unit": r["unit"]}
        if see_cost:
            it["margin_pct"] = round((1 - r["cogs"] / r["rev"]) * 100, 1)
            it["gross_profit_만원"] = _man(r["rev"] - r["cogs"])
            it["현재 정가 기준 마진율_pct"] = round((1 - r["cost_price"] / r["list_price"]) * 100, 1)
        p = prev.get(pid)
        if p and p["q"] and r["q"]:                # 전년 동기와 개당 평균 단가 비교
            it["판매가 변동률_전년동기대비_pct"] = round(((r["rev"] / r["q"]) / (p["rev"] / p["q"]) - 1) * 100, 1)
            if see_cost:
                it["사입가 변동률_전년동기대비_pct"] = round(((r["cogs"] / r["q"]) / (p["cogs"] / p["q"]) - 1) * 100, 1)
        items.append(it)
    key = {"margin_pct": "margin_pct", "gross_profit": "gross_profit_만원", "revenue": "revenue_만원", "qty": "qty",
           "cost_change_pct": "사입가 변동률_전년동기대비_pct", "price_change_pct": "판매가 변동률_전년동기대비_pct"}[sort_by]
    items = [i for i in items if key in i]
    if not items:
        return {"error": f"전년 동기({prior[0]}~{prior[-1]}) 판매 데이터가 없어 변동률을 비교할 수 없습니다."}
    items.sort(key=lambda i: i[key], reverse=(order == "desc"))
    total = len(items)
    for n, it in enumerate(items, 1):
        it["순위"] = n
    top = items[:limit]

    label = RANK_KEYS[sort_by]
    period = f"{months[0]}~{months[-1]}" if len(months) > 1 else months[0]
    if sort_by in CHANGE_KEYS:
        phrase = "가장 많이 내려간" if order == "asc" else "가장 많이 오른"
        summary = f"{period}을(를) 전년 동기({prior[0]}~{prior[-1]})와 비교한 {label} 기준 {phrase} 상품은 {top[0]['product']}({top[0][key]:+}%)입니다."
        if len(top) > 1:
            summary += " 이어서 " + ", ".join(f"{t['product']}({t[key]:+}%)" for t in top[1:3]) + " 순입니다."
    else:
        low, high = {"margin_pct": ("낮은", "높은"), "qty": ("적은", "많은")}.get(sort_by, ("작은", "큰"))
        direction = low if order == "asc" else high
        unit = {"margin_pct": "%", "gross_profit": "만원", "revenue": "만원", "qty": ""}[sort_by]
        summary = f"{period} 기준 {label}이(가) 가장 {direction} 상품은 {top[0]['product']}({top[0][key]:,}{unit})입니다."
        if len(top) > 1:
            summary += " 이어서 " + ", ".join(f"{t['product']}({t[key]:,}{unit})" for t in top[1:3]) + " 순입니다."
    direction_txt = ("내려간 순" if order == "asc" else "오른 순") if sort_by in CHANGE_KEYS else None
    result = {"핵심 요약": summary, "기간": period, "대상": "사입하는 물품 + 서비스" if include_services else "사입하는 물품(서비스 제외)",
              "정렬": f"{label} {direction_txt}" if direction_txt else f"{label} {'낮은' if order == 'asc' else '높은'} 순" if sort_by == "margin_pct" else f"{label} {'적은' if sort_by == 'qty' else '작은'} 순" if order == "asc" else f"{label} {'많은' if sort_by == 'qty' else '큰'} 순",
              "전체 상품 수": total, "products": top}
    if not see_cost:
        result["권한 안내"] = f"현재 역할({role})로는 마진·원가 정보를 볼 수 없어 제외했습니다."
    if any(_is_partial(mo) for mo in months):
        result["note"] = f"{TODAY.strftime('%Y-%m')}은(는) {TODAY.day}일까지의 부분 월이 포함된 수치입니다."
    return result


def get_expense_breakdown(role, from_month=None, to_month=None):
    """운영비를 항목별(인건비·임대료·공과금·차량·유류·보험·기타)로 나눠 보여준다. 금전(운영비) 열람 권한이 필요하다."""
    require_read(role, "finance")
    months = _default_closed_period(from_month, to_month)
    with connect() as con:
        rows = con.execute("SELECT category, SUM(amount) v FROM expenses WHERE substr(date,1,7) BETWEEN ? AND ? "
                           "GROUP BY category ORDER BY v DESC", (months[0], months[-1])).fetchall()
    if not rows:
        return {"error": f"{months[0]}~{months[-1]} 기간의 운영비 데이터가 없습니다."}
    total = sum(r["v"] for r in rows)
    period = f"{months[0]}~{months[-1]}" if len(months) > 1 else months[0]
    cats = [{"category": r["category"], "amount_만원": _man(r["v"]), "share_pct": round(r["v"] / total * 100, 1)} for r in rows]
    result = {"핵심 요약": f"{period} 운영비 합계는 {_man(total):,}만원이고, 가장 큰 항목은 {cats[0]['category']}({cats[0]['amount_만원']:,}만원, {cats[0]['share_pct']}%)입니다.",
              "기간": period, "total_만원": _man(total), "categories": cats}
    if any(_is_partial(mo) for mo in months):
        result["note"] = f"{TODAY.strftime('%Y-%m')}은(는) {TODAY.day}일까지의 부분 월 수치입니다 (월말 지출은 아직 반영되지 않음)."
    return result


# ============================================================ tool 등록/실행
_MONTH_P = {"type": "string", "description": "YYYY-MM"}

SCHEMAS = [
    _fn("get_monthly_summary",
        "월별 매출, 매입, 매출총이익률, 운영비, 순현금흐름을 반환한다. 자금 흐름·월별 실적 추이를 볼 때 쓴다. "
        "가격·거래처별 원인은 알 수 없다.",
        {"from_month": _MONTH_P, "to_month": _MONTH_P}, ["from_month", "to_month"]),
    _fn("get_monthly_price_trend",
        "특정 월의 매출을 작년 동월과 비교해 판매가·사입가(원가)·물량 중 무엇이 원인인지 분해해 반환한다. "
        "특정 월의 매출 상승·하락이 가격·마진·수량 때문인지 확인할 때 쓴다.",
        {"year": {"type": "integer", "description": "조회 연도"},
         "month": {"type": "integer", "description": "조회 월 (1~12)"}}, ["year", "month"]),
    _fn("get_client_sales_trend",
        "거래처별 월 매출 추이, 거래처 이탈(churn) 여부와 시점, 매출 감소에 기여한 거래처 순위를 반환한다. "
        "매출이 줄거나 늘어난 이유가 특정 거래처 때문인지 확인할 때 쓴다. 가격·원가 정보는 없다.",
        {"from_month": _MONTH_P, "to_month": _MONTH_P,
         "client_name": {"type": "string", "description": "특정 거래처만 볼 때 이름 (선택)"}},
        ["from_month", "to_month"]),
    _fn("get_product_ranking",
        "여러 상품을 비교해 순위를 매긴다. '이익률(마진율)이 가장 낮은/높은 물품', '가장 많이 팔린 상품', '매출 상위 상품', "
        "'이익이 가장 큰 상품'처럼 상품끼리 비교하는 질문에 쓴다. 상품명을 몰라도 호출할 수 있다. 기본은 사입하는 물품(서비스 제외), 최근 3개월.",
        {"sort_by": {"type": "string", "enum": list(RANK_KEYS), "description": "순위 기준: margin_pct(마진율=이익률), gross_profit(매출총이익), revenue(매출), qty(판매수량), cost_change_pct(사입가 변동률: 전년 동기 대비), price_change_pct(판매가 변동률: 전년 동기 대비)"},
         "order": {"type": "string", "enum": ["asc", "desc"], "description": "asc=낮은/적은 순(변동률은 가장 많이 내려간 순), desc=높은/많은 순(변동률은 가장 많이 오른 순)"},
         "limit": {"type": "integer", "description": "몇 개까지 (기본 5)"},
         "from_month": _MONTH_P, "to_month": _MONTH_P,
         "include_services": {"type": "boolean", "description": "true면 배송·설치 서비스도 포함 (기본 false)"}},
        ["sort_by", "order"]),
    _fn("get_expense_breakdown",
        "운영비를 항목별(인건비, 임대료, 공과금, 차량·유류, 보험, 기타)로 나눠 금액과 비중을 반환한다. "
        "'운영비 중 가장 큰 항목', '인건비·임대료가 얼마'처럼 항목별로 물을 때 쓴다. 기간이 없으면 마감된 최근 한 달.",
        {"from_month": _MONTH_P, "to_month": _MONTH_P}, []),
    _fn("get_inventory", "상품별 현재 재고와 재주문 기준, 부족 여부를 반환한다.",
        {"low_only": {"type": "boolean", "description": "true면 재고 부족 상품만"}}, []),
    _fn("get_product_price", "상품의 현재 판매가(정가)와 사입가(원가), 마진율을 반환한다.",
        {"product": {"type": "string", "description": "상품명 일부 또는 상품ID (예: P001)"}}, ["product"]),
    _fn("get_change_log", "최근 데이터 변경이력(누가, 언제, 무엇을 바꿨는지)을 반환한다.",
        {"limit": {"type": "integer", "description": "건수 (기본 15, 최대 50)"},
         "table_name": {"type": "string", "description": "products, clients, sales 등 (선택)"}}, []),
]

FUNCS = {f.__name__: f for f in (get_monthly_summary, get_monthly_price_trend, get_client_sales_trend, get_product_ranking, get_expense_breakdown,
                                 get_inventory, get_product_price, get_change_log)}

LABELS = {
    "get_monthly_summary": "월별 매출·자금 흐름 조회 중",
    "get_monthly_price_trend": "판매가·사입가 추이 분석 중",
    "get_client_sales_trend": "거래처별 매출 추이 조회 중",
    "get_product_ranking": "상품별 순위 분석 중",
    "get_expense_breakdown": "운영비 항목별 조회 중",
    "get_inventory": "재고 현황 조회 중",
    "get_product_price": "상품 가격 조회 중",
    "get_change_log": "변경이력 조회 중",
}


def call_tool(role, name, args):
    return _call_tool(FUNCS, role, name, args)
