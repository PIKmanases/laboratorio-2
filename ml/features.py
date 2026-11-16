"""UNA sola función de features (`construir_features`), usada por el entrenamiento y por la API.

Regla de información (ver DECISIONES.md D9): al predecir el día D solo se conoce
  - las ventas reales hasta D-1,
  - la `en_promocion` del día D (viene en la solicitud); la de días anteriores NO se usa
    (no aportó y en días sin solicitud es un supuesto: ver DECISIONES.md D9),
  - el calendario de D (feriados.csv cubre fechas futuras),
  - los atributos fijos de la tienda y la familia.
Nada del día D ni posteriores se usa: todo lo que sale de ventas/promociones pasadas pasa por
`shift` de al menos 1 día sobre un calendario DIARIO completo, de modo que un día sin dato
(observación que no llegó, registro inválido por E08) queda en NaN en vez de "correrse".
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

HISTORIA_DIAS = 60  # lo que la API lee de la base: sobra para el mayor retroceso (28 días)
LAGS = (1, 7, 14, 21, 28)
MISMO_DIA_SEMANA = (7, 14, 21, 28)

COLUMNAS_HISTORIA = ["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"]
CATEGORICAS = ["tienda_id", "familia_id", "tipo", "ciudad", "departamento"]
NUMERICAS = [
    # calendario
    "dia_semana", "dia_mes", "mes", "semana_anio", "es_fin_de_semana",
    "es_feriado_nacional", "es_feriado_local", "es_medio_dia", "es_comercial", "es_pago",
    "es_vispera_feriado", "es_dia_post_feriado",
    # promoción
    "en_promocion",
    # historia de la serie (tienda x familia)
    "lag_1", "lag_7", "lag_14", "lag_21", "lag_28", "media_mismo_dia_4",
    "media_7", "media_28", "std_28", "ratio_7_28", "n_obs_28",
]  # fmt: skip
FEATURES = NUMERICAS + CATEGORICAS


def _calendario(fechas: pd.Series, ciudades: pd.Series, feriados: pd.DataFrame) -> pd.DataFrame:
    """Banderas de calendario para cada fila (fecha, ciudad de la tienda)."""
    f = feriados.assign(fecha=pd.to_datetime(feriados["fecha"]))
    nacional = f[f["ambito"] == "nacional"]
    local = f[(f["ambito"] != "nacional") & (f["tipo"] == "feriado")]  # ámbito = ciudad

    def fechas_de(tipo: str) -> set:
        return set(nacional.loc[nacional["tipo"] == tipo, "fecha"])

    feriado_nac = fechas_de("feriado")
    locales = set(zip(local["fecha"], local["ambito"], strict=True))
    es_nac = fechas.isin(feriado_nac)
    es_loc = pd.Series([(d, c) in locales for d, c in zip(fechas, ciudades, strict=True)], index=fechas.index)
    manana, ayer = fechas + pd.Timedelta(days=1), fechas - pd.Timedelta(days=1)
    vispera = manana.isin(feriado_nac) | pd.Series([(d, c) in locales for d, c in zip(manana, ciudades, strict=True)], index=fechas.index)
    post = ayer.isin(feriado_nac) | pd.Series([(d, c) in locales for d, c in zip(ayer, ciudades, strict=True)], index=fechas.index)
    return pd.DataFrame(
        {
            "dia_semana": fechas.dt.day_of_week,
            "dia_mes": fechas.dt.day,
            "mes": fechas.dt.month,
            "semana_anio": fechas.dt.isocalendar().week.astype(int),
            "es_fin_de_semana": (fechas.dt.day_of_week >= 5).astype(int),
            "es_feriado_nacional": es_nac.astype(int),
            "es_feriado_local": es_loc.astype(int),
            "es_medio_dia": fechas.isin(fechas_de("medio_dia")).astype(int),
            "es_comercial": fechas.isin(fechas_de("comercial")).astype(int),
            "es_pago": fechas.isin(fechas_de("pago")).astype(int),
            "es_vispera_feriado": vispera.astype(int),
            "es_dia_post_feriado": post.astype(int),
        },
        index=fechas.index,
    )


def construir_features(historia: pd.DataFrame, tiendas: pd.DataFrame, feriados: pd.DataFrame) -> pd.DataFrame:
    """Devuelve las `FEATURES` de CADA fila de `historia`, en el mismo orden e índice.

    `historia`: columnas COLUMNAS_HISTORIA. Las filas cuyas `ventas` son NaN son las que se
    quieren predecir (la API las agrega con la `en_promocion` de la solicitud); las demás son
    historia. En el entrenamiento todas las filas traen ventas y cada una se trata como si
    fuera la que se predice: sus features solo miran días anteriores.

    `tiendas`: tienda_id, ciudad, departamento, tipo. `feriados`: fecha, nombre, tipo, ambito.
    """
    df = historia[COLUMNAS_HISTORIA].copy()
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.normalize()
    if df.duplicated(["tienda_id", "familia_id", "fecha"]).any():
        raise ValueError("historia con (tienda, familia, fecha) repetidos")
    df = df.reset_index(drop=True)

    # Calendario DIARIO completo (los huecos quedan NaN) para que shift(k) sea "k días atrás".
    dias = pd.date_range(df["fecha"].min(), df["fecha"].max(), freq="D")
    claves = pd.MultiIndex.from_frame(df[["tienda_id", "familia_id"]])
    ventas = df.pivot(index="fecha", columns=["tienda_id", "familia_id"], values="ventas").reindex(dias).astype("float64")

    v1 = ventas.shift(1)
    mismo_dia = np.stack([ventas.shift(k).to_numpy() for k in MISMO_DIA_SEMANA])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # nanmean de todo-NaN -> NaN
        media_mismo_dia = np.nanmean(mismo_dia, axis=0)
    r7 = v1.rolling(7, min_periods=3).mean()
    r28 = v1.rolling(28, min_periods=7).mean()
    anchos: dict[str, pd.DataFrame | np.ndarray] = {f"lag_{k}": ventas.shift(k) for k in LAGS}
    anchos |= {
        "media_mismo_dia_4": media_mismo_dia,
        "media_7": r7,
        "media_28": r28,
        "std_28": v1.rolling(28, min_periods=7).std(),
        "ratio_7_28": r7 / r28.where(r28 > 0),
        "n_obs_28": v1.rolling(28, min_periods=1).count().fillna(0),
    }

    fila = dias.get_indexer(df["fecha"])
    col = ventas.columns.get_indexer(claves)
    out = pd.DataFrame(index=df.index)
    for nombre, ancho in anchos.items():
        out[nombre] = np.asarray(ancho)[fila, col]

    atributos = tiendas[["tienda_id", "ciudad", "departamento", "tipo"]]
    fijos = df[["tienda_id", "familia_id"]].merge(atributos, on="tienda_id", how="left")
    cal = _calendario(df["fecha"], fijos["ciudad"], feriados)
    out = pd.concat([out, cal, fijos[["ciudad", "departamento", "tipo"]]], axis=1)
    out["en_promocion"] = df["en_promocion"].astype("float64")
    out["tienda_id"] = df["tienda_id"]
    out["familia_id"] = df["familia_id"]
    out = out[FEATURES]
    out.index = historia.index
    return out
