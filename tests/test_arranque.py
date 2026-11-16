"""Arranque desde cero (paso 8): el modelo inicial se entrena solo si no hay champion. Sin MLflow."""

import pytest

from ml.arranque import asegurar_modelo_inicial


class Registro:
    def __init__(self, vigente=None, fallos_previos=0):
        self.vigente, self.fallos = vigente, fallos_previos
        self.consultas = self.entrenamientos = 0

    def consultar(self):
        self.consultas += 1
        if self.fallos > 0:
            self.fallos -= 1
            raise ConnectionError("MLflow aún no responde")
        return self.vigente

    def entrenar(self):
        self.entrenamientos += 1
        self.vigente = "1"
        return {"version": "1", "alias": "champion"}


def correr(reg, **kw):
    return asegurar_modelo_inicial(reg.consultar, reg.entrenar, dormir=lambda s: None, **kw)


def test_primer_arranque_sin_champion_entrena_y_registra():
    reg = Registro()
    assert correr(reg) == {"entrenado": True, "version": "1"}
    assert reg.entrenamientos == 1


def test_si_ya_hay_champion_no_entrena_de_nuevo():
    reg = Registro(vigente="4")
    assert correr(reg) == {"entrenado": False, "version": "4"}
    assert reg.entrenamientos == 0


def test_es_idempotente_al_correr_dos_veces():
    reg = Registro()
    correr(reg)
    assert correr(reg) == {"entrenado": False, "version": "1"}
    assert reg.entrenamientos == 1


def test_espera_a_que_mlflow_responda():
    reg = Registro(fallos_previos=3)
    assert correr(reg)["entrenado"] is True
    assert reg.consultas == 4


def test_si_mlflow_nunca_responde_falla_sin_entrenar():
    reg = Registro(fallos_previos=99)
    with pytest.raises(ConnectionError):
        correr(reg, intentos=3)
    assert reg.consultas == 3 and reg.entrenamientos == 0


def test_si_el_entrenamiento_falla_el_error_se_propaga():
    reg = Registro()
    reg.entrenar = lambda: (_ for _ in ()).throw(ValueError("datos insuficientes"))
    with pytest.raises(ValueError):
        correr(reg)
