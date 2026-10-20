"""도소매 업종 API: 대시보드 + 입력폼(쓰기). core.main 이 이 router 를 붙인다."""
from fastapi import APIRouter, Body, Query

from core.config import TODAY
from core.main_helpers import payload as _payload
from core.main_helpers import role as _role
from core.permissions import PERMISSIONS, can_read

from . import forms, tools

router = APIRouter()


@router.get("/api/dashboard")
def dashboard(role: str, from_month: str = "2024-01"):
    role = _role(role)
    months = tools.monthly_series(role, from_month, TODAY.strftime("%Y-%m"))
    inventory = None
    if can_read(role, "purchase"):
        inventory = tools.get_inventory(role)["items"]
    return {"role": role, "permissions": PERMISSIONS[role], "today": TODAY.isoformat(),
            "show_cash_flow": can_read(role, "finance"), "months": months, "snapshots": tools.month_snapshots(role), "inventory": inventory,
            "change_log": tools.get_change_log(role, limit=15)["logs"]}


# ---- 입력폼 API (쓰기): 검증 + 재고 반영 + 변경이력 자동 기록을 한 트랜잭션으로 처리
@router.get("/api/lookups")
def lookups(role: str):
    return forms.lookups(_role(role))


@router.get("/api/sales")
def sales_list(role: str, limit: int = Query(30, ge=1, le=200), client_id: str | None = None,
               month: str | None = None):
    return {"sales": forms.list_sales(_role(role), limit, client_id, month)}


@router.post("/api/sales", status_code=201)
def sales_create(payload: dict = Body(...)):
    role, data = _payload(payload)
    return forms.create_sale(role, data)


@router.patch("/api/sales/{sale_id}")
def sales_update(sale_id: int, payload: dict = Body(...)):
    role, data = _payload(payload)
    return forms.update_sale(role, sale_id, data)


@router.patch("/api/inventory/{product_id}")
def inventory_update(product_id: str, payload: dict = Body(...)):
    role, data = _payload(payload)
    return forms.update_inventory(role, product_id, data)


@router.patch("/api/products/{product_id}")
def product_update(product_id: str, payload: dict = Body(...)):
    role, data = _payload(payload)
    return forms.update_product(role, product_id, data)


@router.post("/api/clients", status_code=201)
def client_create(payload: dict = Body(...)):
    role, data = _payload(payload)
    return forms.create_client(role, data)


@router.patch("/api/clients/{client_id}")
def client_update(client_id: str, payload: dict = Body(...)):
    role, data = _payload(payload)
    return forms.update_client(role, client_id, data)
