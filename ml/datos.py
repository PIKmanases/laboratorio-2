"""Carga de datos para entrenar (CSV o base de datos) y lectura de la historia reciente para la API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .features import COLUMNAS_HISTORIA, HISTORIA_DIAS


@dataclass
class Datos:
    ventas: pd.DataFrame  # COLUMNAS_HISTORIA
    tiendas: pd.DataFrame
    feriados: pd.DataFrame
    costos: pd.DataFrame


def desde_csv(directorio: Path | str) -> Datos:
    d = Path(directorio)
    return Datos(
        ventas=pd.read_csv(d / "historico_ventas.csv", parse_dates=["fecha"])[COLUMNAS_HISTORIA],
        tiendas=pd.read_csv(d / "tiendas.csv"),
        feriados=pd.read_csv(d / "feriados.csv", parse_dates=["fecha"]),
        costos=pd.read_csv(d / "costos.csv"),
    )


def desde_db(engine: Engine, hasta: date | None = None) -> Datos:
    """Entrena sobre la vista `ventas_para_entrenamiento` (histórico + observaciones válidas).

    `hasta`: último día incluido. El DAG de reentrenamiento fija esta «foto» al preparar los
    datos para que todas sus tareas vean el mismo conjunto aunque lleguen observaciones nuevas."""
    with engine.connect() as c:
        leer = lambda sql, **p: pd.read_sql(text(sql), c, params=p or None)  # noqa: E731
        ventas = leer(
            "SELECT fecha, tienda_id, familia_id, ventas, en_promocion FROM ventas_para_entrenamiento "
            "WHERE CAST(:hasta AS date) IS NULL OR fecha <= CAST(:hasta AS date)",
            hasta=hasta,
        )
        return Datos(
            ventas=ventas.assign(fecha=pd.to_datetime(ventas["fecha"]))[COLUMNAS_HISTORIA],
            tiendas=leer("SELECT tienda_id, nombre, ciudad, departamento, tipo FROM tiendas"),
            feriados=leer("SELECT fecha, nombre, tipo, ambito FROM feriados").assign(fecha=lambda d: pd.to_datetime(d["fecha"])),
            costos=leer("SELECT familia_id, costo_faltante, costo_sobrante FROM costos"),
        )


def historia_reciente(engine: Engine, tienda_id: int, fecha: date, familias: list[str], dias: int = HISTORIA_DIAS) -> pd.DataFrame:
    """Ventas de la tienda en los `dias` ANTERIORES a `fecha` (nunca el día D ni posteriores)."""
    q = text(
        "SELECT fecha, tienda_id, familia_id, ventas, en_promocion FROM ventas_para_entrenamiento "
        "WHERE tienda_id = :t AND fecha < :f AND fecha >= :d AND familia_id = ANY(:fams)"
    )
    with engine.connect() as c:
        h = pd.read_sql(q, c, params={"t": tienda_id, "f": fecha, "d": fecha - timedelta(days=dias), "fams": familias})
    return h.assign(fecha=pd.to_datetime(h["fecha"]))[COLUMNAS_HISTORIA]
