"""Pruebas contra un PostgreSQL REAL (upsert, vista, transacciones, carga).

Se omiten si no hay `TEST_DATABASE_URL`. Ejemplo con una base desechable:
    docker run -d --rm --name pg-test -e POSTGRES_PASSWORD=t -p 55432:5432 postgres:17
    TEST_DATABASE_URL=postgresql+psycopg://postgres:t@localhost:55432/postgres pytest -m integracion
OJO: cada prueba BORRA y recrea las tablas de esa base; no apuntes a app-db.
"""

import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from app.db.carga import cargar
from app.db.esquema import crear_esquema
from app.db.modelos import Base
from app.db.repositorio import ErrorPersistencia, RepositorioSQL, historia_desde_db
from app.esquemas import (
    FamiliaSolicitada,
    ObservacionVentas,
    PrediccionFamilia,
    RespuestaPrediccion,
    SolicitudPrediccion,
    VentaFamilia,
)
from app.limpieza import Calidad

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = [pytest.mark.integracion, pytest.mark.skipif(not URL, reason="falta TEST_DATABASE_URL")]

FAMILIAS = ["abarrotes", "bebidas"]
D = date(2025, 11, 17)


@pytest.fixture
def engine():
    eng = create_engine(URL)
    with eng.begin() as c:
        c.execute(text("DROP VIEW IF EXISTS ventas_para_entrenamiento, promocion_solicitada"))
    Base.metadata.drop_all(eng)
    crear_esquema(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO tiendas VALUES (5,'T','c','d','t','2019-01-01'),(6,'U','c','d','t','2019-01-01')"))
        c.execute(text("INSERT INTO familias VALUES ('abarrotes','Abarrotes'),('bebidas','Bebidas')"))
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    return RepositorioSQL(engine)


def obs(ventas: dict, fecha=D, tienda=5):
    return ObservacionVentas(tienda_id=tienda, fecha=fecha, ventas=tuple(VentaFamilia(familia_id=f, ventas=v) for f, v in ventas.items()))


def pedir(repo, promo: dict, fecha=D, tienda=5, esperada=50.0):
    s = SolicitudPrediccion(tienda_id=tienda, fecha=fecha, familias=tuple(FamiliaSolicitada(familia_id=f, en_promocion=p) for f, p in promo.items()))
    r = RespuestaPrediccion(
        tienda_id=tienda, fecha=fecha, version_modelo="0",
        predicciones=[PrediccionFamilia(familia_id=f, venta_esperada=esperada, cantidad_sugerida=int(esperada)) for f in promo],
        calidad={"estado": "ok"},
    )  # fmt: skip
    return repo.guardar_prediccion(s, Calidad(), {"crudo": True}, r)


def contar(engine, tabla):
    with engine.connect() as c:
        return c.execute(text(f"SELECT count(*) FROM {tabla}")).scalar_one()


# ------------------------------------------------------------------ esquema
def test_crear_esquema_es_idempotente(engine):
    crear_esquema(engine)
    crear_esquema(engine)
    with engine.connect() as c:
        tablas = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    assert tablas >= {"tiendas", "familias", "costos", "feriados", "ventas_historicas", "solicitudes", "correcciones",
                      "predicciones", "observaciones", "solicitudes_rechazadas", "reentrenamientos", "estado_sistema"}  # fmt: skip


# ------------------------------------------------------------------ idempotencia
def test_upsert_solo_cuenta_filas_nuevas_o_modificadas(repo, engine):
    assert repo.guardar_observacion(obs({"abarrotes": 10, "bebidas": 20}), Calidad(), {}) == 2
    assert repo.guardar_observacion(obs({"abarrotes": 10, "bebidas": 20}), Calidad(), {}) == 0
    assert repo.guardar_observacion(obs({"abarrotes": 10, "bebidas": 21}), Calidad(), {}) == 1
    assert repo.guardar_observacion(obs({"abarrotes": 10}), Calidad(), {}) == 0  # subconjunto idéntico
    assert contar(engine, "observaciones") == 2
    assert contar(engine, "solicitudes") == 4  # cada envío queda en el registro
    with engine.connect() as c:
        assert c.execute(text("SELECT ventas FROM observaciones WHERE familia_id='bebidas'")).scalar_one() == 21


def test_upsert_no_toca_valido(repo, engine):
    repo.guardar_observacion(obs({"abarrotes": 10}), Calidad(), {})
    with engine.begin() as c:
        c.execute(text("UPDATE observaciones SET valido = false"))
    assert repo.guardar_observacion(obs({"abarrotes": 10}), Calidad(), {}) == 0
    with engine.connect() as c:
        assert c.execute(text("SELECT valido FROM observaciones")).scalar_one() is False


def test_registros_guardados_queda_en_la_solicitud(repo, engine):
    repo.guardar_observacion(obs({"abarrotes": 10}), Calidad(), {})
    repo.guardar_observacion(obs({"abarrotes": 10}), Calidad(), {})
    with engine.connect() as c:
        assert [r[0] for r in c.execute(text("SELECT registros_guardados FROM solicitudes ORDER BY id"))] == [1, 0]


# ------------------------------------------------------------------ transacciones
def test_prediccion_y_correcciones_se_guardan_juntas(repo, engine):
    c = Calidad()
    c.reparar("tienda_id", "'T05' -> 5")
    s = SolicitudPrediccion(tienda_id=5, fecha=D, familias=(FamiliaSolicitada(familia_id="abarrotes", en_promocion=3),))
    r = RespuestaPrediccion(tienda_id=5, fecha=D, version_modelo="0", calidad={"estado": "corregido", "observaciones": [{"campo": "tienda_id", "accion": "reparar", "detalle": "x"}]},
                            predicciones=[PrediccionFamilia(familia_id="abarrotes", venta_esperada=1.5, cantidad_sugerida=2)])  # fmt: skip
    repo.guardar_prediccion(s, c, {"tienda_id": "T05"}, r)
    assert (contar(engine, "solicitudes"), contar(engine, "correcciones"), contar(engine, "predicciones")) == (1, 1, 1)


def test_si_falla_una_parte_no_se_guarda_nada(repo, engine):
    s = SolicitudPrediccion(tienda_id=5, fecha=D, familias=(FamiliaSolicitada(familia_id="inexistente", en_promocion=0),))
    r = RespuestaPrediccion(tienda_id=5, fecha=D, version_modelo="0", calidad={"estado": "ok"},
                            predicciones=[PrediccionFamilia(familia_id="inexistente", venta_esperada=1.0, cantidad_sugerida=1)])  # fmt: skip
    with pytest.raises(ErrorPersistencia):  # viola la FK de predicciones.familia_id
        repo.guardar_prediccion(s, Calidad(), {}, r)
    assert (contar(engine, "solicitudes"), contar(engine, "predicciones")) == (0, 0)  # sin solicitud huérfana


def test_observacion_atomica(repo, engine):
    with pytest.raises(ErrorPersistencia):
        repo.guardar_observacion(obs({"abarrotes": 10, "inexistente": 1}), Calidad(), {})
    assert (contar(engine, "solicitudes"), contar(engine, "observaciones")) == (0, 0)


def test_base_caida_lanza_error_de_persistencia():
    muerto = create_engine("postgresql+psycopg://x:x@127.0.0.1:1/x", connect_args={"connect_timeout": 1})
    with pytest.raises(ErrorPersistencia):
        RepositorioSQL(muerto).guardar_rechazo("prediccion", {}, None, "m")
    with pytest.raises(ErrorPersistencia):
        historia_desde_db(muerto)(5, D, ["abarrotes"])


def test_cuerpo_con_nan_y_nul_no_rompe_el_registro(repo, engine):
    repo.guardar_rechazo("prediccion", {"a": float("nan"), "b": "x\x00y"}, "c\x00", "m\x00")
    assert contar(engine, "solicitudes_rechazadas") == 1


# ------------------------------------------------------------------ vista de entrenamiento
def vista(engine, **filtros):
    with engine.connect() as c:
        return [tuple(r) for r in c.execute(text("SELECT fecha, tienda_id, familia_id, ventas, en_promocion, promocion_conocida, origen FROM ventas_para_entrenamiento ORDER BY fecha, tienda_id, familia_id"))]


def test_vista_promocion_de_la_ultima_solicitud(repo, engine):
    pedir(repo, {"abarrotes": 1})
    pedir(repo, {"abarrotes": 4})  # la tienda corrigió: vale la más reciente
    repo.guardar_observacion(obs({"abarrotes": 30}), Calidad(), {})
    assert vista(engine) == [(D, 5, "abarrotes", 30, 4, True, "observacion")]


def test_vista_sin_solicitud_supone_cero_y_lo_marca(repo, engine):
    repo.guardar_observacion(obs({"abarrotes": 30}), Calidad(), {})
    assert vista(engine) == [(D, 5, "abarrotes", 30, 0, False, "observacion")]


def test_vista_solo_observaciones_validas(repo, engine):
    repo.guardar_observacion(obs({"abarrotes": 30, "bebidas": 5}), Calidad(), {})
    with engine.begin() as c:
        c.execute(text("UPDATE observaciones SET valido = false WHERE familia_id = 'bebidas'"))
    assert [f[2] for f in vista(engine)] == ["abarrotes"]


def test_vista_no_duplica_dias_que_estan_en_el_historico(repo, engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO ventas_historicas VALUES (5,'abarrotes','2025-11-17',100,7),(5,'bebidas','2025-11-17',200,0)"))
    repo.guardar_observacion(obs({"abarrotes": 111}), Calidad(), {})  # corrige el histórico
    filas = vista(engine)
    assert (D, 5, "abarrotes", 111, 7, True, "observacion") in filas  # promoción: la del histórico
    assert (D, 5, "bebidas", 200, 0, True, "historico") in filas
    assert len(filas) == 2


# ------------------------------------------------------------------ historia para el modelo
def test_historia_reciente_solo_dias_anteriores_y_en_ventana(repo, engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO ventas_historicas VALUES (5,'abarrotes','2025-08-01',1,0),(5,'abarrotes','2025-11-14',100,0),(5,'abarrotes','2025-11-15',200,0),(6,'abarrotes','2025-11-15',7,0)"))
    repo.guardar_observacion(obs({"abarrotes": 600}, fecha=date(2025, 11, 16)), Calidad(), {})
    repo.guardar_observacion(obs({"abarrotes": 9999}), Calidad(), {})  # el propio día D: NO debe entrar
    h = historia_desde_db(engine)(5, D, ["abarrotes", "bebidas"])
    assert sorted(h["ventas"]) == [100, 200, 600]  # sin 2025-08-01 (fuera de 60 días), sin D, sin otra tienda
    assert h["fecha"].max() < pd.Timestamp(D)


def test_prediccion_de_punta_a_punta_con_la_bd(repo, engine):
    """Pronosticador real (entrenado con datos sintéticos) + historia leída de Postgres."""
    from app.modelo_mlflow import ModeloCargado
    from app.predictor import PredictorML
    from sinteticos import datos_sinteticos

    from ml.entrenar import entrenar

    pron = entrenar(datos_sinteticos(dias=150), tipo="baseline", dias_calibracion=20, dias_prueba=20).modelo

    class Directo:
        def predict(self, df):
            return pron.predecir(df)

    with engine.begin() as c:
        c.execute(text("INSERT INTO ventas_historicas SELECT 5, 'abarrotes', d::date, 100, 0 FROM generate_series('2025-10-01'::date, '2025-11-16', '1 day') d"))
    modelo = ModeloCargado("m", "champion", "7", Directo())
    r = PredictorML(historia_desde_db(engine), lambda: modelo).predecir(5, D, [FamiliaSolicitada(familia_id="abarrotes", en_promocion=2)])
    assert r.version == "7" and r.pronosticos[0].familia_id == "abarrotes"
    assert r.pronosticos[0].venta_esperada == pytest.approx(100.0)  # media de los 4 mismos días: todos 100
    assert r.pronosticos[0].cantidad_sugerida >= 100


# ------------------------------------------------------------------ carga de CSV
def escribir(dir: Path, familia="abarrotes", tienda="5", extra=""):
    (dir / "tiendas.csv").write_text("tienda_id,nombre,ciudad,departamento,tipo,fecha_apertura\n5,T,c,d,t,2019-01-01\n")
    (dir / "familias.csv").write_text("familia_id,nombre\nabarrotes,Abarrotes\nlacteos,Lácteos\n")
    (dir / "costos.csv").write_text("familia_id,costo_faltante,costo_sobrante\nabarrotes,1.0,0.2\nlacteos,1.0,1.2\n")
    (dir / "feriados.csv").write_text("fecha,nombre,tipo,ambito\n2024-03-28,Jueves Santo,feriado,nacional\n")
    (dir / "historico_ventas.csv").write_text(f"fecha,tienda_id,familia_id,ventas,en_promocion\n2024-03-01,{tienda},{familia},10,1\n2024-03-01,5,lacteos,7,0\n{extra}")


@pytest.fixture
def vacia(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE familias, tiendas CASCADE"))
    return engine


def test_carga_idempotente_y_marca_estado(vacia, tmp_path):
    escribir(tmp_path)
    assert cargar(vacia, tmp_path) is True
    assert cargar(vacia, tmp_path) is False  # ya había datos: no hace nada
    assert (contar(vacia, "ventas_historicas"), contar(vacia, "costos"), contar(vacia, "feriados"), contar(vacia, "tiendas")) == (2, 2, 1, 1)
    with vacia.connect() as c:
        assert c.execute(text("SELECT valor FROM estado_sistema WHERE clave='carga_inicial'")).scalar_one() == "completada"


def test_carga_normaliza_ids_como_la_limpieza(vacia, tmp_path):
    escribir(tmp_path, familia="LÁCTEOS ", tienda="T05", extra="2024-03-02,05,Abarrotes,3,0\n")
    cargar(vacia, tmp_path)
    with vacia.connect() as c:
        assert {r[0] for r in c.execute(text("SELECT DISTINCT familia_id FROM ventas_historicas"))} == {"lacteos", "abarrotes"}
        assert c.execute(text("SELECT DISTINCT tienda_id FROM ventas_historicas")).scalar_one() == 5


def test_carga_con_id_irresoluble_aborta_y_no_deja_nada(vacia, tmp_path):
    escribir(tmp_path, familia="pescados")
    with pytest.raises(SystemExit):
        cargar(vacia, tmp_path)
    assert contar(vacia, "ventas_historicas") == 0 and contar(vacia, "tiendas") == 0
    with vacia.connect() as c:
        assert c.execute(text("SELECT count(*) FROM estado_sistema")).scalar_one() == 0
