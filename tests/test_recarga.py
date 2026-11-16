"""Sondeo del alias y recarga en caliente (paso 7). Sin MLflow: consulta y carga son funciones falsas."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.limpieza import Catalogos
from app.main import create_app
from app.modelo_mlflow import ModeloCargado
from app.recarga import SondeoModelo
from conftest import FAMILIAS
from fakes import AirflowFalso, PredictorPromedio, ReentrenamientosMemoria, RepositorioMemoria


def modelo(version: str) -> ModeloCargado:
    return ModeloCargado("novamercado-ventas", "champion", version, object())


class RegistroFalso:
    """Imita al Model Registry: a qué versión apunta el alias y qué pasa al consultarlo/cargarlo."""

    def __init__(self, version="1"):
        self.version = version
        self.caido = False
        self.carga_falla = False
        self.cargas: list[str] = []

    def consultar(self):
        if self.caido:
            raise ConnectionError("MLflow caído (simulado)")
        return self.version

    def cargar(self, version):
        if self.caido or self.carga_falla:
            raise RuntimeError("no se pudo cargar (simulado)")
        self.cargas.append(version)
        return modelo(version)


def sondeo(estado, reg, **kw):
    return SondeoModelo(estado, reg.consultar, reg.cargar, **kw)


def correr(coro):
    return asyncio.run(coro)


def test_adopta_la_version_nueva_cuando_el_alias_se_mueve():
    estado, reg = SimpleNamespace(model=modelo("1")), RegistroFalso("2")
    assert correr(sondeo(estado, reg).sondear_una_vez()) is True
    assert estado.model.version == "2" and reg.cargas == ["2"]


def test_si_el_alias_no_cambio_no_vuelve_a_cargar():
    estado, reg = SimpleNamespace(model=modelo("1")), RegistroFalso("1")
    assert correr(sondeo(estado, reg).sondear_una_vez()) is False
    assert reg.cargas == []


def test_no_recarga_en_cada_ciclo_una_version_ya_adoptada():
    estado, reg = SimpleNamespace(model=modelo("1")), RegistroFalso("2")
    s = sondeo(estado, reg)
    correr(s.sondear_una_vez())
    correr(s.sondear_una_vez())
    assert reg.cargas == ["2"]


def test_con_mlflow_caido_conserva_el_modelo_en_memoria():
    previo = modelo("1")
    estado, reg = SimpleNamespace(model=previo), RegistroFalso("2")
    reg.caido = True
    assert correr(sondeo(estado, reg).sondear_una_vez()) is False
    assert estado.model is previo


def test_si_la_carga_falla_conserva_el_modelo_y_reintenta_en_el_siguiente_ciclo():
    previo = modelo("1")
    estado, reg = SimpleNamespace(model=previo), RegistroFalso("2")
    s = sondeo(estado, reg)
    reg.carga_falla = True
    assert correr(s.sondear_una_vez()) is False and estado.model is previo
    reg.carga_falla = False
    assert correr(s.sondear_una_vez()) is True and estado.model.version == "2"


def test_si_arranco_sin_modelo_lo_carga_cuando_mlflow_regresa():
    estado, reg = SimpleNamespace(model=None), RegistroFalso("1")
    reg.caido = True
    s = sondeo(estado, reg)
    assert correr(s.sondear_una_vez()) is False and estado.model is None
    reg.caido = False
    assert correr(s.sondear_una_vez()) is True and estado.model.version == "1"


def test_una_consulta_colgada_no_bloquea_ni_cambia_el_modelo():
    previo = modelo("1")
    estado = SimpleNamespace(model=previo)
    s = SondeoModelo(estado, lambda: time.sleep(0.5) or "2", lambda v: modelo(v), timeout_consulta=0.05)

    async def medir():  # dentro del loop: asyncio.run() esperaría después al hilo colgado
        t0 = time.monotonic()
        return await s.sondear_una_vez(), time.monotonic() - t0

    adoptado, segundos = correr(medir())
    assert adoptado is False and segundos < 0.4 and estado.model is previo


def test_run_sondea_periodicamente_y_se_puede_cancelar():
    estado, reg = SimpleNamespace(model=modelo("1")), RegistroFalso("1")

    async def escenario():
        tarea = asyncio.create_task(sondeo(estado, reg, intervalo=0.02).run())
        await asyncio.sleep(0.05)
        reg.version = "2"  # el DAG promovió un modelo
        await asyncio.sleep(0.15)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea

    correr(escenario())
    assert estado.model.version == "2"


def test_la_api_responde_con_el_modelo_nuevo_sin_reiniciar():
    app = create_app(catalogos=Catalogos.desde_filas(range(1, 11), FAMILIAS), predictor=PredictorPromedio([]),
                     repositorio=RepositorioMemoria(), airflow=AirflowFalso(), reentrenamientos=ReentrenamientosMemoria(),
                     modelo=modelo("1"))  # fmt: skip
    client, reg = TestClient(app), RegistroFalso("2")
    assert client.get("/modelo").json()["version"] == "1"
    correr(sondeo(app.state, reg).sondear_una_vez())
    assert client.get("/modelo").json() == {"nombre": "novamercado-ventas", "alias": "champion", "version": "2"}
