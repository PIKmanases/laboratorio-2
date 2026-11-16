FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# LightGBM necesita OpenMP (la imagen slim no lo trae).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 1) Dependencias primero: esta capa se reutiliza mientras requirements.txt no cambie.
COPY api/requirements.txt .
RUN pip install -r requirements.txt

# 2) Código después: cambiarlo no invalida la capa de dependencias.
COPY api/app ./app
COPY ml ./ml

RUN useradd --system --no-create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
