import httpx
import pytest
from fastapi.testclient import TestClient

from app.airflow_client import AirflowClient, AirflowNoDisponible
from app.limpieza import Catalogos
from app.main import create_app
from conftest import FAMILIAS
from fakes import AirflowFalso, PredictorPromedio, RepositorioMemoria, ReentrenamientosMemoria


@pytest.fixture
def af():
    return AirflowFalso()


@pytest.fixture
def rt():
    return ReentrenamientosMemoria()


@pytest.fixture
def client(af, rt):
    app = create_app(catalogos=Catalogos.desde_filas(range(1, 11), FAMILIAS), predictor=PredictorPromedio([]),
                     repositorio=RepositorioMemoria(), airflow=af, reentrenamientos=rt)  # fmt: skip
    return TestClient(app)


# ------------------------------------------------------------------ POST
def test_post_dispara_guarda_en_curso_y_responde_202_con_el_dag_run_id(client, af, rt):
    r = client.post("/admin/reentrenar")
    assert r.status_code == 202
    id_ = r.json()["id"]
    assert set(r.json()) == {"id"} and af.disparados == [id_]  # el id es el dag_run_id de Airflow
    assert rt.filas[id_]["estado"] == "en_curso"


def test_post_si_airflow_no_responde_503_y_no_deja_fila(client, af, rt):
    af.caido = True
    r = client.post("/admin/reentrenar")
    assert r.status_code == 503 and "Airflow" in r.json()["detail"]
    assert rt.filas == {}


def test_post_si_app_db_esta_caida_503_y_no_dispara(client, af, rt):
    rt.caida = True
    assert client.post("/admin/reentrenar").status_code == 503
    assert af.disparados == []


def test_dos_disparos_seguidos_ambos_202_y_airflow_los_encola(client, af, rt):
    a, b = client.post("/admin/reentrenar").json()["id"], client.post("/admin/reentrenar").json()["id"]
    assert a != b and af.disparados == [a, b] and set(rt.filas) == {a, b}


# ------------------------------------------------------------------ GET
def test_get_id_desconocido_404_y_no_consulta_a_airflow(client, af):
    r = client.get("/admin/reentrenar/no-existe")
    assert r.status_code == 404 and "detail" in r.json()
    assert af.consultados == []  # solo ids que creó la API


def test_get_en_curso_no_trae_los_campos_finales(client, af):
    id_ = client.post("/admin/reentrenar").json()["id"]
    for estado in ("queued", "running"):
        af.estados[id_] = estado
        assert client.get(f"/admin/reentrenar/{id_}").json() == {"estado": "en_curso"}


def terminar(rt, id_, **campos):
    rt.filas[id_].update({"estado": "exitoso", **campos})


def test_get_exitoso_promovido_devuelve_la_version_nueva(client, af, rt):
    id_ = client.post("/admin/reentrenar").json()["id"]
    af.estados[id_] = "success"
    terminar(rt, id_, promovido=True, version_anterior="3", version_nueva="4", motivo="mejora 9 %")
    assert client.get(f"/admin/reentrenar/{id_}").json() == {"estado": "exitoso", "promovido": True, "version": "4", "motivo": "mejora 9 %"}


def test_get_exitoso_rechazado_devuelve_la_version_vigente_que_sigue(client, af, rt):
    id_ = client.post("/admin/reentrenar").json()["id"]
    af.estados[id_] = "success"
    terminar(rt, id_, promovido=False, version_anterior="3", version_nueva="4", motivo="no supera el margen")
    j = client.get(f"/admin/reentrenar/{id_}").json()
    assert j == {"estado": "exitoso", "promovido": False, "version": "3", "motivo": "no supera el margen"}  # la vigente, no la candidata


def test_get_fallido_usa_el_motivo_guardado(client, af, rt):
    id_ = client.post("/admin/reentrenar").json()["id"]
    af.estados[id_] = "failed"
    rt.filas[id_].update({"estado": "fallido", "promovido": False, "version_anterior": "3", "motivo": "falló antes de decidir"})
    assert client.get(f"/admin/reentrenar/{id_}").json() == {"estado": "fallido", "promovido": False, "version": "3", "motivo": "falló antes de decidir"}


def test_get_fallido_sin_fila_completada_por_timeout_da_motivo_por_omision(client, af):
    id_ = client.post("/admin/reentrenar").json()["id"]
    af.estados[id_] = "failed"  # p. ej. dagrun_timeout: registrar_resultado nunca corrió
    j = client.get(f"/admin/reentrenar/{id_}").json()
    assert j["estado"] == "fallido" and j["promovido"] is False and "tiempo límite" in j["motivo"] and isinstance(j["version"], str)


def test_get_con_airflow_caido_responde_con_lo_guardado(client, af, rt):
    id_ = client.post("/admin/reentrenar").json()["id"]
    af.caido = True
    assert client.get(f"/admin/reentrenar/{id_}").json() == {"estado": "en_curso"}
    terminar(rt, id_, promovido=False, version_anterior="3", motivo="x")
    assert client.get(f"/admin/reentrenar/{id_}").json()["estado"] == "exitoso"


def test_get_con_app_db_caida_503(client, af, rt):
    id_ = client.post("/admin/reentrenar").json()["id"]
    rt.caida = True
    assert client.get(f"/admin/reentrenar/{id_}").status_code == 503


# ------------------------------------------------------------------ cliente HTTP de Airflow
def cliente(handler, **kw):
    http = httpx.Client(base_url="http://airflow", transport=httpx.MockTransport(handler))
    return AirflowClient("http://airflow", "admin", "secreto", "reentrenar_modelo", http=http, **kw)


def test_cliente_autentica_con_jwt_y_dispara_con_el_id_y_sin_logical_date():
    vistos = []

    def h(req: httpx.Request):
        vistos.append(req)
        if req.url.path == "/auth/token":
            return httpx.Response(201, json={"access_token": "T0K3N"})
        return httpx.Response(200, json={"dag_run_id": "x"})

    cliente(h).disparar("reentrenar_abc")
    token, disparo = vistos
    assert token.url.path == "/auth/token" and b'"username":"admin"' in token.content.replace(b" ", b"")
    assert disparo.url.path == "/api/v2/dags/reentrenar_modelo/dagRuns" and disparo.headers["authorization"] == "Bearer T0K3N"
    assert b'"dag_run_id":"reentrenar_abc"' in disparo.content.replace(b" ", b"") and b'"logical_date":null' in disparo.content.replace(b" ", b"")


def test_cliente_reutiliza_el_token_y_lo_renueva_ante_un_401():
    llamadas = {"token": 0}

    def h(req):
        if req.url.path == "/auth/token":
            llamadas["token"] += 1
            return httpx.Response(201, json={"access_token": f"t{llamadas['token']}"})
        return httpx.Response(401) if req.headers["authorization"] == "Bearer t1" else httpx.Response(200, json={"state": "running"})

    c = cliente(h)
    assert c.estado("r1") == "running"  # t1 dio 401 -> renovó -> t2
    assert llamadas["token"] == 2
    c.estado("r1")
    assert llamadas["token"] == 2  # ya no pide otro


@pytest.mark.parametrize("respuesta", [httpx.Response(401, json={}), httpx.Response(500)])
def test_cliente_credenciales_malas_o_error_del_servidor(respuesta):
    c = cliente(lambda req: respuesta)
    with pytest.raises(AirflowNoDisponible):
        c.disparar("x")


def test_cliente_dag_inexistente_o_disparo_rechazado():
    def h(req):
        return httpx.Response(201, json={"access_token": "t"}) if req.url.path == "/auth/token" else httpx.Response(404, json={"detail": "DAG not found"})

    with pytest.raises(AirflowNoDisponible):
        cliente(h).disparar("x")


def test_cliente_red_caida_o_timeout():
    def h(req):
        raise httpx.ConnectError("sin red")

    with pytest.raises(AirflowNoDisponible):
        cliente(h).disparar("x")
    with pytest.raises(AirflowNoDisponible):
        cliente(h).estado("x")


def test_cliente_estado_desconocido_es_none():
    def h(req):
        return httpx.Response(201, json={"access_token": "t"}) if req.url.path == "/auth/token" else httpx.Response(404)

    assert cliente(h).estado("nope") is None
