"""입력폼 API 테스트 (LLM 불필요). 매 테스트마다 DB 임시 복사본을 사용하고 원본은 해시로 무결성을 확인한다.

사용: python -m pytest tests/sme_retail/test_forms.py -q
"""
import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import db as app_db  # noqa: E402
from verticals.sme_retail import forms, tools  # noqa: E402
from core.main import app  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "data" / "company.db"
GOOD = {"role": "영업부", "date": "2026-09-26", "client_id": "C02", "product_id": "P001", "qty": 2, "sell_price": 36000}


def _hash():
    return hashlib.sha256(SRC.read_bytes()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def original_untouched():
    before = _hash()
    yield
    assert _hash() == before, "원본 data/company.db 가 테스트 중 변경됨"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    shutil.copy(SRC, path)
    monkeypatch.setattr(app_db, "DB_PATH", path)
    client = TestClient(app)

    class E:
        c = client
        p = path

        @staticmethod
        def q(sql, *a):
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            try:
                return con.execute(sql, a).fetchall()
            finally:
                con.close()

        @classmethod
        def n(cls, table):
            return cls.q(f"SELECT COUNT(*) c FROM {table}")[0]["c"]

        @classmethod
        def stock(cls, pid):
            return cls.q("SELECT qty FROM inventory WHERE product_id=?", pid)[0]["qty"]

    return E


def fields(resp):
    return {e["field"] for e in resp.json()["errors"]}


# ------------------------------------------------------------------ 권한
@pytest.mark.parametrize("role,status", [("대표", 201), ("영업부", 201), ("구매부", 403), ("경리", 403)])
def test_sale_create_permission(env, role, status):
    r = env.c.post("/api/sales", json={**GOOD, "role": role})
    assert r.status_code == status, r.text


@pytest.mark.parametrize("role,status", [("대표", 200), ("구매부", 200), ("영업부", 403), ("경리", 403)])
def test_inventory_permission(env, role, status):
    r = env.c.patch("/api/inventory/P001", json={"role": role, "qty": 500})
    assert r.status_code == status, r.text


@pytest.mark.parametrize("role,field,status", [
    ("영업부", "list_price", 200), ("영업부", "cost_price", 403),
    ("구매부", "cost_price", 200), ("구매부", "list_price", 403),
    ("경리", "list_price", 403), ("경리", "cost_price", 403),
    ("대표", "list_price", 200), ("대표", "cost_price", 200)])
def test_product_field_level_permission(env, role, field, status):
    cur = env.q("SELECT list_price, cost_price FROM products WHERE product_id='P001'")[0]
    new = (cur[field] - 100) if field == "cost_price" else (cur[field] + 100)
    r = env.c.patch("/api/products/P001", json={"role": role, field: new})
    assert r.status_code == status, r.text


@pytest.mark.parametrize("role,status", [("영업부", 201), ("대표", 201), ("구매부", 403), ("경리", 403)])
def test_client_create_permission(env, role, status):
    r = env.c.post("/api/clients", json={"role": role, "name": "테스트상사", "joined_date": "2026-09-01"})
    assert r.status_code == status, r.text


def test_denied_write_leaves_no_trace(env):
    logs, sales = env.n("change_log"), env.n("sales")
    for role in ("구매부", "경리"):
        assert env.c.post("/api/sales", json={**GOOD, "role": role}).status_code == 403
    assert (env.n("change_log"), env.n("sales")) == (logs, sales)


def test_unknown_or_missing_role_rejected(env):
    assert env.c.post("/api/sales", json={**GOOD, "role": "해커"}).status_code == 400
    assert env.c.post("/api/sales", json={k: v for k, v in GOOD.items() if k != "role"}).status_code == 400


# ------------------------------------------------------------ 검증: 매출
@pytest.mark.parametrize("patch,field", [
    ({"qty": -1}, "qty"), ({"qty": 0}, "qty"), ({"qty": 1.5}, "qty"), ({"qty": "abc"}, "qty"),
    ({"qty": ""}, "qty"), ({"qty": None}, "qty"), ({"qty": True}, "qty"), ({"qty": 10 ** 9}, "qty"),
    ({"sell_price": -1}, "sell_price"), ({"sell_price": ""}, "sell_price"), ({"sell_price": 1.5}, "sell_price"),
    ({"sell_price": 10}, "sell_price"),                       # 사입가보다 낮음
    ({"date": "2026-13-40"}, "date"), ({"date": "26-09-01"}, "date"), ({"date": ""}, "date"),
    ({"date": "2026-09-28"}, "date"),                         # 미래
    ({"date": "2026-02-30"}, "date"),                         # 존재하지 않는 날짜
    ({"client_id": ""}, "client_id"), ({"client_id": "C99"}, "client_id"),
    ({"product_id": ""}, "product_id"), ({"product_id": "P999"}, "product_id"),
    ({"qty": 10 ** 6}, "qty"),                                # 재고 부족
    ({"client_id": "C01", "date": "2026-07-10"}, "date"),     # 이탈일 이후 주문
    ({"client_id": "C13", "date": "2025-01-01"}, "date"),     # 등록일 이전 주문
])
def test_sale_validation_rejects(env, patch, field):
    before = (env.n("sales"), env.n("change_log"), env.stock("P001"))
    r = env.c.post("/api/sales", json={**GOOD, **patch})
    assert r.status_code == 422, r.text
    assert field in fields(r), r.json()
    assert (env.n("sales"), env.n("change_log"), env.stock("P001")) == before   # 부분 저장 없음


def test_sale_missing_fields_rejected(env):
    for f in ("date", "client_id", "product_id", "qty", "sell_price"):
        r = env.c.post("/api/sales", json={k: v for k, v in GOOD.items() if k != f})
        assert r.status_code == 422 and f in fields(r), f


def test_all_errors_reported_together(env):
    r = env.c.post("/api/sales", json={"role": "대표", "date": "bad", "client_id": "", "product_id": "",
                                       "qty": -5, "sell_price": ""})
    assert r.status_code == 422
    assert fields(r) >= {"date", "client_id", "product_id", "qty", "sell_price"}
    assert all("message" in e and e["message"] for e in r.json()["errors"])


def test_sql_injection_is_inert(env):
    r = env.c.post("/api/sales", json={**GOOD, "product_id": "P001'; DROP TABLE sales;--"})
    assert r.status_code == 422
    r = env.c.patch("/api/clients/C02", json={"role": "영업부", "name": "x'); DELETE FROM sales;--"})
    assert r.status_code in (200, 422)
    assert env.n("sales") > 20000


def test_non_object_body_rejected(env):
    assert env.c.post("/api/sales", json=[1, 2, 3]).status_code == 422


# ---------------------------------------------------------- 성공: 매출 + 재고 + 이력
def test_sale_create_success_updates_inventory_and_logs(env):
    stock, logs = env.stock("P001"), env.n("change_log")
    r = env.c.post("/api/sales", json=GOOD)
    assert r.status_code == 201, r.text
    body = r.json()
    row = env.q("SELECT * FROM sales WHERE sale_id=?", body["sale_id"])[0]
    cost = env.q("SELECT cost_price FROM products WHERE product_id='P001'")[0]["cost_price"]
    assert (row["client_id"], row["product_id"], row["qty"], row["sell_price"]) == ("C02", "P001", 2, 36000)
    assert row["cost_price"] == cost                       # 사입가는 서버가 채운다 (클라이언트 값 신뢰 안 함)
    assert env.stock("P001") == stock - 2 == body["stock_after"]
    new_logs = env.q("SELECT * FROM change_log ORDER BY log_id DESC LIMIT ?", env.n("change_log") - logs)
    assert len(new_logs) == 2
    assert {(l["table_name"], l["field"]) for l in new_logs} == {("sales", "신규 등록"), ("inventory", "qty")}
    assert all(l["role"] == "영업부" for l in new_logs)
    inv = next(l for l in new_logs if l["table_name"] == "inventory")
    assert (inv["old_value"], inv["new_value"], inv["record_id"]) == (str(stock), str(stock - 2), "P001")


def test_client_supplied_cost_price_ignored(env):
    r = env.c.post("/api/sales", json={**GOOD, "cost_price": 1})
    assert r.status_code == 201
    row = env.q("SELECT cost_price FROM sales WHERE sale_id=?", r.json()["sale_id"])[0]
    assert row["cost_price"] != 1


def test_service_sale_does_not_touch_inventory(env):
    before_inv, logs = env.q("SELECT * FROM inventory"), env.n("change_log")
    r = env.c.post("/api/sales", json={**GOOD, "product_id": "S001", "sell_price": 15000, "qty": 1})
    assert r.status_code == 201, r.text
    assert [tuple(x) for x in env.q("SELECT * FROM inventory")] == [tuple(x) for x in before_inv]
    assert env.n("change_log") == logs + 1


def test_sale_update_qty_adjusts_inventory(env):
    sid = env.c.post("/api/sales", json=GOOD).json()["sale_id"]
    stock = env.stock("P001")
    r = env.c.patch(f"/api/sales/{sid}", json={"role": "대표", "qty": 5})
    assert r.status_code == 200, r.text
    assert env.stock("P001") == stock - 3
    last = env.q("SELECT * FROM change_log WHERE table_name='sales' AND record_id=? AND field='qty'", str(sid))[0]
    assert (last["old_value"], last["new_value"], last["role"]) == ("2", "5", "대표")
    r = env.c.patch(f"/api/sales/{sid}", json={"role": "대표", "qty": 1})           # 줄이면 재고 복구
    assert r.status_code == 200 and env.stock("P001") == stock - 3 + 4


def test_sale_update_beyond_stock_rejected_and_unchanged(env):
    sid = env.c.post("/api/sales", json=GOOD).json()["sale_id"]
    stock, logs = env.stock("P001"), env.n("change_log")
    r = env.c.patch(f"/api/sales/{sid}", json={"role": "대표", "qty": 10 ** 6})
    assert r.status_code == 422 and "qty" in fields(r)
    assert (env.stock("P001"), env.n("change_log")) == (stock, logs)
    assert env.q("SELECT qty FROM sales WHERE sale_id=?", sid)[0]["qty"] == 2


def test_sale_update_rules(env):
    sid = env.c.post("/api/sales", json=GOOD).json()["sale_id"]
    assert env.c.patch(f"/api/sales/{sid}", json={"role": "대표", "qty": 2}).status_code == 422       # 변경 없음
    assert env.c.patch(f"/api/sales/{sid}", json={"role": "대표"}).status_code == 422                # 항목 없음
    assert env.c.patch(f"/api/sales/{sid}", json={"role": "대표", "client_id": "C03"}).status_code == 422
    assert env.c.patch(f"/api/sales/{sid}", json={"role": "대표", "sell_price": 10}).status_code == 422
    assert env.c.patch("/api/sales/99999999", json={"role": "대표", "qty": 2}).status_code == 404
    assert env.c.patch(f"/api/sales/{sid}", json={"role": "구매부", "qty": 3}).status_code == 403
    old = env.q("SELECT sale_id FROM sales WHERE client_id='C01' ORDER BY date DESC LIMIT 1")[0]["sale_id"]
    r = env.c.patch(f"/api/sales/{old}", json={"role": "대표", "date": "2026-09-01"})           # 이탈 후 날짜로 수정
    assert r.status_code == 422 and "date" in fields(r)


def test_sale_price_edit_logged(env):
    sid = env.c.post("/api/sales", json=GOOD).json()["sale_id"]
    assert env.c.patch(f"/api/sales/{sid}", json={"role": "영업부", "sell_price": "37,000"}).status_code == 200
    row = env.q("SELECT sell_price FROM sales WHERE sale_id=?", sid)[0]
    assert row["sell_price"] == 37000


# ---------------------------------------------------------------- 재고
def test_inventory_update_logs_each_field(env):
    old = env.q("SELECT * FROM inventory WHERE product_id='P003'")[0]
    logs = env.n("change_log")
    r = env.c.patch("/api/inventory/P003", json={"role": "구매부", "qty": old["qty"] + 50, "reorder_level": 99})
    assert r.status_code == 200, r.text
    assert env.n("change_log") == logs + 2
    new = env.q("SELECT * FROM inventory WHERE product_id='P003'")[0]
    assert (new["qty"], new["reorder_level"], new["updated_at"]) == (old["qty"] + 50, 99, "2026-09-27")


@pytest.mark.parametrize("payload", [{"qty": -1}, {"qty": "x"}, {"qty": 1.5}, {"qty": ""}, {}, {"foo": 1}])
def test_inventory_validation(env, payload):
    r = env.c.patch("/api/inventory/P003", json={"role": "구매부", **payload})
    assert r.status_code == 422, r.text


def test_inventory_service_or_unknown_product_404(env):
    assert env.c.patch("/api/inventory/S001", json={"role": "구매부", "qty": 5}).status_code == 404
    assert env.c.patch("/api/inventory/P999", json={"role": "구매부", "qty": 5}).status_code == 404


# ------------------------------------------------------------- 상품 단가
def test_product_price_rules(env):
    cur = env.q("SELECT * FROM products WHERE product_id='P001'")[0]
    r = env.c.patch("/api/products/P001", json={"role": "대표", "list_price": cur["cost_price"] - 1})
    assert r.status_code == 422                                    # 판매가 < 사입가
    r = env.c.patch("/api/products/P001", json={"role": "구매부", "cost_price": cur["list_price"] + 1})
    assert r.status_code == 422
    r = env.c.patch("/api/products/P001", json={"role": "구매부", "cost_price": cur["cost_price"] - 500})
    assert r.status_code == 200
    log = env.q("SELECT * FROM change_log WHERE table_name='products' ORDER BY log_id DESC LIMIT 1")[0]
    assert (log["role"], log["field"], log["old_value"], log["new_value"]) == (
        "구매부", "cost_price", str(cur["cost_price"]), str(cur["cost_price"] - 500))


# ---------------------------------------------------------------- 거래처
def test_client_create_and_duplicate(env):
    r = env.c.post("/api/clients", json={"role": "영업부", "name": "  다대포마트 ", "joined_date": "2026-09-20"})
    assert r.status_code == 201, r.text
    assert r.json()["client_id"] == "C14" and r.json()["name"] == "다대포마트"
    assert env.q("SELECT status FROM clients WHERE client_id='C14'")[0]["status"] == "active"
    assert env.q("SELECT * FROM change_log WHERE table_name='clients' AND record_id='C14'")[0]["field"] == "신규 등록"
    dup = env.c.post("/api/clients", json={"role": "영업부", "name": "다대포마트", "joined_date": "2026-09-21"})
    assert dup.status_code == 422 and "name" in fields(dup)
    dup2 = env.c.post("/api/clients", json={"role": "영업부", "name": "센텀유통", "joined_date": "2026-09-21"})
    assert dup2.status_code == 422


@pytest.mark.parametrize("payload,field", [
    ({"name": "", "joined_date": "2026-09-01"}, "name"), ({"name": "  ", "joined_date": "2026-09-01"}, "name"),
    ({"name": "가" * 51, "joined_date": "2026-09-01"}, "name"),
    ({"name": "가나다", "joined_date": ""}, "joined_date"), ({"name": "가나다", "joined_date": "2026-09-31"}, "joined_date"),
    ({"name": "가나다", "joined_date": "2027-01-01"}, "joined_date")])
def test_client_create_validation(env, payload, field):
    n = env.n("clients")
    r = env.c.post("/api/clients", json={"role": "영업부", **payload})
    assert r.status_code == 422 and field in fields(r), r.text
    assert env.n("clients") == n


def test_client_churn_lifecycle(env):
    api = lambda **p: env.c.patch("/api/clients/C05", json={"role": "영업부", **p})  # noqa: E731
    assert api(status="churned").status_code == 422                                   # 이탈일 없음
    assert api(status="churned", churn_date="2026-01-01").status_code == 422          # 마지막 주문일 이전
    assert api(status="active", churn_date="2026-09-01").status_code == 422           # 거래중인데 이탈일
    assert api(status="폐업").status_code == 422
    r = api(status="churned", churn_date="2026-09-26")
    assert r.status_code == 200, r.text
    row = env.q("SELECT status, churn_date FROM clients WHERE client_id='C05'")[0]
    assert (row["status"], row["churn_date"]) == ("churned", "2026-09-26")
    fields_logged = {l["field"] for l in env.q("SELECT field FROM change_log WHERE record_id='C05' AND table_name='clients'")}
    assert {"status", "churn_date"} <= fields_logged
    # 이탈 후 주문 불가
    r = env.c.post("/api/sales", json={**GOOD, "client_id": "C05", "date": "2026-09-27"})
    assert r.status_code == 422 and "date" in fields(r)
    # 복구하면 이탈일이 지워진다
    assert api(status="active").status_code == 200
    row = env.q("SELECT status, churn_date FROM clients WHERE client_id='C05'")[0]
    assert (row["status"], row["churn_date"]) == ("active", None)


def test_client_update_no_change_and_unknown(env):
    assert env.c.patch("/api/clients/C02", json={"role": "영업부", "name": "센텀유통"}).status_code == 422
    assert env.c.patch("/api/clients/C99", json={"role": "영업부", "name": "없는곳"}).status_code == 404
    assert env.c.patch("/api/clients/C02", json={"role": "영업부", "name": "광안리마트"}).status_code == 422


# --------------------------------------------------------------- 원자성
def test_failure_midway_rolls_back_everything(env, monkeypatch):
    stock, sales, logs = env.stock("P001"), env.n("sales"), env.n("change_log")
    calls = {"n": 0}
    real = forms._log

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:                       # 매출 저장·재고 차감·첫 이력까지 끝난 뒤 실패
            raise RuntimeError("boom")
        return real(*a, **k)

    monkeypatch.setattr(forms, "_log", flaky)
    with pytest.raises(RuntimeError):
        forms.create_sale("영업부", dict(GOOD))
    assert (env.stock("P001"), env.n("sales"), env.n("change_log")) == (stock, sales, logs)


# ------------------------------------------------- 다른 계층과의 정합성
def test_new_sale_visible_to_tools_and_dashboard(env):
    before = {m["month"]: m["revenue"] for m in tools.monthly_series("대표", "2026-09", "2026-09")}["2026-09"]
    assert env.c.post("/api/sales", json=GOOD).status_code == 201
    after = {m["month"]: m["revenue"] for m in tools.monthly_series("대표", "2026-09", "2026-09")}["2026-09"]
    assert after == before + 2 * 36000
    dash = env.c.get("/api/dashboard", params={"role": "대표"}).json()
    assert dash["change_log"][0]["table_name"] in ("sales", "inventory")


def test_change_log_readable_by_everyone_after_write(env):
    env.c.post("/api/sales", json=GOOD)
    for role in ("대표", "구매부", "영업부", "경리"):
        assert env.c.get("/api/dashboard", params={"role": role}).json()["change_log"]


def test_lookups_respect_permissions(env):
    sales = env.c.get("/api/lookups", params={"role": "영업부"}).json()
    assert sales["can"] == {"sale": True, "client": True, "inventory": False,
                            "product_list_price": True, "product_cost_price": False}
    buyer = env.c.get("/api/lookups", params={"role": "구매부"}).json()
    assert buyer["can"]["inventory"] and not buyer["can"]["sale"]
    acct = env.c.get("/api/lookups", params={"role": "경리"}).json()
    assert not any(acct["can"].values())
    assert all("cost_price" in p for p in sales["products"])          # 영업부는 원가 열람 가능 (확정)


def test_sales_list_endpoint(env):
    rows = env.c.get("/api/sales", params={"role": "경리", "limit": 5}).json()["sales"]
    assert len(rows) == 5 and rows[0]["date"] >= rows[-1]["date"]


# ---------------------------------------------------- 채팅은 조회 전용
def test_chat_cannot_write():
    import inspect
    from core import chat
    for mod in (tools, chat):
        src = inspect.getsource(mod)
        assert "import forms" not in src and "from .forms" not in src, f"{mod.__name__} 이 forms 를 import"
    names = {s["function"]["name"] for s in tools.SCHEMAS}
    assert not any(w in n for n in names for w in ("create", "update", "delete", "insert", "set_"))
    assert "error" in tools.call_tool("대표", "create_sale", dict(GOOD))
    with app_db.connect() as con:                                       # 읽기 전용 연결은 쓰기 자체가 실패
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM sales")
