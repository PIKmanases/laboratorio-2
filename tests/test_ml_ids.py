"""Consistencia de los familia_id: el modelo, los factores y la API usan exactamente el canónico."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.limpieza import Catalogos
from app.main import create_app
from app.modelo_mlflow import ModeloCargado
from app.predictor import PredictorML
from conftest import FAMILIAS
from fakes import RepositorioMemoria
from ml.entrenar import entrenar, validar_ids
from sinteticos import datos_sinteticos

RAPIDO = {"num_boost_round": 20, "num_threads": 1}


def test_factores_plan_b_y_categorias_usan_el_id_canonico_de_costos():
    d = datos_sinteticos()
    canon = set(d.costos["familia_id"])
    r = entrenar(d, tipo="lightgbm", params=RAPIDO, dias_calibracion=20, dias_prueba=20)
    f = r.modelo.factores
    assert set(f.tau) == set(f.q_familia) == canon
    assert set(f.q_celda) <= canon  # las celdas con pocos datos heredan el factor de la familia
    assert set(r.modelo.modelo.plan_b.f["familia_id"]) == set(r.modelo.modelo.plan_b.fdt["familia_id"]) == canon
    assert set(r.modelo.modelo.categorias["familia_id"]) == canon
    assert {k.removeprefix("costo_familia_") for k in r.metricas if k.startswith("costo_familia_")} == canon


def test_entrenar_rechaza_ids_distintos_entre_ventas_y_costos():
    d = datos_sinteticos()
    d.ventas["familia_id"] = d.ventas["familia_id"].replace({"lacteos": "Lácteos"})
    with pytest.raises(ValueError, match="familia_id distintos"):
        validar_ids(d)
    with pytest.raises(ValueError):
        entrenar(d, tipo="baseline", dias_calibracion=20, dias_prueba=20)


def test_entrenar_rechaza_tienda_que_no_existe():
    d = datos_sinteticos()
    d.ventas.loc[0, "tienda_id"] = 99
    with pytest.raises(ValueError, match="tienda_id"):
        validar_ids(d)


def test_predecir_con_id_no_canonico_falla_fuerte_no_en_silencio():
    d = datos_sinteticos()
    r = entrenar(d, tipo="lightgbm", params=RAPIDO, dias_calibracion=20, dias_prueba=20)
    pedido = pd.DataFrame({"fecha": [pd.Timestamp("2025-06-01")], "tienda_id": [1], "familia_id": ["Lácteos"], "ventas": [np.nan], "en_promocion": [0.0]})
    with pytest.raises(ValueError, match="sin factor"):
        r.modelo.predecir(pedido)


def test_la_api_entrega_al_modelo_el_id_canonico_aunque_llegue_escrito_distinto():
    """«LÁCTEOS » y «Frutas y Verduras» se limpian ANTES de llegar al modelo."""
    vistos = []

    class Py:
        def predict(self, df):
            vistos.append(set(df.loc[df["ventas"].isna(), "familia_id"]))
            o = df[df["ventas"].isna()]
            return pd.DataFrame({"tienda_id": o["tienda_id"], "familia_id": o["familia_id"], "fecha": o["fecha"], "venta_esperada": 1.0, "cantidad_sugerida": 1})

    vacia = lambda *_: pd.DataFrame(columns=["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"])  # noqa: E731
    app = create_app(catalogos=Catalogos.desde_filas(range(1, 11), FAMILIAS), repositorio=RepositorioMemoria(), modelo=ModeloCargado("m", "champion", "1", Py()))
    app.state.predictor = PredictorML(vacia, lambda: app.state.model)
    body = {"tienda_id": 5, "fecha": "2025-11-17", "familias": [{"familia_id": "LÁCTEOS ", "en_promocion": 0}, {"familia_id": "Frutas y Verduras", "en_promocion": 0}, {"familia_id": "Panadería", "en_promocion": 0}]}
    r = TestClient(app).post("/v1/prediccion", json=body)
    assert r.status_code == 200
    assert vistos == [{"lacteos", "frutas_verduras", "panaderia"}]
    assert [p["familia_id"] for p in r.json()["predicciones"]] == ["lacteos", "frutas_verduras", "panaderia"]
