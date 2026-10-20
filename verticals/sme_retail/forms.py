"""입력폼(쓰기) 로직 — 매출·재고·거래처·상품 단가의 등록/수정.

원칙
- 저장 + 재고 반영 + 변경이력 기록을 한 트랜잭션으로 처리한다 (중간 실패 시 전부 롤백).
- 서버가 최종 검증한다: 빈 값·음수·소수·날짜 형식·미래 날짜 + 비즈니스 규칙. 오류는 필드별로 모아서 한 번에 반환.
- 쓰기 권한은 permissions.py 의 매트릭스를 따른다 (필드 단위: 판매가=영업실적, 사입가·재고=상품구매).
- 채팅은 조회 전용이므로 이 모듈은 채팅 tool 에서 절대 import 하지 않는다.
"""
from datetime import date

from core.config import TODAY
from core.db import connect
from core.permissions import can_read, can_write, require_read, require_write
from core.validation import (MAX_QTY, NotFound, ValidationFailed, _begin, _blank, _date, _err, _id, _int, _log,  # noqa: F401
                             _only, _text)


# ------------------------------------------------------------------- 매출
def _check_client_date(errors, client, d, field="date"):
    if d < date.fromisoformat(client["joined_date"]):
        _err(errors, field, f"판매일: 거래처 등록일({client['joined_date']}) 이전 날짜입니다")
    elif client["churn_date"] and d > date.fromisoformat(client["churn_date"]):
        _err(errors, field, f"판매일: 이탈한 거래처입니다 (이탈일 {client['churn_date']} 이후 주문 불가)")


def create_sale(role, data):
    require_write(role, "sales")
    errors = []
    d = _date(data, "date", "판매일", errors)
    client_id = _id(data, "client_id", "거래처", errors)
    product_id = _id(data, "product_id", "상품", errors)
    qty = _int(data, "qty", "수량", errors, positive=True, maximum=MAX_QTY)
    price = _int(data, "sell_price", "판매가", errors)
    if errors:
        raise ValidationFailed(errors)

    with connect(readonly=False) as con:
        _begin(con)
        client = con.execute("SELECT * FROM clients WHERE client_id=?", (client_id,)).fetchone()
        product = con.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
        if not client:
            _err(errors, "client_id", "거래처: 존재하지 않는 거래처입니다")
        if not product:
            _err(errors, "product_id", "상품: 존재하지 않는 상품입니다")
        if errors:
            raise ValidationFailed(errors)
        _check_client_date(errors, client, d)
        if price < product["cost_price"]:
            _err(errors, "sell_price",
                 f"판매가: 사입가({product['cost_price']:,}원)보다 낮게 입력할 수 없습니다")
        inv = None
        if product["type"] == "goods":
            inv = con.execute("SELECT qty FROM inventory WHERE product_id=?", (product_id,)).fetchone()
            if inv["qty"] < qty:
                _err(errors, "qty", f"수량: 재고가 부족합니다 (현재 {inv['qty']:,}{product['unit']})")
        if errors:
            raise ValidationFailed(errors)

        cur = con.execute(
            "INSERT INTO sales (date, client_id, product_id, qty, sell_price, cost_price) VALUES (?,?,?,?,?,?)",
            (d.isoformat(), client_id, product_id, qty, price, product["cost_price"]))
        sale_id = cur.lastrowid
        _log(con, role, "sales", sale_id, "신규 등록", None,
             f"{d.isoformat()} {client['name']} {product['name']} x{qty} @{price:,}원")
        stock = None
        if inv is not None:
            stock = inv["qty"] - qty
            con.execute("UPDATE inventory SET qty=?, updated_at=? WHERE product_id=?",
                        (stock, TODAY.isoformat(), product_id))
            _log(con, role, "inventory", product_id, "qty", inv["qty"], stock)
    return {"sale_id": sale_id, "date": d.isoformat(), "client_id": client_id, "product_id": product_id,
            "qty": qty, "sell_price": price, "cost_price": product["cost_price"], "stock_after": stock}


def update_sale(role, sale_id, data):
    require_write(role, "sales")
    errors = []
    _only(data, {"date", "qty", "sell_price"}, errors)
    new = {
        "date": _date(data, "date", "판매일", errors, required=False),
        "qty": _int(data, "qty", "수량", errors, required=False, positive=True, maximum=MAX_QTY),
        "sell_price": _int(data, "sell_price", "판매가", errors, required=False),
    }
    new = {k: v for k, v in new.items() if v is not None}
    if not errors and not new:
        _err(errors, "_form", "수정할 항목을 입력해 주세요")
    if errors:
        raise ValidationFailed(errors)

    with connect(readonly=False) as con:
        _begin(con)
        row = con.execute("SELECT * FROM sales WHERE sale_id=?", (sale_id,)).fetchone()
        if not row:
            raise NotFound(f"매출 #{sale_id}을(를) 찾을 수 없습니다")
        old = {"date": row["date"], "qty": row["qty"], "sell_price": row["sell_price"]}
        changed = {}
        for k, v in new.items():
            nv = v.isoformat() if k == "date" else v
            if nv != old[k]:
                changed[k] = nv
        if not changed:
            raise ValidationFailed([{"field": "_form", "message": "변경된 내용이 없습니다"}])

        client = con.execute("SELECT * FROM clients WHERE client_id=?", (row["client_id"],)).fetchone()
        product = con.execute("SELECT * FROM products WHERE product_id=?", (row["product_id"],)).fetchone()
        if "date" in changed:
            _check_client_date(errors, client, date.fromisoformat(changed["date"]))
        if "sell_price" in changed and changed["sell_price"] < row["cost_price"]:
            _err(errors, "sell_price", f"판매가: 판매 당시 사입가({row['cost_price']:,}원)보다 낮게 입력할 수 없습니다")
        inv, new_stock = None, None
        if "qty" in changed and product["type"] == "goods":
            inv = con.execute("SELECT qty FROM inventory WHERE product_id=?", (row["product_id"],)).fetchone()
            new_stock = inv["qty"] - (changed["qty"] - row["qty"])
            if new_stock < 0:
                _err(errors, "qty", f"수량: 재고가 부족합니다 (현재 {inv['qty']:,}{product['unit']}, "
                                    f"추가로 {changed['qty'] - row['qty']:,} 필요)")
        if errors:
            raise ValidationFailed(errors)

        sets = ", ".join(f"{k}=?" for k in changed)
        con.execute(f"UPDATE sales SET {sets} WHERE sale_id=?", (*changed.values(), sale_id))
        for k, v in changed.items():
            _log(con, role, "sales", sale_id, k, old[k], v)
        if inv is not None:
            con.execute("UPDATE inventory SET qty=?, updated_at=? WHERE product_id=?",
                        (new_stock, TODAY.isoformat(), row["product_id"]))
            _log(con, role, "inventory", row["product_id"], "qty", inv["qty"], new_stock)
    return {"sale_id": sale_id, "changed": {k: {"old": old[k], "new": v} for k, v in changed.items()},
            "stock_after": new_stock}


def list_sales(role, limit=30, client_id=None, month=None):
    require_read(role, "sales")
    sql = ("SELECT s.sale_id, s.date, c.name client, s.client_id, p.name product, s.product_id, s.qty, "
           "s.sell_price FROM sales s JOIN clients c USING(client_id) JOIN products p USING(product_id) WHERE 1=1 ")
    args = []
    if client_id:
        sql += "AND s.client_id=? "
        args.append(client_id)
    if month:
        sql += "AND substr(s.date,1,7)=? "
        args.append(month)
    with connect() as con:
        rows = con.execute(sql + "ORDER BY s.date DESC, s.sale_id DESC LIMIT ?",
                           (*args, max(1, min(int(limit), 200)))).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- 재고
def update_inventory(role, product_id, data):
    require_write(role, "purchase")
    errors = []
    _only(data, {"qty", "reorder_level"}, errors)
    new = {"qty": _int(data, "qty", "재고 수량", errors, required=False, maximum=MAX_QTY),
           "reorder_level": _int(data, "reorder_level", "재주문 기준", errors, required=False, maximum=MAX_QTY)}
    new = {k: v for k, v in new.items() if v is not None}
    if not errors and not new:
        _err(errors, "_form", "수정할 항목을 입력해 주세요")
    if errors:
        raise ValidationFailed(errors)

    with connect(readonly=False) as con:
        _begin(con)
        row = con.execute("SELECT * FROM inventory WHERE product_id=?", (product_id,)).fetchone()
        if not row:
            raise NotFound("재고 관리 대상 상품이 아닙니다 (서비스 상품이거나 존재하지 않는 상품)")
        changed = {k: v for k, v in new.items() if v != row[k]}
        if not changed:
            raise ValidationFailed([{"field": "_form", "message": "변경된 내용이 없습니다"}])
        sets = ", ".join(f"{k}=?" for k in changed)
        con.execute(f"UPDATE inventory SET {sets}, updated_at=? WHERE product_id=?",
                    (*changed.values(), TODAY.isoformat(), product_id))
        for k, v in changed.items():
            _log(con, role, "inventory", product_id, k, row[k], v)
    return {"product_id": product_id, "changed": {k: {"old": row[k], "new": v} for k, v in changed.items()}}


# ------------------------------------------------------------------- 상품 단가
def update_product(role, product_id, data):
    errors = []
    _only(data, {"list_price", "cost_price"}, errors)
    # 필드 단위 권한: 판매가=영업실적, 사입가=상품구매
    if "list_price" in data and not _blank(data["list_price"]):
        require_write(role, "sales")
    if "cost_price" in data and not _blank(data["cost_price"]):
        require_write(role, "purchase")
    new = {"list_price": _int(data, "list_price", "판매가", errors, required=False),
           "cost_price": _int(data, "cost_price", "사입가", errors, required=False)}
    new = {k: v for k, v in new.items() if v is not None}
    if not errors and not new:
        _err(errors, "_form", "수정할 항목을 입력해 주세요")
    if errors:
        raise ValidationFailed(errors)

    with connect(readonly=False) as con:
        _begin(con)
        row = con.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
        if not row:
            raise NotFound("존재하지 않는 상품입니다")
        changed = {k: v for k, v in new.items() if v != row[k]}
        if not changed:
            raise ValidationFailed([{"field": "_form", "message": "변경된 내용이 없습니다"}])
        final_list = changed.get("list_price", row["list_price"])
        final_cost = changed.get("cost_price", row["cost_price"])
        if final_list < final_cost:
            _err(errors, "list_price" if "list_price" in changed else "cost_price",
                 f"판매가({final_list:,}원)가 사입가({final_cost:,}원)보다 낮을 수 없습니다")
            raise ValidationFailed(errors)
        sets = ", ".join(f"{k}=?" for k in changed)
        con.execute(f"UPDATE products SET {sets} WHERE product_id=?", (*changed.values(), product_id))
        for k, v in changed.items():
            _log(con, role, "products", product_id, k, row[k], v)
    return {"product_id": product_id, "changed": {k: {"old": row[k], "new": v} for k, v in changed.items()}}


# ------------------------------------------------------------------- 거래처
def create_client(role, data):
    require_write(role, "sales")
    errors = []
    name = _text(data, "name", "거래처명", errors)
    joined = _date(data, "joined_date", "등록일", errors)
    if errors:
        raise ValidationFailed(errors)
    with connect(readonly=False) as con:
        _begin(con)
        if con.execute("SELECT 1 FROM clients WHERE lower(name)=lower(?)", (name,)).fetchone():
            raise ValidationFailed([{"field": "name", "message": f"거래처명: '{name}'은(는) 이미 등록된 거래처입니다"}])
        last = con.execute("SELECT MAX(CAST(substr(client_id,2) AS INTEGER)) n FROM clients").fetchone()["n"] or 0
        client_id = f"C{last + 1:02d}"
        con.execute("INSERT INTO clients (client_id, name, status, joined_date, churn_date) VALUES (?,?,?,?,NULL)",
                    (client_id, name, "active", joined.isoformat()))
        _log(con, role, "clients", client_id, "신규 등록", None, f"{name} (등록일 {joined.isoformat()})")
    return {"client_id": client_id, "name": name, "status": "active", "joined_date": joined.isoformat()}


def update_client(role, client_id, data):
    require_write(role, "sales")
    errors = []
    _only(data, {"name", "status", "churn_date"}, errors)
    name = _text(data, "name", "거래처명", errors, required=False)
    churn = _date(data, "churn_date", "이탈일", errors, required=False)
    status = data.get("status")
    if not _blank(status) and status not in ("active", "churned"):
        _err(errors, "status", "상태: 'active'(거래중) 또는 'churned'(이탈)만 가능합니다")
    if not errors and name is None and churn is None and _blank(status):
        _err(errors, "_form", "수정할 항목을 입력해 주세요")
    if errors:
        raise ValidationFailed(errors)

    with connect(readonly=False) as con:
        _begin(con)
        row = con.execute("SELECT * FROM clients WHERE client_id=?", (client_id,)).fetchone()
        if not row:
            raise NotFound("존재하지 않는 거래처입니다")
        new_status = status if not _blank(status) else row["status"]
        new_churn = None
        if new_status == "churned":
            new_churn = churn.isoformat() if churn else row["churn_date"]
            if not new_churn:
                _err(errors, "churn_date", "이탈일: 이탈 상태로 바꾸려면 이탈일을 입력해야 합니다")
            else:
                if new_churn < row["joined_date"]:
                    _err(errors, "churn_date", f"이탈일: 등록일({row['joined_date']}) 이전일 수 없습니다")
                last_sale = con.execute("SELECT MAX(date) d FROM sales WHERE client_id=?", (client_id,)).fetchone()["d"]
                if last_sale and new_churn < last_sale:
                    _err(errors, "churn_date", f"이탈일: 마지막 주문일({last_sale}) 이후여야 합니다")
        elif churn is not None:
            _err(errors, "churn_date", "이탈일: 거래중 상태에서는 이탈일을 입력할 수 없습니다")
        if name and name.lower() != row["name"].lower() and con.execute(
                "SELECT 1 FROM clients WHERE lower(name)=lower(?)", (name,)).fetchone():
            _err(errors, "name", f"거래처명: '{name}'은(는) 이미 등록된 거래처입니다")
        if errors:
            raise ValidationFailed(errors)

        new = {"name": name if name is not None else row["name"], "status": new_status, "churn_date": new_churn}
        changed = {k: v for k, v in new.items() if v != row[k]}
        if not changed:
            raise ValidationFailed([{"field": "_form", "message": "변경된 내용이 없습니다"}])
        sets = ", ".join(f"{k}=?" for k in changed)
        con.execute(f"UPDATE clients SET {sets} WHERE client_id=?", (*changed.values(), client_id))
        for k, v in changed.items():
            _log(con, role, "clients", client_id, k, row[k], v)
    return {"client_id": client_id, "changed": {k: {"old": row[k], "new": v} for k, v in changed.items()}}


# ------------------------------------------------------------- 폼용 조회
def lookups(role):
    """폼 드롭다운과 버튼 활성화용: 거래처·상품 목록과 이 역할의 폼별 쓰기 가능 여부."""
    require_read(role, "sales")
    see_purchase = can_read(role, "purchase")
    with connect() as con:
        clients = [dict(r) for r in con.execute(
            "SELECT client_id, name, status, joined_date, churn_date FROM clients ORDER BY client_id")]
        products = []
        for r in con.execute("SELECT p.*, i.qty stock, i.reorder_level FROM products p "
                             "LEFT JOIN inventory i USING(product_id) ORDER BY p.product_id"):
            item = {"product_id": r["product_id"], "name": r["name"], "type": r["type"], "unit": r["unit"],
                    "list_price": r["list_price"]}
            if see_purchase:
                item.update(cost_price=r["cost_price"], stock=r["stock"], reorder_level=r["reorder_level"])
            products.append(item)
    return {"clients": clients, "products": products, "today": TODAY.isoformat(),
            "can": {"sale": can_write(role, "sales"), "client": can_write(role, "sales"),
                    "inventory": can_write(role, "purchase"),
                    "product_list_price": can_write(role, "sales"),
                    "product_cost_price": can_write(role, "purchase")}}
