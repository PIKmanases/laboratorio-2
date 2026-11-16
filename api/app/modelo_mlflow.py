"""Carga del modelo vigente (alias `champion`) desde el Model Registry de MLflow."""

import logging
import os
from dataclasses import dataclass
from typing import Any

import pandas as pd

NOMBRE = "novamercado-ventas"
ALIAS = "champion"

logger = logging.getLogger("api.modelo")


@dataclass(frozen=True)
class ModeloCargado:
    nombre: str
    alias: str
    version: str
    _pyfunc: Any

    def predecir(self, historia: pd.DataFrame) -> pd.DataFrame:
        return self._pyfunc.predict(historia)


def cargar_modelo(tracking_uri: str, nombre: str = NOMBRE, alias: str = ALIAS) -> ModeloCargado:
    """Bloqueante. Se llama al arrancar (y, en el paso 7, desde el sondeo en segundo plano),
    jamás dentro de una predicción."""
    # Si MLflow está caído no debemos quedarnos minutos reintentando.
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "1")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "10")
    import mlflow
    from mlflow import MlflowClient

    mlflow.set_tracking_uri(tracking_uri)
    version = MlflowClient().get_model_version_by_alias(nombre, alias).version
    # Se carga por NÚMERO de versión (no por alias): si el alias se mueve entre las dos
    # llamadas, el número y el modelo cargado siguen coincidiendo.
    pyfunc = mlflow.pyfunc.load_model(f"models:/{nombre}/{version}")
    logger.info("modelo cargado: %s v%s (@%s)", nombre, version, alias)
    return ModeloCargado(nombre, alias, str(version), pyfunc)
