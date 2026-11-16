"""PredictorML y su conexión con la API, con un modelo y una historia falsos (sin MLflow ni Postgres)."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.limpieza import Catalogos
from app.main import create_app
from app.modelo_mlflow import ModeloCargado
from app.predictor import ModeloNoDisponible, PredictorML
from conftest import FAMILIAS
from fakes import RepositorioMemoria
from app.esquemas import FamiliaSolicitada
from datetime import date


class PyfuncFalso:
    def __init__(self):
        self.entradas = []

    def predict(self, df):
        self.entradas.append(df)
        objetivo = df[df["ventas"].isna()]
        return pd.DataFrame({"tienda_id": objetivo["tienda_id"].to_numpy(), "familia_id": objetivo["familia_id"].to_numpy(),
                             "fecha": objetivo["fecha"].to_numpy(), "venta_esperada": 10.5, "cantidad_sugerida": 12})  # fmt: skip


def historia(*_):
    return pd.DataFrame({"fecha": pd.to_datetime(["2025-11-15", "2025-11-16"]), "tienda_id": [5, 5], "familia_id": ["abarrotes"] * 2,
                         "ventas": [100, 120], "en_promocion": [0, 1]})  # fmt: skip


def modelo(py=None):
    return ModeloCargado("novamercado-ventas", "champion", "3", py or PyfuncFalso())


def test_arma_historia_mas_filas_a_pronosticar_con_la_promocion_de_la_solicitud():
    py = PyfuncFalso()
    p = PredictorML(historia, lambda: modelo(py))
    r = p.predecir(5, date(2025, 11, 17), [FamiliaSolicitada(familia_id="abarrotes", en_promocion=4), FamiliaSolicitada(familia_id="lacteos", en_promocion=0)])
    (entrada,) = py.entradas
    objetivo = entrada[entrada["ventas"].isna()]
    assert len(entrada) == 4 and set(objetivo["familia_id"]) == {"abarrotes", "lacteos"}
    assert (objetivo["fecha"] == pd.Timestamp("2025-11-17")).all()
    assert objetivo.set_index("familia_id")["en_promocion"].to_dict() == {"abarrotes": 4.0, "lacteos": 0.0}
    assert entrada.loc[entrada["ventas"].notna(), "fecha"].max() < pd.Timestamp("2025-11-17")  # solo días < D
    assert r.version == "3" and [(x.familia_id, x.venta_esperada, x.cantidad_sugerida) for x in r.pronosticos] == [("abarrotes", 10.5, 12), ("lacteos", 10.5, 12)]


def test_historia_vacia_tambien_funciona():
    vacia = lambda *_: pd.DataFrame(columns=["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"])  # noqa: E731
    r = PredictorML(vacia, lambda: modelo()).predecir(5, date(2025, 11, 17), [FamiliaSolicitada(familia_id="abarrotes", en_promocion=0)])
    assert r.pronosticos[0].cantidad_sugerida == 12


def test_sin_modelo_lanza_modelo_no_disponible():
    with pytest.raises(ModeloNoDisponible):
        PredictorML(historia, lambda: None).predecir(5, date(2025, 11, 17), [FamiliaSolicitada(familia_id="abarrotes", en_promocion=0)])


def api(m):
    cat = Catalogos.desde_filas(range(1, 11), FAMILIAS)
    app = create_app(catalogos=cat, predictor=PredictorML(historia, lambda: app.state.model), repositorio=RepositorioMemoria(), modelo=m)
    return TestClient(app)


BODY = {"tienda_id": 5, "fecha": "2025-11-17", "familias": [{"familia_id": "abarrotes", "en_promocion": 2}]}


def test_api_responde_con_la_version_del_modelo():
    r = api(modelo()).post("/v1/prediccion", json=BODY)
    j = r.json()
    assert r.status_code == 200 and j["version_modelo"] == "3"
    assert j["predicciones"] == [{"familia_id": "abarrotes", "venta_esperada": 10.5, "cantidad_sugerida": 12}]


def test_api_sin_modelo_responde_503_y_modelo_tambien():
    c = api(None)
    assert c.post("/v1/prediccion", json=BODY).status_code == 503
    assert c.get("/modelo").status_code == 503


def test_endpoint_modelo():
    assert api(modelo()).get("/modelo").json() == {"nombre": "novamercado-ventas", "alias": "champion", "version": "3"}
