"""/health según el contrato: ok / degradado (200, con motivo) / 503 sin modelo. Sin MLflow ni Postgres."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.health import HealthMonitor
from app.limpieza import Catalogos
from app.main import create_app
from app.modelo_mlflow import ModeloCargado
from conftest import FAMILIAS
from fakes import AirflowFalso, PredictorPromedio, ReentrenamientosMemoria, RepositorioMemoria


class MonitorFalso:
    def __init__(self, **caidos):
        self.caidos = caidos  # {"mlflow": "ConnectError"}

    def snapshot(self):
        return {n: {"ok": n not in self.caidos, "detail": self.caidos.get(n, "ok")} for n in ("database", "mlflow", "airflow")}

    async def run(self):  # el lifespan no se ejecuta en estas pruebas
        pass


def cliente(monitor, con_modelo=True):
    app = create_app(monitor=monitor, catalogos=Catalogos.desde_filas(range(1, 11), FAMILIAS), predictor=PredictorPromedio([]),
                     repositorio=RepositorioMemoria(), airflow=AirflowFalso(), reentrenamientos=ReentrenamientosMemoria(),
                     modelo=ModeloCargado("novamercado-ventas", "champion", "1", object()) if con_modelo else None)  # fmt: skip
    return TestClient(app)


def test_todo_bien_es_ok():
    r = cliente(MonitorFalso()).get("/health")
    assert r.status_code == 200 and r.json()["estado"] == "ok" and "motivo" not in r.json()


@pytest.mark.parametrize("dependencia", ["mlflow", "airflow", "database"])
def test_una_dependencia_caida_es_degradado_con_motivo_y_200(dependencia):
    r = cliente(MonitorFalso(**{dependencia: "ConnectError"})).get("/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "degradado" and dependencia in r.json()["motivo"]


def test_sin_modelo_es_503_aunque_todo_lo_demas_este_bien():
    r = cliente(MonitorFalso(), con_modelo=False).get("/health")
    assert r.status_code == 503 and r.json()["estado"] != "ok" and r.json()["motivo"]


def test_sin_modelo_y_mlflow_caido_sigue_siendo_503():
    assert cliente(MonitorFalso(mlflow="ConnectError"), con_modelo=False).get("/health").status_code == 503


# ------------------------------------------------------------------ chequeo real de Airflow (transporte falso)
def monitor_con(handler):
    s = Settings(database_url="x", mlflow_tracking_uri="http://mlflow:5000", airflow_user="u", airflow_password="p")
    return HealthMonitor(s, None, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def chequear(handler):
    return await monitor_con(handler)._check_airflow()


def sano(estados=None):
    cuerpo = {"metadatabase": {"status": "healthy"}, "scheduler": {"status": "healthy"}, "dag_processor": {"status": "healthy"},
              "triggerer": {"status": None}} | (estados or {})  # fmt: skip
    return lambda req: httpx.Response(200, json=cuerpo)


@pytest.mark.anyio
async def test_airflow_sano():
    assert (await chequear(sano())).ok


@pytest.mark.anyio
async def test_airflow_con_scheduler_o_dag_processor_caido_cuenta_como_caido():
    r = await chequear(sano({"scheduler": {"status": "unhealthy"}}))
    assert not r.ok and r.detail == "scheduler_unhealthy"
    assert not (await chequear(sano({"dag_processor": {"status": "unhealthy"}}))).ok


@pytest.mark.anyio
async def test_airflow_sin_respuesta_o_con_5xx_es_caido():
    def sin_red(req):
        raise httpx.ConnectError("sin red")

    assert not (await chequear(sin_red)).ok
    assert not (await chequear(lambda req: httpx.Response(503))).ok
