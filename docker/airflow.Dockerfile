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
