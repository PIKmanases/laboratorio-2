"""Flujo del reentrenamiento (sin Airflow) contra un MLflow local (SQLite):
linaje completo, comparación justa y protección del alias."""

import mlflow
import pandas as pd
import pytest
from mlflow import MlflowClient

from ml import registro, reentrenamiento as rt
from ml.datos import Datos
from ml.entrenar import entrenar
from sinteticos import datos_sinteticos

RAPIDO = {"num_boost_round": 20, "num_threads": 1}


@pytest.fixture(autouse=True)
def mlflow_local(tmp_path, monkeypatch):
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/mlflow.db")
    # ventanas más cortas y LightGBM rápido: los datos sintéticos son pequeños
    monkeypatch.setattr(rt, "DIAS_CALIBRACION", 20)
    monkeypatch.setattr(rt, "DIAS_PRUEBA", 20)
    original = rt.entrenar
    monkeypatch.setattr(rt, "entrenar", lambda datos, fecha_corte=None, tipo="lightgbm", params=None, **kw: original(datos, fecha_corte, tipo, params or RAPIDO, dias_calibracion=20, dias_prueba=20, **kw))
    yield
    mlflow.set_tracking_uri("")


def cargador(d: Datos):
    def cargar(hasta=None):
        v = d.ventas if hasta is None else d.ventas[d.ventas.fecha <= pd.Timestamp(hasta)]
        return Datos(v.copy(), d.tiendas, d.feriados, d.costos)

    return cargar


def pasos(cargar, dag_run_id="r1", margen=0.015):
    plan = rt.preparar_datos(cargar)
    cand = rt.entrenar_candidato(cargar, plan, dag_run_id)
    dec = rt.evaluar_y_decidir(cargar, plan, cand, margen)
    promo = rt.promover(cand["version"]) if dec["promover"] else None
    return plan, cand, dec, promo


def alias():
    try:
        return str(MlflowClient().get_model_version_by_alias(registro.MODELO_REGISTRADO, registro.ALIAS).version)
    except mlflow.exceptions.MlflowException:
        return None


def tags(version):
    return MlflowClient().get_model_version(registro.MODELO_REGISTRADO, version).tags


def test_primer_reentrenamiento_sin_vigente_promueve_y_deja_alias():
    c = cargador(datos_sinteticos())
    plan, cand, dec, promo = pasos(c)
    assert plan["vigente_version"] is None and dec["modo_comparacion"] == "sin_vigente"
    assert dec["promover"] and promo == {"promovido": True, "version": "1"} and alias() == "1"
    assert tags("1")["decision"] == "promovido" and tags("1")["dag_run_id"] == "r1"


def test_candidato_que_no_gana_se_registra_igual_con_tags_y_el_alias_no_se_mueve():
    """Sin datos nuevos el vigente ya vio la ventana -> se reajusta su receta; misma receta = empate."""
    c = cargador(datos_sinteticos())
    pasos(c)  # v1 promovida
    plan, cand, dec, promo = pasos(c, "r2")
    assert dec["modo_comparacion"] == "vigente_reajustado"
    assert not dec["promover"] and promo is None
    assert alias() == "1" and cand["version"] == "2"  # el candidato quedó registrado (linaje) pero sin alias
    t = tags("2")
    assert t["decision"] == "rechazado" and "no supera el margen" in t["motivo"] and t["vigente_version"] == "1"
    assert float(t["costo_candidato"]) == pytest.approx(float(t["costo_vigente"]))
    assert MlflowClient().get_model_version(registro.MODELO_REGISTRADO, "2").description == t["motivo"]


def test_con_datos_nuevos_el_vigente_se_evalua_tal_cual_y_ninguno_vio_la_ventana():
    d = datos_sinteticos(dias=150)
    # el vigente se entrenó con datos hasta el día 110 (antes de que llegaran los últimos 40)
    viejo = Datos(d.ventas[d.ventas.fecha <= d.ventas.fecha.min() + pd.Timedelta(days=109)], d.tiendas, d.feriados, d.costos)
    r = entrenar(viejo, tipo="lightgbm", params=RAPIDO, dias_calibracion=20, dias_prueba=20)
    registro.registrar([r])
    assert alias() == "1"
    plan, cand, dec, _ = pasos(cargador(d), "r2")
    assert plan["fin"] == "2025-05-30" and plan["fin_calibracion"] == "2025-05-11"
    assert dec["modo_comparacion"] == "vigente_real"  # ultimo_dia del vigente (2025-04-20) < inicio de la prueba
    assert pd.Timestamp(r.modelo.meta["ultimo_dia"]) < pd.Timestamp(dec["ventana_prueba"].split("..")[0])
    assert dec["candidato"]["n_prueba"] == dec["vigente"]["n_prueba"] == 4 * 20  # MISMA ventana


def test_vigente_malo_es_reemplazado_por_un_candidato_claramente_mejor(monkeypatch):
    d = datos_sinteticos(dias=150)
    c = cargador(d)
    plan = rt.preparar_datos(c)
    baseline = entrenar(d, tipo="baseline", dias_calibracion=20, dias_prueba=20)
    # el vigente es un baseline cuyo ultimo_dia es anterior a la prueba (no la vio)
    baseline.modelo.meta["ultimo_dia"] = "2025-01-31"
    registro.registrar([baseline], promover=True)
    plan = rt.preparar_datos(c)
    cand = rt.entrenar_candidato(c, plan, "r2")
    # forzamos un vigente 30 % más caro que el candidato: la regla debe promover
    monkeypatch.setattr(rt, "evaluar_modelo", lambda pron, datos, desde: {**cand["metricas"], "costo_total": cand["metricas"]["costo_total"] * 1.3})
    dec = rt.evaluar_y_decidir(c, plan, cand)
    assert dec["promover"] and dec["modo_comparacion"] == "vigente_real"
    rt.promover(cand["version"])
    assert alias() == cand["version"] == "2" and tags("2")["decision"] == "promovido"


def test_si_falla_el_entrenamiento_el_alias_no_se_toca(monkeypatch):
    c = cargador(datos_sinteticos())
    pasos(c)
    monkeypatch.setattr(rt, "entrenar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falló el entrenamiento")))
    plan = rt.preparar_datos(c)
    with pytest.raises(RuntimeError):
        rt.entrenar_candidato(c, plan, "r2")
    assert alias() == "1" and len(MlflowClient().search_model_versions(f"name='{registro.MODELO_REGISTRADO}'")) == 1


def test_si_faltan_datos_falla_al_preparar_sin_tocar_nada():
    corto = datos_sinteticos(dias=50)
    with pytest.raises(ValueError, match="insuficientes"):
        rt.preparar_datos(cargador(corto))
    assert alias() is None


def test_ids_inconsistentes_fallan_al_preparar():
    d = datos_sinteticos()
    d.ventas["familia_id"] = d.ventas["familia_id"].replace({"lacteos": "Lácteos"})
    with pytest.raises(ValueError, match="familia_id distintos"):
        rt.preparar_datos(cargador(d))


def test_si_mlflow_no_responde_al_mover_el_alias_el_vigente_queda_intacto(monkeypatch):
    c = cargador(datos_sinteticos())
    pasos(c)
    plan, cand, dec, _ = pasos(c, "r2")  # v2 rechazada
    monkeypatch.setattr(MlflowClient, "set_registered_model_alias", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("MLflow caído")))
    with pytest.raises(ConnectionError):
        rt.promover(cand["version"])
    monkeypatch.undo()
    assert alias() == "1"


# ------------------------------------------------------------------ resumen del resultado (puro)
PLAN = {"vigente_version": "3"}
CAND = {"version": "4"}


def test_resumen_promovido():
    r = rt.resumen_resultado(PLAN, CAND, {"promover": True, "motivo": "mejora 9 %"}, {"promovido": True, "version": "4"})
    assert r == {"estado": "exitoso", "promovido": True, "version_anterior": "3", "version_nueva": "4", "motivo": "mejora 9 %"}


def test_resumen_rechazado_es_exitoso_pero_no_promovido():
    r = rt.resumen_resultado(PLAN, CAND, {"promover": False, "motivo": "no supera el margen"}, None)
    assert r["estado"] == "exitoso" and r["promovido"] is False and r["version_nueva"] == "4"


def test_resumen_ganaba_pero_no_se_pudo_promover_es_fallido():
    r = rt.resumen_resultado(PLAN, CAND, {"promover": True, "motivo": "mejora 9 %"}, None)
    assert r["estado"] == "fallido" and r["promovido"] is False and "no se pudo mover el alias" in r["motivo"]


def test_resumen_sin_decision_es_fallido():
    r = rt.resumen_resultado(PLAN, None, None, None)
    assert r["estado"] == "fallido" and r["version_nueva"] is None and "no se tocó" in r["motivo"]
    assert rt.resumen_resultado(None, None, None, None)["version_anterior"] is None
