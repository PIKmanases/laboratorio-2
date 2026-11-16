"""Predictor falso (hasta el paso 5): promedio histórico por tienda y familia."""

import csv
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from itertools import accumulate
from pathlib import Path
from typing import Protocol

from .esquemas import FamiliaSolicitada


class Predictor(Protocol):
    version: str

    def predecir(self, tienda_id: int, fecha: date, familias: Sequence[FamiliaSolicitada]) -> dict[str, float]:
        """venta_esperada por familia_id."""


class PredictorPromedio:
    """Promedio de las ventas de (tienda, familia) en fechas ESTRICTAMENTE anteriores a `fecha`.

    Sin fuga de información: no mira el día pedido ni posteriores. Sin historia previa
    devuelve 0.0 (es un predictor de relleno; el modelo real decidirá qué hacer).
    """

    version = "0"

    def __init__(self, filas: Iterable[tuple[date, int, str, float]]):
        series: dict[tuple[int, str], list[tuple[date, float]]] = defaultdict(list)
        for fecha, tienda, familia, ventas in filas:
            series[(tienda, familia)].append((fecha, ventas))
        self._fechas: dict[tuple[int, str], list[date]] = {}
        self._acumulado: dict[tuple[int, str], list[float]] = {}
        for clave, valores in series.items():
            valores.sort()
            self._fechas[clave] = [f for f, _ in valores]
            self._acumulado[clave] = list(accumulate(v for _, v in valores))

    @classmethod
    def desde_csv(cls, ruta: Path) -> "PredictorPromedio":
        with open(ruta, newline="", encoding="utf-8") as f:
            filas = (
                (date.fromisoformat(r["fecha"]), int(r["tienda_id"]), r["familia_id"], float(r["ventas"]))
                for r in csv.DictReader(f)
            )
            return cls(filas)

    def predecir(self, tienda_id: int, fecha: date, familias: Sequence[FamiliaSolicitada]) -> dict[str, float]:
        resultado = {}
        for fam in familias:
            clave = (tienda_id, fam.familia_id)
            n = bisect_left(self._fechas.get(clave, []), fecha)  # cuántos días < fecha
            resultado[fam.familia_id] = self._acumulado[clave][n - 1] / n if n else 0.0
        return resultado
