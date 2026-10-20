"""권한 검사 엔진. 역할·영역·매트릭스 정의는 업종 패키지의 roles.py 에 있다.

대시보드·채팅 tool·입력폼이 모두 이 파일만 참조한다.
값: "rw" 열람+수정, "r" 열람만, None 접근 불가.
"""
import importlib

from .config import VERTICAL_PKG

_roles = importlib.import_module(VERTICAL_PKG + ".roles")
ROLES = _roles.ROLES
PERMISSIONS = _roles.PERMISSIONS
AREA_KO = _roles.AREA_KO


class PermissionDenied(Exception):
    def __init__(self, role, area, action="조회"):
        self.role, self.area, self.action = role, area, action
        super().__init__(f"권한 없음: 현재 역할({role})로는 {AREA_KO[area]} 정보를 {action}할 수 없습니다.")


def check_role(role):
    if role not in PERMISSIONS:
        raise ValueError(f"알 수 없는 역할: {role}")
    return role


def can_read(role, area):
    return PERMISSIONS[check_role(role)][area] in ("r", "rw")


def can_write(role, area):
    return PERMISSIONS[check_role(role)][area] == "rw"


def require_read(role, area):
    if not can_read(role, area):
        raise PermissionDenied(role, area)


def require_write(role, area):
    if not can_write(role, area):
        raise PermissionDenied(role, area, "수정")
