"""업종 router 와 core.main 이 같이 쓰는 요청 헬퍼."""
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
