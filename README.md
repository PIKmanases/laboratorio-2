# Laboratorio 2 – MLOps

Proyecto del curso **Data Science Development**: flujo de Machine Learning con seguimiento de experimentos en MLflow.

## Requisitos
- Python 3.10+
- Git

## Instalación
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # completar credenciales
```

## Estructura
```
data/        datos (los pesados no se versionan)
notebooks/   exploración y experimentos
src/         código fuente
.githooks/   hooks de git (protección de main)
```

## MLflow
Los artefactos locales (`mlruns/`, `mlartifacts/`) no se versionan.
```bash
mlflow ui
```

## Flujo de trabajo
`main` está protegida: **no se permiten commits directos**. Todo cambio va por una rama:
```bash
git checkout -b feature/mi-cambio
git commit -am "mensaje"
git push -u origin feature/mi-cambio   # y abrir Pull Request
```
Tras clonar, activa los hooks:
```bash
git config core.hooksPath .githooks
```
