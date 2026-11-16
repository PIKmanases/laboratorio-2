from fastapi import APIRouter, Request

from .db.repositorio import ErrorPersistencia
from .esquemas import (
    Calidad as CalidadRespuesta,
    EntradaObservacion,
    EntradaPrediccion,
    ObservacionCalidad,
    PrediccionFamilia,
    RespuestaObservaciones,
    RespuestaPrediccion,
)
from .limpieza import Calidad, SolicitudRechazada, limpiar_observacion, limpiar_prediccion

router = APIRouter(prefix="/v1")


def _a_respuesta(calidad: Calidad) -> CalidadRespuesta:
    return CalidadRespuesta(
        estado=calidad.estado,
        observaciones=[ObservacionCalidad(campo=o.campo, accion=o.accion, detalle=o.detalle) for o in calidad.observaciones],
    )


def _registrar_rechazo(repo, endpoint: str, crudo, e: SolicitudRechazada) -> None:
    """El rechazo se guarda 'a mejor esfuerzo': si app-db falla igual se responde el 422."""
    try:
        repo.guardar_rechazo(endpoint, crudo, e.campo, e.motivo)
    except ErrorPersistencia:
        pass


@router.post("/prediccion", response_model=RespuestaPrediccion)
def prediccion(entrada: EntradaPrediccion, request: Request) -> RespuestaPrediccion:
    estado = request.app.state
    crudo = entrada.model_dump(exclude_unset=True)
    try:
        limpia = limpiar_prediccion(entrada, estado.catalogos)
    except SolicitudRechazada as e:
        _registrar_rechazo(estado.repositorio, "prediccion", crudo, e)
        raise
    s = limpia.solicitud
    resultado = estado.predictor.predecir(s.tienda_id, s.fecha, s.familias)  # puede dar 503
    respuesta = RespuestaPrediccion(
        tienda_id=s.tienda_id,
        fecha=s.fecha,
        version_modelo=resultado.version,
        predicciones=[
            PrediccionFamilia(
                familia_id=p.familia_id, venta_esperada=p.venta_esperada, cantidad_sugerida=p.cantidad_sugerida
            )
            for p in resultado.pronosticos
        ],
        calidad=_a_respuesta(limpia.calidad),
    )
    # Una transacción: solicitud + correcciones + predicciones. Si falla -> 503 y no se
    # responde una predicción que no quedó registrada.
    estado.repositorio.guardar_prediccion(s, limpia.calidad, crudo, respuesta)
    return respuesta


@router.post("/observaciones", response_model=RespuestaObservaciones)
def observaciones(entrada: EntradaObservacion, request: Request) -> RespuestaObservaciones:
    estado = request.app.state
    crudo = entrada.model_dump(exclude_unset=True)
    try:
        limpia = limpiar_observacion(entrada, estado.catalogos)
    except SolicitudRechazada as e:
        _registrar_rechazo(estado.repositorio, "observaciones", crudo, e)
        raise
    guardados = estado.repositorio.guardar_observacion(limpia.solicitud, limpia.calidad, crudo)
    return RespuestaObservaciones(registros_guardados=guardados, calidad=_a_respuesta(limpia.calidad))
