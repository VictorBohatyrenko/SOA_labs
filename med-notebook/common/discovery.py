"""Service discovery через Consul.

1. Кожен інстанс при старті реєструється в Consul (ім'я сервісу, IP, порт,
   health-check і теги для Traefik). При зупинці — знімається з реєстрації.
2. Traefik (API Gateway) читає каталог Consul і балансує зовнішні запити
   між усіма здоровими інстансами (server-side балансування).
3. Для викликів сервіс->сервіс використовується ServiceClient: він питає в
   Consul список здорових інстансів і перебирає їх по колу (client-side
   балансування). Виклики обгорнуті в Circuit Breaker.
"""
import itertools
import logging
import socket
import threading
import time

import httpx

from .config import CONSUL_URL, INSTANCE, SERVICE_NAME, SERVICE_PORT

log = logging.getLogger("discovery")

_service_id = f"{SERVICE_NAME}-{INSTANCE}"


def _my_ip() -> str:
    return socket.gethostbyname(socket.gethostname())


def register(route_prefix: str) -> None:
    """Реєстрація інстансу в Consul (з повторами, поки Consul не стане доступним)."""
    ip = _my_ip()
    body = {
        "ID": _service_id,
        "Name": SERVICE_NAME,
        "Address": ip,
        "Port": SERVICE_PORT,
        "Meta": {"instance": INSTANCE},
        "Tags": [
            "traefik.enable=true",
            f"traefik.http.routers.{SERVICE_NAME}.rule=PathPrefix(`{route_prefix}`)",
            f"traefik.http.routers.{SERVICE_NAME}.entrypoints=web",
        ],
        "Check": {
            "HTTP": f"http://{ip}:{SERVICE_PORT}/health",
            "Interval": "5s",
            "Timeout": "2s",
            "DeregisterCriticalServiceAfter": "1m",
        },
    }
    for attempt in range(1, 31):
        try:
            r = httpx.put(f"{CONSUL_URL}/v1/agent/service/register", json=body, timeout=3)
            r.raise_for_status()
            log.info("Зареєстровано в Consul: %s (%s:%s)", _service_id, ip, SERVICE_PORT)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("Consul недоступний (спроба %s): %s", attempt, e)
            time.sleep(2)
    log.error("Не вдалося зареєструватися в Consul")


def deregister() -> None:
    try:
        httpx.put(f"{CONSUL_URL}/v1/agent/service/deregister/{_service_id}", timeout=3)
        log.info("Знято з реєстрації: %s", _service_id)
    except Exception as e:  # noqa: BLE001
        log.warning("Не вдалося зняти з реєстрації: %s", e)


class CircuitOpenError(Exception):
    """Запит не виконувався, бо 'запобіжник' розімкнений."""


class ServiceUnavailableError(Exception):
    """Жоден інстанс сервісу не відповів."""


class CircuitBreaker:
    """Простий Circuit Breaker: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.

    Після `failure_threshold` невдач поспіль запобіжник розмикається і
    `reset_timeout` секунд запити одразу відхиляються, не навантажуючи
    зламаний сервіс. Потім пропускається один пробний запит (HALF_OPEN).
    """

    def __init__(self, name: str, failure_threshold: int = 3, reset_timeout: float = 15):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.state = "CLOSED"
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def before_call(self) -> None:
        with self._lock:
            if self.state == "OPEN":
                if time.monotonic() - self.opened_at >= self.reset_timeout:
                    self.state = "HALF_OPEN"
                    log.info("Circuit %s: HALF_OPEN (пробний запит)", self.name)
                else:
                    raise CircuitOpenError(f"Circuit breaker для '{self.name}' розімкнений")

    def on_success(self) -> None:
        with self._lock:
            if self.state != "CLOSED":
                log.info("Circuit %s: CLOSED (сервіс відновився)", self.name)
            self.failures = 0
            self.state = "CLOSED"

    def on_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.state == "HALF_OPEN" or self.failures >= self.failure_threshold:
                self.state = "OPEN"
                self.opened_at = time.monotonic()
                log.warning("Circuit %s: OPEN після %s невдач", self.name, self.failures)


class ServiceClient:
    """HTTP-клієнт до іншого сервісу з discovery через Consul,
    round-robin балансуванням і circuit breaker."""

    def __init__(self, service: str):
        self.service = service
        self.breaker = CircuitBreaker(service)
        self._rr = itertools.count()

    def instances(self) -> list[str]:
        r = httpx.get(f"{CONSUL_URL}/v1/health/service/{self.service}", params={"passing": "true"}, timeout=3)
        r.raise_for_status()
        return [f"http://{e['Service']['Address']}:{e['Service']['Port']}" for e in r.json()]

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.breaker.before_call()
        try:
            urls = self.instances()
        except Exception as e:  # noqa: BLE001
            self.breaker.on_failure()
            raise ServiceUnavailableError(f"Consul недоступний: {e}") from e
        if not urls:
            self.breaker.on_failure()
            raise ServiceUnavailableError(f"Немає живих інстансів '{self.service}'")

        start = next(self._rr)
        last_error = None
        # перебираємо інстанси по колу, починаючи з наступного (round-robin + retry)
        for i in range(len(urls)):
            url = urls[(start + i) % len(urls)]
            try:
                resp = httpx.request(method, url + path, timeout=5, **kwargs)
                if resp.status_code >= 500:
                    last_error = f"{url} -> {resp.status_code}"
                    continue
                self.breaker.on_success()
                return resp
            except httpx.HTTPError as e:
                last_error = f"{url} -> {e}"
        self.breaker.on_failure()
        raise ServiceUnavailableError(f"'{self.service}' не відповідає: {last_error}")

    def get(self, path: str, **kw) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.request("POST", path, **kw)
