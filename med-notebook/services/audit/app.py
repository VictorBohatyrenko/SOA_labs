"""Audit Service — журнал усіх дій з медичними даними.

Споживає ВСІ події з RabbitMQ (binding key '#') і записує їх у журнал.
Обробка ідемпотентна: повторна доставка тієї ж події не створює дубль.
Якщо запустити кілька інстансів, вони ділять одну чергу
(competing consumers) — видно в /api/audit/stats.
"""
import logging
import threading

from common import broker
from common.config import whoami
from common.db import Jsonb, connect, init_schema
from common.web import create_app

PREFIX = "/api/audit"
QUEUE = "audit.all-events"

DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    event_id     UUID PRIMARY KEY,
    type         TEXT NOT NULL,
    source       TEXT NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL,
    data         JSONB NOT NULL,
    consumed_by  TEXT NOT NULL,
    consumed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

log = logging.getLogger("audit")


def handle(event: dict) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO audit_log (event_id, type, source, occurred_at, data, consumed_by)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING""",
            (event["event_id"], event["type"], event["source"], event["occurred_at"],
             Jsonb(event["data"]), whoami()),
        )
    log.info("<- [%s] %s від %s", whoami(), event["type"], event["source"])


def start():
    init_schema(DDL)
    threading.Thread(target=broker.consume, args=(QUEUE, ["#"], handle), daemon=True).start()


app = create_app("Audit Service", PREFIX, on_startup=start)


@app.get(PREFIX, tags=["audit"])
def journal(limit: int = 50, type: str | None = None):
    """Останні події журналу."""
    with connect() as conn:
        if type:
            return conn.execute("SELECT * FROM audit_log WHERE type=%s ORDER BY occurred_at DESC LIMIT %s",
                                (type, limit)).fetchall()
        return conn.execute("SELECT * FROM audit_log ORDER BY occurred_at DESC LIMIT %s", (limit,)).fetchall()


@app.get(PREFIX + "/stats", tags=["audit"])
def stats():
    """Скільки подій якого типу і який інстанс-споживач їх обробив."""
    with connect() as conn:
        by_type = conn.execute("SELECT type, count(*) AS n FROM audit_log GROUP BY type ORDER BY type").fetchall()
        by_consumer = conn.execute(
            "SELECT consumed_by, count(*) AS n FROM audit_log GROUP BY consumed_by ORDER BY consumed_by").fetchall()
    return {"by_type": by_type, "by_consumer": by_consumer}
