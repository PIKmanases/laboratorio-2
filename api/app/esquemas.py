"""Modelos Pydantic de las etapas 1, 3 y 4 del flujo.

    1. Entrada TOLERANTE   -> solo verifica la estructura mínima (que sea un objeto JSON).
    2. Limpieza            -> módulos puros en `app.limpieza` (no vive aquí).
    3. Modelo CANÓNICO     -> tipos exactos y estrictos, ya limpios.
    4. Respuesta           -> garantiza el contrato de salida.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------------------
# Etapa 1: entrada tolerante
# --------------------------------------------------------------------------------------
# Los campos son `Any` a propósito: aquí NO se decide si un valor es válido (eso es la
# limpieza). `extra="allow"` deja pasar campos con otro nombre (E07) para que la limpieza
# los reconozca. Pydantic solo rechaza lo que no se puede ni leer: que el cuerpo no sea un
# objeto, o que `familias`/`ventas` sea algo distinto de una lista de objetos.


class _Tolerante(BaseModel):
    model_config = ConfigDict(extra="allow")


class EntradaPrediccion(_Tolerante):
    tienda_id: Any = None
    fecha: Any = None
    familias: list[dict[str, Any]] | None = None


class EntradaObservacion(_Tolerante):
    tienda_id: Any = None
    fecha: Any = None
    ventas: list[dict[str, Any]] | None = None


# --------------------------------------------------------------------------------------
# Etapa 3: modelo canónico estricto
# --------------------------------------------------------------------------------------
# strict=True: un "5" o un 5.0 aquí es un error de programación (la limpieza falló),
# no un dato sucio. extra="forbid" por la misma razón.


class _Canonico(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class FamiliaSolicitada(_Canonico):
    familia_id: str
    en_promocion: int = Field(ge=0)


class SolicitudPrediccion(_Canonico):
    tienda_id: int
    fecha: date
    familias: tuple[FamiliaSolicitada, ...] = Field(min_length=1)


class VentaFamilia(_Canonico):
    familia_id: str
    ventas: int = Field(ge=0)


class ObservacionVentas(_Canonico):
    tienda_id: int
    fecha: date
    ventas: tuple[VentaFamilia, ...] = Field(min_length=1)


# --------------------------------------------------------------------------------------
# Etapa 4: respuestas (contrato de salida)
# --------------------------------------------------------------------------------------


class ObservacionCalidad(BaseModel):
    campo: str
    accion: Literal["reparar", "advertir"]
    detalle: str


class Calidad(BaseModel):
    estado: Literal["ok", "corregido", "advertencia"]
    observaciones: list[ObservacionCalidad] = Field(default_factory=list)

    @model_validator(mode="after")
    def _estado_coherente(self) -> Calidad:
        acciones = {o.accion for o in self.observaciones}
        if "advertir" in acciones:
            esperado = "advertencia"
        elif acciones:
            esperado = "corregido"
        else:
            esperado = "ok"
        if self.estado != esperado:
            raise ValueError(f"estado '{self.estado}' no coincide con las observaciones ({esperado})")
        return self


class PrediccionFamilia(BaseModel):
    familia_id: str
    venta_esperada: float
    cantidad_sugerida: int = Field(ge=0)


class RespuestaPrediccion(BaseModel):
    tienda_id: int
    fecha: date
    version_modelo: str
    predicciones: list[PrediccionFamilia]
    calidad: Calidad


class RespuestaObservaciones(BaseModel):
    registros_guardados: int = Field(ge=0)
    calidad: Calidad
