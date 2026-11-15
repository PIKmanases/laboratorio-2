"""Orquestadores de la etapa 2: reciben la entrada tolerante, devuelven el modelo canónico
estricto (etapa 3) y la `Calidad` acumulada. Lanzan `SolicitudRechazada` si no se puede."""

from dataclasses import dataclass

from ..esquemas import (
    EntradaObservacion,
    EntradaPrediccion,
    FamiliaSolicitada,
    ObservacionVentas,
    SolicitudPrediccion,
    VentaFamilia,
)
from .calidad import Calidad, SolicitudRechazada
from .campos import (
    ALIAS_FAMILIAS,
    ALIAS_FECHA,
    ALIAS_ITEM_PRED,
    ALIAS_ITEM_VENTA,
    ALIAS_TIENDA,
    ALIAS_VENTAS_LISTA,
    a_entero,
    a_familia,
    a_fecha,
    a_tienda,
    exigir,
    lista_de_objetos,
    resolver_campos,
)
from .catalogos import Catalogos


@dataclass(frozen=True)
class PrediccionLimpia:
    solicitud: SolicitudPrediccion
    calidad: Calidad


@dataclass(frozen=True)
class ObservacionLimpia:
    solicitud: ObservacionVentas
    calidad: Calidad


def _encabezado(crudo: dict, alias_lista: dict, calidad: Calidad, catalogos: Catalogos):
    """Campos comunes a ambos endpoints: tienda, fecha y la lista de items (cruda)."""
    alias = {**ALIAS_TIENDA, **ALIAS_FECHA, **alias_lista}
    campos = resolver_campos(crudo, alias, calidad)
    tienda = a_tienda(exigir(campos, "tienda_id"), "tienda_id", calidad, catalogos)
    fecha = a_fecha(exigir(campos, "fecha"), "fecha", calidad)
    nombre_lista = next(iter(alias_lista))
    items = lista_de_objetos(exigir(campos, nombre_lista), nombre_lista)
    return tienda, fecha, nombre_lista, items


def _avisar_faltantes(vistas: dict, catalogos: Catalogos, campo: str, calidad: Calidad) -> None:
    faltan = [f for f in catalogos.familias_ids if f not in vistas]
    if faltan:
        calidad.advertir(
            campo, f"faltan {len(faltan)} familias ({', '.join(faltan)}); solo se procesan las recibidas"
        )


def _registrar(vistas: dict, familia: str, valor: int, campo: str, calidad: Calidad, que: str) -> None:
    """Familia repetida: idéntica -> reparar (se deja una); distinta -> advertir (gana la última)."""
    if familia in vistas:
        if vistas[familia] == valor:
            calidad.reparar(campo, f"'{familia}' venía repetida con el mismo valor; se dejó un registro")
        else:
            calidad.advertir(
                campo,
                f"'{familia}' venía repetida con {que} distinto ({vistas[familia]} y {valor}); se usó el último",
            )
    vistas[familia] = valor


def limpiar_prediccion(entrada: EntradaPrediccion, catalogos: Catalogos) -> PrediccionLimpia:
    calidad = Calidad()
    crudo = entrada.model_dump(exclude_unset=True)
    tienda, fecha, nombre_lista, items = _encabezado(crudo, ALIAS_FAMILIAS, calidad, catalogos)

    promos: dict[str, int] = {}
    for i, item in enumerate(items):
        pref = f"familias[{i}]."
        campos = resolver_campos(item, ALIAS_ITEM_PRED, calidad, pref)
        familia = a_familia(exigir(campos, "familia_id", pref), pref + "familia_id", calidad, catalogos)
        if "en_promocion" in campos:
            promo = a_entero(campos["en_promocion"], pref + "en_promocion", calidad)
            if promo < 0:
                raise SolicitudRechazada(pref + "en_promocion", f"{promo} es negativo")
        else:
            promo = 0
            calidad.advertir(pref + "en_promocion", "no venía; se asumió 0 (sin promoción)")
        _registrar(promos, familia, promo, f"familias[{i}]", calidad, "en_promocion")

    _avisar_faltantes(promos, catalogos, "familias", calidad)
    solicitud = SolicitudPrediccion(
        tienda_id=tienda,
        fecha=fecha,
        familias=tuple(FamiliaSolicitada(familia_id=f, en_promocion=p) for f, p in promos.items()),
    )
    return PrediccionLimpia(solicitud, calidad)


def limpiar_observacion(entrada: EntradaObservacion, catalogos: Catalogos) -> ObservacionLimpia:
    calidad = Calidad()
    crudo = entrada.model_dump(exclude_unset=True)
    tienda, fecha, nombre_lista, items = _encabezado(crudo, ALIAS_VENTAS_LISTA, calidad, catalogos)

    ventas: dict[str, int] = {}
    for i, item in enumerate(items):
        pref = f"ventas[{i}]."
        campos = resolver_campos(item, ALIAS_ITEM_VENTA, calidad, pref)
        familia = a_familia(exigir(campos, "familia_id", pref), pref + "familia_id", calidad, catalogos)
        if "ventas" not in campos:
            calidad.advertir(pref + "ventas", f"'{familia}' sin valor de ventas: se omitió (no se asume 0)")
            continue
        valor = a_entero(campos["ventas"], pref + "ventas", calidad)
        if valor < 0:
            calidad.advertir(
                pref + "ventas", f"'{familia}' con ventas negativas ({valor}): se omitió (no se asume 0)"
            )
            continue
        _registrar(ventas, familia, valor, f"ventas[{i}]", calidad, "ventas")

    if not ventas:
        raise SolicitudRechazada("ventas", "ningún registro es utilizable")
    _avisar_faltantes(ventas, catalogos, "ventas", calidad)
    solicitud = ObservacionVentas(
        tienda_id=tienda,
        fecha=fecha,
        ventas=tuple(VentaFamilia(familia_id=f, ventas=v) for f, v in ventas.items()),
    )
    return ObservacionLimpia(solicitud, calidad)
