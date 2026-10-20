"""가상 데이터 생성 (약 3년, 2023-10-01 ~ 2026-09-27) -> data/company.db

의도적으로 심어둔 스토리
  1. 2026-03-02부터 판매단가 +10%, 공급처 변경으로 사입단가 -6~10% (판매량은 그대로)
  2. 해운대상사(매출 비중 약 25%)가 4~6월 주문이 줄다가 2026-07-03 이탈
매입: 매주 월요일 보충 발주라 매입이 매출원가를 따라간다 (이탈 뒤에도 급감하지 않음)
노이즈: 2024-08 소형 거래처 이탈, 2025-11 신규 거래처 유입, 매년 1월 2% 물가 반영

사용: python -m verticals.sme_retail.seed
"""
import math
import random
import sqlite3
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "company.db"
SCHEMA = Path(__file__).resolve().parent / "schema.sql"

START = date(2023, 10, 1)
END = date(2026, 9, 27)
PRICE_REVISION = date(2026, 3, 1)      # 판매단가 인상 + 공급처 변경 시행일
BASE_REF_SPEND = 85_000_000            # 거래처 비중 1.0 기준 월 기준매출(2023-10 기준단가)
MONTHLY_GROWTH = 0.001                 # 물량 자연 성장 (월 0.1%)
SEASON = {1: 1.04, 2: 0.92, 3: 1.0, 4: 1.0, 5: 1.03, 6: 1.02,
          7: 1.0, 8: 1.0, 9: 1.05, 10: 1.0, 11: 1.02, 12: 1.08}

random.seed(20260927)

# ---------------------------------------------------------------- 기준 정보
SUPPLIERS = [("SUP01", "한진산업(부산)"), ("SUP02", "동남케미칼상사"), ("SUP03", "부산종합자재")]
OLD_SUPPLIER = {"P001": "SUP01", "P002": "SUP01", "P003": "SUP01", "P006": "SUP01",
                "P007": "SUP01", "P010": "SUP01", "P004": "SUP02", "P005": "SUP02",
                "P008": "SUP02", "P009": "SUP02", "P011": "SUP02", "P012": "SUP02"}
NEW_SUPPLIER = "SUP03"  # 2026-03부터 전 상품 통합 공급

# id, 이름, 분류, 종류, 단위, 기준판매가(2023-10), 기준사입가, 인기도, 1회 주문수량 후보
PRODUCTS = [
    ("P001", "일회용 종이컵 12oz (1000입)", "컵/용기", "goods", "박스", 32000, 23000, 10, (2, 3, 4, 6, 8)),
    ("P002", "PET 투명컵 (1000입)", "컵/용기", "goods", "박스", 38000, 27500, 8, (2, 3, 4, 6)),
    ("P003", "포장 박스 중 (50입)", "포장재", "goods", "묶음", 24000, 17000, 9, (3, 4, 6, 10)),
    ("P004", "업소용 주방세제 18L", "위생용품", "goods", "통", 28000, 20500, 6, (1, 2, 3, 4)),
    ("P005", "니트릴 장갑 (100매x10)", "위생용품", "goods", "박스", 42000, 31000, 5, (1, 2, 3)),
    ("P006", "냅킨 (5000매)", "일회용품", "goods", "박스", 18000, 12800, 8, (3, 5, 8, 12)),
    ("P007", "비닐봉투 롤 (대)", "포장재", "goods", "롤", 9500, 6600, 10, (10, 15, 20, 30, 40)),
    ("P008", "알루미늄 호일 (대)", "주방용품", "goods", "롤", 15500, 11300, 4, (2, 3, 5)),
    ("P009", "주방용 랩 (30cm x 300m)", "주방용품", "goods", "박스", 36000, 26500, 4, (1, 2, 3)),
    ("P010", "배달 용기 세트 (500입)", "컵/용기", "goods", "세트", 54000, 39500, 7, (1, 2, 3, 4)),
    ("P011", "손소독제 5L", "위생용품", "goods", "통", 21000, 14800, 3, (2, 3, 5)),
    ("P012", "대용량 쓰레기봉투 (100매)", "위생용품", "goods", "묶음", 12000, 8300, 7, (5, 8, 12, 20)),
    ("S001", "배송 서비스", "서비스", "service", "건", 15000, 9500, 5, (1, 2, 3, 4)),
    ("S002", "설치·세팅 서비스", "서비스", "service", "건", 80000, 52000, 1, (1,)),
]
PROD = {p[0]: dict(id=p[0], name=p[1], category=p[2], type=p[3], unit=p[4], list=p[5],
                   cost=p[6], pop=p[7], qtys=p[8]) for p in PRODUCTS}
# 공급처 변경에 따른 사입단가 하락률 (상품별 6~10%)
for _p in PROD.values():
    _p["drop"] = round(random.uniform(0.06, 0.10), 3) if _p["type"] == "goods" else 0.0

# id, 이름, 매출 비중, 가입일, 마지막 주문일(None=계속), 이탈일, 할인율
CLIENTS = [
    ("C01", "해운대상사", 0.26, "2019-05-10", date(2026, 7, 2), date(2026, 7, 3), 0.93),
    ("C02", "센텀유통", 0.14, "2020-02-03", None, None, 0.96),
    ("C03", "광안리마트", 0.10, "2020-09-15", None, None, 0.97),
    ("C04", "남포동식자재", 0.09, "2021-01-20", None, None, 0.96),
    ("C05", "서면푸드", 0.08, "2021-06-08", None, None, 0.98),
    ("C06", "동래한정식", 0.07, "2021-11-02", None, None, 0.99),
    ("C07", "영도카페협동조합", 0.06, "2022-03-14", None, None, 0.97),
    ("C08", "사하물산", 0.05, "2022-07-25", None, None, 1.00),
    ("C09", "기장수산", 0.05, "2022-10-11", None, None, 0.98),
    ("C10", "부산진분식", 0.04, "2023-02-06", None, None, 1.00),
    ("C11", "온천장호텔", 0.04, "2023-05-30", None, None, 0.95),
    ("C12", "송도베이커리", 0.03, "2023-08-21", date(2024, 8, 12), date(2024, 8, 15), 1.00),
    ("C13", "명지프레시", 0.015, "2025-11-10", None, None, 0.98),
]
# 해운대상사 이탈 전 실적 부진 (물량 배율)
C01_DECLINE = {(2026, 4): 0.90, (2026, 5): 0.80, (2026, 6): 0.55, (2026, 7): 0.08}

# 월 고정 운영비 (2023-10 기준)
PAYROLL = 15_500_000       # 5인
RENT = 3_200_000


def d2s(d):
    return d.isoformat()


def round_to(x, unit):
    return int(round(x / unit)) * unit


def infl(d):
    return 1.02 ** max(0, d.year - 2023)   # 매년 1월 2% 반영


def list_price(p, d):
    v = p["list"] * infl(d)
    if p["type"] == "goods" and d >= PRICE_REVISION:
        v *= 1.10
    return round_to(v, 100 if v >= 10000 else 50)


def cost_price(p, d):
    v = p["cost"] * infl(d)
    if p["type"] == "goods" and d >= PRICE_REVISION:
        v *= 1 - p["drop"]
    return round_to(v, 10)


def supplier_at(pid, d):
    return NEW_SUPPLIER if d >= PRICE_REVISION else OLD_SUPPLIER[pid]


def business_days(y, m, lo, hi):
    d, out = date(y, m, 1), []
    while d.month == m:
        if d.weekday() < 5 and lo <= d <= hi:
            out.append(d)
        d += timedelta(days=1)
    return out


def add_bdays(d, n):
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def month_iter():
    y, m = START.year, START.month
    while date(y, m, 1) <= END:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


# ---------------------------------------------------------------- 매출 생성
def gen_sales():
    rows = []
    client_products = {}
    for c in CLIENTS:
        ids = [k for k, v in PROD.items() if v["type"] == "goods"]
        chosen = random.sample(ids, random.randint(7, 10))
        client_products[c[0]] = chosen + ["S001"] + (["S002"] if random.random() < 0.5 else [])

    for idx, (y, m) in enumerate(month_iter()):
        for cid, _, w, joined, last, _, disc in CLIENTS:
            lo = max(date.fromisoformat(joined), START)
            hi = min(last or END, END)
            days = business_days(y, m, lo, hi)
            if not days:
                continue
            full = len(business_days(y, m, date(y, m, 1), date(y, m, 28) + timedelta(days=10)))
            frac = min(1.0, len(days) / full)
            factor = 1 + MONTHLY_GROWTH * idx
            target = (BASE_REF_SPEND * w * factor * SEASON[m]
                      * random.uniform(0.95, 1.05) * frac)
            if cid == "C01":
                target *= C01_DECLINE.get((y, m), 1.0)
            pool = client_products[cid]
            weights = [PROD[p]["pop"] for p in pool]
            spent = 0.0
            while spent < target:
                pid = random.choices(pool, weights)[0]
                p = PROD[pid]
                qty = random.choice(p["qtys"])
                d = random.choice(days)
                spent += qty * p["list"]          # 기준단가로 누적 -> 단가가 올라도 물량은 동일
                rows.append(dict(date=d, client_id=cid, product_id=pid, qty=qty,
                                 sell_price=round_to(list_price(p, d) * disc, 10),
                                 cost_price=cost_price(p, d)))
    rows.sort(key=lambda r: (r["date"], r["client_id"], r["product_id"]))
    for i, r in enumerate(rows, 1):
        r["sale_id"] = i
    return rows


# ------------------------------------------------- 재고 시뮬레이션 -> 매입
def simulate_inventory(sales):
    """매주 월요일에 보충 발주한다: 최근 4주 평균 판매량 + (목표재고 - 재고포지션)의 일부.
    수요가 줄면(예: 거래처 이탈) 목표재고도 함께 줄어 매입이 매출원가를 따라간다.
    (예전 방식은 3년 전체 평균으로 재주문점을 잡아, 이탈 뒤 과잉재고가 쌓이고 매입이 급감했다.)
    매입 전용 난수를 써서 매출·운영비 생성과 독립적이다."""
    rng = random.Random(20260928)
    weeks_cover, correction = 4.5, 0.35          # 목표재고 = 약 4.5주치, 부족/초과분의 35%를 매주 보정
    purchases, inventory = [], {}
    mondays, d = [], START - timedelta(days=START.weekday())
    while d <= END:
        mondays.append(d)
        d += timedelta(days=7)
    by_prod, weekly = defaultdict(list), defaultdict(lambda: defaultdict(int))
    for r in sales:
        by_prod[r["product_id"]].append(r)
        weekly[r["product_id"]][r["date"] - timedelta(days=r["date"].weekday())] += r["qty"]

    for pid, p in PROD.items():
        if p["type"] != "goods":
            continue
        rows, wk = by_prod[pid], weekly[pid]
        opening_avg = sum(wk.get(m, 0) for m in mondays[1:5]) / 4     # mondays[0]은 판매가 없는 시작 전 주

        def est(monday):                                                # 직전 4주 평균 주간 판매량
            prev = [wk.get(monday - timedelta(days=7 * k), 0) for k in range(1, 5)
                    if monday - timedelta(days=7 * k) >= mondays[1]]
            return sum(prev) / len(prev) if prev else opening_avg

        stock, pending, orders = round(weeks_cover * opening_avg), [], deque(mondays)

        def receive(upto):
            nonlocal stock
            while pending and pending[0][0] <= upto:
                d, q = pending.pop(0)
                stock += q
                purchases.append(dict(date=d, supplier_id=supplier_at(pid, d), product_id=pid,
                                      qty=q, unit_cost=cost_price(p, d)))

        def place_orders(upto):
            while orders and orders[0] <= upto:
                m = orders.popleft()
                receive(m)
                e, position = est(m), stock + sum(q for _, q in pending)
                q = round(e * rng.uniform(0.92, 1.08) + correction * (weeks_cover * e - position))
                if q >= 25:
                    q = int(round(q / 5) * 5)
                if q > 0:
                    pending.append((add_bdays(m, rng.randint(1, 3)), q))
                    pending.sort()

        for r in rows:
            d = r["date"]
            place_orders(d)
            receive(d)
            if stock < r["qty"]:               # 긴급 입고
                q = max(round(weeks_cover * est(d - timedelta(days=d.weekday()))) - stock, r["qty"] - stock)
                purchases.append(dict(date=d, supplier_id=supplier_at(pid, d), product_id=pid,
                                      qty=q, unit_cost=cost_price(p, d)))
                stock += q
            stock -= r["qty"]
            assert stock >= 0
        place_orders(END)
        receive(END)                            # END 이후 도착분은 반영하지 않는다
        recent = est(mondays[-1] + timedelta(days=7))
        inventory[pid] = dict(qty=stock, reorder_level=math.ceil(recent * 4.33 * 0.5))   # 재주문 기준 = 최근 수요의 반달치
    purchases.sort(key=lambda r: (r["date"], r["product_id"]))
    return purchases, inventory


# ------------------------------------------------------------- 운영비
def gen_expenses():
    rows = []
    for y, m in month_iter():
        first = date(y, m, 1)
        pay = round_to(PAYROLL * (1.03 ** max(0, y - 2023)), 10000)
        rent = round_to(RENT * (1.03 if y >= 2025 else 1.0), 10000)
        pay_day = date(y, m, 25)
        if pay_day > END:
            continue
        rows.append(dict(date=first, category="임대료", amount=rent))
        rows.append(dict(date=pay_day, category="인건비", amount=pay))
        rows.append(dict(date=date(y, m, 20), category="공과금", amount=round_to(random.uniform(0.8, 1.1) * 1_000_000, 1000)))
        rows.append(dict(date=date(y, m, 15), category="차량·유류", amount=round_to(random.uniform(1.2, 1.6) * 1_000_000, 1000)))
        rows.append(dict(date=date(y, m, 28 if m != 2 else 27), category="기타", amount=round_to(random.uniform(0.4, 1.2) * 1_000_000, 1000)))
        if m in (3, 6, 9, 12):
            rows.append(dict(date=date(y, m, 10), category="보험", amount=1_200_000))
    return [r for r in rows if r["date"] <= END]


# ------------------------------------------------------------ 변경이력
def ts(d, hour=None):
    return f"{d.isoformat()} {hour or random.randint(9, 17):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}"


def gen_change_log(sales_corrections):
    log = []
    goods = [p for p in PROD.values() if p["type"] == "goods"]
    # 매년 1월 물가 반영 (대표: 판매가, 구매부: 사입가)
    for y in (2024, 2025, 2026):
        d = date(y, 1, 2)
        for p in goods:
            log.append((ts(d), "대표", "products", p["id"], "list_price",
                        list_price(p, date(y - 1, 12, 31)), list_price(p, d)))
            log.append((ts(d), "구매부", "products", p["id"], "cost_price",
                        cost_price(p, date(y - 1, 12, 31)), cost_price(p, d)))
    # 2026-03 판매단가 인상 + 공급처 변경
    d0, d1 = date(2026, 2, 27), date(2026, 3, 2)
    for p in goods:
        log.append((ts(d1, 9), "대표", "products", p["id"], "list_price", list_price(p, d0), list_price(p, d1)))
        log.append((ts(d1, 10), "구매부", "products", p["id"], "cost_price", cost_price(p, d0), cost_price(p, d1)))
    # 거래처 상태 변경
    log.append((ts(date(2024, 8, 20)), "영업부", "clients", "C12", "status", "active", "churned"))
    log.append((ts(date(2024, 8, 20)), "영업부", "clients", "C12", "churn_date", None, "2024-08-15"))
    log.append((ts(date(2025, 11, 10)), "영업부", "clients", "C13", "status", None, "active"))
    log.append((ts(date(2026, 7, 8)), "영업부", "clients", "C01", "status", "active", "churned"))
    log.append((ts(date(2026, 7, 8)), "영업부", "clients", "C01", "churn_date", None, "2026-07-03"))
    # 매출 수량 정정
    for sale_id, d, old, new in sales_corrections:
        log.append((ts(d + timedelta(days=1)), "영업부", "sales", str(sale_id), "qty", old, new))
    log.sort(key=lambda r: r[0])
    return [(t, r, tb, rid, f, None if o is None else str(o), None if n is None else str(n))
            for t, r, tb, rid, f, o, n in log]


def apply_sales_corrections(sales, n=8):
    picks = random.sample([r for r in sales if date(2024, 1, 1) <= r["date"] <= date(2026, 9, 1)
                           and r["qty"] >= 3], n)
    out = []
    for r in picks:
        old = r["qty"]
        r["qty"] = old + random.choice([-2, -1, 1, 2])
        out.append((r["sale_id"], r["date"], old, r["qty"]))
    return out


# --------------------------------------------------------------- 저장
def main():
    sales = gen_sales()
    corrections = apply_sales_corrections(sales)
    purchases, inventory = simulate_inventory(sales)
    expenses = gen_expenses()
    log = gen_change_log(corrections)

    DB_PATH.parent.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    con.executemany("INSERT INTO suppliers VALUES (?,?)", SUPPLIERS)
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)",
                    [(p["id"], p["name"], p["category"], p["type"], p["unit"],
                      list_price(p, END), cost_price(p, END)) for p in PROD.values()])
    con.executemany("INSERT INTO clients VALUES (?,?,?,?,?)",
                    [(c[0], c[1], "churned" if c[5] else "active", c[3], d2s(c[5]) if c[5] else None)
                     for c in CLIENTS])
    con.executemany(
        "INSERT INTO sales (sale_id,date,client_id,product_id,qty,sell_price,cost_price) VALUES (?,?,?,?,?,?,?)",
        [(r["sale_id"], d2s(r["date"]), r["client_id"], r["product_id"], r["qty"], r["sell_price"], r["cost_price"])
         for r in sales])
    con.executemany(
        "INSERT INTO purchases (date,supplier_id,product_id,qty,unit_cost) VALUES (?,?,?,?,?)",
        [(d2s(r["date"]), r["supplier_id"], r["product_id"], r["qty"], r["unit_cost"]) for r in purchases])
    con.executemany("INSERT INTO expenses (date,category,amount) VALUES (?,?,?)",
                    [(d2s(r["date"]), r["category"], r["amount"]) for r in sorted(expenses, key=lambda r: r["date"])])
    con.executemany("INSERT INTO inventory VALUES (?,?,?,?)",
                    [(pid, v["qty"], v["reorder_level"], d2s(END)) for pid, v in inventory.items()])
    con.executemany(
        "INSERT INTO change_log (timestamp,role,table_name,record_id,field,old_value,new_value) VALUES (?,?,?,?,?,?,?)", log)
    con.commit()
    con.close()
    print(f"생성 완료: {DB_PATH}")
    print(f"  sales {len(sales)}건, purchases {len(purchases)}건, expenses {len(expenses)}건, change_log {len(log)}건")


if __name__ == "__main__":
    main()
