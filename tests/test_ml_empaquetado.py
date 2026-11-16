"""Empaquetado pyfunc + registro en MLflow, sobre una base SQLite local (sin servidor ni Docker)."""

import mlflow
import numpy as np
import pandas as pd
import pytest
from mlflow import MlflowClient

from ml import registro
from ml.entrenar import entrenar
from sinteticos import datos_sinteticos

RAPIDO = {"num_boost_round": 20, "num_threads": 1}


@pytest.fixture
def mlflow_local(tmp_path):
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/mlflow.db")
    yield
    mlflow.set_tracking_uri("")


def test_registrar_crea_runs_version_y_alias_y_el_modelo_se_carga_y_predice(mlflow_local):
    d = datos_sinteticos()
    res = [entrenar(d, tipo=t, params=RAPIDO, dias_calibracion=20, dias_prueba=20) for t in ("baseline", "lightgbm")]
    salida = registro.registrar(res)

    c = MlflowClient()
    exp = c.get_experiment_by_name(registro.EXPERIMENTO)
    runs = c.search_runs([exp.experiment_id])
    assert {r.info.run_name for r in runs} == {"baseline_seasonal_naive", "lightgbm_global"}  # runs separados
    m = {r.info.run_name: r.data.metrics for r in runs}
    assert {"mae", "rmse", "wape", "sesgo", "costo_total", "pct_dias_sin_faltante", "ahorro_vs_esperada_pct"} <= set(m["lightgbm_global"])

    mv = c.get_model_version_by_alias(registro.MODELO_REGISTRADO, registro.ALIAS)
    assert str(mv.version) == salida["version"] == "1"
    ganador = min(m, key=lambda n: m[n]["costo_total"])
    assert salida["ganador"] == ganador

    modelo = mlflow.pyfunc.load_model(f"models:/{registro.MODELO_REGISTRADO}/{mv.version}")
    dia = pd.Timestamp("2025-05-20")
    entrada = pd.concat(
        [d.ventas[d.ventas.fecha < dia].tail(400),
         pd.DataFrame({"fecha": [dia], "tienda_id": [2], "familia_id": ["lacteos"], "ventas": [np.nan], "en_promocion": [1.0]})],
        ignore_index=True,
    )  # fmt: skip
    out = modelo.predict(entrada)
    assert list(out.columns) == ["tienda_id", "familia_id", "fecha", "venta_esperada", "cantidad_sugerida"] and len(out) == 1
    assert out["cantidad_sugerida"].iloc[0] >= 0


def test_un_segundo_registro_crea_otra_version_y_mueve_el_alias(mlflow_local):
    d = datos_sinteticos()
    res = [entrenar(d, tipo="baseline", dias_calibracion=20, dias_prueba=20)]
    registro.registrar(res)
    registro.registrar(res)
    c = MlflowClient()
    assert str(c.get_model_version_by_alias(registro.MODELO_REGISTRADO, registro.ALIAS).version) == "2"
