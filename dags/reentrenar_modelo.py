"""DAG de reentrenamiento. Solo se dispara desde la API (POST /admin/reentrenar).

    preparar_datos -> entrenar_candidato -> evaluar_y_decidir -> promover (si gana) -> registrar_resultado

La lógica vive en `ml/reentrenamiento.py` (probada sin Airflow); aquí solo se orquesta.

- Las tareas leen de app-db con su PROPIA conexión (APP_DB_URL), nunca de la base de metadatos
  de Airflow (en Airflow 3 está prohibido para las tareas).
- Sin reintentos: un reintento dentro de una ejecución ya lenta rompería el límite de 10 min.
- max_active_runs=1: un segundo disparo queda en cola en lugar de entrenar dos modelos a la vez.
- Solo `promover` cambia el modelo vigente (mueve el alias). Si algo falla antes, el alias no se toca.
"""

import os
from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException


def _engine():
    from sqlalchemy import create_engine

    return create_engine(os.environ["APP_DB_URL"], pool_pre_ping=True)


def _cargar_datos(engine):
    from ml.datos import desde_db

    return lambda hasta: desde_db(engine, hasta)


@dag(
    dag_id="reentrenar_modelo",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=9),
    default_args={"retries": 0},
    tags=["modelo", "reentrenamiento"],
)
def reentrenar_modelo():
    @task(execution_timeout=timedelta(minutes=1))
    def preparar_datos() -> dict:
        from ml import reentrenamiento

        engine = _engine()
        try:
            return reentrenamiento.preparar_datos(_cargar_datos(engine))
        finally:
            engine.dispose()

    @task(execution_timeout=timedelta(minutes=3))
    def entrenar_candidato(plan: dict) -> dict:
        from ml import reentrenamiento

        engine = _engine()
        try:
            return reentrenamiento.entrenar_candidato(_cargar_datos(engine), plan, get_current_context()["dag_run"].run_id)
        finally:
            engine.dispose()

    @task(execution_timeout=timedelta(minutes=3))
    def evaluar_y_decidir(plan: dict, candidato: dict) -> dict:
        from ml import reentrenamiento

        engine = _engine()
        try:
            return reentrenamiento.evaluar_y_decidir(_cargar_datos(engine), plan, candidato)
        finally:
            engine.dispose()

    @task(execution_timeout=timedelta(minutes=1))
    def promover(decision: dict, candidato: dict) -> dict:
        from ml import reentrenamiento

        if not decision["promover"]:
            raise AirflowSkipException("el candidato no supera al vigente: el alias no se toca")
        return reentrenamiento.promover(candidato["version"])

    @task(execution_timeout=timedelta(minutes=1), trigger_rule="all_done")
    def registrar_resultado() -> dict:
        """Corre SIEMPRE (también si algo falló) para dejar el resultado en `reentrenamientos`;
        si el reentrenamiento falló, falla al final para que la ejecución quede como `failed`."""
        from ml import reentrenamiento

        ctx = get_current_context()
        ti = ctx["ti"]
        pull = lambda tarea: ti.xcom_pull(task_ids=tarea)  # noqa: E731  (None si falló u omitida)
        engine = _engine()
        try:
            r = reentrenamiento.registrar_resultado(
                engine, ctx["dag_run"].run_id, pull("preparar_datos"), pull("entrenar_candidato"),
                pull("evaluar_y_decidir"), pull("promover"),
            )  # fmt: skip
        finally:
            engine.dispose()
        if r["estado"] == "fallido":
            raise AirflowFailException(r["motivo"])
        return r

    plan = preparar_datos()
    candidato = entrenar_candidato(plan)
    decision = evaluar_y_decidir(plan, candidato)
    promocion = promover(decision, candidato)
    [plan, candidato, decision, promocion] >> registrar_resultado()


reentrenar_modelo()
