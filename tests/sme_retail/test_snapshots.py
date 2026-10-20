"""전월(마감)/당월(진행 중) 스냅샷 테스트. DB 임시 복사본 사용.

사용: python -m pytest tests/sme_retail/test_snapshots.py -q
"""
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import db as app_db  # noqa: E402
from verticals.sme_retail import tools  # noqa: E402
from core.main import app  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "data" / "company.db"
SALE = {"role": "영업부", "date": "2026-09-26", "client_id": "C02", "product_id": "P001", "qty": 2, "sell_price": 36000}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    shutil.copy(SRC, path)
    monkeypatch.setattr(app_db, "DB_PATH", path)
    return TestClient(app)


def test_same_day_last_year_handles_leap_day():
    assert tools._same_day_last_year(date(2028, 2, 29)) == date(2027, 2, 28)
    assert tools._same_day_last_year(date(2026, 9, 27)) == date(2025, 9, 27)


def test_prev_and_current_month_definition():
    sn = tools.month_snapshots("대표")
    assert sn["prev_month"]["month"] == "2026-08" and sn["prev_month"]["closed"] is True
    assert sn["current_month"]["month"] == "2026-09" and sn["current_month"]["closed"] is False
    assert sn["current_month"]["through_day"] == 27
    assert sn["current_month"]["last_year_label"] == "2025-09 (1~27일)"
    assert sn["prev_month"]["last_year_label"] == "2025-08"


def test_matches_monthly_series():
    ms = {m["month"]: m for m in tools.monthly_series("대표", "2026-08", "2026-09")}
    sn = tools.month_snapshots("대표")
    for key, month in (("prev_month", "2026-08"), ("current_month", "2026-09")):
        v = sn[key]["values"]
        for f in ("revenue", "purchases", "expenses", "net_cash_flow", "gross_margin_pct"):
            assert v[f] == ms[month][f], (key, f)


def test_last_year_same_period_is_partial_not_full_month():
    """전년 동기간(1~27일)이 전년 9월 전체보다 작거나 같아야 한다 (28~30일 매출 제외)."""
    with app_db.connect() as con:
        full = con.execute("SELECT SUM(qty*sell_price) v FROM sales WHERE substr(date,1,7)='2025-09'").fetchone()["v"]
        upto = con.execute("SELECT SUM(qty*sell_price) v FROM sales WHERE date BETWEEN '2025-09-01' AND '2025-09-27'").fetchone()["v"]
    sn = tools.month_snapshots("대표")
    assert sn["current_month"]["last_year"]["revenue"] == upto
    assert upto < full


@pytest.mark.parametrize("role,has_purchase,has_finance", [("대표", True, True), ("경리", True, True), ("구매부", True, False), ("영업부", True, False)])
def test_permission_fields(role, has_purchase, has_finance):
    for key in ("prev_month", "current_month"):
        for part in ("values", "last_year"):
            d = tools.month_snapshots(role)[key][part]
            assert "revenue" in d
            assert ("purchases" in d) == has_purchase
            assert ("net_cash_flow" in d) == has_finance and ("expenses" in d) == has_finance


def test_dashboard_api_returns_snapshots_without_hidden_fields(client):
    r = client.get("/api/dashboard", params={"role": "영업부"}).json()
    assert "snapshots" in r
    assert "net_cash_flow" not in r["snapshots"]["current_month"]["values"]
    assert "net_cash_flow" in client.get("/api/dashboard", params={"role": "대표"}).json()["snapshots"]["current_month"]["values"]


def test_current_month_updates_immediately_after_save_but_prev_month_does_not(client):
    before = client.get("/api/dashboard", params={"role": "대표"}).json()["snapshots"]
    r = client.post("/api/sales", json=SALE)
    assert r.status_code == 201, r.text
    after = client.get("/api/dashboard", params={"role": "대표"}).json()["snapshots"]
    assert after["current_month"]["values"]["revenue"] == before["current_month"]["values"]["revenue"] + 2 * 36000
    assert after["prev_month"] == before["prev_month"]                      # 마감된 전월은 그대로
    assert after["current_month"]["last_year"] == before["current_month"]["last_year"]


def test_sale_edit_in_current_month_updates_snapshot(client):
    sid = client.post("/api/sales", json=SALE).json()["sale_id"]
    mid = client.get("/api/dashboard", params={"role": "대표"}).json()["snapshots"]["current_month"]["values"]["revenue"]
    assert client.patch(f"/api/sales/{sid}", json={"role": "영업부", "qty": 5}).status_code == 200
    now = client.get("/api/dashboard", params={"role": "대표"}).json()["snapshots"]["current_month"]["values"]["revenue"]
    assert now == mid + 3 * 36000


def test_expense_side_unaffected_by_sale_entry(client):
    before = client.get("/api/dashboard", params={"role": "대표"}).json()["snapshots"]["current_month"]["values"]
    client.post("/api/sales", json=SALE)
    after = client.get("/api/dashboard", params={"role": "대표"}).json()["snapshots"]["current_month"]["values"]
    assert after["expenses"] == before["expenses"] and after["purchases"] == before["purchases"]
    assert after["net_cash_flow"] == before["net_cash_flow"] + 2 * 36000
