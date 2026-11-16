"""Creación del esquema: tablas con `create_all` y vistas con SQL."""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .modelos import Base

# Última solicitud de predicción por (tienda, familia, fecha): lo que la tienda declaró como
# promoción de ese día. Si pidió varias veces, vale la más reciente (id mayor).
VISTA_PROMOCION_SOLICITADA = """
CREATE OR REPLACE VIEW promocion_solicitada AS
SELECT DISTINCT ON (tienda_id, familia_id, fecha)
       tienda_id, familia_id, fecha, en_promocion
FROM predicciones
ORDER BY tienda_id, familia_id, fecha, id DESC
"""

# Insumo único del modelo (paso 5) y del reentrenamiento (paso 6).
#  - Histórico + observaciones válidas, sin duplicar: si un día existe en ambos, manda la
#    observación (es el dato más reciente).
#  - Las observaciones NO traen promoción. Se toma de la última predicción pedida para ese
#    día; si no hubo solicitud, de la fila histórica que reemplaza; si tampoco, SE SUPONE 0 y
#    `promocion_conocida` queda en false para poder filtrarlo o ponderarlo.
VISTA_VENTAS_ENTRENAMIENTO = """
CREATE OR REPLACE VIEW ventas_para_entrenamiento AS
SELECT h.fecha, h.tienda_id, h.familia_id, h.ventas, h.en_promocion,
       TRUE AS promocion_conocida, 'historico'::text AS origen
FROM ventas_historicas h
WHERE NOT EXISTS (
    SELECT 1 FROM observaciones o
    WHERE o.valido AND o.tienda_id = h.tienda_id AND o.familia_id = h.familia_id AND o.fecha = h.fecha
)
UNION ALL
SELECT o.fecha, o.tienda_id, o.familia_id, o.ventas,
       COALESCE(p.en_promocion, h.en_promocion, 0) AS en_promocion,
       (p.en_promocion IS NOT NULL OR h.en_promocion IS NOT NULL) AS promocion_conocida,
       'observacion'::text AS origen
FROM observaciones o
LEFT JOIN promocion_solicitada p USING (tienda_id, familia_id, fecha)
LEFT JOIN ventas_historicas h USING (tienda_id, familia_id, fecha)
WHERE o.valido
"""


def crear_esquema(engine: Engine) -> None:
    """Idempotente. Lo ejecuta el contenedor `init`, no la API."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(VISTA_PROMOCION_SOLICITADA))
        conn.execute(text(VISTA_VENTAS_ENTRENAMIENTO))
