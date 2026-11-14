from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración leída de variables de entorno (sin valores en el código)."""

    model_config = SettingsConfigDict(extra="ignore")

    # Obligatorias: si faltan, la API no arranca con una configuración a medias.
    database_url: str
    mlflow_tracking_uri: str

    # Chequeos en segundo plano. Peor caso para detectar una caída:
    # intervalo + timeout < 60 s.
    health_check_interval: float = 15.0
    health_check_timeout: float = 3.0
    # Si el último resultado es más viejo que esto, se considera caído.
    health_stale_after: float = 45.0


def get_settings() -> Settings:
    return Settings()
