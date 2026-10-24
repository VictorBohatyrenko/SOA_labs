"""Notification Worker — сервіс-консьюмер без HTTP API.

Слухає події про записи прийому та призначення і «надсилає» лікарю
нагадування (у лабораторній — пише в лог контейнера).
"""
import logging

from common import broker
from common.config import whoami

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("notifications")

QUEUE = "notifications.reminders"
KEYS = ["note.created", "note.updated", "prescription.created"]


def handle(event: dict) -> None:
    d = event["data"]
    t = event["type"]
    if t in ("note.created", "note.updated") and d.get("follow_up"):
        who = d.get("patient_name") or f"пацієнт #{d['patient_id']}"
        log.info("[%s] 📅 Нагадування: повторний огляд — %s, дата %s (запис #%s)",
                 whoami(), who, d["follow_up"], d["note_id"])
    elif t == "prescription.created":
        log.info("[%s] 💊 Пацієнту %s призначено %s (%s) на %s дн. — нагадування про закінчення курсу",
                 whoami(), d["patient_name"], d["drug"], d["dose"], d["days"])
    else:
        log.info("[%s] подія %s без потреби в нагадуванні", whoami(), t)


if __name__ == "__main__":
    broker.consume(QUEUE, KEYS, handle)
