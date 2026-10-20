"""업종 router 와 core.main 이 같이 쓰는 요청 헬퍼."""
import time
from collections import deque
from datetime import date

from fastapi import HTTPException

from .permissions import ROLES
from .validation import ValidationFailed


def role(value: str) -> str:
    if value not in ROLES:
        raise HTTPException(400, f"알 수 없는 역할: {value}")
    return value


def payload(body: dict):
    """본문에서 역할을 꺼내 검증하고 나머지 필드를 반환."""
    if not isinstance(body, dict):
        raise ValidationFailed([{"field": "_form", "message": "요청 형식이 올바르지 않습니다"}])
    return role(str(body.get("role", ""))), body


class ChatLimiter:
    """IP 별 분당 횟수 + 전체 일일 횟수 제한. 허용이면 None, 거절이면 안내 문구를 돌려준다.
    ponytail: 서버 한 대의 메모리 안에서만 센다 (재시작하면 초기화). 여러 대로 늘리면 Redis 등으로 옮긴다."""

    def __init__(self, per_min, per_day):
        self.per_min, self.per_day = per_min, per_day
        self.hits, self.day, self.day_count = {}, date.today(), 0

    def allow(self, ip):
        now = time.time()
        if date.today() != self.day:
            self.day, self.day_count = date.today(), 0
        if self.day_count >= self.per_day:
            return "오늘 데모 채팅 한도에 도달했어요. 내일 다시 이용해 주세요."
        if len(self.hits) > 10000:                  # IP 를 바꿔가며 메모리를 채우는 것을 막는다
            self.hits.clear()
        q = self.hits.setdefault(ip, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_min:
            return "질문이 너무 빨라요. 1분쯤 뒤에 다시 시도해 주세요."
        q.append(now)
        self.day_count += 1
        return None
