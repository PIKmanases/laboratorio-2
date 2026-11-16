"""Tabla `reentrenamientos`: la API crea la fila (en_curso) y el DAG la completa al terminar."""

from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .modelos import Reentrenamiento
from .repositorio import ErrorPersistencia


class RepositorioReentrenamientos(Protocol):
    def crear(self, dag_run_id: str) -> None: ...
    def borrar(self, dag_run_id: str) -> None: ...
    def obtener(self, dag_run_id: str) -> dict[str, Any] | None: ...


class ReentrenamientosSQL:
    def __init__(self, engine: Engine):
        self._engine = engine

    def crear(self, dag_run_id: str) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(insert(Reentrenamiento).values(dag_run_id=dag_run_id, estado="en_curso"))
        except SQLAlchemyError as e:
            raise ErrorPersistencia(f"no se pudo registrar el reentrenamiento: {type(e).__name__}") from e

    def borrar(self, dag_run_id: str) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(delete(Reentrenamiento).where(Reentrenamiento.dag_run_id == dag_run_id))
        except SQLAlchemyError as e:
            raise ErrorPersistencia(f"no se pudo deshacer el registro: {type(e).__name__}") from e

    def obtener(self, dag_run_id: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                fila = conn.execute(select(Reentrenamiento).where(Reentrenamiento.dag_run_id == dag_run_id)).mappings().first()
        except SQLAlchemyError as e:
            raise ErrorPersistencia(f"no se pudo consultar el reentrenamiento: {type(e).__name__}") from e
        return dict(fila) if fila else None
