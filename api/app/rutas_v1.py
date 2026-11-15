from fastapi import APIRouter, Request

from .esquemas import (
    Calidad as CalidadRespuesta,
    EntradaObservacion,
    EntradaPrediccion,
    ObservacionCalidad,
    PrediccionFamilia,
    RespuestaObservaciones,
    RespuestaPrediccion,
)
from .limpieza import Calidad, limpiar_observacion, limpiar_prediccion

router = APIRouter(prefix="/v1")


def _a_respuesta(calidad: Calidad) -> CalidadRespuesta:
    return CalidadRespuesta(
        estado=calidad.estado,
        observaciones=[ObservacionCalidad(campo=o.campo, accion=o.accion, detalle=o.detalle) for o in calidad.observaciones],
    )


@router.post("/prediccion", response_model=RespuestaPrediccion)
def prediccion(entrada: EntradaPrediccion, request: Request) -> RespuestaPrediccion:
    limpia = limpiar_prediccion(entrada, request.app.state.catalogos)  # SolicitudRechazada -> 422
    s = limpia.solicitud
    predictor = request.app.state.predictor
    esperadas = predictor.predecir(s.tienda_id, s.fecha, s.familias)
    return RespuestaPrediccion(
        tienda_id=s.tienda_id,
        fecha=s.fecha,
        version_modelo=predictor.version,
        predicciones=[
            PrediccionFamilia(
                familia_id=f.familia_id,
                venta_esperada=esperadas[f.familia_id],
                # Provisional: el paso 5 lo reemplaza por el cuantil newsvendor.
                cantidad_sugerida=max(0, round(esperadas[f.familia_id])),
            )
            for f in s.familias
        ],
        calidad=_a_respuesta(limpia.calidad),
    )


@router.post("/observaciones", response_model=RespuestaObservaciones)
def observaciones(entrada: EntradaObservacion, request: Request) -> RespuestaObservaciones:
    limpia = limpiar_observacion(entrada, request.app.state.catalogos)
    # Paso 4: aquí irá el upsert idempotente. Todavía no hay base de datos.
    return RespuestaObservaciones(registros_guardados=0, calidad=_a_respuesta(limpia.calidad))
