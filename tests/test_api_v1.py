import json
from pathlib import Path

import pytest

EJEMPLOS = Path(__file__).parent.parent / "data" / "ejemplos"


def pedir(client, cuerpo):
    return client.post("/v1/prediccion", json=cuerpo)


def cuerpo_pred(todas, **cambios):
    base = {"tienda_id": 5, "fecha": "2025-11-17", "familias": [{"familia_id": f, "en_promocion": 0} for f in todas]}
    return {**base, **cambios}


def test_contrato_de_respuesta_y_predictor_sin_fuga(client, todas_las_familias):
    r = pedir(client, cuerpo_pred(todas_las_familias))
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"tienda_id", "fecha", "version_modelo", "predicciones", "calidad"}
    assert (j["tienda_id"], j["fecha"], j["version_modelo"]) == (5, "2025-11-17", "0")
    assert j["calidad"] == {"estado": "ok", "observaciones": []}
    abarrotes = j["predicciones"][0]
    # promedio de 100 y 200; el 9999 del propio día no entra
    assert abarrotes == {"familia_id": "abarrotes", "venta_esperada": 150.0, "cantidad_sugerida": 150}
    assert all(p["cantidad_sugerida"] >= 0 and isinstance(p["cantidad_sugerida"], int) for p in j["predicciones"])


def test_misma_fecha_se_puede_pedir_varias_veces(client, todas_las_familias):
    assert pedir(client, cuerpo_pred(todas_las_familias)).json() == pedir(client, cuerpo_pred(todas_las_familias)).json()


def test_prediccion_corregida_informa_la_calidad(client, todas_las_familias):
    j = pedir(client, cuerpo_pred(todas_las_familias, tienda_id="T05", fecha="17/11/2025")).json()
    assert j["calidad"]["estado"] == "corregido"
    assert {(o["campo"], o["accion"]) for o in j["calidad"]["observaciones"]} == {("tienda_id", "reparar"), ("fecha", "reparar")}


def test_prediccion_con_advertencia(client, todas_las_familias):
    j = pedir(client, cuerpo_pred(todas_las_familias[:10])).json()
    assert j["calidad"]["estado"] == "advertencia" and len(j["predicciones"]) == 10


@pytest.mark.parametrize("cambios", [{"tienda_id": "T99"}, {"fecha": "2025-02-30"}, {"tienda_id": "T05-2"}])
def test_rechazo_responde_422_con_detail_de_texto(client, todas_las_familias, cambios):
    r = pedir(client, cuerpo_pred(todas_las_familias, **cambios))
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and r.json()["detail"]


@pytest.mark.parametrize("cuerpo", [[1, 2], {"familias": "abarrotes"}, {"tienda_id": 5, "fecha": "2025-11-17", "familias": [1]}])
def test_estructura_ilegible_responde_422(client, cuerpo):
    r = pedir(client, cuerpo)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_json_roto_responde_422(client):
    r = client.post("/v1/prediccion", content=b"{no es json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and "detail" in r.json()


def test_observaciones_calidad_sin_base_de_datos(client):
    cuerpo = {"tienda_id": 5, "fecha": "2025-11-17", "ventas": [{"familia_id": "abarrotes", "ventas": "1,234"}, {"familia_id": "carnes", "ventas": -4}]}
    r = client.post("/v1/observaciones", json=cuerpo)
    j = r.json()
    assert r.status_code == 200 and j["registros_guardados"] == 0
    assert j["calidad"]["estado"] == "advertencia"
    assert {(o["campo"], o["accion"]) for o in j["calidad"]["observaciones"]} >= {("ventas[0].ventas", "reparar"), ("ventas[1].ventas", "advertir")}


def test_observaciones_rechazo(client):
    r = client.post("/v1/observaciones", json={"tienda_id": 5, "fecha": "2025-11-17", "ventas": [{"familia_id": "abarrotes", "ventas": "mucho"}]})
    assert r.status_code == 422 and "ventas[0].ventas" in r.json()["detail"]


# --- los ejemplos del equipo docente ------------------------------------------------
ESPERADO = {  # archivo -> (endpoint, status, estado)
    "E01_tienda_texto": ("prediccion", 200, "corregido"),
    "E02_familia_otra_forma": ("prediccion", 200, "corregido"),
    "E03_promocion_texto": ("prediccion", 200, "corregido"),
    "E04_fecha_formato": ("prediccion", 200, "corregido"),
    "E05_faltan_familias": ("prediccion", 200, "advertencia"),
    "E06_familia_repetida": ("prediccion", 200, "advertencia"),
    "E07_campo_renombrado": ("prediccion", 200, "corregido"),
    "E08_ventas_negativas": ("observaciones", 200, "advertencia"),
    "E09_envio_repetido": ("observaciones", 200, "ok"),
    "E10_numero_formato": ("observaciones", 200, "corregido"),
}


@pytest.mark.skipif(not EJEMPLOS.exists(), reason="data/ no está en el repo (ignorado por git)")
@pytest.mark.parametrize("nombre", ESPERADO)
def test_ejemplos_del_catalogo(client, nombre):
    endpoint, status, estado = ESPERADO[nombre]
    r = client.post(f"/v1/{endpoint}", json=json.loads((EJEMPLOS / "errores" / f"{nombre}.json").read_text()))
    assert (r.status_code, r.json()["calidad"]["estado"]) == (status, estado)


@pytest.mark.skipif(not EJEMPLOS.exists(), reason="data/ no está en el repo (ignorado por git)")
def test_ejemplos_validos_son_ok(client):
    for archivo, endpoint in (("prediccion_valida", "prediccion"), ("observacion_valida", "observaciones")):
        r = client.post(f"/v1/{endpoint}", json=json.loads((EJEMPLOS / f"{archivo}.json").read_text()))
        assert r.status_code == 200 and r.json()["calidad"] == {"estado": "ok", "observaciones": []}
