"""Pasos del reentrenamiento (los llama el DAG de Airflow, pero no dependen de Airflow).

    preparar_datos -> entrenar_candidato -> evaluar_y_decidir -> promover -> registrar_resultado

PROTECCIÓN DEL VIGENTE. Lo único que cambia el modelo vigente es `promover`, que mueve el alias
`champion` con UNA sola operación del Model Registry (no hay un estado «a medias»). Todo lo
anterior solo AÑADE una versión candidata sin alias. Si falla cualquier paso (datos,
entrenamiento, evaluación, MLflow caído) el alias sigue donde estaba.

COMPARACIÓN JUSTA. Candidato y vigente se evalúan en la MISMA ventana de prueba y ninguno
entrenó con ella:
  - el candidato se entrena con fecha < corte (la ventana queda después);
  - si el vigente terminó de entrenar ANTES de la ventana (`ultimo_dia` < inicio de la prueba),
    se evalúa tal cual está desplegado (modo `vigente_real`);
  - si ya vio parte de la ventana (p. ej. no hubo datos nuevos desde que se entrenó), compararlo
    sería injusto a su favor: se REAJUSTA su misma receta (tipo y parámetros) con fecha < corte y
    se evalúa ese (modo `vigente_reajustado`).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .datos import Datos
from .entrenar import DIAS_CALIBRACION, DIAS_PRUEBA, entrenar, evaluar_modelo, validar_ids, ventanas
from .modelos import Pronosticador
from .promocion import MARGEN_PROMOCION, decidir_promocion
from .registro import ALIAS, EXPERIMENTO, MODELO_REGISTRADO, log_resultado

CargarDatos = Callable[[date | None], Datos]  # recibe «hasta» (None = todo) y devuelve los datos
CLAVES_METRICAS = ("mae", "rmse", "wape", "sesgo", "costo_total", "costo_total_sugerida_igual_esperada",
                   "ahorro_vs_esperada_pct", "pct_dias_sin_faltante", "n_prueba")  # fmt: skip


def _cliente():
    from mlflow import MlflowClient

    return MlflowClient()


def version_vigente() -> str | None:
    """Versión que tiene el alias `champion`, o None si todavía no hay ninguna.
    Cualquier otro error (p. ej. MLflow caído) se propaga: el DAG falla y nada cambia."""
    from mlflow.exceptions import MlflowException

    try:
        return str(_cliente().get_model_version_by_alias(MODELO_REGISTRADO, ALIAS).version)
    except MlflowException as e:
        if getattr(e, "error_code", "") == "RESOURCE_DOES_NOT_EXIST":
            return None
        raise


def preparar_datos(cargar_datos: CargarDatos) -> dict:
    """Fija la «foto» de datos y las ventanas. Devuelve un dict pequeño (va por XCom)."""
    datos = cargar_datos(None)
    validar_ids(datos)
    fechas = pd.to_datetime(datos.ventas["fecha"])
    fin = fechas.max()
    corte, fin_cal = ventanas(fin, None, DIAS_CALIBRACION, DIAS_PRUEBA)
    if corte < fechas.min() + pd.Timedelta(days=60):
        raise ValueError(f"datos insuficientes: hay {fechas.min().date()}..{fin.date()} y se necesitan al menos 60 días de entrenamiento más {DIAS_CALIBRACION + DIAS_PRUEBA} de calibración y prueba")
    return {
        "fin": str(fin.date()), "corte": str(corte.date()), "fin_calibracion": str(fin_cal.date()),
        "n_filas": int(len(datos.ventas)), "vigente_version": version_vigente(),
    }  # fmt: skip


def entrenar_candidato(cargar_datos: CargarDatos, plan: dict, dag_run_id: str | None = None) -> dict:
    """Entrena el candidato y lo REGISTRA como una versión nueva (sin alias), gane o pierda."""
    import mlflow

    datos = cargar_datos(date.fromisoformat(plan["fin"]))
    res = entrenar(datos, fecha_corte=date.fromisoformat(plan["corte"]), tipo="lightgbm")
    mlflow.set_experiment(EXPERIMENTO)
    run_id, uri = log_resultado(res, run_name=f"candidato_{dag_run_id or 'manual'}")
    version = str(mlflow.register_model(uri, MODELO_REGISTRADO).version)
    etiquetas = {"estado": "candidato", "run_id": run_id, "tipo": "lightgbm", "ultimo_dia": plan["fin"],
                 "fecha_corte": plan["corte"], "dag_run_id": dag_run_id or "manual"}  # fmt: skip
    for k, v in etiquetas.items():
        _cliente().set_model_version_tag(MODELO_REGISTRADO, version, k, v)
    return {"version": version, "run_id": run_id, "metricas": res.metricas}


def _cargar_vigente(version: str) -> Pronosticador:
    import mlflow.pyfunc

    return mlflow.pyfunc.load_model(f"models:/{MODELO_REGISTRADO}/{version}").unwrap_python_model()._p


def evaluar_y_decidir(cargar_datos: CargarDatos, plan: dict, candidato: dict, margen: float = MARGEN_PROMOCION) -> dict:
    """Evalúa al vigente en la misma ventana que el candidato, decide y deja la decisión
    (más el motivo y las métricas) como tags de la versión candidata."""
    inicio_prueba = pd.Timestamp(plan["fin_calibracion"])
    m_cand = candidato["metricas"]
    vigente = plan["vigente_version"]
    m_vig, modo = None, "sin_vigente"
    if vigente is not None:
        datos = cargar_datos(date.fromisoformat(plan["fin"]))
        pron = _cargar_vigente(vigente)
        if pd.Timestamp(pron.meta["ultimo_dia"]) < inicio_prueba:
            modo, m_vig = "vigente_real", evaluar_modelo(pron, datos, desde=inicio_prueba)
        else:
            modo = "vigente_reajustado"
            m_vig = entrenar(datos, fecha_corte=date.fromisoformat(plan["corte"]), tipo=pron.meta["tipo"], params=pron.meta.get("params")).metricas
        if int(m_vig["n_prueba"]) != int(m_cand["n_prueba"]):
            raise RuntimeError(f"ventanas distintas: candidato {m_cand['n_prueba']} filas, vigente {m_vig['n_prueba']}")
    d = decidir_promocion(m_cand["costo_total"], None if m_vig is None else m_vig["costo_total"], margen)
    out = {
        "promover": d.promover, "motivo": d.motivo, "modo_comparacion": modo, "margen": margen,
        "ventana_prueba": f"{plan['fin_calibracion']}..{plan['fin']}",
        "candidato": {k: m_cand[k] for k in CLAVES_METRICAS}, "vigente": None if m_vig is None else {k: m_vig[k] for k in CLAVES_METRICAS},
    }  # fmt: skip
    etiquetas = {
        "decision": "promovido" if d.promover else "rechazado", "motivo": d.motivo, "modo_comparacion": modo,
        "margen": f"{margen:.4f}", "ventana_prueba": out["ventana_prueba"], "vigente_version": str(vigente),
        "costo_candidato": f"{m_cand['costo_total']:.2f}", "wape_candidato": f"{m_cand['wape']:.4f}",
    }  # fmt: skip
    if m_vig is not None:
        etiquetas |= {"costo_vigente": f"{m_vig['costo_total']:.2f}", "wape_vigente": f"{m_vig['wape']:.4f}"}
    c = _cliente()
    for k, v in etiquetas.items():
        c.set_model_version_tag(MODELO_REGISTRADO, candidato["version"], k, v[:5000])
    c.update_model_version(MODELO_REGISTRADO, candidato["version"], description=d.motivo)
    return out


def promover(version: str) -> dict:
    """ÚNICO paso que cambia el modelo vigente: una sola operación atómica del registro."""
    _cliente().set_registered_model_alias(MODELO_REGISTRADO, ALIAS, str(version))
    return {"promovido": True, "version": str(version)}


def _sin_nan(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: _sin_nan(v) for k, v in x.items()}
    return x


def resumen_resultado(plan: dict | None, candidato: dict | None, decision: dict | None, promocion: dict | None) -> dict:
    """Qué quedó realmente (puro: sin I/O). Cualquier argumento puede faltar si su tarea falló."""
    anterior = plan["vigente_version"] if plan else None
    nueva = candidato["version"] if candidato else None
    promovido = bool(promocion and promocion.get("promovido"))
    if decision is None:
        return {"estado": "fallido", "promovido": False, "version_anterior": anterior, "version_nueva": nueva,
                "motivo": "falló antes de decidir (ver los logs de la ejecución en Airflow); el modelo vigente no se tocó"}  # fmt: skip
    if decision["promover"] and not promovido:
        return {"estado": "fallido", "promovido": False, "version_anterior": anterior, "version_nueva": nueva,
                "motivo": f"el candidato ganaba ({decision['motivo']}) pero no se pudo mover el alias; el modelo vigente no se tocó"}  # fmt: skip
    return {"estado": "exitoso", "promovido": promovido, "version_anterior": anterior, "version_nueva": nueva, "motivo": decision["motivo"]}


def registrar_resultado(engine: Engine, dag_run_id: str, plan, candidato, decision, promocion) -> dict:
    """Guarda el resultado en `reentrenamientos` (upsert por dag_run_id: sirve aunque el run se
    haya disparado desde la UI de Airflow y la API no haya creado la fila) y, si el candidato quedó
    sin decisión por un fallo, lo marca en el registro para no perder el linaje."""
    r = resumen_resultado(plan, candidato, decision, promocion)
    if decision is None and candidato:
        c = _cliente()
        c.set_model_version_tag(MODELO_REGISTRADO, candidato["version"], "decision", "fallido")
        c.set_model_version_tag(MODELO_REGISTRADO, candidato["version"], "motivo", r["motivo"][:5000])
    metricas = _sin_nan({"decision": decision, "plan": plan, "candidato_version": r["version_nueva"]})
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reentrenamientos (dag_run_id, estado, promovido, version_anterior, version_nueva, motivo, metricas, terminado_en) "
                "VALUES (:id, :estado, :promovido, :anterior, :nueva, :motivo, CAST(:metricas AS jsonb), now()) "
                "ON CONFLICT (dag_run_id) DO UPDATE SET estado = EXCLUDED.estado, promovido = EXCLUDED.promovido, "
                "version_anterior = EXCLUDED.version_anterior, version_nueva = EXCLUDED.version_nueva, "
                "motivo = EXCLUDED.motivo, metricas = EXCLUDED.metricas, terminado_en = EXCLUDED.terminado_en"
            ),
            {"id": dag_run_id, "estado": r["estado"], "promovido": r["promovido"], "anterior": r["version_anterior"],
             "nueva": r["version_nueva"], "motivo": r["motivo"], "metricas": json.dumps(metricas, allow_nan=False)},
        )  # fmt: skip
    return r
