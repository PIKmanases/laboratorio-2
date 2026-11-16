"""Exploración reproducible de alternativas (las cifras de DECISIONES.md D10-D11 salen de aquí).

Para no ajustar decisiones sobre la ventana de prueba final, TODO se compara en un protocolo
desplazado 112 días hacia atrás (entrenar < t0; calibrar [t0, t0+56); probar [t0+56, t0+112)),
que termina justo donde empieza la calibración final. Uso: python scripts/explorar_modelos.py
"""

import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from ml import metricas  # noqa: E402
from ml.datos import desde_csv  # noqa: E402
from ml.entrenar import DIAS_CALIBRACION, DIAS_PRUEBA, ventanas  # noqa: E402
from ml.features import CATEGORICAS, FEATURES, construir_features  # noqa: E402
from ml.modelos import Baseline, ModeloLightGBM, PlanB  # noqa: E402
from ml.newsvendor import calibrar, error_relativo, tau_por_familia  # noqa: E402

d = desde_csv(Path(__file__).resolve().parent.parent / "data")
X = construir_features(d.ventas, d.tiendas, d.feriados)
y = d.ventas["ventas"].astype(float)
fecha = pd.to_datetime(d.ventas["fecha"])
corte_final, _ = ventanas(fecha.max(), None, DIAS_CALIBRACION, DIAS_PRUEBA)
t0 = corte_final - pd.Timedelta(days=DIAS_CALIBRACION + DIAS_PRUEBA)
t1 = t0 + pd.Timedelta(days=DIAS_CALIBRACION)
entr, cal, prueba = fecha < t0, (fecha >= t0) & (fecha < t1), (fecha >= t1) & (fecha < corte_final)
print(f"protocolo de exploración: entrenar < {t0.date()} | calibrar [{t0.date()}, {t1.date()}) | probar [{t1.date()}, {corte_final.date()})")
print(f"filas: entrenamiento {entr.sum()}, calibración {cal.sum()}, prueba {prueba.sum()}\n")


def evaluar(nombre, esp_cal, esp_prueba, X_=X):
    p_cal = pd.DataFrame({"familia_id": X_.loc[cal, "familia_id"], "tipo": X_.loc[cal, "tipo"], "real": y[cal], "esperada": esp_cal})
    p_pr = pd.DataFrame({"familia_id": X_.loc[prueba, "familia_id"], "tipo": X_.loc[prueba, "tipo"], "real": y[prueba], "esperada": esp_prueba})
    f = calibrar(p_cal, d.costos, por_formato=True)
    ev = p_pr.assign(sugerida=f.cantidad(p_pr["esperada"].to_numpy(), p_pr["familia_id"], p_pr["tipo"]))
    r = metricas.tecnicas(ev["real"].to_numpy(), ev["esperada"].to_numpy()) | metricas.negocio(ev, d.costos)
    return {"modelo": nombre, **{k: r[k] for k in ("mae", "rmse", "wape", "sesgo_rel", "costo_total", "costo_total_sugerida_igual_esperada")}}


filas = []
# ---------- baselines ----------
bl = Baseline().ajustar(X[entr], y[entr], fecha[entr])
filas.append(evaluar("baseline sn4 (media 4 mismos días) + plan B", bl.esperada(X[cal]), bl.esperada(X[prueba])))
pb = PlanB.ajustar(X[entr], y[entr], fecha[entr])


def sn1(M):
    v = M["lag_7"].to_numpy(float)
    return np.where(np.isnan(v), pb.predecir(M), v)


filas.append(evaluar("baseline sn1 (D-7) + plan B", sn1(X[cal]), sn1(X[prueba])))
filas.append(evaluar("solo plan B (familia x formato x día)", pb.predecir(X[cal]), pb.predecir(X[prueba])))

# ---------- LightGBM: objetivo y tamaño ----------
def lgbm(nombre, params=None, cols=None, Xv=None):
    Xv = X if Xv is None else Xv
    t = time.time()
    m = ModeloLightGBM(params, columnas=cols).ajustar(Xv[entr], y[entr], fecha[entr])
    filas.append(evaluar(nombre, m.esperada(Xv[cal]), m.esperada(Xv[prueba])))
    print(f"  {nombre}: {time.time() - t:.0f}s", flush=True)
    return m


m = lgbm("LGBM tweedie 1.2 (31 hojas, 600 rondas)")
lgbm("LGBM L2 (regresión)", {"objective": "regression"})
lgbm("LGBM L2 sobre log1p(ventas)", {"objective": "log1p"})
lgbm("LGBM poisson", {"objective": "poisson"})
lgbm("LGBM tweedie 15 hojas", {"num_leaves": 15})
lgbm("LGBM tweedie 63 hojas", {"num_leaves": 63})
lgbm("LGBM tweedie 300 rondas", {"num_boost_round": 300})
lgbm("LGBM tweedie 1200 rondas, lr 0.025", {"num_boost_round": 1200, "learning_rate": 0.025})

# ---------- ablación de grupos de features ----------
cal_cols = ["dia_semana", "dia_mes", "mes", "semana_anio", "es_fin_de_semana", "es_feriado_nacional", "es_feriado_local", "es_medio_dia", "es_comercial", "es_pago", "es_vispera_feriado", "es_dia_post_feriado"]
hist = ["lag_1", "lag_7", "lag_14", "lag_21", "lag_28", "media_mismo_dia_4", "media_7", "media_28", "std_28", "ratio_7_28", "n_obs_28"]
lgbm("  sin calendario", cols=[c for c in FEATURES if c not in cal_cols])
lgbm("  sin historia de ventas", cols=[c for c in FEATURES if c not in hist])
lgbm("  sin atributos fijos (formato/ciudad/depto)", cols=[c for c in FEATURES if c not in ("tipo", "ciudad", "departamento")])

# ---------- features candidatas que se probaron y NO se incluyeron ----------
X2 = X.copy()
fx = pd.to_datetime(d.ventas["fecha"])
pv = d.ventas.assign(fecha=fx).pivot(index="fecha", columns=["tienda_id", "familia_id"], values="en_promocion").astype(float)
pv = pv.reindex(pd.date_range(pv.index.min(), pv.index.max()))
fila, col = pv.index.get_indexer(fx), pv.columns.get_indexer(pd.MultiIndex.from_frame(d.ventas[["tienda_id", "familia_id"]]))
X2["promo_lag_1"] = pv.shift(1).to_numpy()[fila, col]
X2["promo_lag_7"] = pv.shift(7).to_numpy()[fila, col]
X2["promo_media_7"] = pv.shift(1).rolling(7, min_periods=1).mean().to_numpy()[fila, col]
X2["promo_tienda_dia"] = d.ventas.groupby([fx, "tienda_id"])["en_promocion"].transform("sum").to_numpy()
X2["dias_a_fin_de_mes"] = (fx.dt.days_in_month - fx.dt.day).to_numpy()
lgbm("  + historia de promoción (3 cols)", cols=FEATURES + ["promo_lag_1", "promo_lag_7", "promo_media_7"], Xv=X2)
lgbm("  + promoción total de la tienda ese día", cols=FEATURES + ["promo_tienda_dia"], Xv=X2)
lgbm("  + días al fin de mes", cols=FEATURES + ["dias_a_fin_de_mes"], Xv=X2)

print("\n== Alternativas de modelo (protocolo de exploración) ==")
tabla = pd.DataFrame(filas).set_index("modelo").round(3)
print(tabla.to_string())

print("\n== Importancia de features (ganancia, LGBM tweedie) ==")
imp = pd.Series(m.booster.feature_importance("gain"), index=m.booster.feature_name()).sort_values(ascending=False)
print((100 * imp / imp.sum()).round(1).head(15).to_string())

# ---------- newsvendor: variantes de cuantil con el LGBM base ----------
print("\n== Variantes de la regla de cantidad (LGBM tweedie) ==")
e_cal, e_pr = m.esperada(X[cal]), m.esperada(X[prueba])
p_cal = pd.DataFrame({"familia_id": X.loc[cal, "familia_id"], "tipo": X.loc[cal, "tipo"], "real": y[cal], "esperada": e_cal})
p_pr = pd.DataFrame({"familia_id": X.loc[prueba, "familia_id"], "tipo": X.loc[prueba, "tipo"], "real": y[prueba], "esperada": e_pr})
c = d.costos.set_index("familia_id")
cf, cs = p_pr["familia_id"].map(c["costo_faltante"]).to_numpy(float), p_pr["familia_id"].map(c["costo_sobrante"]).to_numpy(float)
real = p_pr["real"].to_numpy(float)
tau = tau_por_familia(d.costos)
res = {}
res["sugerida = esperada redondeada"] = np.round(p_pr["esperada"].to_numpy())
for nombre, pf in (("relativo, solo familia", False), ("relativo, familia x formato", True)):
    f = calibrar(p_cal, d.costos, por_formato=pf)
    res[f"cuantil {nombre} (ceil)"] = f.cantidad(p_pr["esperada"].to_numpy(), p_pr["familia_id"], p_pr["tipo"])
    q = np.array([f.q(a, b) for a, b in zip(p_pr["familia_id"], p_pr["tipo"])])
    res[f"cuantil {nombre} (round)"] = np.round(np.maximum(p_pr["esperada"].to_numpy() + q * np.maximum(p_pr["esperada"].to_numpy(), 1), 0))
# aditivo: cuantil de (real - esperada) por familia
qa = {f_: np.quantile(g["real"] - g["esperada"], tau[f_]) for f_, g in p_cal.groupby("familia_id")}
res["cuantil ADITIVO por familia (ceil)"] = np.ceil(np.maximum(p_pr["esperada"].to_numpy() + p_pr["familia_id"].map(qa).to_numpy(), 0))
# nivel global (un solo tau medio): ilustra lo que se pierde ignorando los costos por familia
res["factor único (tau medio) relativo"] = np.ceil(np.maximum(p_pr["esperada"].to_numpy() * (1 + np.quantile(error_relativo(p_cal["real"].to_numpy(), p_cal["esperada"].to_numpy()), np.mean(list(tau.values())))), 0))
out = []
for nombre, s in res.items():
    cost = metricas.costo(real, np.asarray(s, float), cf, cs)
    out.append({"regla": nombre, "costo_total": cost.sum(), "pct_sin_faltante": 100 * np.mean(np.asarray(s) >= real)})
print(pd.DataFrame(out).set_index("regla").round(1).to_string())
print("\nFactores familia x formato con datos suficientes (calibración 56 d):")
f = calibrar(p_cal, d.costos, True)
print({k: v for k, v in f.q_celda.items()} or "ninguno (todas heredan el factor de la familia)")
print("\nObservaciones por (familia x formato) en la calibración:", p_cal.groupby(["familia_id", "tipo"]).size().min(), "a", p_cal.groupby(["familia_id", "tipo"]).size().max())
