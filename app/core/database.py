from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from app.core.config import PROJECT_ROOT, get_settings


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now_db() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_db_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    if len(value) == 10:
        return datetime.fromisoformat(f"{value}T00:00:00")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _connect(*, include_database: bool = True) -> Connection:
    settings = get_settings()
    kwargs = {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password,
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
    }
    if include_database:
        kwargs["database"] = settings.db_name
    return pymysql.connect(**kwargs)


def _split_sql_script(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip(";"))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements


def ensure_database() -> None:
    schema_path = PROJECT_ROOT / "database" / "mariadb_schema.sql"
    script = schema_path.read_text(encoding="utf-8")

    conn = _connect(include_database=False)
    try:
        with conn.cursor() as cursor:
            for statement in _split_sql_script(script):
                cursor.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_connection() -> Iterator[Connection]:
    ensure_database()
    conn = _connect(include_database=True)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
