"""Interpretación de valores sueltos. Cada función devuelve el valor limpio, registra en
`Calidad` lo que reparó o advirtió, y lanza `SolicitudRechazada` si no hay interpretación
honesta. Las políticas están en DECISIONES.md (E01-E10 y las reglas generales)."""

import math
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from .calidad import Calidad, SolicitudRechazada
from .catalogos import Catalogos
from .texto import normalizar_texto

# --------------------------------------------------------------------------------------
# Nombres de campo (E07)
# --------------------------------------------------------------------------------------
# Se compara la clave NORMALIZADA con el nombre del contrato y con una lista corta de
# sinónimos. Lista conservadora: un sinónimo dudoso es peor que rechazar por campo faltante.

ALIAS_TIENDA = {"tienda_id": {"tienda", "id_tienda", "store_id", "codigo_tienda", "cod_tienda"}}
ALIAS_FECHA = {"fecha": {"date", "dia", "fecha_prediccion", "fecha_solicitud"}}
ALIAS_FAMILIAS = {"familias": {"lista_familias", "categorias", "productos"}}
ALIAS_VENTAS_LISTA = {"ventas": {"registros", "observaciones", "ventas_por_familia"}}
ALIAS_ITEM_PRED = {
    "familia_id": {"familia", "id_familia", "categoria", "nombre_familia"},
    "en_promocion": {"promocion", "promo", "cantidad_en_promocion", "productos_en_promocion"},
}
ALIAS_ITEM_VENTA = {
    "familia_id": {"familia", "id_familia", "categoria", "nombre_familia"},
    "ventas": {"venta", "total", "unidades", "ventas_dia"},
}


def resolver_campos(
    crudo: Mapping[str, Any],
    alias: Mapping[str, set[str]],
    calidad: Calidad,
    prefijo: str = "",
) -> dict[str, Any]:
    """Devuelve {nombre_del_contrato: valor}. Valores None cuentan como ausentes.

    - Clave distinta pero equivalente (E07)        -> reparar.
    - Dos claves equivalentes con valores distintos -> rechazar (no se sabe cuál vale).
    - Clave que no corresponde a nada               -> advertir y se ignora.
    """
    por_clave = {c: c for c in alias}
    for canonico, sinonimos in alias.items():
        por_clave.update({s: canonico for s in sinonimos})

    resultado: dict[str, Any] = {}
    for clave, valor in crudo.items():
        canonico = por_clave.get(normalizar_texto(clave))
        if canonico is None:
            if valor is not None:
                calidad.advertir(f"{prefijo}{clave}", "campo desconocido: se ignoró")
            continue
        if valor is None:
            continue
        if canonico in resultado and resultado[canonico] != valor:
            raise SolicitudRechazada(
                f"{prefijo}{canonico}", f"viene dos veces con valores distintos ('{clave}' y otro equivalente)"
            )
        resultado[canonico] = valor
        if clave != canonico:
            calidad.reparar(f"{prefijo}{canonico}", f"el campo '{clave}' se interpretó como '{canonico}'")
    return resultado


def exigir(campos: Mapping[str, Any], nombre: str, prefijo: str = "") -> Any:
    if nombre not in campos:
        raise SolicitudRechazada(f"{prefijo}{nombre}", "falta el campo o viene vacío")
    return campos[nombre]


def lista_de_objetos(valor: Any, campo: str) -> list[dict[str, Any]]:
    if not isinstance(valor, list) or not all(isinstance(x, dict) for x in valor):
        raise SolicitudRechazada(campo, "debe ser una lista de objetos")
    if not valor:
        raise SolicitudRechazada(campo, "la lista viene vacía")
    return valor


# --------------------------------------------------------------------------------------
# Números (E03, E10)
# --------------------------------------------------------------------------------------
# Convención: la coma agrupa miles ("1,234") y el punto es decimal ("812.0"), como en
# Guatemala/EE. UU. Una coma sola entre dígitos que NO forma grupos de tres es decimal
# ("812,0"). Un punto con tres decimales ("1.234") NO se adivina: queda como decimal
# 1.234, que no es entero, y se rechaza. Las cantidades son unidades enteras.
_PATRONES = (
    (re.compile(r"\d+"), lambda s: s),
    (re.compile(r"\d{1,3}(,\d{3})+(\.\d+)?"), lambda s: s.replace(",", "")),  # 1,234 / 1,234.5
    (re.compile(r"\d{1,3}(\.\d{3})+,\d+"), lambda s: s.replace(".", "").replace(",", ".")),  # 1.234,5
    (re.compile(r"\d+,\d+"), lambda s: s.replace(",", ".")),  # 812,0
    (re.compile(r"\d+\.\d+"), lambda s: s),  # 812.0
)


def _texto_a_decimal(texto: str) -> Decimal | None:
    t = texto.strip()
    signo = ""
    if t and t[0] in "+-":
        signo, t = ("-" if t[0] == "-" else ""), t[1:]
    for patron, convertir in _PATRONES:
        if patron.fullmatch(t):
            return Decimal(signo + convertir(t))
    return None


def a_entero(valor: Any, campo: str, calidad: Calidad) -> int:
    """int exacto pasa sin comentarios; float entero o texto numérico -> reparar."""
    if isinstance(valor, bool):
        raise SolicitudRechazada(campo, f"{valor!r} no es un número")
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        if not math.isfinite(valor) or not valor.is_integer():
            raise SolicitudRechazada(campo, f"{valor!r} no es un entero; no se redondea")
        n = int(valor)
    elif isinstance(valor, str):
        d = _texto_a_decimal(valor)
        if d is None:
            raise SolicitudRechazada(campo, f"'{valor}' no se reconoce como número")
        if d != d.to_integral_value():
            raise SolicitudRechazada(campo, f"'{valor}' no es un entero; no se redondea")
        n = int(d)
    else:
        raise SolicitudRechazada(campo, "debe ser un número")
    calidad.reparar(campo, f"{valor!r} se interpretó como el entero {n}")
    return n


# --------------------------------------------------------------------------------------
# Tienda (E01)
# --------------------------------------------------------------------------------------


def a_tienda(valor: Any, campo: str, calidad: Calidad, catalogos: Catalogos) -> int:
    if isinstance(valor, bool):
        raise SolicitudRechazada(campo, f"{valor!r} no es un identificador de tienda")
    if isinstance(valor, int):
        numero, reparado = valor, False
    elif isinstance(valor, float):
        if not math.isfinite(valor) or not valor.is_integer():
            raise SolicitudRechazada(campo, f"{valor!r} no es un identificador de tienda")
        numero, reparado = int(valor), True
    elif isinstance(valor, str):
        # "5.0" como texto es el float 5.0 escrito a mano: no son dos números.
        texto = re.sub(r"^(\s*\d+)\.0+\s*$", r"\1", valor)
        numeros = re.findall(r"\d+", normalizar_texto(texto))
        if len(numeros) != 1:
            raise SolicitudRechazada(
                campo, f"'{valor}' debe contener exactamente un número (contiene {len(numeros)})"
            )
        numero, reparado = int(numeros[0]), True
    else:
        raise SolicitudRechazada(campo, "debe ser un identificador de tienda")

    if numero not in catalogos.tiendas:
        raise SolicitudRechazada(campo, f"la tienda {numero} no existe: no se sabe para quién es el pronóstico")
    if reparado:
        calidad.reparar(campo, f"{valor!r} se interpretó como la tienda {numero}")
    return numero


# --------------------------------------------------------------------------------------
# Familia (E02)
# --------------------------------------------------------------------------------------


def a_familia(valor: Any, campo: str, calidad: Calidad, catalogos: Catalogos) -> str:
    if not isinstance(valor, str):
        raise SolicitudRechazada(campo, "debe ser texto")
    familia = catalogos.resolver_familia(valor)
    if familia is None:
        raise SolicitudRechazada(campo, f"la familia '{valor}' no existe en el catálogo")
    if valor != familia:
        calidad.reparar(campo, f"'{valor}' se interpretó como '{familia}'")
    return familia


# --------------------------------------------------------------------------------------
# Fecha (E04). Nunca se consulta el reloj del sistema.
# --------------------------------------------------------------------------------------
_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_ISO_HORA = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_ANIO_PRIMERO = re.compile(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})")
_DIA_MES = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")


def _construir(campo: str, original: str, anio: int, mes: int, dia: int) -> date:
    try:
        return date(anio, mes, dia)
    except ValueError:
        raise SolicitudRechazada(campo, f"'{original}' es una fecha imposible") from None


def a_fecha(valor: Any, campo: str, calidad: Calidad) -> date:
    if not isinstance(valor, str):
        raise SolicitudRechazada(campo, "debe ser texto AAAA-MM-DD")
    t = valor.strip()

    if m := _ISO.fullmatch(t):
        anio, mes, dia = map(int, m.groups())
        f = _construir(campo, valor, anio, mes, dia)
        if valor != f.isoformat():
            calidad.reparar(campo, f"'{valor}' se interpretó como {f.isoformat()}")
        return f

    if m := _ISO_HORA.fullmatch(t):
        anio, mes, dia, hh, mm, ss = (int(x or 0) for x in m.groups())
        f = _construir(campo, valor, anio, mes, dia)
        if hh == mm == ss == 0:
            calidad.reparar(campo, f"'{valor}' traía hora 00:00:00; se usó {f.isoformat()}")
        else:
            calidad.advertir(campo, f"'{valor}' traía hora y/o zona horaria; se usó la fecha escrita {f.isoformat()}")
        return f

    if m := _ANIO_PRIMERO.fullmatch(t):
        anio, mes, dia = map(int, m.groups())
        f = _construir(campo, valor, anio, mes, dia)
        calidad.reparar(campo, f"'{valor}' se interpretó como {f.isoformat()} (año/mes/día)")
        return f

    if m := _DIA_MES.fullmatch(t):
        a, b, anio = map(int, m.groups())
        if a > 12 or a == b:
            dia, mes, regla = a, b, None
        elif b > 12:
            mes, dia, regla = a, b, None
        else:
            dia, mes, regla = a, b, "ambigua: se asumió día/mes (convención local)"
        f = _construir(campo, valor, anio, mes, dia)
        if regla:
            calidad.advertir(campo, f"'{valor}' es {regla}; se usó {f.isoformat()}")
        else:
            calidad.reparar(campo, f"'{valor}' se interpretó como {f.isoformat()}")
        return f

    raise SolicitudRechazada(campo, f"'{valor}' no se reconoce como fecha")
