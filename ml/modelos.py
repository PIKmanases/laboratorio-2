"""Modelos de esperada (baseline y LightGBM), plan B ante falta de historia y el empaque
`Pronosticador` (modelo + factores newsvendor + catálogos) que se guarda y se sirve."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from .features import CATEGORICAS, FEATURES, construir_features
from .newsvendor import Factores


class PlanB:
    """Qué predecir cuando NO hay historia (observaciones que no llegaron, registros inválidos
    por E08 o una combinación tienda-familia sin datos). Cascada, de lo más a lo menos específico:
        media de (familia, formato de tienda, día de la semana)  ->  (familia, formato)  ->  familia
    sobre los últimos `dias` de entrenamiento; si ni la familia existe, 0.
    """

    def __init__(self, fdt: pd.DataFrame, ft: pd.DataFrame, f: pd.DataFrame):
        self.fdt, self.ft, self.f = fdt, ft, f

    @classmethod
    def ajustar(cls, X: pd.DataFrame, y: pd.Series, fecha: pd.Series, dias: int = 90) -> PlanB:
        d = X[["familia_id", "tipo", "dia_semana"]].assign(m=y.to_numpy(), fecha=fecha.to_numpy())
        d = d[d["fecha"] > d["fecha"].max() - pd.Timedelta(days=dias)]
        return cls(
            d.groupby(["familia_id", "tipo", "dia_semana"], as_index=False)["m"].mean(),
            d.groupby(["familia_id", "tipo"], as_index=False)["m"].mean(),
            d.groupby("familia_id", as_index=False)["m"].mean(),
        )

    def predecir(self, X: pd.DataFrame) -> np.ndarray:
        k = X[["familia_id", "tipo", "dia_semana"]].reset_index(drop=True)
        a = k.merge(self.fdt, how="left", on=["familia_id", "tipo", "dia_semana"])["m"].to_numpy()
        b = k.merge(self.ft, how="left", on=["familia_id", "tipo"])["m"].to_numpy()
        c = k.merge(self.f, how="left", on="familia_id")["m"].to_numpy()
        return np.nan_to_num(np.where(np.isnan(a), np.where(np.isnan(b), c, b), a), nan=0.0)

    def guardar(self, d: Path) -> None:
        self.fdt.to_csv(d / "plan_b_fdt.csv", index=False)
        self.ft.to_csv(d / "plan_b_ft.csv", index=False)
        self.f.to_csv(d / "plan_b_f.csv", index=False)

    @classmethod
    def cargar(cls, d: Path) -> PlanB:
        return cls(*(pd.read_csv(d / n) for n in ("plan_b_fdt.csv", "plan_b_ft.csv", "plan_b_f.csv")))


class Baseline:
    """Seasonal naive: media de los últimos 4 mismos días de la semana (D-7, D-14, D-21, D-28),
    ignorando los que faltan. Si faltan los 4 -> plan B."""

    tipo = "baseline"

    def __init__(self, plan_b: PlanB | None = None):
        self.plan_b = plan_b

    def ajustar(self, X: pd.DataFrame, y: pd.Series, fecha: pd.Series) -> Baseline:
        self.plan_b = PlanB.ajustar(X, y, fecha)
        return self

    def esperada(self, X: pd.DataFrame) -> np.ndarray:
        sn = X["media_mismo_dia_4"].to_numpy(float)
        return np.where(np.isnan(sn), self.plan_b.predecir(X), sn)

    def guardar(self, d: Path) -> None:
        self.plan_b.guardar(d)

    @classmethod
    def cargar(cls, d: Path, meta: dict) -> Baseline:
        return cls(PlanB.cargar(d))


PARAMS_LGBM = {
    "objective": "tweedie", "tweedie_variance_power": 1.2, "learning_rate": 0.05, "num_leaves": 31,
    "min_data_in_leaf": 50, "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1,
    "lambda_l2": 1.0, "num_boost_round": 600, "seed": 42, "deterministic": True,
    "force_row_wise": True, "num_threads": 2, "verbosity": -1,
}  # fmt: skip


class ModeloLightGBM:
    """Modelo GLOBAL (uno para todas las tiendas y familias). Los NaN de la historia los maneja
    LightGBM de forma nativa; solo si una serie no tiene NINGUNA venta en 28 días se usa el plan B."""

    tipo = "lightgbm"

    def __init__(self, params: dict | None = None, booster: lgb.Booster | None = None,
                 categorias: dict | None = None, plan_b: PlanB | None = None, log_objetivo: bool = False,
                 columnas: list[str] | None = None):  # fmt: skip
        self.params = {**PARAMS_LGBM, **(params or {})}
        self.booster, self.categorias, self.plan_b, self.log_objetivo = booster, categorias, plan_b, log_objetivo
        self.columnas = list(columnas or FEATURES)  # las usadas al entrenar; viajan con el modelo

    def _preparar(self, X: pd.DataFrame) -> pd.DataFrame:
        d = X[self.columnas].copy()
        for c in self.categorias:
            d[c] = pd.Categorical(d[c], categories=self.categorias[c])
        return d

    def ajustar(self, X: pd.DataFrame, y: pd.Series, fecha: pd.Series) -> ModeloLightGBM:
        self.categorias = {c: sorted(X[c].dropna().unique().tolist()) for c in CATEGORICAS if c in self.columnas}
        self.plan_b = PlanB.ajustar(X, y, fecha)
        p = dict(self.params)
        rondas = p.pop("num_boost_round")
        self.log_objetivo = p.get("objective") == "log1p"
        if self.log_objetivo:
            p["objective"] = "regression"
        yy = np.log1p(y.to_numpy(float)) if self.log_objetivo else y.to_numpy(float)
        self.booster = lgb.train(p, lgb.Dataset(self._preparar(X), yy), num_boost_round=rondas)
        return self

    def esperada(self, X: pd.DataFrame) -> np.ndarray:
        pred = self.booster.predict(self._preparar(X))
        pred = np.expm1(pred) if self.log_objetivo else pred
        pred = np.maximum(pred, 0.0)
        sin_historia = X["n_obs_28"].to_numpy(float) == 0
        if sin_historia.any():
            pred = np.where(sin_historia, self.plan_b.predecir(X), pred)
        return pred

    def guardar(self, d: Path) -> None:
        self.booster.save_model(str(d / "lightgbm.txt"))
        self.plan_b.guardar(d)
        (d / "lgbm_meta.json").write_text(json.dumps({"categorias": self.categorias, "log_objetivo": self.log_objetivo, "params": self.params, "columnas": self.columnas}))

    @classmethod
    def cargar(cls, d: Path, meta: dict) -> ModeloLightGBM:
        m = json.loads((d / "lgbm_meta.json").read_text())
        return cls(m["params"], lgb.Booster(model_file=str(d / "lightgbm.txt")), m["categorias"], PlanB.cargar(d), m["log_objetivo"], m["columnas"])


MODELOS = {"baseline": Baseline, "lightgbm": ModeloLightGBM}


class Pronosticador:
    """Todo lo necesario para predecir: modelo + factores newsvendor + catálogos de tiendas y
    calendario. `predecir` recibe historia reciente + las filas a pronosticar (ventas = NaN)."""

    def __init__(self, modelo, factores: Factores, tiendas: pd.DataFrame, feriados: pd.DataFrame, meta: dict | None = None):
        self.modelo, self.factores, self.tiendas, self.feriados, self.meta = modelo, factores, tiendas, feriados, meta or {}

    def predecir(self, historia: pd.DataFrame) -> pd.DataFrame:
        desconocidas = set(historia["familia_id"]) - set(self.factores.q_familia)
        if desconocidas:  # ni siquiera NaN en silencio: un id no canónico es un error
            raise ValueError(f"familia_id sin factor en el modelo (¿no es el id canónico?): {sorted(desconocidas)}")
        X = construir_features(historia, self.tiendas, self.feriados)
        objetivo = historia["ventas"].isna().to_numpy()
        if not objetivo.any():
            raise ValueError("no hay filas a pronosticar (ventas = NaN)")
        Xo = X[objetivo]
        esperada = self.modelo.esperada(Xo)
        sugerida = self.factores.cantidad(esperada, Xo["familia_id"], Xo["tipo"])
        return pd.DataFrame(
            {
                "tienda_id": historia.loc[objetivo, "tienda_id"].to_numpy(),
                "familia_id": historia.loc[objetivo, "familia_id"].to_numpy(),
                "fecha": pd.to_datetime(historia.loc[objetivo, "fecha"]).to_numpy(),
                "venta_esperada": esperada,
                "cantidad_sugerida": sugerida,
            }
        )

    def guardar(self, d: Path | str) -> None:
        d = Path(d)
        d.mkdir(parents=True, exist_ok=True)
        self.modelo.guardar(d)
        self.tiendas.to_csv(d / "tiendas.csv", index=False)
        self.feriados.to_csv(d / "feriados.csv", index=False)
        (d / "factores.json").write_text(json.dumps(self.factores.a_dict()))
        (d / "meta.json").write_text(json.dumps({**self.meta, "tipo": self.modelo.tipo}))

    @classmethod
    def cargar(cls, d: Path | str) -> Pronosticador:
        d = Path(d)
        meta = json.loads((d / "meta.json").read_text())
        return cls(
            MODELOS[meta["tipo"]].cargar(d, meta),
            Factores.desde_dict(json.loads((d / "factores.json").read_text())),
            pd.read_csv(d / "tiendas.csv"),
            pd.read_csv(d / "feriados.csv"),
            meta,
        )
