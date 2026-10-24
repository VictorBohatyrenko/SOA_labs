"""Асинхронна комунікація через RabbitMQ.

Усі доменні події публікуються в topic-exchange `medical.events`
з routing key виду `<сутність>.<дія>`, наприклад `note.created`.
Кожен сервіс-споживач має власну чергу і сам вирішує, які події йому
потрібні (хореографія). Кілька інстансів одного споживача читають одну
чергу і RabbitMQ роздає їм повідомлення по черзі (competing consumers).
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import pika

from .config import RABBIT_URL, whoami

log = logging.getLogger("broker")

EXCHANGE = "medical.events"


def _params() -> pika.URLParameters:
    p = pika.URLParameters(RABBIT_URL)
    p.heartbeat = 30
    p.blocked_connection_timeout = 10
    return p


def publish(event_type: str, data: dict) -> None:
    """Публікує подію. Помилка брокера не ламає основний запит — лише логується."""
    event = {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "source": whoami(),
        "data": data,
    }
    try:
        conn = pika.BlockingConnection(_params())
        try:
            ch = conn.channel()
            ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
            ch.basic_publish(
                exchange=EXCHANGE,
                routing_key=event_type,
                body=json.dumps(event, ensure_ascii=False, default=str).encode(),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,  # persistent
                    message_id=event["event_id"],
                ),
            )
        finally:
            conn.close()
        log.info("-> подія %s опублікована (%s)", event_type, event["event_id"])
    except Exception as e:  # noqa: BLE001
        log.error("Не вдалося опублікувати %s: %s", event_type, e)


def consume(queue: str, binding_keys: list[str], handler) -> None:
    """Нескінченний цикл споживання з автоматичним перепідключенням.

    handler(event: dict) викликається для кожного повідомлення;
    підтвердження (ack) надсилається лише після успішної обробки.
    """
    while True:
        try:
            conn = pika.BlockingConnection(_params())
            ch = conn.channel()
            ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
            ch.queue_declare(queue=queue, durable=True)
            for key in binding_keys:
                ch.queue_bind(queue=queue, exchange=EXCHANGE, routing_key=key)
            ch.basic_qos(prefetch_count=1)

            def _on_message(channel, method, _props, body):
                try:
                    handler(json.loads(body))
                    channel.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as e:  # noqa: BLE001
                    log.exception("Помилка обробки повідомлення: %s", e)
                    channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            ch.basic_consume(queue=queue, on_message_callback=_on_message)
            log.info("Слухаю чергу '%s' (ключі: %s)", queue, ", ".join(binding_keys))
            ch.start_consuming()
        except Exception as e:  # noqa: BLE001
            log.warning("RabbitMQ недоступний (%s), повтор через 5 с", e)
            time.sleep(5)
