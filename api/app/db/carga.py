"""Carga inicial de los CSV a app-db. Se ejecuta con `python -m app.db.carga` (contenedor `init`).

- Crea el esquema (create_all + vistas) si falta.
- IDEMPOTENTE: si `ventas_historicas` ya tiene datos no hace nada.
- Una sola transacción: o queda todo cargado (y `carga_inicial = completada`) o nada.
- Los IDs de los CSV pasan por la MISMA normalización que usa la limpieza de la API, para que
  `familia_id` y `tienda_id` coincidan exactamente. Un ID que no se resuelve aborta la carga.
"""

import csv
import logging
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine

from ..config import get_settings
from ..limpieza import Calidad, Catalogos, SolicitudRechazada
from ..limpieza.campos import a_familia, a_tienda
from .esquema import crear_esquema
from .modelos import Costo, EstadoSistema, Familia, Feriado, Tienda, VentaHistorica

logger = logging.getLogger("api.carga")
LOTE = 5000


def _filas(ruta: Path) -> Iterator[dict[str, str]]:
    with open(ruta, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def _lotes(it: Iterator[dict], n: int = LOTE) -> Iterator[list[dict]]:
    lote: list[dict] = []
    for fila in it:
        lote.append(fila)
        if len(lote) >= n:
            yield lote
            lote = []
    if lote:
        yield lote


def _insertar(conn: Connection, modelo, filas: Iterator[dict]) -> int:
    """INSERT ... ON CONFLICT DO NOTHING por lotes. Devuelve las filas leídas."""
    total = 0
    for lote in _lotes(filas):
        conn.execute(insert(modelo).on_conflict_do_nothing(), lote)
        total += len(lote)
    return total


def cargar(engine: Engine, directorio: Path) -> bool:
    """True si cargó, False si ya estaba cargado."""
    crear_esquema(engine)
    with engine.begin() as conn:
        if conn.execute(select(func.count()).select_from(VentaHistorica)).scalar_one() > 0:
            conn.execute(
                insert(EstadoSistema)
                .values(clave="carga_inicial", valor="completada")
                .on_conflict_do_nothing()
            )
            logger.info("ventas_historicas ya tiene datos: no se hace nada")
            return False

        tiendas = [
            {**f, "tienda_id": int(f["tienda_id"]), "fecha_apertura": date.fromisoformat(f["fecha_apertura"])}
            for f in _filas(directorio / "tiendas.csv")
        ]
        familias = list(_filas(directorio / "familias.csv"))
        catalogos = Catalogos.desde_filas((t["tienda_id"] for t in tiendas), ((f["familia_id"], f["nombre"]) for f in familias))
        calidad = Calidad()  # los CSV no deberían traer nada que reparar; se cuenta para avisar

        def tienda(valor: str, donde: str) -> int:
            try:
                limpio = int(valor) if valor.strip().isdigit() else valor  # "5" ya es un entero válido
                return a_tienda(limpio, "tienda_id", calidad, catalogos)
            except SolicitudRechazada as e:
                raise SystemExit(f"{donde}: {e}") from e

        def familia(valor: str, donde: str) -> str:
            try:
                return a_familia(valor, "familia_id", calidad, catalogos)
            except SolicitudRechazada as e:
                raise SystemExit(f"{donde}: {e}") from e

        conn.execute(insert(Tienda).on_conflict_do_nothing(), tiendas)
        conn.execute(insert(Familia).on_conflict_do_nothing(), familias)
        conn.execute(
            insert(Costo).on_conflict_do_nothing(),
            [
                {"familia_id": familia(f["familia_id"], "costos.csv"),
                 "costo_faltante": float(f["costo_faltante"]), "costo_sobrante": float(f["costo_sobrante"])}
                for f in _filas(directorio / "costos.csv")
            ],
        )  # fmt: skip
        n_fer = _insertar(
            conn, Feriado,
            ({**f, "fecha": date.fromisoformat(f["fecha"])} for f in _filas(directorio / "feriados.csv")),
        )  # fmt: skip
        n_ventas = _insertar(
            conn, VentaHistorica,
            (
                {
                    "fecha": date.fromisoformat(f["fecha"]),
                    "tienda_id": tienda(f["tienda_id"], "historico_ventas.csv"),
                    "familia_id": familia(f["familia_id"], "historico_ventas.csv"),
                    "ventas": int(f["ventas"]),
                    "en_promocion": int(f["en_promocion"]),
                }
                for f in _filas(directorio / "historico_ventas.csv")
            ),
        )  # fmt: skip
        conn.execute(
            insert(EstadoSistema)
            .values(clave="carga_inicial", valor="completada")
            .on_conflict_do_update(index_elements=["clave"], set_={"valor": "completada", "actualizado_en": func.now()})
        )
    if calidad.observaciones:
        logger.warning("la normalización corrigió %d valores de los CSV", len(calidad.observaciones))
    logger.info("carga completada: %d tiendas, %d familias, %d feriados, %d ventas", len(tiendas), len(familias), n_fer, n_ventas)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    cargar(engine, Path(settings.data_dir))


if __name__ == "__main__":
    sys.exit(main())
