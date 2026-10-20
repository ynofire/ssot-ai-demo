"""생성된 data/company.db 의 무결성과 '심어둔 스토리'가 수치로 성립하는지 검증.

사용: python -m verticals.sme_retail.check_seed
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "company.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
failures = []


def q(sql, *a):
    return con.execute(sql, a).fetchall()


def check(name, ok, detail=""):
    print(f"  [{'OK' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


def won(x):
    return f"{x / 1e6:,.1f}M"


# ------------------------------------------------------------ 1. 무결성
print("1. 무결성")
for t in ("suppliers", "products", "clients", "sales", "purchases", "expenses", "inventory", "change_log"):
    print(f"  {t}: {q(f'SELECT COUNT(*) c FROM {t}')[0]['c']:,}행")
r = q("SELECT MIN(date) a, MAX(date) b FROM sales")[0]
print(f"  매출 기간: {r['a']} ~ {r['b']}")
check("FK 위반 없음", not q("PRAGMA foreign_key_check"))
check("이탈 거래처는 이탈일 이후 주문 없음",
      not q("SELECT 1 FROM sales s JOIN clients c USING(client_id) WHERE c.churn_date IS NOT NULL AND s.date > c.churn_date"))
check("판매가가 사입가 이상 (역마진 없음)", not q("SELECT 1 FROM sales WHERE sell_price < cost_price"))
opening = q("""SELECT i.product_id, i.qty + IFNULL((SELECT SUM(qty) FROM sales WHERE product_id=i.product_id),0)
               - IFNULL((SELECT SUM(qty) FROM purchases WHERE product_id=i.product_id),0) AS open
               FROM inventory i""")
check("재고 역산(기초재고) 전 상품 양수", all(r["open"] > 0 for r in opening))
low = q("SELECT p.name, i.qty, i.reorder_level FROM inventory i JOIN products p USING(product_id) WHERE i.qty <= i.reorder_level")
print(f"  현재 재고 부족(<=재주문점) 상품: {len(low)}개 " + ", ".join(f"{r['name']}({r['qty']}/{r['reorder_level']})" for r in low))

# ------------------------------------------------------------ 2. 월별 요약
print("\n2. 월별 요약 (매출·매출총이익률·매입·운영비·순현금흐름)")
rows = q("""
WITH s AS (SELECT substr(date,1,7) m, SUM(qty*sell_price) rev, SUM(qty*cost_price) cogs, SUM(qty) qty FROM sales GROUP BY m),
     p AS (SELECT substr(date,1,7) m, SUM(qty*unit_cost) pur FROM purchases GROUP BY m),
     e AS (SELECT substr(date,1,7) m, SUM(amount) exp FROM expenses GROUP BY m)
SELECT s.m, s.rev, s.cogs, s.qty, IFNULL(p.pur,0) pur, IFNULL(e.exp,0) exp FROM s LEFT JOIN p USING(m) LEFT JOIN e USING(m) ORDER BY s.m""")
monthly = {r["m"]: r for r in rows}
print(f"  {'월':7} {'매출':>8} {'YoY':>7} {'총이익률':>7} {'매입':>8} {'운영비':>7} {'순현금':>8}")
for m, r in monthly.items():
    y, mm = m.split("-")
    prev = monthly.get(f"{int(y) - 1}-{mm}")
    yoy = f"{(r['rev'] / prev['rev'] - 1) * 100:+.1f}%" if prev else "-"
    net = r["rev"] - r["pur"] - r["exp"]
    print(f"  {m:7} {won(r['rev']):>8} {yoy:>7} {(1 - r['cogs'] / r['rev']) * 100:6.1f}% "
          f"{won(r['pur']):>8} {won(r['exp']):>7} {won(net):>8}" + ("  (부분월)" if m == "2026-09" else ""))

# ------------------------------------------------------------ 3. 3월 스토리
print("\n3. 2026-03 vs 2025-03 (상품별 가격/물량/구성 분해)")
prod = {}
for r in q("""SELECT substr(date,1,7) m, product_id, SUM(qty) q, SUM(qty*sell_price)*1.0/SUM(qty) ps, SUM(qty*cost_price)*1.0/SUM(qty) pc
              FROM sales WHERE substr(date,1,7) IN ('2025-03','2026-03') GROUP BY m, product_id"""):
    prod.setdefault(r["product_id"], {})[r["m"]] = r
price_eff = vol_eff = 0.0
for pid, v in prod.items():
    a, b = v.get("2025-03"), v.get("2026-03")
    if a and b:
        price_eff += b["q"] * (b["ps"] - a["ps"])
        vol_eff += (b["q"] - a["q"]) * a["ps"]
r25, r26 = monthly["2025-03"], monthly["2026-03"]
diff = r26["rev"] - r25["rev"]
print(f"  매출 {won(r25['rev'])} -> {won(r26['rev'])} ({diff / r25['rev'] * 100:+.1f}%), 물량 {r25['qty']:,} -> {r26['qty']:,} ({(r26['qty'] / r25['qty'] - 1) * 100:+.1f}%)")
print(f"  분해: 판매가 효과 {won(price_eff)} / 물량 효과 {won(vol_eff)} / 합계 {won(price_eff + vol_eff)} (차이는 신규·소멸 상품)")
gm25, gm26 = 1 - r25["cogs"] / r25["rev"], 1 - r26["cogs"] / r26["rev"]
print(f"  매출총이익률 {gm25 * 100:.1f}% -> {gm26 * 100:.1f}%")
avg = {}
for r in q("""SELECT substr(date,1,7) m, AVG(sell_price*1.0/(SELECT list_price FROM products WHERE product_id=sales.product_id)) x
              FROM sales WHERE product_id LIKE 'P%' AND substr(date,1,7) IN ('2025-03','2026-03') GROUP BY m"""):
    avg[r["m"]] = r["x"]
check("매출 증가는 판매가 효과가 주도 (물량 효과의 3배 이상)", price_eff > 3 * abs(vol_eff), f"{won(price_eff)} vs {won(vol_eff)}")
check("물량(전년 단가 기준)은 전년 매출의 ±3% 이내", abs(vol_eff / r25["rev"]) < 0.03, f"{vol_eff / r25['rev'] * 100:+.1f}%")
check("총이익률 8%p 이상 개선", gm26 - gm25 >= 0.08, f"{(gm26 - gm25) * 100:+.1f}%p")
c25 = q("SELECT SUM(qty*cost_price)*1.0/SUM(qty) c FROM sales WHERE product_id='P001' AND substr(date,1,7)='2025-03'")[0]["c"]
c26 = q("SELECT SUM(qty*cost_price)*1.0/SUM(qty) c FROM sales WHERE product_id='P001' AND substr(date,1,7)='2026-03'")[0]["c"]
check("사입가 하락 (P001 예시)", c26 < c25, f"{c25:,.0f} -> {c26:,.0f}원")
feb, mar = monthly["2026-02"], monthly["2026-03"]
print(f"  참고: 2026-02 -> 03 전월 대비 매출 {(mar['rev'] / feb['rev'] - 1) * 100:+.1f}%")

# ------------------------------------------------------------ 4. 7월 스토리
print("\n4. 2026-06 vs 2026-08 거래처별 매출 (7월 이후 하락의 원인)")
cl = q("""SELECT c.name, c.status,
       SUM(CASE WHEN substr(s.date,1,7)='2026-05' THEN s.qty*s.sell_price ELSE 0 END) may,
       SUM(CASE WHEN substr(s.date,1,7)='2026-06' THEN s.qty*s.sell_price ELSE 0 END) jun,
       SUM(CASE WHEN substr(s.date,1,7)='2026-07' THEN s.qty*s.sell_price ELSE 0 END) jul,
       SUM(CASE WHEN substr(s.date,1,7)='2026-08' THEN s.qty*s.sell_price ELSE 0 END) aug
       FROM clients c LEFT JOIN sales s USING(client_id) GROUP BY c.client_id ORDER BY jun DESC""")
print(f"  {'거래처':12} {'5월':>7} {'6월':>7} {'7월':>7} {'8월':>7}")
for r in cl:
    print(f"  {r['name']:12} {won(r['may']):>7} {won(r['jun']):>7} {won(r['jul']):>7} {won(r['aug']):>7}" + ("  <- 이탈" if r["status"] == "churned" and r["aug"] == 0 and r["jun"] else ""))
tot_jun = sum(r["jun"] for r in cl)
tot_aug = sum(r["aug"] for r in cl)
c01 = next(r for r in cl if r["name"] == "해운대상사")
others_jun = tot_jun - c01["jun"]
others_aug = tot_aug - c01["aug"]
drop = tot_jun - tot_aug
print(f"  전체 {won(tot_jun)} -> {won(tot_aug)} ({(tot_aug / tot_jun - 1) * 100:+.1f}%), 해운대상사 기여 하락 {won(c01['jun'] - c01['aug'])} ({(c01['jun'] - c01['aug']) / drop * 100:.0f}%)")
print(f"  해운대상사 제외 6월 -> 8월: {won(others_jun)} -> {won(others_aug)} ({(others_aug / others_jun - 1) * 100:+.1f}%)")
check("6월 대비 8월 전체 하락", tot_aug < tot_jun * 0.9)
check("하락의 80% 이상이 해운대상사", (c01["jun"] - c01["aug"]) / drop >= 0.8)
check("나머지 거래처는 ±10% 이내 유지", abs(others_aug / others_jun - 1) < 0.10)
check("해운대상사 4~6월 점진적 부진", c01["jun"] < c01["may"])
share = q("""SELECT SUM(CASE WHEN client_id='C01' THEN qty*sell_price END)*1.0/SUM(qty*sell_price) x
             FROM sales WHERE date BETWEEN '2025-07-01' AND '2026-03-31'""")[0]["x"]
print(f"  참고: 해운대상사 매출 비중(2025-07~2026-03) {share * 100:.1f}%")

# ------------------------------------------------------------ 5. 노이즈
print("\n5. 노이즈 확인")
print("  연도별 매출:", ", ".join(f"{y}: {won(q(f'SELECT SUM(qty*sell_price) s FROM sales WHERE substr(date,1,4)=?', y)[0]['s'])}" for y in ("2024", "2025", "2026")))
print("  변경이력 역할별:", ", ".join(f"{r['role']} {r['c']}" for r in q("SELECT role, COUNT(*) c FROM change_log GROUP BY role")))

print("\n결과:", "모두 통과" if not failures else f"실패 {len(failures)}건 -> {failures}")
sys.exit(1 if failures else 0)
