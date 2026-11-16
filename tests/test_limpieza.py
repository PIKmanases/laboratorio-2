from datetime import date

import pytest

from app.esquemas import EntradaObservacion, EntradaPrediccion
from app.limpieza import SolicitudRechazada, limpiar_observacion, limpiar_prediccion
from app.limpieza.texto import normalizar_texto


def pred(catalogos, familias=None, **extra):
    cuerpo = {"tienda_id": 5, "fecha": "2025-11-17", "familias": familias or [{"familia_id": f, "en_promocion": 0} for f in catalogos.familias_ids]}
    cuerpo.update(extra)
    cuerpo = {k: v for k, v in cuerpo.items() if v is not ...}
    return limpiar_prediccion(EntradaPrediccion(**cuerpo), catalogos)


def obs(catalogos, ventas=None, **extra):
    cuerpo = {"tienda_id": 5, "fecha": "2025-11-17", "ventas": ventas or [{"familia_id": f, "ventas": 10} for f in catalogos.familias_ids]}
    cuerpo.update(extra)
    return limpiar_observacion(EntradaObservacion(**cuerpo), catalogos)


def acciones(r):
    return [(o.campo, o.accion) for o in r.calidad.observaciones]


def completa(todas, **cambios):
    return [{"familia_id": f, "en_promocion": 0, **cambios.get(f, {})} for f in todas]


# --- normalización -------------------------------------------------------------------
@pytest.mark.parametrize("entrada,esperado", [
    ("LÁCTEOS ", "lacteos"), ("Frutas y Verduras", "frutas_y_verduras"), ("T05", "t05"),
    ("  Tienda-ID ", "tienda_id"), ("ñandú", "nandu"),
])  # fmt: skip
def test_normalizar_texto(entrada, esperado):
    assert normalizar_texto(entrada) == esperado


# --- caso feliz ----------------------------------------------------------------------
def test_solicitud_perfecta_es_ok_sin_observaciones(catalogos, todas_las_familias):
    r = pred(catalogos, completa(todas_las_familias))
    assert r.calidad.estado == "ok" and r.calidad.observaciones == ()


# --- E01 tienda ----------------------------------------------------------------------
@pytest.mark.parametrize("valor", ["5", "T05", "tienda-5", " 05", 5.0, "5.0"])
def test_e01_tienda_reparable(catalogos, todas_las_familias, valor):
    r = pred(catalogos, completa(todas_las_familias), tienda_id=valor)
    assert r.solicitud.tienda_id == 5
    assert acciones(r) == [("tienda_id", "reparar")] and r.calidad.estado == "corregido"


@pytest.mark.parametrize("valor", ["T05-2", "tienda", "", "T99", 99, 5.5, True, [5], "0"])
def test_e01_tienda_rechazada(catalogos, valor):
    with pytest.raises(SolicitudRechazada) as e:
        pred(catalogos, tienda_id=valor)
    assert e.value.campo == "tienda_id"


# --- E02 familia ---------------------------------------------------------------------
@pytest.mark.parametrize("valor,canonica", [
    ("LÁCTEOS", "lacteos"), ("lacteos ", "lacteos"), ("Frutas y Verduras", "frutas_verduras"),
    ("Snacks y dulces", "snacks"), ("Bebé", "bebe"), ("cuidado-personal", "cuidado_personal"),
])  # fmt: skip
def test_e02_familia_reparable(catalogos, valor, canonica):
    r = pred(catalogos, [{"familia_id": valor, "en_promocion": 1}])
    assert r.solicitud.familias[0].familia_id == canonica
    assert ("familias[0].familia_id", "reparar") in acciones(r)


@pytest.mark.parametrize("valor", ["lacteo", "pescados", "", 7])
def test_e02_familia_desconocida_se_rechaza(catalogos, valor):
    with pytest.raises(SolicitudRechazada):
        pred(catalogos, [{"familia_id": valor, "en_promocion": 1}])


# --- E03 en_promocion ----------------------------------------------------------------
@pytest.mark.parametrize("valor,esperado", [("3", 3), (2.0, 2), (" 9 ", 9), ("1,0", 1)])
def test_e03_promocion_reparable(catalogos, valor, esperado):
    r = pred(catalogos, [{"familia_id": "abarrotes", "en_promocion": valor}])
    assert r.solicitud.familias[0].en_promocion == esperado
    assert ("familias[0].en_promocion", "reparar") in acciones(r)


@pytest.mark.parametrize("valor", [2.5, "2.5", "tres", -1, "-1", True, [1]])
def test_e03_promocion_rechazada(catalogos, valor):
    with pytest.raises(SolicitudRechazada):
        pred(catalogos, [{"familia_id": "abarrotes", "en_promocion": valor}])


def test_promocion_ausente_se_asume_cero_con_advertencia(catalogos):
    r = pred(catalogos, [{"familia_id": "abarrotes"}])
    assert r.solicitud.familias[0].en_promocion == 0
    assert ("familias[0].en_promocion", "advertir") in acciones(r)


# --- E04 fecha -----------------------------------------------------------------------
@pytest.mark.parametrize("valor", ["2025-11-17T00:00:00", "17/11/2025", "11/17/2025", "2025/11/17", " 2025-11-17 ", "17-11-2025"])
def test_e04_fecha_reparable(catalogos, valor):
    r = pred(catalogos, fecha=valor)
    assert r.solicitud.fecha == date(2025, 11, 17)
    assert ("fecha", "reparar") in acciones(r) and r.calidad.estado == "corregido"


def test_e04_fecha_ambigua_advierte_dia_mes(catalogos):
    r = pred(catalogos, fecha="05/06/2025")
    assert r.solicitud.fecha == date(2025, 6, 5)
    assert ("fecha", "advertir") in acciones(r) and r.calidad.estado == "advertencia"


def test_e04_fecha_con_hora_distinta_de_cero_advierte(catalogos):
    r = pred(catalogos, fecha="2025-11-17T23:30:00")
    assert r.solicitud.fecha == date(2025, 11, 17) and ("fecha", "advertir") in acciones(r)


@pytest.mark.parametrize("valor", ["2025-02-30", "31/04/2025", "13/13/2025", "ayer", "", 20251117, "2025-11"])
def test_e04_fecha_imposible_o_ilegible(catalogos, valor):
    with pytest.raises(SolicitudRechazada) as e:
        pred(catalogos, fecha=valor)
    assert e.value.campo == "fecha"


# --- E05 faltan familias -------------------------------------------------------------
def test_e05_faltan_familias_advierte_y_procesa_las_recibidas(catalogos, todas_las_familias):
    r = pred(catalogos, completa(todas_las_familias[:10]))
    assert len(r.solicitud.familias) == 10
    assert acciones(r) == [("familias", "advertir")]
    assert "mascotas" in r.calidad.observaciones[0].detalle


# --- E06 familia repetida ------------------------------------------------------------
def test_e06_repetida_distinta_advierte_y_gana_la_ultima(catalogos):
    r = pred(catalogos, [{"familia_id": "abarrotes", "en_promocion": 0}, {"familia_id": "ABARROTES", "en_promocion": 3}])
    assert [(f.familia_id, f.en_promocion) for f in r.solicitud.familias] == [("abarrotes", 3)]
    assert ("familias[1]", "advertir") in acciones(r)


def test_e06_repetida_identica_se_repara(catalogos):
    r = pred(catalogos, [{"familia_id": "abarrotes", "en_promocion": 2}] * 2)
    assert len(r.solicitud.familias) == 1 and ("familias[1]", "reparar") in acciones(r)


# --- E07 campo renombrado ------------------------------------------------------------
def test_e07_campo_renombrado(catalogos):
    r = pred(catalogos, tienda_id=..., tienda=5)
    assert r.solicitud.tienda_id == 5 and ("tienda_id", "reparar") in acciones(r)


def test_e07_variante_de_mayusculas_y_items(catalogos):
    r = pred(catalogos, [{"Familia": "abarrotes", "promo": 1}], tienda_id=..., **{"Tienda ID": 5})
    assert r.solicitud.tienda_id == 5 and r.solicitud.familias[0].en_promocion == 1


def test_e07_conflicto_entre_equivalentes_rechaza(catalogos):
    with pytest.raises(SolicitudRechazada):
        pred(catalogos, tienda=6)


def test_campo_desconocido_se_ignora_con_advertencia(catalogos):
    r = pred(catalogos, clima="lluvia")
    assert acciones(r) == [("clima", "advertir")]


@pytest.mark.parametrize("faltante", ["tienda_id", "fecha", "familias"])
def test_campo_obligatorio_faltante_rechaza(catalogos, faltante):
    with pytest.raises(SolicitudRechazada) as e:
        pred(catalogos, **{faltante: ...})
    assert e.value.campo == faltante


def test_lista_vacia_rechaza(catalogos):
    with pytest.raises(SolicitudRechazada):
        limpiar_prediccion(EntradaPrediccion(tienda_id=5, fecha="2025-11-17", familias=[]), catalogos)


# --- E08 ventas negativas ------------------------------------------------------------
def test_e08_ventas_negativas_se_omiten_con_advertencia(catalogos):
    r = obs(catalogos, [{"familia_id": "abarrotes", "ventas": 5}, {"familia_id": "carnes", "ventas": -4}])
    assert [(v.familia_id, v.ventas) for v in r.solicitud.ventas] == [("abarrotes", 5)]
    assert ("ventas[1].ventas", "advertir") in acciones(r)


def test_ventas_cero_es_valido(catalogos):
    r = obs(catalogos, [{"familia_id": "abarrotes", "ventas": 0}])
    assert r.solicitud.ventas[0].ventas == 0 and ("ventas[0].ventas", "advertir") not in acciones(r)


def test_todos_los_registros_inutilizables_rechaza(catalogos):
    with pytest.raises(SolicitudRechazada):
        obs(catalogos, [{"familia_id": "abarrotes", "ventas": -1}])


def test_ventas_sin_valor_se_omiten_con_advertencia(catalogos):
    r = obs(catalogos, [{"familia_id": "abarrotes", "ventas": 5}, {"familia_id": "carnes"}])
    assert len(r.solicitud.ventas) == 1 and ("ventas[1].ventas", "advertir") in acciones(r)


# --- E09 repetido dentro de la solicitud ---------------------------------------------
def test_e09_repetida_con_ventas_distintas_advierte(catalogos):
    r = obs(catalogos, [{"familia_id": "abarrotes", "ventas": 5}, {"familia_id": "abarrotes", "ventas": 7}])
    assert r.solicitud.ventas[0].ventas == 7 and ("ventas[1]", "advertir") in acciones(r)


# --- E10 formato numérico ------------------------------------------------------------
@pytest.mark.parametrize("valor,esperado", [
    ("1,234", 1234), ("812,0", 812), ("50,0", 50), (812.0, 812), ("812.0", 812), ("1,234,567", 1234567),
    ("1,234.0", 1234), ("1.234,0", 1234), (" 77 ", 77), ("0", 0),
])  # fmt: skip
def test_e10_numero_reparable(catalogos, valor, esperado):
    r = obs(catalogos, [{"familia_id": "abarrotes", "ventas": valor}])
    assert r.solicitud.ventas[0].ventas == esperado
    assert ("ventas[0].ventas", "reparar") in acciones(r)


@pytest.mark.parametrize("valor", ["1.234", "812,5", "12,34", "abc", "", "1,23,4", 812.5, float("nan"), float("inf"), True, [1], {"a": 1}])
def test_e10_numero_ilegible_rechaza(catalogos, valor):
    with pytest.raises(SolicitudRechazada):
        obs(catalogos, [{"familia_id": "abarrotes", "ventas": valor}])


def test_observacion_perfecta_es_ok(catalogos, todas_las_familias):
    r = obs(catalogos, [{"familia_id": f, "ventas": 1} for f in todas_las_familias])
    assert r.calidad.estado == "ok"
