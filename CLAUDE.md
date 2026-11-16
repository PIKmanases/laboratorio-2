# CLAUDE.md — NovaMercado: servicio de pronóstico diario de ventas

Contexto para agentes de programación (Claude Code, Codex, etc.) que trabajen en este repositorio.

## Qué es este proyecto

Laboratorio 2 del curso Product Development (Maestría en Data Science). Es un **servicio de ML en
producción** para NovaMercado, una cadena guatemalteca de 10 supermercados. Cada mañana, cada tienda pide un
pronóstico de ventas del día por familia de producto, y al día siguiente reporta las ventas reales.

La dificultad principal **no es el modelo, sino operar el servicio**:
1. Un contrato de API que se defiende de datos defectuosos (reparar / advertir / rechazar).
2. Un registro persistente de cada solicitud, predicción, corrección y observación.
3. Un reentrenamiento automático con un criterio explícito de promoción, que el servicio adopta sin reiniciarse.

Todo el producto se levanta con `docker compose up`.

**El estudiante es responsable de entender y justificar todo el código.** Al proponer cambios, explica
el porqué, señala las alternativas y no introduzcas dependencias o complejidad sin justificarlas.

## Arquitectura

| Servicio (compose) | Tecnología | Puerto | Función |
|---|---|---|---|
| `api` | FastAPI + Uvicorn + Pydantic + SQLAlchemy | **8000** (publicado) | Contrato del Anexo. Es lo único que usa el evaluador. |
| `app-db` | PostgreSQL 17 | 5432 (interno) | BD del producto: solicitudes, predicciones, correcciones, observaciones, histórico. |
| `mlflow` | MLflow 3.18.0 | **5000** (publicado) | Tracking y Model Registry. **El servicio debe llamarse `mlflow`.** |
| `mlflow-db` | PostgreSQL 17 | 5432 (interno) | Backend store de MLflow. |
| `airflow-apiserver` | Airflow 3.x (`airflow api-server`) | **8080** (publicado) | UI y API REST v2. |
| `airflow-scheduler` | Airflow 3.x, LocalExecutor | — | Ejecuta las tareas de los DAGs. |
| `airflow-dag-processor` | Airflow 3.x | — | Analiza `dags/` (servicio obligatorio en Airflow 3). |
| `airflow-init` | Airflow 3.x (una sola vez) | — | Migra la BD de Airflow y crea el usuario admin. |
| `airflow-db` | PostgreSQL 17 | 5432 (interno) | BD de Airflow. **Separada de `app-db`** (requisito). |
| `init` | Imagen de API/ML (una sola vez) | — | Arranque desde cero: crea tablas (`create_all`) y carga los CSV (idempotente, monta `./data:/data:ro`) y entrena el primer modelo **solo si MLflow no tiene `@champion`** (`ml/arranque.py`, D16). |

Conexiones dentro de la red de compose:
- `api` → `app-db` (persistencia, catálogos y features recientes; arranca después de `init`), `mlflow` (sondea el alias del modelo vigente), `airflow-apiserver` (dispara y consulta el DAG).
- Los servicios de Airflow → `app-db` (datos para entrenar) y `mlflow` (registro y promoción).
- `mlflow` sirve los artefactos por HTTP (`--serve-artifacts`, volumen `mlflow-artifacts`), así que ningún otro contenedor monta ese volumen.

Volúmenes con nombre: `app-db-data`, `mlflow-db-data`, `mlflow-artifacts`, `airflow-db-data` y los logs de Airflow.

## Estructura del repositorio

```
├── docker-compose.yml
├── .env.example            # plantilla; .env NUNCA se sube
├── README.md               # cómo levantar desde cero + carpeta de datos
├── DECISIONES.md           # decisiones técnicas y de producto con alternativas
├── CLAUDE.md
├── data/                   # CSV publicados por el equipo docente (ignorado por git)
├── api/                    # FastAPI: app/, requirements.txt
├── ml/                     # features (UNA función), modelos, newsvendor, métricas, entrenar, registro MLflow (criterio de promoción y pasos del reentrenamiento)
├── dags/                   # DAGs de Airflow
├── docker/                 # Dockerfiles (api, mlflow, airflow)
├── scripts/                # utilidades (p. ej. simulador/evaluador propio)
└── tests/                  # pytest
```

## Estado actual

- [x] Paso 1: entorno (WSL + Docker), repo en GitHub, exploración de los datos.
- [x] Paso 2a: `app-db`, `mlflow-db`, `mlflow` y `api` con `GET /health` (monitor en segundo plano). `/health` responde 503 porque todavía no hay modelo; es el comportamiento esperado.
- [ ] Paso 2b: Airflow 3 (apiserver, scheduler, dag-processor, init, airflow-db) + DAG de prueba disparado por la API REST + chequeo de Airflow en `/health`.
- [x] Paso 3: contrato con Pydantic + capa de limpieza con pruebas.
- [x] Paso 4: esquema de BD (`app/db/`), persistencia por transacción, observaciones idempotentes (upsert en la BD), vista `ventas_para_entrenamiento`, carga de CSV por `init`. Si app-db cae, las predicciones/observaciones responden **503** (DECISIONES.md D6).
- [x] Paso 5: `ml/` (features únicas, baseline seasonal naive, LightGBM global, newsvendor, métricas), experimento `novamercado-pronostico`, modelo `novamercado-ventas@champion` y conexión a la API (carga al arrancar; sondeo = paso 7). Entrenar: `docker compose run --rm init python -m ml.entrenar`.
- [x] Paso 6: DAG `reentrenar_modelo` (`dags/`), lógica en `ml/reentrenamiento.py` y criterio de promoción en `ml/promocion.py` (costo de negocio con margen de 1,5 %, DECISIONES.md D14), endpoints `/admin/reentrenar` (`api/app/rutas_admin.py`, `airflow_client.py`, tabla `reentrenamientos`). Pruebas unitarias escritas; **falta verificarlo de extremo a extremo con el compose levantado** (promoción, rechazo, fallo y límite de 10 min).
- [x] Paso 7: sondeo del alias cada 30 s y recarga en caliente (`api/app/recarga.py`, D15); si MLflow cae se conserva el modelo en memoria; `/health` alineado con el contrato (`estado`/`degradado`/503 sin modelo) y con chequeo de Airflow. Pruebas unitarias escritas; **falta verificarlo con el compose levantado** (adopción ≤ 2 min, `degradado` < 1 min, regreso a `ok` < 2 min).
- [x] Paso 8: `init` = `app.db.carga` + `ml.arranque` (entrena solo si no hay `@champion`; fuente de verdad: el Model Registry; DECISIONES.md D16). Depende de `mlflow` con `service_healthy`. Pruebas unitarias escritas; **falta cronometrar `down -v` + `up`** (≤ 10 min hasta `/health` ok) y comprobar que `down` + `up` no reentrena.
- [ ] Paso 9: README, DECISIONES.md, evidencia y tag `entrega-lab2`.

## Contrato de la API (Anexo del enunciado — no omitir ni renombrar campos)

Puerto 8000. Fechas en formato `AAAA-MM-DD`. Un rechazo responde **422** con un campo `detail` que explique el motivo.
Se pueden **agregar** campos extra a las respuestas.

- `POST /v1/prediccion` — cuerpo: `{tienda_id, fecha, familias: [{familia_id, en_promocion}]}`.
  Respuesta 200: `{tienda_id, fecha, version_modelo (str), predicciones: [{familia_id, venta_esperada, cantidad_sugerida (int ≥ 0)}], calidad: {estado, observaciones: [{campo, accion, detalle}]}}`.
  `calidad.estado` ∈ {`ok`, `corregido`, `advertencia`}; `accion` ∈ {`reparar`, `advertir`}.
  Si hay alguna acción `advertir` → `advertencia`; si solo hay `reparar` → `corregido`; `ok` va **sin** observaciones.
  Una tienda puede pedir varias veces el pronóstico de la misma fecha.
- `POST /v1/observaciones` — cuerpo: `{tienda_id, fecha, ventas: [{familia_id, ventas}]}`.
  Respuesta 200: `{registros_guardados, calidad}`. `registros_guardados` = registros nuevos o modificados; reenviar una observación idéntica devuelve **0** (idempotente, upsert por tienda+familia+fecha).
- `POST /admin/reentrenar` — sin cuerpo. Responde **202** `{id: str}` (el `dag_run_id` de Airflow) o **503** si no se puede iniciar.
- `GET /admin/reentrenar/{id}` — `{estado ∈ {en_curso, exitoso, fallido}, promovido, version, motivo}`; los últimos tres campos solo cuando termina. `version` = versión vigente al terminar. Un id desconocido responde **404**.
  Mapeo desde Airflow: `queued`/`running` → `en_curso`, `success` → `exitoso`, `failed` → `fallido`.
- `GET /modelo` — `{nombre, alias, version}` (todos texto).
- `GET /health` — 200 `{estado: "ok"}` si la API, la BD, MLflow, Airflow y el modelo están bien; 200 `{estado: "degradado", motivo}` si alguna dependencia falla; **503** mientras no haya modelo cargado.

## Reglas no negociables

- **No usar el reloj del sistema** para validar fechas: el servicio opera con fechas simuladas.
- **Sin fuga de información:** al predecir la fecha D solo se usan observaciones de días anteriores a D. Las promociones del día vienen en la solicitud; las observaciones no las traen.
- **Lógica de limpieza fuera de los endpoints**: va en módulos de Python con pruebas. Las reglas deben ser **generales**, porque el evaluador enviará problemas que no están en `catalogo_errores.md`. Cada tipo de problema tiene una política fija (reparar / advertir / rechazar), documentada en DECISIONES.md y aplicada de forma consistente.
- **No inventar datos**: si algo no se puede interpretar sin ambigüedad, advertir o rechazar.
- **Pruebas con pytest que no dependan de MLflow, Postgres ni Docker**: inyectar dependencias (p. ej. un monitor de salud falso o un modelo falso) mediante fábricas como `create_app(...)`.
- **Datos y credenciales fuera del repo y de las imágenes.** `data/` y `.env` están en `.gitignore`; mantener `.env.example` actualizado.
- **Versiones fijas** en imágenes (nada de `latest`) y en `requirements` (`==`). La versión de mlflow y de las librerías de ML debe ser **la misma** en `api`, `mlflow` y la imagen de Airflow.
- **El modelo vigente se identifica con un alias** en el Model Registry (p. ej. `@champion`). Un reentrenamiento que falla o que no supera el criterio **no toca** el alias.
- **La API sigue prediciendo con el modelo en memoria aunque MLflow esté caído.** Nunca contactar a MLflow dentro de una predicción.
- **`cantidad_sugerida` ≠ `venta_esperada`**: se elige según los costos de `costos.csv` (problema *newsvendor*, cuantil crítico `c_faltante / (c_faltante + c_sobrante)`).

## Tiempos que se evalúan

- Producto listo (`/health` = ok) en ≤ 10 min desde `up`, sin contar la construcción de las imágenes.
- Cada reentrenamiento termina en ≤ 10 min.
- La API adopta un modelo promovido en ≤ 2 min sin reiniciarse (sondeo del alias cada ~30 s).
- `/health` muestra `degradado` en < 1 min tras la caída de MLflow y vuelve a `ok` en < 2 min cuando regresa (chequeos en segundo plano cada ~15 s).
- Tras `down` + `up` (sin `-v`) se conserva todo y **no** se repite el entrenamiento inicial.

## Métrica de negocio

Por tienda, familia y día:
`costo = c_faltante · max(real − sugerido, 0) + c_sobrante · max(sugerido − real, 0)`
Se reporta en MLflow junto con las métricas técnicas (MAE, RMSE, etc.) y es la base del criterio de promoción.

## Comandos

```bash
cp .env.example .env                 # primera vez
docker compose up -d --build
docker compose ps
docker compose logs -f <servicio>
curl -i localhost:8000/health
docker compose stop mlflow           # simular la caída de MLflow
docker compose down                  # detener conservando volúmenes
docker compose down -v               # reinicio TOTAL desde cero (borra los datos)

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Disparar un DAG por la API REST de Airflow 3:
```bash
TOKEN=$(curl -s -X POST localhost:8080/auth/token -H "Content-Type: application/json" \
  -d '{"username":"<usuario>","password":"<clave>"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST localhost:8080/api/v2/dags/<dag_id>/dagRuns -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"logical_date": null}'
curl -s localhost:8080/api/v2/dags/<dag_id>/dagRuns/<dag_run_id> -H "Authorization: Bearer $TOKEN"
```

## Persistencia (paso 4)

- Código en `api/app/db/`: `modelos.py` (tablas), `esquema.py` (`crear_esquema`: `create_all` + vistas), `repositorio.py` (`RepositorioSQL`, `PredictorSQL`, `ErrorPersistencia`), `carga.py` (`python -m app.db.carga`).
- Tablas: referencia (`tiendas`, `familias`, `costos`, `feriados`, `ventas_historicas`), operación (`solicitudes`, `solicitudes_rechazadas`, `correcciones`, `predicciones`, `observaciones`) y control (`reentrenamientos`, `estado_sistema`). Detalle en DECISIONES.md D5.
- **`create_all` no altera tablas existentes**: si cambias una columna, `docker compose down -v`. Solo `init` crea el esquema; la API no.
- **Datos de entrenamiento = vista `ventas_para_entrenamiento`** (histórico + observaciones válidas). Las promociones de días observados salen de la última predicción pedida; sin solicitud se supone 0 y `promocion_conocida = false`.
- Los repositorios/predictores se inyectan en `create_app(...)`; las pruebas unitarias usan `tests/fakes.py` (sin Postgres).
- Pruebas contra Postgres real (`-m integracion`, se omiten sin `TEST_DATABASE_URL`; **borran las tablas de esa base**, no uses app-db):
  `docker run -d --rm --name pg-test -e POSTGRES_PASSWORD=t -p 55432:5432 postgres:17` y
  `TEST_DATABASE_URL=postgresql+psycopg://postgres:t@localhost:55432/postgres .venv/bin/pytest -m integracion`.
- Entorno local: las versiones fijadas piden Python ≥ 3.11 (el del sistema es 3.10): `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r api/requirements.txt mlflow==3.18.0 scikit-learn==1.9.1 pytest`.

## Modelo (paso 5)

- `ml/features.py`: **`construir_features` es la única función de features**; la usan el entrenamiento y la API (`PredictorML`). No dupliques lógica de features en la API. Solo datos hasta D-1 + `en_promocion` de D + calendario + atributos fijos (DECISIONES.md D9).
- Falta de historia: LightGBM maneja NaN; si una serie no tiene ventas en 28 días se usa el **plan B** (media familia × formato × día). Ver D10.
- Validación SIEMPRE temporal: entrenamiento → calibración (56 d) → prueba (56 d). `entrenar(datos, fecha_corte)`; los factores newsvendor se recalculan en cada entrenamiento y viajan dentro del modelo (D11, D12).
- `ml/` se copia a la imagen de la API (`docker/api.Dockerfile`); la imagen de Airflow también la incluye (`COPY ml`, `PYTHONPATH=/opt/airflow`). Versiones de `mlflow`/`pandas`/`lightgbm` iguales en `api`, `mlflow` y Airflow (en la API se usa `mlflow-skinny`).
- `python scripts/explorar_modelos.py` reproduce las comparaciones de DECISIONES.md D11/D12.
- La API adopta un `champion` nuevo sola (sondeo cada 30 s, paso 7); no hace falta reiniciarla tras entrenar o reentrenar. Si arrancó sin modelo, también lo carga el sondeo.

## Reentrenamiento (paso 6)

- Flujo del DAG: `preparar_datos → entrenar_candidato → evaluar_y_decidir → promover → registrar_resultado`. **Solo `promover` mueve el alias `champion`**; el candidato se registra siempre como versión sin alias. No añadas otro punto que mueva el alias.
- Criterio: `costo_candidato < costo_vigente × (1 − MARGEN_PROMOCION)` en la misma ventana de prueba; el vigente se reajusta si ya vio esa ventana (D14). La lógica no depende de Airflow: pruébala en `tests/test_reentrenamiento.py` y `tests/test_promocion.py`.
- La API elige el `dag_run_id`, lo guarda en `reentrenamientos` antes de disparar y lo borra si Airflow no responde. Un segundo disparo se encola (`max_active_runs=1`), no se rechaza.
- Sin reintentos en el DAG y `dagrun_timeout` de 9 min (límite de 10 min por reentrenamiento).
- Las tareas del DAG leen de `app-db` con `APP_DB_URL`, nunca de la BD de Airflow.

## Problemas conocidos y sus soluciones
- **El servicio `mlflow` necesita ~1,5 GB** (con 768 MB lo mata el OOM-killer al registrar modelos: «server process exited with code -9»). `docker inspect -f '{{.State.OOMKilled}}' <contenedor>` lo confirma.
- `docker compose build api` **no** reconstruye `init` (imagen propia con el mismo Dockerfile): usa `docker compose build api init`.

- **MLflow 3.x responde 403 a otros contenedores** si no se pasa `--allowed-hosts mlflow,mlflow:5000,localhost,localhost:5000` (valida el header Host). Comprobado.
- En el compose, `api` depende de `mlflow` con `condition: service_started`, **no** `service_healthy`: la API debe arrancar aunque MLflow esté caído.
- La imagen slim de Python no trae `curl`: los healthchecks usan `python -c "import urllib.request; ..."`.
- Las variables `POSTGRES_*` solo se aplican al crear el volumen; si cambias una contraseña, hay que borrar el volumen con `down -v`.
- **Airflow 3:** el `webserver` ahora es `airflow api-server`; `dag-processor` es un servicio aparte obligatorio; la autenticación de la API REST es JWT (`/auth/token`, `/api/v2/...`). Los tutoriales de Airflow 2 (`/api/v1`, basic auth) no aplican.
- `AIRFLOW__CORE__EXECUTION_API_SERVER_URL=http://airflow-apiserver:8080/execution/` y `AIRFLOW__API_AUTH__JWT_SECRET` deben ser idénticos en todos los servicios de Airflow, o las tareas fallan con "Invalid auth token".
- `AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=false`: si el DAG está pausado, el disparo se acepta pero la ejecución nunca corre.
- `_AIRFLOW_WWW_USER_CREATE` se ignora con SimpleAuthManager → usar el FAB auth manager para tener un usuario y contraseña fijos.
- No usar `_PIP_ADDITIONAL_REQUIREMENTS` en Airflow: instalar los paquetes en la imagen propia (`docker/airflow.Dockerfile`).
- WSL: el proyecto va en el sistema de archivos de Linux (`~/...`), no en `/mnt/c`. Finales de línea LF (`.gitattributes` con `* text=auto eol=lf`); un `.sh` con CRLF falla dentro del contenedor.
- `.env` debe incluir `AIRFLOW_UID` (salida de `id -u`, normalmente 1000) para los permisos de los logs.

## Convenciones

- Código, comentarios y documentación en español; los identificadores siguen los nombres del contrato (`tienda_id`, `familia_id`, `venta_esperada`, ...).
- Las solicitudes y respuestas se definen con modelos de Pydantic v2; la configuración se lee de variables de entorno con `pydantic-settings`.
- La configuración (URLs, credenciales, intervalos) nunca va escrita en el código.
- Cada decisión de producto relevante (políticas ante datos defectuosos, features usadas al predecir, regla de la cantidad sugerida, criterio de promoción) se registra en `DECISIONES.md` con alternativas y justificación.
- Commits pequeños y descriptivos; la entrega se marca con el tag `entrega-lab2`.
