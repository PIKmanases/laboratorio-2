"""DAG mínimo para comprobar que Airflow se puede disparar por la API REST."""
from datetime import datetime

from airflow.sdk import dag, task


@dag(
    dag_id="hello_dag",
    schedule=None,  # solo manual / API
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["demo"],
)
def hello_dag():
    @task
    def hola():
        print("Hola desde Airflow")
        return "ok"

    hola()


hello_dag()
