import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .airflow_client import AirflowNoDisponible

router = APIRouter(prefix="/admin")
logger = logging.getLogger("api.admin")

# Airflow -> contrato. queued y running son «en_curso».
ESTADOS = {"queued": "en_curso", "running": "en_curso", "success": "exitoso", "failed": "fallido"}


@router.post("/reentrenar", status_code=202)
def reentrenar(request: Request):
    """Dispara el DAG. Si ya hay una ejecución en curso NO se rechaza: Airflow la deja en cola
    (max_active_runs=1) y el id nuevo pasará de `en_curso` a terminar cuando le toque (D14)."""
    st = request.app.state
    dag_run_id = f"reentrenar_{uuid.uuid4().hex}"  # el id lo elige la API: se guarda ANTES de disparar
    st.reentrenamientos.crear(dag_run_id)  # ErrorPersistencia -> 503
    try:
        st.airflow.disparar(dag_run_id)
    except AirflowNoDisponible as e:
        logger.error("no se pudo iniciar el reentrenamiento: %s", e)
        st.reentrenamientos.borrar(dag_run_id)  # nunca arrancó: que no quede una fila en_curso eterna
        raise HTTPException(status_code=503, detail="no se pudo iniciar el reentrenamiento: Airflow no disponible") from e
    return {"id": dag_run_id}


@router.get("/reentrenar/{dag_run_id}")
def estado_reentrenamiento(dag_run_id: str, request: Request):
    st = request.app.state
    fila = st.reentrenamientos.obtener(dag_run_id)
    if fila is None:  # solo ids que esta API creó: no se le pregunta nada a Airflow
        raise HTTPException(status_code=404, detail=f"reentrenamiento '{dag_run_id}' desconocido")
    try:
        estado = ESTADOS.get(st.airflow.estado(dag_run_id) or "", None)
    except AirflowNoDisponible:
        estado = None
    if estado is None:  # Airflow no responde o no lo conoce: vale lo que dejó guardado el DAG
        estado = fila["estado"]
    if estado == "en_curso":
        return {"estado": "en_curso"}
    promovido = bool(fila["promovido"]) if fila["promovido"] is not None else False
    # version = versión VIGENTE al terminar: la nueva si se promovió; si no, la que ya estaba.
    version = fila["version_nueva"] if promovido else fila["version_anterior"]
    if version is None and st.model is not None:
        version = st.model.version
    motivo = fila["motivo"] or (
        "la ejecución terminó con error o excedió el tiempo límite (ver Airflow); el modelo vigente no se tocó"
        if estado == "fallido" else "sin detalle"
    )
    return JSONResponse({"estado": estado, "promovido": promovido, "version": str(version or ""), "motivo": motivo})
