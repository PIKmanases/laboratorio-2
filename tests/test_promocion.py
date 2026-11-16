import math

import pytest

from ml.promocion import MARGEN_PROMOCION, decidir_promocion


def test_promueve_si_mejora_mas_que_el_margen():
    d = decidir_promocion(90.0, 100.0)  # 10 % mejor
    assert d.promover and "10.0%" in d.motivo


def test_no_promueve_si_la_mejora_es_menor_al_margen():
    d = decidir_promocion(99.0, 100.0)  # 1 % < 1,5 %
    assert not d.promover and "no supera el margen" in d.motivo


def test_el_borde_exacto_no_promueve():
    """La regla es estricta: costo_candidato < costo_vigente * (1 - margen)."""
    assert not decidir_promocion(100.0 * (1 - MARGEN_PROMOCION), 100.0).promover
    assert decidir_promocion(100.0 * (1 - MARGEN_PROMOCION) - 1e-9, 100.0).promover


def test_empate_o_peor_no_promueve():
    assert not decidir_promocion(100.0, 100.0).promover
    assert not decidir_promocion(120.0, 100.0).promover


def test_margen_configurable():
    assert decidir_promocion(97.0, 100.0, margen=0.02).promover
    assert not decidir_promocion(97.0, 100.0, margen=0.05).promover
    with pytest.raises(ValueError):
        decidir_promocion(1.0, 2.0, margen=1.5)


def test_sin_vigente_se_promueve_el_primero():
    d = decidir_promocion(123.0, None)
    assert d.promover and "no hay modelo vigente" in d.motivo


@pytest.mark.parametrize("cand,vig", [(math.nan, 100.0), (math.inf, 100.0), (-1.0, 100.0), (50.0, math.nan), (50.0, 0.0), (50.0, -5.0)])
def test_valores_invalidos_nunca_promueven(cand, vig):
    assert not decidir_promocion(cand, vig).promover
