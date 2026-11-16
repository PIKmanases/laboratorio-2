FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# 1) Dependencias primero: esta capa se reutiliza mientras requirements.txt no cambie.
COPY api/requirements.txt .
RUN pip install -r requirements.txt

# 2) Código después: cambiarlo no invalida la capa de dependencias.
COPY api/app ./app

RUN useradd --system --no-create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
