import asyncio
import contextlib
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response
from sqlalchemy import create_engine

from .config import get_settings
from .health import HealthMonitor


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # create_engine es perezoso: no conecta, así la API arranca aunque la base tarde.
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": int(settings.health_check_timeout)},
    )
    client = httpx.AsyncClient()
    monitor = HealthMonitor(settings, engine, client)
    app.state.monitor = monitor
    app.state.model = None  # todavía no hay modelo cargado
    task = asyncio.create_task(monitor.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await client.aclose()
        engine.dispose()


app = FastAPI(title="API", lifespan=lifespan)


@app.get("/health")
async def health(response: Response):
    checks = app.state.monitor.snapshot()
    model_loaded = app.state.model is not None
    checks["model"] = {"ok": model_loaded, "detail": "loaded" if model_loaded else "not_loaded"}

    if not (model_loaded and checks["database"]["ok"]):
        status, code = "unavailable", 503
    elif not checks["mlflow"]["ok"]:
        status, code = "degraded", 200
    else:
        status, code = "ok", 200

    response.status_code = code
    return {"status": status, "checks": checks}
