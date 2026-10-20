"""중소기업(도소매) 업종 — 상수만 둔다 (core 가 가장 먼저 import 하므로 core 를 import 하지 않는다)."""
from datetime import date

DB_FILE = "company.db"
# 시연용 고정 '오늘' (가상 데이터의 마지막 날)
TODAY = date(2026, 9, 27)

# 프론트 셸 표시 문구
DISPLAY = {
    "title": "SSOT AI 대시보드",
    "subtitle": "부산 도소매업체 (가상 데이터 · 5인)",
    "footer": "",
    "scope_note": "",
}
