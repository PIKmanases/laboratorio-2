import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine

from . import rutas_v1
from .config import get_settings
from .health import HealthMonitor
from .limpieza import Catalogos, SolicitudRechazada
from .predictor import Predictor, PredictorPromedio


def create_app(monitor=None, catalogos: Catalogos | None = None, predictor: Predictor | None = None) -> FastAPI:
    """Fábrica de la aplicación. Lo que se pasa aquí se inyecta (las pruebas usan fakes);
    lo que no, se construye en el arranque a partir de la configuración."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        inyectado = app.state.monitor and app.state.catalogos and app.state.predictor
        settings = None if inyectado else get_settings()
        engine = client = None
        mon = app.state.monitor
        if mon is None:
            # create_engine es perezoso: no conecta, así la API arranca aunque la base tarde.
            engine = create_engine(
                settings.database_url,
                pool_pre_ping=True,
                connect_args={"connect_timeout": int(settings.health_check_timeout)},
            )
            client = httpx.AsyncClient()
            mon = app.state.monitor = HealthMonitor(settings, engine, client)
        if app.state.catalogos is None:
            app.state.catalogos = Catalogos.desde_csv(Path(settings.data_dir))
        if app.state.predictor is None:
            app.state.predictor = PredictorPromedio.desde_csv(Path(settings.data_dir) / "historico_ventas.csv")
        task = asyncio.create_task(mon.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if client:
                await client.aclose()
            if engine:
                engine.dispose()

    app = FastAPI(title="API", lifespan=lifespan)
    app.state.monitor = monitor
    app.state.catalogos = catalogos
    app.state.predictor = predictor
    app.state.model = None  # todavía no hay modelo real cargado (el predictor falso no cuenta)

    @app.exception_handler(SolicitudRechazada)
    async def _rechazo(_: Request, exc: SolicitudRechazada):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _estructura(_: Request, exc: RequestValidationError):
        # Mismo formato que los rechazos de la limpieza: `detail` como texto legible.
        motivos = "; ".join(f"{'.'.join(map(str, e['loc'][1:])) or 'cuerpo'}: {e['msg']}" for e in exc.errors())
        return JSONResponse(status_code=422, content={"detail": motivos})

    app.include_router(rutas_v1.router)

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

    return app


app = create_app()
