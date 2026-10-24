import os
import socket

SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8000"))
# Ім'я контейнера (docker дає кожному інстансу унікальний hostname)
INSTANCE = socket.gethostname()

CONSUL_URL = os.getenv("CONSUL_URL", "http://consul:8500")
RABBIT_URL = os.getenv("RABBIT_URL", "amqp://guest:guest@rabbitmq:5672/%2F")
DATABASE_URL = os.getenv("DATABASE_URL", "")


def whoami() -> str:
    return f"{SERVICE_NAME}@{INSTANCE}"
