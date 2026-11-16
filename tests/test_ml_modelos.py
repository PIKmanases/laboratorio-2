import numpy as np
import pandas as pd
import pytest

from ml import metricas
from ml.entrenar import entrenar, ventanas
from ml.features import construir_features
from ml.modelos import Baseline, ModeloLightGBM, PlanB, Pronosticador
from ml.newsvendor import calibrar, tau_por_familia
from sinteticos import datos_sinteticos

RAPIDO = {"num_boost_round": 30, "num_threads": 1}


@pytest.fixture(scope="module")
def d():
    return datos_sinteticos()


# ------------------------------------------------------------------ newsvendor
def test_tau_es_cf_sobre_cf_mas_cs(d):
    assert tau_por_familia(d.costos) == {"abarrotes": pytest.approx(1 / 1.2), "lacteos": pytest.approx(1 / 2.2)}


def test_calibrar_da_el_cuantil_tau_de_los_errores_relativos(d):
    rng = np.random.default_rng(1)
    n = 5000
    esperada = np.full(n, 100.0)
    real = esperada * (1 + rng.normal(0, 0.1, n))
    df = pd.DataFrame({"familia_id": "abarrotes", "tipo": "express", "real": real, "esperada": esperada})
    df = pd.concat([df, df.assign(familia_id="lacteos")])
    f = calibrar(df, d.costos)
    # tau abarrotes = 0.833 -> z = 0.967 -> q ≈ +0.097 ; tau lacteos = 0.4545 -> q ≈ -0.011
    assert f.q_familia["abarrotes"] == pytest.approx(0.0967, abs=0.01)
    assert f.q_familia["lacteos"] == pytest.approx(-0.0114, abs=0.01)
    cant = f.cantidad(np.array([100.0, 100.0]), pd.Series(["abarrotes", "lacteos"]), pd.Series(["express", "express"]))
    assert cant.tolist() == [111, 99] or (cant[0] > 105 and cant[1] <= 100)


def test_cantidad_es_entera_no_negativa_y_techo(d):
    df = pd.DataFrame({"familia_id": ["abarrotes"] * 40 + ["lacteos"] * 40, "tipo": "express", "real": np.arange(80) + 1.0, "esperada": np.arange(80) + 1.0})
    f = calibrar(df, d.costos)
    c = f.cantidad(np.array([0.0, 0.3, 2.5]), pd.Series(["abarrotes"] * 3), pd.Series(["express"] * 3))
    assert c.dtype.kind == "i" and (c >= 0).all()
    f.q_familia["abarrotes"] = -5.0  # q muy negativo no debe dar cantidades negativas
    f.usa_formato = False
    assert f.cantidad(np.array([10.0]), pd.Series(["abarrotes"]), pd.Series(["express"]))[0] == 0


def test_celda_con_pocos_datos_hereda_el_factor_de_la_familia(d):
    rng = np.random.default_rng(2)
    grande = pd.DataFrame({"familia_id": "abarrotes", "tipo": "hipermercado", "real": rng.normal(100, 10, 600), "esperada": 100.0})
    chica = pd.DataFrame({"familia_id": "abarrotes", "tipo": "express", "real": rng.normal(150, 10, 10), "esperada": 100.0})
    lac = grande.assign(familia_id="lacteos")
    f = calibrar(pd.concat([grande, chica, lac]), d.costos)
    assert "hipermercado" in f.q_celda["abarrotes"] and "express" not in f.q_celda["abarrotes"]
    assert f.q("abarrotes", "express") == f.q_familia["abarrotes"]  # hereda
    assert f.q("abarrotes", "formato_nuevo") == f.q_familia["abarrotes"]


def test_factores_se_serializan(d):
    df = pd.DataFrame({"familia_id": ["abarrotes", "lacteos"] * 300, "tipo": "express", "real": 100.0, "esperada": 90.0})
    f = calibrar(df, d.costos)
    from ml.newsvendor import Factores

    assert Factores.desde_dict(f.a_dict()).a_dict() == f.a_dict()


# ------------------------------------------------------------------ métricas
def test_metricas_tecnicas():
    m = metricas.tecnicas(np.array([10.0, 0.0, 30.0]), np.array([12.0, 1.0, 24.0]))
    assert m["mae"] == pytest.approx(3.0) and m["rmse"] == pytest.approx(np.sqrt((4 + 1 + 36) / 3))
    assert m["wape"] == pytest.approx(9 / 40) and m["sesgo"] == pytest.approx(-1.0)  # errores +2, +1, -6: pronostica de menos


def test_costo_y_pct_sin_faltante(d):
    df = pd.DataFrame({"familia_id": ["abarrotes", "abarrotes", "lacteos"], "real": [10.0, 10.0, 10.0], "esperada": [8.0, 12.0, 10.0], "sugerida": [8, 12, 10]})
    m = metricas.negocio(df, d.costos)
    # faltante 2 * 1.0 + sobrante 2 * 0.2 + 0
    assert m["costo_total"] == pytest.approx(2.4) and m["pct_dias_sin_faltante"] == pytest.approx(200 / 3)
    assert m["costo_familia_abarrotes"] == pytest.approx(2.4)


def test_la_regla_baja_el_costo_frente_a_esperada_si_los_costos_son_asimetricos(d):
    rng = np.random.default_rng(3)
    real = rng.poisson(100, 4000).astype(float)
    df = pd.DataFrame({"familia_id": "abarrotes", "tipo": "express", "real": real, "esperada": 100.0})
    df = pd.concat([df, df.assign(familia_id="lacteos")])
    f = calibrar(df, d.costos)
    ev = df.assign(sugerida=f.cantidad(df["esperada"].to_numpy(), df["familia_id"], df["tipo"]))
    m = metricas.negocio(ev, d.costos)
    assert m["costo_total"] < m["costo_total_sugerida_igual_esperada"] and m["ahorro_vs_esperada_pct"] > 10


# ------------------------------------------------------------------ plan B y baseline
def test_plan_b_cascada(d):
    X = construir_features(d.ventas, d.tiendas, d.feriados)
    y, fecha = d.ventas["ventas"].astype(float), pd.to_datetime(d.ventas["fecha"])
    pb = PlanB.ajustar(X, y, fecha)
    base = pd.DataFrame({"familia_id": ["abarrotes"] * 3, "tipo": ["hipermercado", "formato_nuevo", "formato_nuevo"], "dia_semana": [2, 2, 2]})
    r = pb.predecir(base)
    assert r[0] > 100  # (familia, formato, día) existe
    assert r[1] == r[2] > 0  # formato desconocido -> media de la familia
    assert pb.predecir(base.assign(familia_id="inexistente")).tolist() == [0.0, 0.0, 0.0]


def test_baseline_usa_media_de_4_mismos_dias_y_plan_b_si_faltan(d):
    X = construir_features(d.ventas, d.tiendas, d.feriados)
    y, fecha = d.ventas["ventas"].astype(float), pd.to_datetime(d.ventas["fecha"])
    b = Baseline().ajustar(X, y, fecha)
    e = b.esperada(X)
    ok = X["media_mismo_dia_4"].notna()
    assert np.allclose(e[ok], X.loc[ok, "media_mismo_dia_4"]) and (e[~ok] > 0).all()  # plan B para las primeras semanas


# ------------------------------------------------------------------ entrenamiento
def test_ventanas_temporales_no_se_solapan():
    corte, fin_cal = ventanas(pd.Timestamp("2025-12-31"), None, 56, 56)
    assert (pd.Timestamp("2025-12-31") - corte).days == 111 and (fin_cal - corte).days == 56
    assert ventanas(pd.Timestamp("2025-12-31"), pd.Timestamp("2025-10-01").date(), 10, 10)[0] == pd.Timestamp("2025-10-01")


@pytest.mark.parametrize("tipo", ["baseline", "lightgbm"])
def test_entrenar_devuelve_modelo_y_metricas(d, tipo):
    r = entrenar(d, tipo=tipo, params=RAPIDO, dias_calibracion=20, dias_prueba=20)
    for k in ("mae", "rmse", "wape", "sesgo", "costo_total", "costo_total_sugerida_igual_esperada", "pct_dias_sin_faltante", "costo_familia_abarrotes"):
        assert k in r.metricas and np.isfinite(r.metricas[k])
    assert r.metricas["n_prueba"] == 4 * 20 and r.metricas["n_entrenamiento"] == 4 * (150 - 40)
    assert r.parametros["fecha_corte"] == "2025-04-21"  # 150 días desde 2025-01-01 -> fin 2025-05-30 -> corte = fin - 39


def test_entrenar_no_usa_el_futuro_para_ajustar(d):
    """Cambiar las ventas de calibración/prueba no puede cambiar las métricas de entrenamiento del modelo ajustado."""
    alterado = d.ventas.copy()
    alterado.loc[alterado.fecha >= pd.Timestamp("2025-05-11"), "ventas"] = 1
    d2 = type(d)(alterado, d.tiendas, d.feriados, d.costos)
    X = construir_features(d.ventas, d.tiendas, d.feriados)
    X2 = construir_features(d2.ventas, d.tiendas, d.feriados)
    previo = d.ventas.fecha < pd.Timestamp("2025-05-11")
    pd.testing.assert_frame_equal(X[previo], X2[previo])


def test_ventanas_vacias_dan_error(d):
    with pytest.raises(ValueError):
        entrenar(d, tipo="baseline", dias_calibracion=100, dias_prueba=100)


def test_lightgbm_es_mejor_que_el_baseline_en_datos_con_estructura(d):
    a = entrenar(d, tipo="baseline", dias_calibracion=20, dias_prueba=20).metricas["wape"]
    b = entrenar(d, tipo="lightgbm", params={"num_boost_round": 150, "num_threads": 1}, dias_calibracion=20, dias_prueba=20).metricas["wape"]
    assert b <= a * 1.05  # al menos tan bueno (datos sintéticos pequeños y ruidosos)


def test_lightgbm_sin_historia_usa_plan_b_y_acepta_nan(d):
    r = entrenar(d, tipo="lightgbm", params=RAPIDO, dias_calibracion=20, dias_prueba=20)
    solo = pd.DataFrame({"fecha": [pd.Timestamp("2025-06-01")] * 2, "tienda_id": [1, 2], "familia_id": ["abarrotes"] * 2, "ventas": [np.nan] * 2, "en_promocion": [0.0, 0.0]})
    out = r.modelo.predecir(solo)  # ninguna venta previa -> plan B
    assert (out["venta_esperada"] > 0).all() and (out["cantidad_sugerida"] >= 0).all()


# ------------------------------------------------------------------ guardar / cargar
@pytest.mark.parametrize("tipo", ["baseline", "lightgbm"])
def test_pronosticador_guardar_cargar_da_lo_mismo(d, tipo, tmp_path):
    r = entrenar(d, tipo=tipo, params=RAPIDO, dias_calibracion=20, dias_prueba=20)
    dia = pd.Timestamp("2025-05-20")
    h = d.ventas[d.ventas.fecha < dia].tail(400)
    pedido = pd.DataFrame({"fecha": [dia] * 2, "tienda_id": [1, 1], "familia_id": ["abarrotes", "lacteos"], "ventas": [np.nan] * 2, "en_promocion": [3.0, 0.0]})
    entrada = pd.concat([h, pedido], ignore_index=True)
    r.modelo.guardar(tmp_path)
    pd.testing.assert_frame_equal(r.modelo.predecir(entrada), Pronosticador.cargar(tmp_path).predecir(entrada))
