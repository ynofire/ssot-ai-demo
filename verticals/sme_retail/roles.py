"""역할별 권한 매트릭스 — 마스터 문서 '사용자 역할 및 권한 구조'의 단일 정의.

대시보드·채팅 tool·입력폼이 모두 core.permissions 를 거쳐 이 파일만 참조한다.
값: "rw" 열람+수정, "r" 열람만, None 접근 불가.
"""

ROLES = ("대표", "구매부", "영업부", "경리")

# sales=영업실적, purchase=상품구매, finance=금전 전체(운영비·순현금흐름)
PERMISSIONS = {
    "대표":   {"sales": "rw", "purchase": "rw", "finance": "rw"},
    "구매부": {"sales": "r",  "purchase": "rw", "finance": None},
    "영업부": {"sales": "rw", "purchase": "r",  "finance": None},
    "경리":   {"sales": "r",  "purchase": "r",  "finance": "r"},
}

AREA_KO = {"sales": "영업실적", "purchase": "상품구매", "finance": "금전(운영비·순현금흐름)"}
