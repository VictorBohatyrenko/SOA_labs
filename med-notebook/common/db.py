"""Робота з PostgreSQL. Кожен сервіс має власну базу (database per service)."""
import logging
import time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import DATABASE_URL

log = logging.getLogger("db")

__all__ = ["connect", "init_schema", "Jsonb"]


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)


def init_schema(ddl: str) -> None:
    """Чекає на БД і створює таблиці (якщо їх ще немає)."""
    for attempt in range(1, 31):
        try:
            with connect() as conn:
                try:
                    conn.execute(ddl)
                except psycopg.errors.UniqueViolation:
                    # кілька інстансів одночасно створюють таблицю — не страшно
                    pass
            log.info("Схема БД готова")
            return
        except psycopg.OperationalError as e:
            log.warning("БД недоступна (спроба %s): %s", attempt, e)
            time.sleep(2)
    raise RuntimeError("База даних недоступна")
