"""Repositorio: lo único que escribe/lee las tablas de operación. Una transacción por
solicitud (`engine.begin()`): se guarda todo junto o nada."""

import logging
import math
from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from ..esquemas import ObservacionVentas, RespuestaPrediccion, SolicitudPrediccion
from ..limpieza import Calidad
from .modelos import Correccion, Observacion, Prediccion, Solicitud, SolicitudRechazada

logger = logging.getLogger("api.db")


class ErrorPersistencia(Exception):
    """app-db no respondió o rechazó la escritura. La API contesta 503 (ver DECISIONES.md D6)."""


class Repositorio(Protocol):
    def guardar_prediccion(
        self, solicitud: SolicitudPrediccion, calidad: Calidad, crudo: Any, respuesta: RespuestaPrediccion
    ) -> int: ...

    def guardar_observacion(self, solicitud: ObservacionVentas, calidad: Calidad, crudo: Any) -> int:
        """Devuelve `registros_guardados`: filas nuevas o cuyo valor cambió."""

    def guardar_rechazo(self, endpoint: str, crudo: Any, campo: str | None, motivo: str) -> None: ...


def json_seguro(valor: Any) -> Any:
    """JSONB no admite NaN/Infinity ni el carácter NUL; el cuerpo crudo puede traerlos."""
    if isinstance(valor, float) and not math.isfinite(valor):
        return repr(valor)
    if isinstance(valor, str):
        return valor.replace("\x00", "�")
    if isinstance(valor, dict):
        return {json_seguro(str(k)): json_seguro(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [json_seguro(v) for v in valor]
    return valor


def _texto(valor: str | None) -> str | None:
    return None if valor is None else valor.replace("\x00", "�")


class RepositorioSQL:
    def __init__(self, engine: Engine):
        self._engine = engine

    def guardar_prediccion(self, solicitud, calidad, crudo, respuesta) -> int:
        try:
            with self._engine.begin() as conn:
                sid = self._insertar_solicitud(
                    conn, "prediccion", solicitud.tienda_id, solicitud.fecha, crudo, calidad,
                    version_modelo=respuesta.version_modelo,
                )  # fmt: skip
                self._insertar_correcciones(conn, sid, calidad)
                promo = {f.familia_id: f.en_promocion for f in solicitud.familias}
                conn.execute(
                    insert(Prediccion),
                    [
                        {
                            "solicitud_id": sid, "tienda_id": solicitud.tienda_id, "fecha": solicitud.fecha,
                            "familia_id": p.familia_id, "en_promocion": promo[p.familia_id],
                            "venta_esperada": p.venta_esperada, "cantidad_sugerida": p.cantidad_sugerida,
                            "version_modelo": respuesta.version_modelo,
                        }
                        for p in respuesta.predicciones
                    ],
                )  # fmt: skip
                return sid
        except SQLAlchemyError as e:
            raise ErrorPersistencia(f"no se pudo guardar la predicción: {type(e).__name__}") from e

    def guardar_observacion(self, solicitud, calidad, crudo) -> int:
        try:
            with self._engine.begin() as conn:
                sid = self._insertar_solicitud(
                    conn, "observacion", solicitud.tienda_id, solicitud.fecha, crudo, calidad
                )
                self._insertar_correcciones(conn, sid, calidad)
                guardados = upsert_observaciones(conn, solicitud, sid)
                conn.execute(
                    Solicitud.__table__.update().where(Solicitud.id == sid).values(registros_guardados=guardados)
                )
                return guardados
        except SQLAlchemyError as e:
            raise ErrorPersistencia(f"no se pudo guardar la observación: {type(e).__name__}") from e

    def guardar_rechazo(self, endpoint, crudo, campo, motivo) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert(SolicitudRechazada).values(
                        endpoint=endpoint, cuerpo_crudo=json_seguro(crudo), campo=_texto(campo), motivo=_texto(motivo)
                    )
                )
        except SQLAlchemyError as e:
            raise ErrorPersistencia(f"no se pudo guardar el rechazo: {type(e).__name__}") from e

    @staticmethod
    def _insertar_solicitud(conn, tipo, tienda_id, fecha, crudo, calidad, version_modelo=None) -> int:
        return conn.execute(
            insert(Solicitud)
            .values(
                tipo=tipo, tienda_id=tienda_id, fecha=fecha, cuerpo_crudo=json_seguro(crudo),
                estado_calidad=calidad.estado, version_modelo=version_modelo,
            )
            .returning(Solicitud.id)
        ).scalar_one()  # fmt: skip

    @staticmethod
    def _insertar_correcciones(conn, solicitud_id: int, calidad: Calidad) -> None:
        if calidad.observaciones:
            conn.execute(
                insert(Correccion),
                [
                    {"solicitud_id": solicitud_id, "campo": _texto(o.campo), "accion": o.accion, "detalle": _texto(o.detalle)}
                    for o in calidad.observaciones
                ],
            )


def upsert_observaciones(conn, solicitud: ObservacionVentas, solicitud_id: int) -> int:
    """Upsert que hace la BD: inserta lo nuevo y actualiza SOLO lo que cambió de valor.

    El `WHERE` del `DO UPDATE` descarta las filas idénticas, y `RETURNING` solo devuelve las
    insertadas o realmente modificadas: reenviar lo mismo cuenta 0. No toca `valido`.
    """
    stmt = insert(Observacion).values(
        [
            {"tienda_id": solicitud.tienda_id, "familia_id": v.familia_id, "fecha": solicitud.fecha,
             "ventas": v.ventas, "solicitud_id": solicitud_id}
            for v in solicitud.ventas
        ]
    )  # fmt: skip
    stmt = stmt.on_conflict_do_update(
        index_elements=[Observacion.tienda_id, Observacion.familia_id, Observacion.fecha],
        set_={"ventas": stmt.excluded.ventas, "solicitud_id": solicitud_id, "actualizada_en": func.now()},
        where=Observacion.ventas.is_distinct_from(stmt.excluded.ventas),
    ).returning(Observacion.familia_id)
    return len(conn.execute(stmt).all())


class PredictorSQL:
    """Predictor falso sobre la vista de entrenamiento: promedio de (tienda, familia) en
    fechas ESTRICTAMENTE anteriores a la pedida. Incluye las observaciones que llegaron.
    Sin app-db no puede predecir -> ErrorPersistencia (503)."""

    version = "0"

    def __init__(self, engine: Engine):
        self._engine = engine

    def predecir(self, tienda_id: int, fecha: date, familias: Sequence) -> dict[str, float]:
        from sqlalchemy import column, table

        v = table("ventas_para_entrenamiento", column("tienda_id"), column("familia_id"), column("fecha"), column("ventas"))
        ids = [f.familia_id for f in familias]
        q = (
            select(v.c.familia_id, func.avg(v.c.ventas))
            .where(v.c.tienda_id == tienda_id, v.c.fecha < fecha, v.c.familia_id.in_(ids))
            .group_by(v.c.familia_id)
        )
        try:
            with self._engine.connect() as conn:
                medias = {fam: float(m) for fam, m in conn.execute(q)}
        except SQLAlchemyError as e:
            raise ErrorPersistencia(f"no se pudo consultar el histórico: {type(e).__name__}") from e
        return {fam: medias.get(fam, 0.0) for fam in ids}
