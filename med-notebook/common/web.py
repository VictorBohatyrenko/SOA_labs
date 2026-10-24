"""Спільна обв'язка для HTTP-сервісів: логування, lifespan з реєстрацією
в Consul, /health, /whoami і заголовок X-Served-By."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from . import discovery
from .config import INSTANCE, SERVICE_NAME, whoami

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


def create_app(title: str, route_prefix: str, on_startup=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if on_startup:
            on_startup()
        discovery.register(route_prefix)
        yield
        discovery.deregister()

    app = FastAPI(title=title, lifespan=lifespan, docs_url=f"{route_prefix}/docs",
                  openapi_url=f"{route_prefix}/openapi.json")

    @app.middleware("http")
    async def served_by(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Served-By"] = whoami()
        return response

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "UP"}

    @app.get(f"{route_prefix}/whoami", tags=["infra"])
    def who():
        """Який інстанс обробив запит — для демонстрації балансування."""
        return {"service": SERVICE_NAME, "instance": INSTANCE}

    return app
