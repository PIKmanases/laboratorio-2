"""Esquema de app-db. Tres grupos de tablas:

 a) Referencia (se cargan una vez de los CSV): tiendas, familias, costos, feriados, ventas_historicas.
 b) Operación (las escribe la API): solicitudes, correcciones, predicciones, observaciones,
    solicitudes_rechazadas.
 c) Control: reentrenamientos, estado_sistema.

Se crean con `Base.metadata.create_all` (ver `esquema.py`): idempotente, pero NO altera tablas
que ya existen. Ver DECISIONES.md (D5).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSONB()
BigPK = BigInteger()  # BIGSERIAL en PostgreSQL


class Base(DeclarativeBase):
    pass


def _ahora() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- a) referencia
class Tienda(Base):
    __tablename__ = "tiendas"
    tienda_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    nombre: Mapped[str] = mapped_column(Text)
    ciudad: Mapped[str] = mapped_column(Text)
    departamento: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    fecha_apertura: Mapped[date] = mapped_column(Date)


class Familia(Base):
    __tablename__ = "familias"
    familia_id: Mapped[str] = mapped_column(Text, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text)


class Costo(Base):
    """Costos del problema newsvendor, por familia."""

    __tablename__ = "costos"
    familia_id: Mapped[str] = mapped_column(ForeignKey("familias.familia_id"), primary_key=True)
    costo_faltante: Mapped[float] = mapped_column(Float, CheckConstraint("costo_faltante > 0"))
    costo_sobrante: Mapped[float] = mapped_column(Float, CheckConstraint("costo_sobrante > 0"))


class Feriado(Base):
    __tablename__ = "feriados"
    fecha: Mapped[date] = mapped_column(Date, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, primary_key=True)
    tipo: Mapped[str] = mapped_column(Text)
    ambito: Mapped[str] = mapped_column(Text)  # 'nacional' o la ciudad/departamento


class VentaHistorica(Base):
    __tablename__ = "ventas_historicas"
    tienda_id: Mapped[int] = mapped_column(ForeignKey("tiendas.tienda_id"), primary_key=True)
    familia_id: Mapped[str] = mapped_column(ForeignKey("familias.familia_id"), primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, primary_key=True)
    ventas: Mapped[int] = mapped_column(Integer, CheckConstraint("ventas >= 0"))
    en_promocion: Mapped[int] = mapped_column(Integer, CheckConstraint("en_promocion >= 0"))


# ---------------------------------------------------------------- b) operación
class Solicitud(Base):
    """Cada solicitud ACEPTADA (200), de predicción o de observaciones. No es única por
    (tienda, fecha): una tienda puede pedir varias veces el mismo día."""

    __tablename__ = "solicitudes"
    __table_args__ = (
        CheckConstraint("tipo IN ('prediccion', 'observacion')"),
        CheckConstraint("estado_calidad IN ('ok', 'corregido', 'advertencia')"),
        Index("ix_solicitudes_tienda_fecha", "tienda_id", "fecha"),
    )
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(Text)
    recibida_en: Mapped[datetime] = _ahora()  # solo auditoría; nunca se usa para validar fechas
    tienda_id: Mapped[int] = mapped_column(ForeignKey("tiendas.tienda_id"))
    fecha: Mapped[date] = mapped_column(Date)
    cuerpo_crudo: Mapped[Any] = mapped_column(JSONType)  # lo recibido, antes de limpiar
    estado_calidad: Mapped[str] = mapped_column(Text)
    version_modelo: Mapped[str | None] = mapped_column(Text, nullable=True)  # solo predicción
    registros_guardados: Mapped[int | None] = mapped_column(Integer, nullable=True)  # solo observación


class SolicitudRechazada(Base):
    """Solicitudes que devolvieron 422. Sin FK a tiendas: la tienda puede ser justo lo inválido."""

    __tablename__ = "solicitudes_rechazadas"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    recibida_en: Mapped[datetime] = _ahora()
    endpoint: Mapped[str] = mapped_column(Text)
    cuerpo_crudo: Mapped[Any] = mapped_column(JSONType, nullable=True)
    campo: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivo: Mapped[str] = mapped_column(Text)


class Correccion(Base):
    """Cada observación de `calidad` (reparar/advertir) de una solicitud aceptada."""

    __tablename__ = "correcciones"
    __table_args__ = (CheckConstraint("accion IN ('reparar', 'advertir')"),)
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    solicitud_id: Mapped[int] = mapped_column(ForeignKey("solicitudes.id", ondelete="CASCADE"), index=True)
    campo: Mapped[str] = mapped_column(Text)
    accion: Mapped[str] = mapped_column(Text)
    detalle: Mapped[str] = mapped_column(Text)


class Prediccion(Base):
    """Una fila por familia pronosticada. `en_promocion` es la promoción que la tienda declaró
    para ese día: es la ÚNICA fuente de promociones de días observados (ver la vista).
    `tienda_id` y `fecha` se copian de la solicitud para indexar la búsqueda sin join."""

    __tablename__ = "predicciones"
    __table_args__ = (Index("ix_predicciones_clave", "tienda_id", "fecha", "familia_id"),)
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    solicitud_id: Mapped[int] = mapped_column(ForeignKey("solicitudes.id", ondelete="CASCADE"), index=True)
    tienda_id: Mapped[int] = mapped_column(ForeignKey("tiendas.tienda_id"))
    fecha: Mapped[date] = mapped_column(Date)
    familia_id: Mapped[str] = mapped_column(ForeignKey("familias.familia_id"))
    en_promocion: Mapped[int] = mapped_column(Integer, CheckConstraint("en_promocion >= 0"))
    venta_esperada: Mapped[float] = mapped_column(Float)
    cantidad_sugerida: Mapped[int] = mapped_column(Integer, CheckConstraint("cantidad_sugerida >= 0"))
    version_modelo: Mapped[str] = mapped_column(Text)


class Observacion(Base):
    """Ventas reales. La PK (tienda, familia, fecha) hace posible el upsert idempotente.
    `valido` permite excluir un registro del entrenamiento sin borrarlo; el upsert nunca lo toca."""

    __tablename__ = "observaciones"
    tienda_id: Mapped[int] = mapped_column(ForeignKey("tiendas.tienda_id"), primary_key=True)
    familia_id: Mapped[str] = mapped_column(ForeignKey("familias.familia_id"), primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, primary_key=True)
    ventas: Mapped[int] = mapped_column(Integer, CheckConstraint("ventas >= 0"))
    valido: Mapped[bool] = mapped_column(Boolean, server_default="true")
    solicitud_id: Mapped[int] = mapped_column(ForeignKey("solicitudes.id"))  # la que la creó/modificó
    creada_en: Mapped[datetime] = _ahora()
    actualizada_en: Mapped[datetime] = _ahora()


# ---------------------------------------------------------------- c) control
class Reentrenamiento(Base):
    """Una fila por ejecución del DAG de reentrenamiento (paso 6). `dag_run_id` es el `id`
    que devuelve POST /admin/reentrenar."""

    __tablename__ = "reentrenamientos"
    __table_args__ = (CheckConstraint("estado IN ('en_curso', 'exitoso', 'fallido')"),)
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    dag_run_id: Mapped[str] = mapped_column(Text, unique=True)
    iniciado_en: Mapped[datetime] = _ahora()
    terminado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estado: Mapped[str] = mapped_column(Text, server_default="en_curso")
    promovido: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    version_anterior: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_nueva: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    metricas: Mapped[Any | None] = mapped_column(JSONType, nullable=True)


class EstadoSistema(Base):
    """Clave-valor para banderas del sistema (p. ej. carga_inicial = completada)."""

    __tablename__ = "estado_sistema"
    clave: Mapped[str] = mapped_column(Text, primary_key=True)
    valor: Mapped[str] = mapped_column(Text)
    actualizado_en: Mapped[datetime] = _ahora()
