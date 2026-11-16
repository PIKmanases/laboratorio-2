"""Predictores. `PredictorML` es el real (paso 5): historia reciente de app-db + modelo en
memoria. NUNCA contacta a MLflow: el modelo ya está cargado."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

from .esquemas import FamiliaSolicitada
from .modelo_mlflow import ModeloCargado


class ModeloNoDisponible(Exception):
    """Todavía no hay un modelo cargado (no hay `champion` registrado o MLflow no respondió al
    arrancar). La API contesta 503, igual que /health."""


@dataclass(frozen=True)
class Pronostico:
    familia_id: str
    venta_esperada: float
    cantidad_sugerida: int


@dataclass(frozen=True)
class ResultadoPredictor:
    version: str
    pronosticos: list[Pronostico]


class Predictor(Protocol):
    def predecir(self, tienda_id: int, fecha: date, familias: Sequence[FamiliaSolicitada]) -> ResultadoPredictor: ...


class PredictorML:
    """Arma la entrada del modelo: historia reciente (solo fechas < D, leída de app-db) + una
    fila por familia pedida con ventas = NaN y la en_promocion de la solicitud. Las features las
    calcula el propio modelo con la MISMA función del entrenamiento (`ml.features`)."""

    def __init__(
        self,
        leer_historia: Callable[[int, date, list[str]], pd.DataFrame],
        obtener_modelo: Callable[[], ModeloCargado | None],
    ):
        self._leer_historia = leer_historia
        self._obtener_modelo = obtener_modelo

    def predecir(self, tienda_id: int, fecha: date, familias: Sequence[FamiliaSolicitada]) -> ResultadoPredictor:
        modelo = self._obtener_modelo()
        if modelo is None:
            raise ModeloNoDisponible("no hay un modelo cargado")
        ids = [f.familia_id for f in familias]
        historia = self._leer_historia(tienda_id, fecha, ids)  # puede dar ErrorPersistencia -> 503
        pedido = pd.DataFrame(
            {
                "fecha": pd.Timestamp(fecha),
                "tienda_id": tienda_id,
                "familia_id": ids,
                "ventas": float("nan"),
                "en_promocion": [float(f.en_promocion) for f in familias],
            }
        )
        entrada = pd.concat([historia, pedido], ignore_index=True)
        entrada = entrada.astype({"ventas": "float64", "en_promocion": "float64"}).assign(fecha=lambda d: pd.to_datetime(d["fecha"]))
        salida = modelo.predecir(entrada).set_index("familia_id")
        return ResultadoPredictor(
            version=modelo.version,
            pronosticos=[
                Pronostico(f, float(salida.loc[f, "venta_esperada"]), int(salida.loc[f, "cantidad_sugerida"])) for f in ids
            ],
        )
