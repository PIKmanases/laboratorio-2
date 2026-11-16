"""Persistencia vista desde la API, con un repositorio falso (sin Postgres)."""

import pytest

TODAS = ["abarrotes", "bebidas", "lacteos", "carnes", "panaderia", "frutas_verduras", "limpieza",
         "cuidado_personal", "licores", "snacks", "bebe", "mascotas"]  # fmt: skip


def pred(**cambios):
    return {"tienda_id": 5, "fecha": "2025-11-17", "familias": [{"familia_id": f, "en_promocion": 2} for f in TODAS], **cambios}


def obs(valores=None, **cambios):
    valores = valores or {f: 10 for f in TODAS}
    return {"tienda_id": 5, "fecha": "2025-11-17", "ventas": [{"familia_id": f, "ventas": v} for f, v in valores.items()], **cambios}


def test_prediccion_se_guarda_con_su_calidad_y_crudo(client, repo):
    r = client.post("/v1/prediccion", json=pred(tienda_id="T05"))
    assert r.status_code == 200
    (g,) = repo.solicitudes
    assert g["tipo"] == "prediccion" and g["solicitud"].tienda_id == 5
    assert g["crudo"]["tienda_id"] == "T05"  # lo que llegó, sin limpiar
    assert g["calidad"].estado == "corregido"
    assert g["respuesta"].model_dump(mode="json") == r.json()  # se guarda lo mismo que se respondió


def test_misma_fecha_varias_veces_guarda_cada_solicitud(client, repo):
    for _ in range(3):
        assert client.post("/v1/prediccion", json=pred()).status_code == 200
    assert len(repo.solicitudes) == 3


def test_observacion_idempotente(client, repo):
    assert client.post("/v1/observaciones", json=obs()).json()["registros_guardados"] == 12
    assert client.post("/v1/observaciones", json=obs()).json()["registros_guardados"] == 0
    cambiada = obs({**{f: 10 for f in TODAS}, "carnes": 11})
    assert client.post("/v1/observaciones", json=cambiada).json()["registros_guardados"] == 1
    assert len(repo.solicitudes) == 3  # cada envío queda registrado aunque no cambie nada


def test_observacion_con_registro_omitido_cuenta_solo_lo_guardado(client):
    r = client.post("/v1/observaciones", json=obs({"abarrotes": 5, "carnes": -4}))
    assert r.json()["registros_guardados"] == 1


def test_rechazo_se_registra_y_responde_422(client, repo):
    r = client.post("/v1/prediccion", json=pred(tienda_id="T99"))
    assert r.status_code == 422
    (rec,) = repo.rechazos
    assert rec["endpoint"] == "prediccion" and rec["campo"] == "tienda_id" and rec["crudo"]["tienda_id"] == "T99"
    assert repo.solicitudes == []


def test_rechazo_de_estructura_tambien_se_registra(client, repo):
    r = client.post("/v1/observaciones", json={"tienda_id": 5, "fecha": "2025-11-17", "ventas": "mucho"})
    assert r.status_code == 422
    assert repo.rechazos[0]["endpoint"] == "observaciones"


@pytest.mark.parametrize("ruta,cuerpo", [("prediccion", pred()), ("observaciones", obs())])
def test_sin_base_de_datos_responde_503_y_no_inventa(client, repo, ruta, cuerpo):
    repo.caida = True
    r = client.post(f"/v1/{ruta}", json=cuerpo)
    assert r.status_code == 503 and r.json()["detail"]
    assert repo.solicitudes == []


def test_sin_base_de_datos_un_rechazo_sigue_siendo_422(client, repo):
    repo.caida = True
    assert client.post("/v1/prediccion", json=pred(tienda_id="T99")).status_code == 422


def test_se_recupera_cuando_vuelve_la_base(client, repo):
    repo.caida = True
    assert client.post("/v1/observaciones", json=obs()).status_code == 503
    repo.caida = False
    assert client.post("/v1/observaciones", json=obs()).json()["registros_guardados"] == 12
