"""Arranque desde cero (paso 8): entrena y registra el primer modelo SOLO si no hay `champion`.

Lo ejecuta el contenedor `init` después de cargar los CSV (`python -m app.db.carga`):

    python -m ml.arranque

Idempotente: la fuente de verdad es el Model Registry. Si el alias `champion` ya existe (p. ej. tras
`docker compose down` + `up` sin `-v`, porque el volumen de MLflow se conserva) no entrena nada.
Si el alias no existe (primer arranque, o se borró el volumen de MLflow) entrena y lo crea.
Si MLflow no responde tras varios intentos termina con error: no se puede saber si hay modelo.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable

logger = logging.getLogger("ml.arranque")


def asegurar_modelo_inicial(
    consultar_vigente: Callable[[], str | None],
    entrenar_y_registrar: Callable[[], dict],
    intentos: int = 12,
    espera: float = 5.0,
    dormir: Callable[[float], None] = time.sleep,
) -> dict:
    """`consultar_vigente` devuelve la versión del alias o None si no existe (y lanza si MLflow no
    responde). Devuelve {"entrenado": bool, "version": str}."""
    version = None
    for intento in range(1, intentos + 1):
        try:
            version = consultar_vigente()
            break
        except Exception as e:  # noqa: BLE001  MLflow aún arrancando o sin red
            logger.warning("MLflow no respondió (intento %d/%d): %r", intento, intentos, e)
            if intento == intentos:
                raise
            dormir(espera)
    if version is not None:
        logger.info("ya hay un modelo vigente (versión %s): no se entrena de nuevo", version)
        return {"entrenado": False, "version": str(version)}
    logger.info("no hay modelo vigente: entrenando el modelo inicial")
    resultado = entrenar_y_registrar()
    logger.info("modelo inicial registrado: %s", resultado)
    return {"entrenado": True, "version": str(resultado["version"])}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Si MLflow no responde no queremos que cada consulta se quede minutos reintentando por dentro.
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "1")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "10")
    from sqlalchemy import create_engine

    from . import registro
    from .datos import desde_db
    from .entrenar import entrenar
    from .reentrenamiento import version_vigente

    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)

    def entrenar_y_registrar() -> dict:
        datos = desde_db(engine)
        # Igual que `python -m ml.entrenar`: baseline y LightGBM; el de menor costo queda como champion.
        resultados = [entrenar(datos, None, t) for t in ("baseline", "lightgbm")]
        print(registro.tabla_metricas(resultados))
        return registro.registrar(resultados)

    try:
        asegurar_modelo_inicial(version_vigente, entrenar_y_registrar)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
