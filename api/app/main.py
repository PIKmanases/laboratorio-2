import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import create_engine

from . import rutas_v1
from .config import get_settings
from .health import HealthMonitor
from .limpieza import Catalogos, SolicitudRechazada
from .db.repositorio import ErrorPersistencia, PredictorSQL, Repositorio, RepositorioSQL
from .predictor import Predictor

logger = logging.getLogger("api")


def create_app(
    monitor=None,
    catalogos: Catalogos | None = None,
    predictor: Predictor | None = None,
    repositorio: Repositorio | None = None,
) -> FastAPI:
    """Fábrica de la aplicación. Lo que se pasa aquí se inyecta (las pruebas usan fakes);
    lo que no, se construye en el arranque a partir de la configuración y de app-db."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state
        engine = client = None
        if not (st.monitor and st.catalogos and st.predictor and st.repositorio):
            settings = get_settings()
            # create_engine es perezoso: no conecta, así la API arranca aunque la base tarde.
            engine = create_engine(
                settings.database_url,
                pool_pre_ping=True,
                connect_args={"connect_timeout": int(settings.health_check_timeout)},
            )
            if st.monitor is None:
                client = httpx.AsyncClient()
                st.monitor = HealthMonitor(settings, engine, client)
            if st.catalogos is None:  # tablas de referencia que cargó el contenedor `init`
                st.catalogos = Catalogos.desde_db(engine)
            if st.predictor is None:
                st.predictor = PredictorSQL(engine)
            if st.repositorio is None:
                st.repositorio = RepositorioSQL(engine)
        task = asyncio.create_task(st.monitor.run())
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
    app.state.repositorio = repositorio
    app.state.model = None  # todavía no hay modelo real cargado (el predictor falso no cuenta)

    @app.exception_handler(SolicitudRechazada)
    async def _rechazo(_: Request, exc: SolicitudRechazada):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _estructura(request: Request, exc: RequestValidationError):
        # Mismo formato que los rechazos de la limpieza: `detail` como texto legible.
        motivos = "; ".join(f"{'.'.join(map(str, e['loc'][1:])) or 'cuerpo'}: {e['msg']}" for e in exc.errors())
        repo = request.app.state.repositorio
        if repo is not None and request.url.path.startswith("/v1/"):
            cuerpo = exc.body if isinstance(exc.body, (dict, list, str, int, float, bool)) else None
            with contextlib.suppress(ErrorPersistencia):  # a mejor esfuerzo
                await run_in_threadpool(repo.guardar_rechazo, request.url.path.removeprefix("/v1/"), cuerpo, None, motivos)
        return JSONResponse(status_code=422, content={"detail": motivos})

    @app.exception_handler(ErrorPersistencia)
    async def _sin_base(_: Request, exc: ErrorPersistencia):
        # Política D6: sin app-db no se responde algo que no quedó registrado.
        logger.error("app-db no disponible: %s", exc)
        return JSONResponse(status_code=503, content={"detail": "base de datos no disponible; reintente"})

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
