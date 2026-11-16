"""Métricas técnicas (sobre venta_esperada) y de negocio (sobre cantidad_sugerida)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def tecnicas(real: np.ndarray, esperada: np.ndarray) -> dict[str, float]:
    err = esperada - real
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
        "wape": float(np.sum(np.abs(err)) / np.sum(real)),  # sirve aunque haya ventas en cero
        "sesgo": float(np.mean(err)),  # > 0: pronostica de MÁS; < 0: de menos
        "sesgo_rel": float(np.sum(err) / np.sum(real)),
    }


def costo(real: np.ndarray, sugerida: np.ndarray, cf: np.ndarray, cs: np.ndarray) -> np.ndarray:
    """costo = c_faltante * max(real - sugerido, 0) + c_sobrante * max(sugerido - real, 0)"""
    return cf * np.maximum(real - sugerida, 0) + cs * np.maximum(sugerida - real, 0)


def negocio(df: pd.DataFrame, costos: pd.DataFrame) -> dict[str, float]:
    """`df`: familia_id, real, esperada, sugerida (enteros).

    Devuelve el costo con la regla de cuantiles frente al costo de usar sugerida = esperada
    redondeada: la diferencia es lo que aporta el newsvendor.
    """
    c = costos.set_index("familia_id")
    cf = df["familia_id"].map(c["costo_faltante"]).to_numpy(float)
    cs = df["familia_id"].map(c["costo_sobrante"]).to_numpy(float)
    real = df["real"].to_numpy(float)
    sug = df["sugerida"].to_numpy(float)
    ingenua = np.maximum(np.round(df["esperada"].to_numpy(float)), 0)
    costo_regla, costo_ingenua = costo(real, sug, cf, cs), costo(real, ingenua, cf, cs)
    out = {
        "costo_total": float(costo_regla.sum()),
        "costo_medio_dia": float(costo_regla.mean()),
        "costo_total_sugerida_igual_esperada": float(costo_ingenua.sum()),
        "ahorro_vs_esperada_pct": float(100 * (1 - costo_regla.sum() / costo_ingenua.sum())),
        "pct_dias_sin_faltante": float(100 * np.mean(sug >= real)),
        "pct_dias_sin_faltante_esperada": float(100 * np.mean(ingenua >= real)),
    }
    por_familia = pd.Series(costo_regla).groupby(df["familia_id"].to_numpy()).sum()
    out |= {f"costo_familia_{f}": float(v) for f, v in por_familia.items()}
    return out
