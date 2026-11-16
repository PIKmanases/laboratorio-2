"""Experimentos y Model Registry en MLflow."""

from __future__ import annotations

import mlflow
import pandas as pd
from mlflow import MlflowClient

from .empaquetado import guardar_en_mlflow
from .entrenar import Resultado

EXPERIMENTO = "novamercado-pronostico"
MODELO_REGISTRADO = "novamercado-ventas"
ALIAS = "champion"
CRITERIO = "costo_total"  # menor costo de negocio en la ventana de prueba


def tabla_metricas(resultados: list[Resultado]) -> str:
    cols = ["mae", "rmse", "wape", "sesgo", "costo_total", "costo_total_sugerida_igual_esperada", "ahorro_vs_esperada_pct", "pct_dias_sin_faltante"]
    return pd.DataFrame({r.nombre: {c: r.metricas[c] for c in cols} for r in resultados}).round(3).to_string()


def log_resultado(r: Resultado, run_name: str | None = None) -> tuple[str, str]:
    """Registra UN run (params, métricas de prueba, factores, features y el modelo pyfunc).
    Devuelve (run_id, model_uri). No toca el Model Registry."""
    with mlflow.start_run(run_name=run_name or r.nombre) as run:
        mlflow.log_params({k: v for k, v in r.parametros.items() if k not in ("features", "params")})
        mlflow.log_param("n_features", len(r.parametros["features"]))
        mlflow.log_params({f"lgbm_{k}": v for k, v in r.parametros.get("params", {}).items()})
        mlflow.log_metrics(r.metricas)
        mlflow.log_dict(r.modelo.factores.a_dict(), "factores_newsvendor.json")
        mlflow.log_dict({"features": r.parametros["features"]}, "features.json")
        info = guardar_en_mlflow(r.modelo)
        return run.info.run_id, info.model_uri


def registrar(resultados: list[Resultado], promover: bool = True) -> dict:
    """Un run por modelo; el de menor costo de prueba se registra como una versión de
    `novamercado-ventas` y recibe el alias `champion`."""
    mlflow.set_experiment(EXPERIMENTO)
    corridas = {}
    for r in resultados:
        run_id, uri = log_resultado(r)
        corridas[r.nombre] = (run_id, uri, r.metricas[CRITERIO])
    ganador = min(corridas, key=lambda n: corridas[n][2])
    salida = {"ganador": ganador, "runs": {n: c[0] for n, c in corridas.items()}}
    if promover:
        version = mlflow.register_model(corridas[ganador][1], MODELO_REGISTRADO)
        MlflowClient().set_registered_model_alias(MODELO_REGISTRADO, ALIAS, version.version)
        salida |= {"modelo": MODELO_REGISTRADO, "version": str(version.version), "alias": ALIAS}
    return salida
