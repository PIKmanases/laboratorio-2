import numpy as np
import pandas as pd
import pytest

from ml.features import FEATURES, HISTORIA_DIAS, construir_features
from sinteticos import datos_sinteticos


@pytest.fixture(scope="module")
def d():
    return datos_sinteticos()


@pytest.fixture(scope="module")
def X(d):
    return construir_features(d.ventas, d.tiendas, d.feriados)


def fila(d, t, f, dia):
    return d.ventas.index[(d.ventas.tienda_id == t) & (d.ventas.familia_id == f) & (d.ventas.fecha == dia)][0]


def test_devuelve_las_features_en_el_mismo_orden_e_indice(d, X):
    assert list(X.columns) == FEATURES and X.index.equals(d.ventas.index)


def test_lags_son_dias_calendario_atras(d, X):
    s = d.ventas[(d.ventas.tienda_id == 1) & (d.ventas.familia_id == "abarrotes")].set_index("fecha").ventas
    dia = pd.Timestamp("2025-03-20")
    r = X.loc[fila(d, 1, "abarrotes", dia)]
    assert r["lag_1"] == s[dia - pd.Timedelta(days=1)]
    assert r["lag_7"] == s[dia - pd.Timedelta(days=7)]
    assert r["media_mismo_dia_4"] == pytest.approx(np.mean([s[dia - pd.Timedelta(days=7 * k)] for k in (1, 2, 3, 4)]))
    assert r["media_28"] == pytest.approx(s[dia - pd.Timedelta(days=28) : dia - pd.Timedelta(days=1)].mean())


def test_sin_fuga_el_dia_D_no_cambia_sus_features(d, X):
    """Alterar las ventas de D y de los días posteriores NO puede mover las features de D."""
    dia = pd.Timestamp("2025-04-01")
    alterada = d.ventas.copy()
    alterada.loc[alterada.fecha >= dia, "ventas"] = 999_999
    X2 = construir_features(alterada, d.tiendas, d.feriados)
    idx = d.ventas.index[d.ventas.fecha == dia]
    pd.testing.assert_frame_equal(X.loc[idx], X2.loc[idx])


def test_features_de_la_api_igual_que_las_del_entrenamiento(d, X):
    """La API da historia < D + fila de D con ventas NaN: debe producir las MISMAS features."""
    dia = pd.Timestamp("2025-04-15")
    h = d.ventas[(d.ventas.tienda_id == 2) & (d.ventas.fecha < dia) & (d.ventas.fecha >= dia - pd.Timedelta(days=HISTORIA_DIAS))]
    objetivo = d.ventas[(d.ventas.tienda_id == 2) & (d.ventas.fecha == dia)].assign(ventas=np.nan)
    entrada = pd.concat([h, objetivo], ignore_index=True)
    Xa = construir_features(entrada, d.tiendas, d.feriados).iloc[-len(objetivo):].reset_index(drop=True)
    Xe = X.loc[objetivo.index].reset_index(drop=True)
    pd.testing.assert_frame_equal(Xa, Xe, check_dtype=False)


def test_un_dia_faltante_deja_nan_y_no_corre_los_lags(d):
    v = d.ventas[(d.ventas.tienda_id == 1) & (d.ventas.familia_id == "abarrotes")]
    dia = pd.Timestamp("2025-03-20")
    sin = v[v.fecha != dia - pd.Timedelta(days=1)]  # no llegó la observación de ayer (o fue inválida por E08)
    X = construir_features(sin, d.tiendas, d.feriados)
    r = X.loc[sin.index[sin.fecha == dia][0]]
    assert np.isnan(r["lag_1"])
    assert r["lag_7"] == v.set_index("fecha").ventas[dia - pd.Timedelta(days=7)]
    assert r["n_obs_28"] == 27


def test_serie_sin_historia_tiene_todo_nan_y_n_obs_cero(d):
    solo = pd.DataFrame({"fecha": [pd.Timestamp("2025-06-01")], "tienda_id": [1], "familia_id": ["abarrotes"], "ventas": [np.nan], "en_promocion": [3.0]})
    r = construir_features(solo, d.tiendas, d.feriados).iloc[0]
    assert r["n_obs_28"] == 0 and np.isnan(r["lag_1"]) and np.isnan(r["media_28"]) and r["en_promocion"] == 3


def test_calendario_feriados_nacional_local_medio_dia_comercial(d):
    def fila_(dia, t):
        return pd.DataFrame({"fecha": [pd.Timestamp(dia)], "tienda_id": [t], "familia_id": ["abarrotes"], "ventas": [np.nan], "en_promocion": [0.0]})

    f = lambda dia, t: construir_features(fila_(dia, t), d.tiendas, d.feriados).iloc[0]  # noqa: E731
    assert f("2025-03-01", 1)["es_feriado_nacional"] == 1 and f("2025-03-01", 1)["es_dia_post_feriado"] == 0
    assert f("2025-02-28", 1)["es_vispera_feriado"] == 1 and f("2025-03-02", 1)["es_dia_post_feriado"] == 1
    assert f("2025-04-10", 2)["es_feriado_local"] == 1  # tienda 2 = Cobán
    assert f("2025-04-10", 1)["es_feriado_local"] == 0  # el feriado de Cobán no aplica en Guatemala
    assert f("2025-04-11", 1)["es_medio_dia"] == 1 and f("2025-02-14", 1)["es_comercial"] == 1
    assert f("2025-05-04", 1)["dia_semana"] == 6 and f("2025-05-04", 1)["es_fin_de_semana"] == 1


def test_futuro_sin_historia_solo_necesita_el_calendario(d):
    """feriados.csv cubre fechas futuras: se puede calcular el calendario de D aunque no haya datos."""
    solo = pd.DataFrame({"fecha": [pd.Timestamp("2025-03-01")], "tienda_id": [1], "familia_id": ["lacteos"], "ventas": [np.nan], "en_promocion": [0.0]})
    assert construir_features(solo, d.tiendas, d.feriados).iloc[0]["es_feriado_nacional"] == 1


def test_historia_duplicada_se_rechaza(d):
    dup = pd.concat([d.ventas.head(3), d.ventas.head(1)])
    with pytest.raises(ValueError):
        construir_features(dup, d.tiendas, d.feriados)
