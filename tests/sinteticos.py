"""Datos sintéticos pequeños para probar `ml/` sin depender de data/ (que no está en git)."""

import numpy as np
import pandas as pd

from ml.datos import Datos

FAMILIAS = ["abarrotes", "lacteos"]


def datos_sinteticos(dias: int = 150, seed: int = 0) -> Datos:
    rng = np.random.default_rng(seed)
    fechas = pd.date_range("2025-01-01", periods=dias)
    filas = []
    for t, base in ((1, 300), (2, 80)):
        for f, mult in zip(FAMILIAS, (1.0, 0.5), strict=True):
            for d in fechas:
                promo = int(rng.integers(0, 6))
                nivel = base * mult * (1.3 if d.day_of_week >= 5 else 1.0) * (1 + 0.05 * promo)
                filas.append((d, t, f, int(rng.poisson(nivel)), promo))
    ventas = pd.DataFrame(filas, columns=["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"])
    tiendas = pd.DataFrame({"tienda_id": [1, 2], "nombre": ["A", "B"], "ciudad": ["Guatemala", "Cobán"],
                            "departamento": ["Guatemala", "Alta Verapaz"], "tipo": ["hipermercado", "express"]})  # fmt: skip
    feriados = pd.DataFrame({"fecha": pd.to_datetime(["2025-03-01", "2025-04-10", "2025-04-11", "2025-02-14"]),
                             "nombre": ["Nac", "Local", "Medio", "Comercial"], "tipo": ["feriado", "feriado", "medio_dia", "comercial"],
                             "ambito": ["nacional", "Cobán", "nacional", "nacional"]})  # fmt: skip
    costos = pd.DataFrame({"familia_id": FAMILIAS, "costo_faltante": [1.0, 1.0], "costo_sobrante": [0.2, 1.2]})
    return Datos(ventas, tiendas, feriados, costos)
