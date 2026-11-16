FROM apache/airflow:3.4.0

# LightGBM necesita OpenMP (libgomp) del sistema.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
USER airflow

# mlflow debe coincidir con la versión del servidor (docker/mlflow.Dockerfile).
RUN pip install --no-cache-dir \
    mlflow==3.18.0 \
    pandas==3.1.1 \
    scikit-learn==1.9.1 \
    lightgbm==4.7.0

# Código de ML compartido con la API (features, modelos, newsvendor, criterio de promoción).
# Va en la imagen (no en un volumen) para que DAG, API y entrenamiento usen la misma versión.
COPY --chown=airflow:0 ml /opt/airflow/ml
ENV PYTHONPATH=/opt/airflow
