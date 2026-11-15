"""DAG de prueba: valida desde Airflow las conexiones a app-db y a MLflow.

Las URLs vienen de variables de entorno (APP_DB_URL, MLFLOW_TRACKING_URI),
definidas en docker-compose.yml; no hay credenciales en el código.
"""
import os
from datetime import datetime

from airflow.sdk import dag, task


@dag(
    dag_id="prueba_conexiones",
    schedule=None,  # solo manual / API
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["demo", "conexiones"],
)
def prueba_conexiones():
    @task
    def select_1_app_db() -> int:
        # Imports dentro de la tarea: el dag-processor parsea el archivo seguido.
        from sqlalchemy import create_engine, text

        engine = create_engine(os.environ["APP_DB_URL"])
        try:
            with engine.connect() as conn:
                valor = conn.execute(text("SELECT 1")).scalar_one()
        finally:
            engine.dispose()
        if valor != 1:
            raise RuntimeError(f"SELECT 1 devolvió {valor!r}")
        print(f"app-db OK: SELECT 1 -> {valor}")
        return valor

    @task
    def run_prueba_mlflow() -> str:
        import mlflow

        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        mlflow.set_experiment("prueba_conexiones")
        with mlflow.start_run(run_name="prueba_airflow") as run:
            mlflow.log_metric("prueba", 1.0)
            print(f"MLflow OK: run_id={run.info.run_id}")
            return run.info.run_id

    # En paralelo: son pruebas independientes.
    select_1_app_db()
    run_prueba_mlflow()


prueba_conexiones()
