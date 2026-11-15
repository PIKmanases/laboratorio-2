from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.limpieza import Catalogos
from app.main import create_app
from app.predictor import PredictorPromedio

FAMILIAS = [
    ("abarrotes", "Abarrotes"), ("bebidas", "Bebidas"), ("lacteos", "Lácteos"), ("carnes", "Carnes"),
    ("panaderia", "Panadería"), ("frutas_verduras", "Frutas y verduras"), ("limpieza", "Limpieza"),
    ("cuidado_personal", "Cuidado personal"), ("licores", "Licores"), ("snacks", "Snacks y dulces"),
    ("bebe", "Bebé"), ("mascotas", "Mascotas"),
]  # fmt: skip


@pytest.fixture
def catalogos() -> Catalogos:
    return Catalogos.desde_filas(range(1, 11), FAMILIAS)


@pytest.fixture
def todas_las_familias() -> list[str]:
    return [f for f, _ in FAMILIAS]


@pytest.fixture
def client(catalogos) -> TestClient:
    historia = [
        (date(2025, 11, 14), 5, "abarrotes", 100.0),
        (date(2025, 11, 15), 5, "abarrotes", 200.0),
        (date(2025, 11, 17), 5, "abarrotes", 9999.0),  # el día pedido: NO debe usarse
    ]
    return TestClient(create_app(catalogos=catalogos, predictor=PredictorPromedio(historia)))
