from dataclasses import dataclass
from typing import Literal


class SolicitudRechazada(Exception):
    """La solicitud no se puede interpretar sin inventar datos -> 422."""

    def __init__(self, campo: str, motivo: str):
        self.campo = campo
        self.motivo = motivo
        super().__init__(f"{campo}: {motivo}")


@dataclass(frozen=True)
class Observacion:
    campo: str
    accion: Literal["reparar", "advertir"]
    detalle: str


class Calidad:
    """Acumula lo que la limpieza hizo; de aquí sale el bloque `calidad` de la respuesta."""

    def __init__(self) -> None:
        self._observaciones: list[Observacion] = []

    def reparar(self, campo: str, detalle: str) -> None:
        self._observaciones.append(Observacion(campo, "reparar", detalle))

    def advertir(self, campo: str, detalle: str) -> None:
        self._observaciones.append(Observacion(campo, "advertir", detalle))

    @property
    def observaciones(self) -> tuple[Observacion, ...]:
        return tuple(self._observaciones)

    @property
    def estado(self) -> Literal["ok", "corregido", "advertencia"]:
        if any(o.accion == "advertir" for o in self._observaciones):
            return "advertencia"
        return "corregido" if self._observaciones else "ok"
