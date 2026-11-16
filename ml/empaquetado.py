"""Empaquetado como modelo `pyfunc` de MLflow: guarda el `Pronosticador` completo (modelo,
factores, plan B, catálogos) para que quien lo cargue solo tenga que llamar a `predict`."""

from __future__ import annotations

import tempfile

import lightgbm
import mlflow
import mlflow.pyfunc
import pandas
from mlflow.models import ModelSignature

from .modelos import Pronosticador

# Versiones fijas: las mismas que las imágenes de api y airflow (ver DECISIONES.md).
PIP = [f"mlflow=={mlflow.__version__}", f"pandas=={pandas.__version__}", f"lightgbm=={lightgbm.__version__}"]


class ModeloVentas(mlflow.pyfunc.PythonModel):
    """predict(historia): `historia` = ventas recientes + filas a pronosticar (ventas = NaN, con
    la en_promocion de la solicitud). Devuelve tienda_id, familia_id, fecha, venta_esperada y
    cantidad_sugerida de las filas a pronosticar."""

    def load_context(self, context):
        self._p = Pronosticador.cargar(context.artifacts["paquete"])

    def predict(self, context, model_input, params=None):
        return self._p.predecir(model_input)


def guardar_en_mlflow(p: Pronosticador, nombre: str = "modelo") -> mlflow.models.model.ModelInfo:
    """Debe llamarse dentro de un run activo."""
    with tempfile.TemporaryDirectory() as tmp:
        p.guardar(tmp)
        return mlflow.pyfunc.log_model(
            name=nombre,
            python_model=ModeloVentas(),
            artifacts={"paquete": tmp},
            pip_requirements=PIP,
            metadata={k: v for k, v in p.meta.items() if k in ("nombre", "fecha_corte", "ultimo_dia")},
        )
