"""Cantidad sugerida: problema newsvendor.

Con costo de faltante `cf` y de sobrante `cs`, la cantidad óptima es el cuantil
tau = cf / (cf + cs) de la distribución de la demanda. No hay una distribución explícita:
se estima con los ERRORES RELATIVOS OUT-OF-SAMPLE del modelo,

    r = (real - esperada) / max(esperada, 1)        (la escala crece con el nivel de la serie)
    q = cuantil_tau(r)                              (por familia x formato de tienda)
    cantidad = ceil(max(0, esperada + q * max(esperada, 1)))

Los factores `q` se calculan en CADA entrenamiento y viajan dentro del modelo.
Se usa `ceil` porque la demanda es entera: el cuantil discreto es el menor entero cuya
probabilidad acumulada alcanza tau, y eso es el techo del cuantil continuo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MIN_OBS_COLA = 15  # una celda solo tiene factor propio si hay >= 15 datos en la cola relevante


def tau_por_familia(costos: pd.DataFrame) -> dict[str, float]:
    return dict(zip(costos["familia_id"], costos["costo_faltante"] / (costos["costo_faltante"] + costos["costo_sobrante"]), strict=True))


def error_relativo(real: np.ndarray, esperada: np.ndarray) -> np.ndarray:
    return (real - esperada) / np.maximum(esperada, 1.0)


@dataclass
class Factores:
    tau: dict[str, float]
    q_familia: dict[str, float]
    q_celda: dict[str, dict[str, float]] = field(default_factory=dict)  # familia -> formato -> q
    usa_formato: bool = True

    def q(self, familia: str, formato: str | None) -> float:
        if self.usa_formato and formato is not None:
            q = self.q_celda.get(familia, {}).get(formato)
            if q is not None:
                return q
        return self.q_familia[familia]

    def cantidad(self, esperada: np.ndarray, familias: pd.Series, formatos: pd.Series) -> np.ndarray:
        q = np.array([self.q(f, t) for f, t in zip(familias, formatos, strict=True)])
        bruto = esperada + q * np.maximum(esperada, 1.0)
        return np.ceil(np.maximum(bruto, 0.0)).astype(int)

    def a_dict(self) -> dict:
        return {"tau": self.tau, "q_familia": self.q_familia, "q_celda": self.q_celda, "usa_formato": self.usa_formato}

    @classmethod
    def desde_dict(cls, d: dict) -> Factores:
        return cls(d["tau"], d["q_familia"], d["q_celda"], d["usa_formato"])


def calibrar(
    df: pd.DataFrame, costos: pd.DataFrame, por_formato: bool = True, min_obs_cola: int = MIN_OBS_COLA
) -> Factores:
    """`df`: columnas familia_id, tipo (formato), real, esperada (predicciones out-of-sample).

    Una celda (familia x formato) usa su propio cuantil solo si n * min(tau, 1 - tau) >= min_obs_cola
    (suficientes datos en la cola para que el cuantil sea estable); si no, hereda el de la familia.
    """
    tau = tau_por_familia(costos)
    r = error_relativo(df["real"].to_numpy(float), df["esperada"].to_numpy(float))
    df = df.assign(r=r)
    q_familia, q_celda = {}, {}
    for familia, t in tau.items():
        g = df[df["familia_id"] == familia]
        if g.empty:
            raise ValueError(f"sin datos de calibración para la familia {familia}")
        q_familia[familia] = float(np.quantile(g["r"], t))
        if por_formato:
            for formato, gf in g.groupby("tipo"):
                if len(gf) * min(t, 1 - t) >= min_obs_cola:
                    q_celda.setdefault(familia, {})[str(formato)] = float(np.quantile(gf["r"], t))
    return Factores(tau, q_familia, q_celda, por_formato)
