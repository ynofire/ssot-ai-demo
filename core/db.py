import sqlite3
from contextlib import contextmanager

from .config import DB_PATH


@contextmanager
def connect(readonly=True):
    """읽기 전용이 기본. 입력폼(쓰기)만 readonly=False 로 연다."""
    if readonly:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(DB_PATH)
        con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    try:
        yield con
        if not readonly:
            con.commit()
    except Exception:
        if not readonly:
            con.rollback()
        raise
    finally:
        con.close()
