from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterator

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from app.core.config import get_settings


def utc_now_db() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _connect() -> Connection:
    settings = get_settings()
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.market_db_name,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


@contextmanager
def get_connection() -> Iterator[Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_connection() -> str:
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                cursor.fetchone()
        return "available"
    except Exception:
        return "unavailable"


def row_to_dict(row: dict | None) -> dict | None:
    if row is None:
        return None

    item: dict = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            item[key] = value.replace(tzinfo=timezone.utc).isoformat()
        elif isinstance(value, date):
            item[key] = value.isoformat()
        elif isinstance(value, Decimal):
            item[key] = float(value)
        else:
            item[key] = value
    return item
