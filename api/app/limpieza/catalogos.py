import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .texto import normalizar_texto


@dataclass(frozen=True)
class Catalogos:
    """Catálogos de referencia. Se INYECTAN (no se leen dentro de la limpieza)."""

    tiendas: frozenset[int]
    familias_ids: tuple[str, ...]
    # clave normalizada (del id o del nombre) -> familia_id canónico
    _alias_familia: Mapping[str, str]

    @classmethod
    def desde_filas(cls, tiendas: Iterable[int], familias: Iterable[tuple[str, str]]) -> "Catalogos":
        """`familias`: pares (familia_id, nombre). Ambos se aceptan como forma de escribirla."""
        ids: list[str] = []
        alias: dict[str, str] = {}
        for fid, nombre in familias:
            ids.append(fid)
            for clave in (normalizar_texto(fid), normalizar_texto(nombre)):
                if alias.setdefault(clave, fid) != fid:
                    raise ValueError(f"la clave '{clave}' apunta a dos familias distintas")
        return cls(frozenset(tiendas), tuple(ids), alias)

    @classmethod
    def desde_csv(cls, directorio: Path) -> "Catalogos":
        with open(directorio / "tiendas.csv", newline="", encoding="utf-8") as f:
            tiendas = [int(r["tienda_id"]) for r in csv.DictReader(f)]
        with open(directorio / "familias.csv", newline="", encoding="utf-8") as f:
            familias = [(r["familia_id"], r["nombre"]) for r in csv.DictReader(f)]
        return cls.desde_filas(tiendas, familias)

    @classmethod
    def desde_db(cls, engine) -> "Catalogos":
        """Lee las tablas de referencia (las carga el contenedor `init`)."""
        from sqlalchemy import text

        with engine.connect() as conn:
            tiendas = [r[0] for r in conn.execute(text("SELECT tienda_id FROM tiendas"))]
            familias = [tuple(r) for r in conn.execute(text("SELECT familia_id, nombre FROM familias ORDER BY familia_id"))]
        if not tiendas or not familias:
            raise RuntimeError("tiendas/familias vacías: falta la carga inicial (contenedor init)")
        return cls.desde_filas(tiendas, familias)

    def resolver_familia(self, texto: str) -> str | None:
        return self._alias_familia.get(normalizar_texto(texto))
