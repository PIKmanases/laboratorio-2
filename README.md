# NovaMercado: servicio de pronóstico diario de ventas

Laboratorio 2 del curso Product Development (Maestría en Data Science). Es un **servicio de ML en
producción** para NovaMercado, una cadena de 10 supermercados: cada mañana una tienda pide el
pronóstico de ventas del día por familia de producto y al día siguiente reporta las ventas reales.
Todo el producto se levanta con `docker compose up`.

| Servicio | Puerto | Para qué sirve |
|---|---|---|
| `api` (FastAPI) | **8000** | Contrato de la API: predicciones, observaciones, reentrenamiento, `/health`, `/modelo` |
| `mlflow` | **5000** | Experimentos y Model Registry (el modelo vigente es el alias `champion`) |
| `airflow-apiserver` | **8080** | UI y API REST de Airflow (DAG `reentrenar_modelo`) |
| `app-db`, `mlflow-db`, `airflow-db` | (internos) | PostgreSQL 17, una por función; no se publican |
| `init` | — | Una sola vez: crea tablas, carga los CSV y entrena el primer modelo si no existe |
| `airflow-init`, `airflow-scheduler`, `airflow-dag-processor` | — | Soporte de Airflow 3 |

Las decisiones técnicas y de producto (con alternativas) están en [`DECISIONES.md`](DECISIONES.md).

## 1. Requisitos

- **Docker Engine con Compose v2** (`docker compose version` debe funcionar).
- **Memoria:** el producto completo necesita unos **4 GB libres para Docker** (solo `mlflow` usa 1,5 GB;
  con menos, el sistema operativo mata procesos y el arranque falla). En Windows usa WSL2 y, si hace falta,
  sube el límite en `%UserProfile%\.wslconfig` (`memory=6GB`) y ejecuta `wsl --shutdown`.
- **Puertos libres** en el equipo: 5000, 8000 y 8080.
- **Git** y, para correr las pruebas, **Python 3.11 o superior** (ver sección 7).
- En WSL clona el proyecto dentro del sistema de archivos de Linux (`~/...`), **no** en `/mnt/c`.

## 2. Puesta en marcha desde cero

### 2.1 Clonar

```bash
git clone https://github.com/PIKmanases/laboratorio-2.git
cd laboratorio-2
git config core.hooksPath .githooks      # opcional: protege la rama main (no se hacen commits directos)
```

### 2.2 Datos

Los CSV los publica el equipo docente y **no van en el repositorio**. Copia estos archivos a `./data/`:

```
data/
├── tiendas.csv
├── familias.csv
├── costos.csv
├── feriados.csv
└── historico_ventas.csv
```

(`init` los monta de solo lectura en `/data`. La carpeta puede incluir también `catalogo_errores.md` y `ejemplos/`.)

### 2.3 Configuración (`.env`)

```bash
cp .env.example .env
```

Edita `.env` y completa **todos** los valores (ninguna contraseña real va al repositorio; `.env` está en `.gitignore`):

| Variable | Qué poner |
|---|---|
| `APP_DB_PASSWORD`, `MLFLOW_DB_PASSWORD`, `AIRFLOW_DB_PASSWORD` | Cualquier contraseña (sin espacios) |
| `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD` | Usuario y contraseña de la UI y de la API REST de Airflow |
| `AIRFLOW_UID` | El resultado de `id -u` (normalmente `1000`) |
| `AIRFLOW_FERNET_KEY` | `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `AIRFLOW_JWT_SECRET` | `openssl rand -hex 32` |

> Las contraseñas de las bases **solo se aplican al crear su volumen**. Si las cambias después, hay que
> borrar los volúmenes con `docker compose down -v` (se pierden los datos).

### 2.4 Carpetas de Airflow

Se crean antes para que no queden con dueño `root`:

```bash
mkdir -p airflow/logs airflow/config airflow/plugins
```

### 2.5 Construir y levantar

```bash
docker compose up -d --build
```

La primera vez construye las imágenes (varios minutos) y luego arranca en este orden: bases de datos →
`mlflow` → `init` (crea tablas, carga el histórico y **entrena y registra el primer modelo**) → `api`.
En paralelo arranca Airflow. Sigue el progreso con:

```bash
docker compose ps -a
docker compose logs -f init            # se detiene solo al terminar
```

Está listo cuando:
- `init` y `airflow-init` aparecen como `Exited (0)` (es lo esperado: corren una sola vez);
- el resto de los servicios aparecen `Up` / `healthy`;
- `curl -i localhost:8000/health` responde `200` con `{"estado":"ok", ...}`.

Mientras no haya modelo cargado, `/health` responde **503**; es normal durante el primer arranque.
Si responde `200` con `"estado":"degradado"`, el campo `motivo` dice qué dependencia (base, MLflow o Airflow)
todavía no responde; espera un minuto y vuelve a consultar.

## 3. Probar el producto

Los archivos de ejemplo están en `data/ejemplos/` (si no los tienes, usa los cuerpos de la sección 5).

```bash
# Estado y modelo vigente
curl -s localhost:8000/health
curl -s localhost:8000/modelo

# Pronóstico (venta_esperada y cantidad_sugerida por familia)
curl -s -X POST localhost:8000/v1/prediccion -H "Content-Type: application/json" \
  -d @data/ejemplos/prediccion_valida.json

# Observaciones: la primera vez devuelve registros_guardados = 12; reenviarla idéntica devuelve 0
curl -s -X POST localhost:8000/v1/observaciones -H "Content-Type: application/json" \
  -d @data/ejemplos/observacion_valida.json

# Un cuerpo defectuoso se rechaza con 422 y un `detail` que explica el motivo
curl -s -i -X POST localhost:8000/v1/prediccion -H "Content-Type: application/json" \
  -d '{"tienda_id": 99, "fecha": "2025-11-17", "familias": [{"familia_id": "abarrotes", "en_promocion": 0}]}'
```

### Reentrenamiento

```bash
# Dispara el DAG de Airflow y devuelve {"id": "..."} (202)
ID=$(curl -s -X POST localhost:8000/admin/reentrenar | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "$ID"

# Consulta el estado hasta que deje de ser "en_curso" (puede tardar unos minutos)
curl -s localhost:8000/admin/reentrenar/$ID
```

Al terminar responde `estado` (`exitoso` / `fallido`), `promovido`, `version` (la vigente) y `motivo`.
El candidato **solo reemplaza** al modelo vigente si reduce el costo de negocio más de 1,5 % (D14); si no,
queda registrado en MLflow pero el alias `champion` no se toca. La API adopta un modelo promovido por sí sola
en menos de 2 minutos, sin reiniciarse: compruébalo con `curl -s localhost:8000/modelo`.

### Interfaces web

| Qué | URL | Acceso |
|---|---|---|
| Airflow | http://localhost:8080 | `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD` de tu `.env` |
| MLflow | http://localhost:5000 | Sin autenticación. Experimento `novamercado-pronostico`, modelo `novamercado-ventas` |
| Documentación de la API | http://localhost:8000/docs | — |

### Resiliencia

```bash
docker compose stop mlflow      # /health pasa a "degradado" en < 1 min; las predicciones siguen funcionando
docker compose start mlflow     # /health vuelve a "ok" en < 2 min
docker compose down             # detiene conservando los datos
docker compose up -d            # vuelve a levantar sin repetir la carga ni el entrenamiento inicial
```

## 4. Detener, reiniciar y limpiar

| Comando | Efecto |
|---|---|
| `docker compose stop` | Detiene los contenedores; conserva contenedores y volúmenes |
| `docker compose down` | Elimina los contenedores; **conserva** los volúmenes (datos, modelos, logs) |
| `docker compose down -v` | Reinicio **total**: borra todos los volúmenes; el siguiente `up` empieza desde cero |
| `docker compose build api init` | Reconstruye la imagen tras cambiar `api/` o `ml/` (`init` tiene imagen propia) |
| `docker compose logs -f <servicio>` | Logs de un servicio (`api`, `init`, `mlflow`, `airflow-scheduler`...) |

## 5. Contrato de la API

Puerto 8000, fechas `AAAA-MM-DD`. Un rechazo responde **422** con `detail`.

| Endpoint | Cuerpo | Respuesta |
|---|---|---|
| `POST /v1/prediccion` | `{tienda_id, fecha, familias: [{familia_id, en_promocion}]}` | 200 `{tienda_id, fecha, version_modelo, predicciones: [{familia_id, venta_esperada, cantidad_sugerida}], calidad: {estado, observaciones}}` |
| `POST /v1/observaciones` | `{tienda_id, fecha, ventas: [{familia_id, ventas}]}` | 200 `{registros_guardados, calidad}` (idempotente: reenviar lo mismo devuelve 0) |
| `POST /admin/reentrenar` | — | 202 `{id}` o 503 si no se puede iniciar |
| `GET /admin/reentrenar/{id}` | — | `{estado, promovido, version, motivo}`; 404 si el id es desconocido |
| `GET /modelo` | — | `{nombre, alias, version}` |
| `GET /health` | — | 200 `{estado: "ok"}`; 200 `{estado: "degradado", motivo}`; **503** sin modelo cargado |

`calidad.estado` es `ok`, `corregido` (solo se repararon datos) o `advertencia` (hubo algo que advertir).
Las políticas ante datos defectuosos (reparar / advertir / rechazar) están en `DECISIONES.md` (D1–D3).
Ejemplo mínimo de cuerpo de predicción:

```json
{"tienda_id": 5, "fecha": "2025-11-17", "familias": [{"familia_id": "abarrotes", "en_promocion": 0}]}
```

## 6. Estructura del repositorio

```
├── docker-compose.yml      # todos los servicios
├── .env.example            # plantilla de configuración (.env no se sube)
├── README.md
├── DECISIONES.md           # decisiones técnicas y de producto con alternativas
├── CLAUDE.md               # contexto para agentes de programación
├── data/                   # CSV del equipo docente (no se versiona)
├── api/                    # FastAPI: app/, requirements.txt
├── ml/                     # features (una sola función), modelos, newsvendor, métricas, entrenamiento,
│                           #   registro en MLflow, criterio de promoción, arranque inicial
├── dags/                   # DAGs de Airflow (reentrenar_modelo)
├── docker/                 # Dockerfiles (api, mlflow, airflow)
├── scripts/                # utilidades (explorar_modelos.py)
└── tests/                  # pytest
```

## 7. Pruebas

Las pruebas unitarias **no necesitan Docker, MLflow ni PostgreSQL** (usan dependencias falsas). Requieren Python ≥ 3.11:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt mlflow==3.18.0 scikit-learn==1.9.1 pytest
pytest
```

Con [uv](https://docs.astral.sh/uv/): `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r api/requirements.txt mlflow==3.18.0 scikit-learn==1.9.1 pytest`.
Algunas pruebas de `ml/` entrenan modelos pequeños y tardan más que el resto.

Las pruebas contra PostgreSQL real (`-m integracion`) se omiten salvo que definas `TEST_DATABASE_URL`;
**borran las tablas de esa base**, así que no apuntes a `app-db`:

```bash
docker run -d --rm --name pg-test -e POSTGRES_PASSWORD=t -p 55432:5432 postgres:17
TEST_DATABASE_URL=postgresql+psycopg://postgres:t@localhost:55432/postgres pytest -m integracion
```

## 8. Solución de problemas

| Síntoma | Causa y solución |
|---|---|
| `mlflow` se reinicia o «server process exited with code -9» | Falta de memoria (OOM). Dale más RAM a Docker/WSL2 (≥ 4 GB). Confirmar: `docker inspect -f '{{.State.OOMKilled}}' <contenedor>` |
| `init` falla con «No such file» o un CSV no se encuentra | Faltan archivos en `./data/` (sección 2.2). Corrígelo y ejecuta `docker compose up -d` |
| `init` falla al conectar a MLflow | MLflow tardó o cayó; revisa `docker compose logs mlflow` y vuelve a ejecutar `docker compose up -d` (`init` es idempotente) |
| `/health` devuelve 503 más de 10 minutos | `docker compose logs init`: el entrenamiento inicial falló o no terminó; la API se queda sin modelo hasta que exista `champion` |
| `/health` dice `degradado` por `airflow` | Airflow aún arranca (tarda más que la API). Espera y revisa `docker compose ps` |
| Tras cambiar una contraseña en `.env` falla la conexión | Las contraseñas se aplican solo al crear el volumen: `docker compose down -v` |
| Error de permisos en `airflow/logs` | Falta `AIRFLOW_UID` correcto en `.env` (usa `id -u`) o las carpetas se crearon como root: bórralas y repite 2.4 |
| Un script `.sh` falla dentro del contenedor | Finales de línea CRLF (típico en Windows). El repositorio fuerza LF con `.gitattributes`; vuelve a clonar dentro de WSL |
| Cambié el código de `api/` o `ml/` y no se refleja | `docker compose build api init` (y `docker compose build airflow-scheduler` si cambió `ml/`), luego `up -d` |

## 9. Flujo de trabajo en Git

`main` está protegida: **no se permiten commits directos**. Todo cambio va por una rama y un Pull Request:

```bash
git checkout -b feature/mi-cambio
git commit -am "mensaje"
git push -u origin feature/mi-cambio   # y abrir el Pull Request
```

La entrega está marcada con el tag `entrega-lab2`.
