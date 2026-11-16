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
