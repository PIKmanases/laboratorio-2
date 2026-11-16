from app.db.repositorio import ErrorPersistencia


class RepositorioMemoria:
    """Repositorio falso: imita la semántica de la BD (upsert que solo cuenta cambios)."""

    def __init__(self):
        self.solicitudes: list[dict] = []
        self.rechazos: list[dict] = []
        self.observaciones: dict[tuple, int] = {}
        self.caida = False  # simula app-db caída

    def _revisar(self):
        if self.caida:
            raise ErrorPersistencia("app-db caída (simulada)")

    def guardar_prediccion(self, solicitud, calidad, crudo, respuesta) -> int:
        self._revisar()
        self.solicitudes.append(
            {"tipo": "prediccion", "solicitud": solicitud, "calidad": calidad, "crudo": crudo, "respuesta": respuesta}
        )
        return len(self.solicitudes)

    def guardar_observacion(self, solicitud, calidad, crudo) -> int:
        self._revisar()
        cambiadas = 0
        for v in solicitud.ventas:
            clave = (solicitud.tienda_id, v.familia_id, solicitud.fecha)
            if self.observaciones.get(clave) != v.ventas:
                self.observaciones[clave] = v.ventas
                cambiadas += 1
        self.solicitudes.append({"tipo": "observacion", "solicitud": solicitud, "calidad": calidad, "crudo": crudo})
        return cambiadas

    def guardar_rechazo(self, endpoint, crudo, campo, motivo) -> None:
        self._revisar()
        self.rechazos.append({"endpoint": endpoint, "crudo": crudo, "campo": campo, "motivo": motivo})


# ---------------------------------------------------------------- predictor falso (en memoria)
from bisect import bisect_left  # noqa: E402
from collections import defaultdict  # noqa: E402
from itertools import accumulate  # noqa: E402

from app.predictor import Pronostico, ResultadoPredictor  # noqa: E402


class PredictorPromedio:
    """Promedio de (tienda, familia) en fechas ESTRICTAMENTE anteriores a la pedida."""

    def __init__(self, filas):
        series = defaultdict(list)
        for fecha, tienda, familia, ventas in filas:
            series[(tienda, familia)].append((fecha, ventas))
        self._fechas, self._acum = {}, {}
        for k, v in series.items():
            v.sort()
            self._fechas[k] = [f for f, _ in v]
            self._acum[k] = list(accumulate(x for _, x in v))

    def predecir(self, tienda_id, fecha, familias) -> ResultadoPredictor:
        out = []
        for fam in familias:
            k = (tienda_id, fam.familia_id)
            n = bisect_left(self._fechas.get(k, []), fecha)
            esperada = self._acum[k][n - 1] / n if n else 0.0
            out.append(Pronostico(fam.familia_id, esperada, max(0, round(esperada))))
        return ResultadoPredictor("0", out)
