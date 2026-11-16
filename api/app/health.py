import asyncio
import logging
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .config import Settings

logger = logging.getLogger("api.health")


@dataclass
class CheckResult:
    ok: bool = False
    detail: str = "pending"
    checked_at: float | None = None  # time.monotonic()


class HealthMonitor:
    """Corre chequeos periódicos y guarda el último resultado.

    Las peticiones HTTP solo leen `snapshot()`: nunca esperan a la base ni a MLflow.
    """

    def __init__(self, settings: Settings, engine: Engine, client: httpx.AsyncClient):
        self._settings = settings
        self._engine = engine
        self._client = client
        self._results = {"database": CheckResult(), "mlflow": CheckResult()}

    async def run(self) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(self._settings.health_check_interval)

    async def check_once(self) -> None:
        db, mlflow = await asyncio.gather(self._check_database(), self._check_mlflow())
        self._results["database"] = db
        self._results["mlflow"] = mlflow

    async def _check_database(self) -> CheckResult:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._select_one),
                timeout=self._settings.health_check_timeout,
            )
            return CheckResult(True, "ok", time.monotonic())
        except Exception as exc:
            logger.warning("database check failed: %r", exc)
            return CheckResult(False, type(exc).__name__, time.monotonic())

    def _select_one(self) -> None:
        with self._engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    async def _check_mlflow(self) -> CheckResult:
        url = self._settings.mlflow_tracking_uri.rstrip("/") + "/health"
        try:
            resp = await self._client.get(url, timeout=self._settings.health_check_timeout)
            resp.raise_for_status()
            return CheckResult(True, "ok", time.monotonic())
        except Exception as exc:
            logger.warning("mlflow check failed: %r", exc)
            return CheckResult(False, type(exc).__name__, time.monotonic())

    def snapshot(self) -> dict[str, dict]:
        """Último resultado guardado; si está viejo (tarea muerta o colgada) cuenta como caído."""
        now = time.monotonic()
        out = {}
        for name, r in self._results.items():
            stale = r.checked_at is not None and now - r.checked_at > self._settings.health_stale_after
            if stale:
                out[name] = {"ok": False, "detail": "stale"}
            else:
                out[name] = {"ok": r.ok, "detail": r.detail}
        return out
