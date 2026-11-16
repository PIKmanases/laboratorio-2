from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración leída de variables de entorno (sin valores en el código)."""

    model_config = SettingsConfigDict(extra="ignore")

    # Obligatorias: si faltan, la API no arranca con una configuración a medias.
    database_url: str
    mlflow_tracking_uri: str

    # Airflow (API REST v2, autenticación JWT). Credenciales siempre por variable de entorno.
    airflow_url: str = "http://airflow-apiserver:8080"
    airflow_user: str
    airflow_password: str
    airflow_dag_id: str = "reentrenar_modelo"
    airflow_timeout: float = 5.0

    # Carpeta con los CSV (tiendas, familias, histórico). Se monta como volumen, no va en la imagen.
    data_dir: str = "/data"

    # Sondeo del alias del modelo (paso 7): adopta un modelo promovido sin reiniciar la API.
    modelo_sondeo_intervalo: float = 30.0
    modelo_consulta_timeout: float = 15.0
    modelo_carga_timeout: float = 120.0

    # Chequeos en segundo plano. Peor caso para detectar una caída:
    # intervalo + timeout < 60 s.
    health_check_interval: float = 15.0
    health_check_timeout: float = 3.0
    # Si el último resultado es más viejo que esto, se considera caído.
    health_stale_after: float = 45.0


def get_settings() -> Settings:
    return Settings()
