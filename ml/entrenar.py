"""Entrenamiento reutilizable: `entrenar(datos, fecha_corte)` y el comando `python -m ml.entrenar`.

Validación SIEMPRE temporal (nunca al azar). Tres bloques consecutivos:

    |------------ entrenamiento ------------|-- calibración --|----- prueba -----|
                                      fecha_corte        +dias_calibracion        fin

  - El modelo se ajusta con fecha < fecha_corte.
  - En la CALIBRACIÓN se estiman los factores newsvendor (cuantiles de los errores relativos).
  - En la PRUEBA se miden todas las métricas, con factores que no vieron esos días.
  - Las features de cada día solo usan días anteriores (ver features.py), así que predecir
    el bloque de prueba con la historia real reciente es exactamente lo que hace la API.
  - El modelo que se publica se REAJUSTA con todos los datos; sus factores salen de los errores
    out-of-sample de calibración + prueba (más datos -> cuantiles más estables).
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from . import metricas
from .datos import Datos
from .features import FEATURES, construir_features
from .modelos import MODELOS, Pronosticador
from .newsvendor import calibrar

logger = logging.getLogger("ml.entrenar")

DIAS_CALIBRACION = 56
DIAS_PRUEBA = 56


@dataclass
class Resultado:
    nombre: str
    modelo: Pronosticador  # ya reajustado con todos los datos y con sus factores
    metricas: dict[str, float]  # sobre la ventana de PRUEBA
    parametros: dict[str, Any]


def ventanas(fin: pd.Timestamp, fecha_corte: date | None, dias_calibracion: int, dias_prueba: int):
    """(corte, fin_calibracion): el bloque de prueba es [fin_calibracion, fin]."""
    corte = pd.Timestamp(fecha_corte) if fecha_corte else fin - pd.Timedelta(days=dias_calibracion + dias_prueba - 1)
    return corte, corte + pd.Timedelta(days=dias_calibracion)


def validar_ids(datos: Datos) -> None:
    """Los `familia_id` / `tienda_id` de ventas deben ser EXACTAMENTE los de costos y tiendas.

    Un id escrito distinto («panadería» vs «panaderia») no daría error: la familia quedaría sin
    factor, con costo NaN o como categoría desconocida para LightGBM. Se exige coincidencia."""
    fam_v, fam_c = set(datos.ventas["familia_id"]), set(datos.costos["familia_id"])
    if fam_v != fam_c:
        raise ValueError(f"familia_id distintos entre ventas y costos: solo en ventas={sorted(fam_v - fam_c)}, solo en costos={sorted(fam_c - fam_v)}")
    sin_tienda = set(datos.ventas["tienda_id"]) - set(datos.tiendas["tienda_id"])
    if sin_tienda:
        raise ValueError(f"tienda_id en ventas que no existen en tiendas: {sorted(sin_tienda)}")


def calcular_metricas(ev: pd.DataFrame, costos: pd.DataFrame) -> dict[str, float]:
    """`ev`: familia_id, real, esperada, sugerida. Técnicas (sobre esperada) + negocio (sobre sugerida)."""
    return metricas.tecnicas(ev["real"].to_numpy(), ev["esperada"].to_numpy()) | metricas.negocio(ev, costos)


def evaluar_modelo(pron: Pronosticador, datos: Datos, desde: date | pd.Timestamp, hasta: date | pd.Timestamp | None = None) -> dict[str, float]:
    """Evalúa un modelo YA ENTRENADO (p. ej. el vigente) en [desde, hasta] con sus propios factores.

    Las features de cada día usan solo días anteriores de `datos`, como en producción. Quien llama
    es responsable de que el modelo no haya entrenado con esa ventana (ver `ml.reentrenamiento`)."""
    X = construir_features(datos.ventas, datos.tiendas, datos.feriados)
    fecha = pd.to_datetime(datos.ventas["fecha"])
    m = fecha >= pd.Timestamp(desde)
    if hasta is not None:
        m &= fecha <= pd.Timestamp(hasta)
    if not m.any():
        raise ValueError("la ventana de evaluación no tiene datos")
    Xm = X[m]
    esperada = pron.modelo.esperada(Xm)
    ev = pd.DataFrame(
        {"familia_id": Xm["familia_id"], "real": datos.ventas.loc[m, "ventas"].astype(float),
         "esperada": esperada, "sugerida": pron.factores.cantidad(esperada, Xm["familia_id"], Xm["tipo"])}
    )  # fmt: skip
    return calcular_metricas(ev, datos.costos) | {"n_prueba": float(len(ev))}


def _ajustar(tipo: str, X: pd.DataFrame, y: pd.Series, fecha: pd.Series, params: dict | None):
    modelo = MODELOS[tipo](params) if tipo == "lightgbm" else MODELOS[tipo]()
    return modelo.ajustar(X, y, fecha)


def entrenar(
    datos: Datos,
    fecha_corte: date | None = None,
    tipo: str = "lightgbm",
    params: dict | None = None,
    dias_calibracion: int = DIAS_CALIBRACION,
    dias_prueba: int = DIAS_PRUEBA,
    por_formato: bool = True,
) -> Resultado:
    validar_ids(datos)
    X = construir_features(datos.ventas, datos.tiendas, datos.feriados)
    y = datos.ventas["ventas"].astype(float)
    fecha = pd.to_datetime(datos.ventas["fecha"])
    corte, fin_cal = ventanas(fecha.max(), fecha_corte, dias_calibracion, dias_prueba)
    entr, cal, prueba = fecha < corte, (fecha >= corte) & (fecha < fin_cal), fecha >= fin_cal
    if not (entr.any() and cal.any() and prueba.any()):
        raise ValueError(f"ventanas vacías (corte={corte.date()}, fin calibración={fin_cal.date()}, datos hasta {fecha.max().date()})")

    # 1) ajuste con el pasado, predicción out-of-sample de calibración y prueba
    m1 = _ajustar(tipo, X[entr], y[entr], fecha[entr], params)

    def predicho(mascara: pd.Series) -> pd.DataFrame:
        return pd.DataFrame(
            {"familia_id": X.loc[mascara, "familia_id"], "tipo": X.loc[mascara, "tipo"],
             "real": y[mascara], "esperada": m1.esperada(X[mascara])}
        )  # fmt: skip

    p_cal, p_prueba = predicho(cal), predicho(prueba)

    # 2) factores con la calibración; métricas honestas sobre la prueba
    f_cal = calibrar(p_cal, datos.costos, por_formato)
    sugerida = f_cal.cantidad(p_prueba["esperada"].to_numpy(), p_prueba["familia_id"], p_prueba["tipo"])
    ev = p_prueba.assign(sugerida=sugerida)
    m = calcular_metricas(ev, datos.costos)
    m |= {"n_prueba": float(len(ev)), "n_calibracion": float(len(p_cal)), "n_entrenamiento": float(entr.sum())}

    # 3) modelo publicado: reajuste con todo y factores con calibración + prueba (out-of-sample)
    final = _ajustar(tipo, X, y, fecha, params)
    factores = calibrar(pd.concat([p_cal, p_prueba]), datos.costos, por_formato)
    nombre = {"baseline": "baseline_seasonal_naive", "lightgbm": "lightgbm_global"}[tipo]
    info = {
        "fecha_corte": str(corte.date()), "fin_calibracion": str(fin_cal.date()), "ultimo_dia": str(fecha.max().date()),
        "dias_calibracion": dias_calibracion, "dias_prueba": dias_prueba, "por_formato": por_formato,
        "n_filas": len(X), "features": FEATURES, "factores_formato": sum(len(v) for v in factores.q_celda.values()),
    }  # fmt: skip
    if tipo == "lightgbm":
        info["params"] = {**final.params}
    pron = Pronosticador(final, factores, datos.tiendas, datos.feriados, {"nombre": nombre, **info})
    return Resultado(nombre, pron, m, {"tipo": tipo, **info})


def main() -> None:
    from . import registro

    ap = argparse.ArgumentParser(description="Entrena baseline y LightGBM, registra en MLflow y promueve el ganador.")
    ap.add_argument("--fuente", choices=["db", "csv"], default="db", help="db: vista ventas_para_entrenamiento (DATABASE_URL); csv: carpeta DATA_DIR")
    ap.add_argument("--fecha-corte", type=date.fromisoformat, default=None)
    ap.add_argument("--sin-mlflow", action="store_true", help="solo entrena y muestra las métricas")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.fuente == "db":
        from sqlalchemy import create_engine

        from .datos import desde_db

        datos = desde_db(create_engine(os.environ["DATABASE_URL"]))
    else:
        from .datos import desde_csv

        datos = desde_csv(os.environ.get("DATA_DIR", "data"))
    resultados = [entrenar(datos, args.fecha_corte, t) for t in ("baseline", "lightgbm")]
    print(registro.tabla_metricas(resultados))
    if not args.sin_mlflow:
        print(registro.registrar(resultados))


if __name__ == "__main__":
    main()
