"""Sondeo del alias del modelo vigente y recarga en caliente (paso 7, DECISIONES.md D15).

Cada `intervalo` segundos (≈ 30) pregunta a MLflow a qué versión apunta `@champion`. Si es distinta
de la que está en memoria, la carga en un hilo aparte y, SOLO si la carga terminó bien, reemplaza
`app.state.model` con una única asignación (atómica). Una predicción en curso termina con el objeto
anterior que ya tenía en la mano; la siguiente usa el nuevo. Nunca hay un modelo «a medias».

Si MLflow no responde, el alias no existe o la carga falla, se conserva el modelo en memoria y se
reintenta en el siguiente ciclo. Las predicciones no pasan nunca por aquí: jamás contactan a MLflow.
"""

import asyncio
import logging
from collections.abc import Callable

from .modelo_mlflow import ModeloCargado

logger = logging.getLogger("api.recarga")


class SondeoModelo:
    def __init__(
        self,
        estado,  # app.state: se lee y se reemplaza `estado.model`
        consultar_version: Callable[[], str],
        cargar: Callable[[str], ModeloCargado],
        intervalo: float = 30.0,
        timeout_consulta: float = 15.0,
        timeout_carga: float = 120.0,
    ):
        self._estado = estado
        self._consultar_version = consultar_version
        self._cargar = cargar
        self._intervalo = intervalo
        self._timeout_consulta = timeout_consulta
        self._timeout_carga = timeout_carga

    async def run(self) -> None:
        while True:
            await self.sondear_una_vez()
            await asyncio.sleep(self._intervalo)

    async def sondear_una_vez(self) -> bool:
        """Devuelve True si se cargó y adoptó un modelo nuevo. Nunca lanza (salvo cancelación)."""
        actual = self._estado.model.version if self._estado.model is not None else None
        try:
            version = str(await asyncio.wait_for(asyncio.to_thread(self._consultar_version), self._timeout_consulta))
            if version == actual:
                return False
            logger.info("el alias apunta a la versión %s (en memoria: %s): cargando", version, actual)
            nuevo = await asyncio.wait_for(asyncio.to_thread(self._cargar, version), self._timeout_carga)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  MLflow caído, sin alias, artefacto ilegible, timeout...
            logger.warning("no se pudo sondear/recargar el modelo (se conserva v%s): %r", actual, exc)
            return False
        self._estado.model = nuevo  # un solo reemplazo atómico
        logger.info("modelo adoptado: v%s (antes: %s)", nuevo.version, actual)
        return True

