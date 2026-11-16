"""Criterio de promoción: ¿el candidato reemplaza al modelo vigente?

Regla principal:  promover  <=>  costo_candidato < costo_vigente * (1 - margen)

El costo es el costo de negocio total (cantidad_sugerida contra la venta real, con costos.csv)
en la MISMA ventana de prueba, que ninguno de los dos vio al entrenar. El margen (1,5 %) evita
cambiar de modelo por una diferencia que es solo ruido: cada promoción tiene riesgo operativo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MARGEN_PROMOCION = 0.015


@dataclass(frozen=True)
class Decision:
    promover: bool
    motivo: str


def decidir_promocion(costo_candidato: float, costo_vigente: float | None, margen: float = MARGEN_PROMOCION) -> Decision:
    if not 0 <= margen < 1:
        raise ValueError(f"margen fuera de [0, 1): {margen}")
    if costo_vigente is None:
        return Decision(True, "no hay modelo vigente: el candidato pasa a ser el primero")
    if not math.isfinite(costo_candidato) or costo_candidato < 0:
        return Decision(False, f"el costo del candidato no es válido ({costo_candidato})")
    if not math.isfinite(costo_vigente) or costo_vigente <= 0:
        return Decision(False, f"el costo del vigente no es válido ({costo_vigente}): no se puede comparar")
    umbral = costo_vigente * (1 - margen)
    mejora = 1 - costo_candidato / costo_vigente
    if costo_candidato < umbral:
        return Decision(True, f"el candidato reduce el costo {mejora:.1%} ({costo_candidato:,.0f} < {umbral:,.0f} = vigente {costo_vigente:,.0f} × (1 − {margen:.1%}))")
    return Decision(False, f"el candidato no supera el margen de {margen:.1%}: mejora {mejora:+.1%} (costo {costo_candidato:,.0f} vs vigente {costo_vigente:,.0f}, umbral {umbral:,.0f})")
