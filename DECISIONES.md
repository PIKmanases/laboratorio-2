# DECISIONES

Registro de decisiones técnicas y de producto, con alternativas y justificación.

## D1. Contrato defensivo: flujo de cuatro etapas (Paso 3)

```
JSON ──► 1. Entrada TOLERANTE ──► 2. Limpieza ──► 3. Modelo CANÓNICO ──► 4. Predicción ──► Modelo de RESPUESTA
         (Pydantic, extra=allow)   (Python puro)    (Pydantic strict)       (predictor)       (Pydantic, contrato)
```

| Etapa | Dónde | Responsabilidad | Qué NO hace |
|---|---|---|---|
| 1. Entrada tolerante | `app/esquemas.py` (`EntradaPrediccion`, `EntradaObservacion`) | Verifica la estructura mínima: el cuerpo es un objeto y `familias`/`ventas` es una lista de objetos. Campos `Any`, `extra="allow"`. | No decide si un valor es válido. |
| 2. Limpieza | `app/limpieza/` (sin FastAPI, BD ni MLflow) | Normaliza, decide (reparar / advertir / rechazar) y registra en un objeto `Calidad`. Rechaza con `SolicitudRechazada`. | No toca HTTP; los catálogos se **inyectan** (`Catalogos`). |
| 3. Canónico estricto | `app/esquemas.py` (`SolicitudPrediccion`, `ObservacionVentas`) | Tipos exactos (`int`, `date`), `strict=True`, `extra="forbid"`, inmutable. Si falla aquí es un bug de la limpieza, no un dato sucio. | No convierte nada. |
| 4. Respuesta | `app/esquemas.py` (`RespuestaPrediccion`, `RespuestaObservaciones`) | `response_model` exacto del Anexo; `Calidad` valida que `estado` sea coherente con las observaciones (`ok` ⇒ sin observaciones; algún `advertir` ⇒ `advertencia`; solo `reparar` ⇒ `corregido`). | — |

**Piezas de diseño**

- `Calidad`: un objeto que acumula observaciones (`reparar()`, `advertir()`) y calcula `estado`.
- `SolicitudRechazada(campo, motivo)`: excepción propia; un *exception handler* la convierte en **422** `{"detail": "<campo>: <motivo>"}`. Los errores de estructura de Pydantic (etapa 1) se convierten al mismo formato (`detail` como texto).
- `normalizar_texto()` (`limpieza/texto.py`): **una sola** función (sin acentos, minúsculas, todo lo no alfanumérico → `_`). La usan nombres de campo, familias y tiendas.
- `Catalogos` (`limpieza/catalogos.py`): se construye con `desde_csv()` o `desde_filas()` y se inyecta; las pruebas no leen archivos.

**Alternativas descartadas.** (a) Un solo modelo Pydantic con validadores que reparen: mezcla validar con corregir, no puede registrar qué se corrigió y los rechazos salen como errores genéricos. (b) Limpiar dentro del endpoint: no se puede probar sin HTTP y la lógica se duplica entre los dos endpoints.

## D2. Criterio de clasificación

| Política | Criterio | Efecto en la respuesta |
|---|---|---|
| **Reparar** | Hay **una sola** interpretación razonable y **no se pierde** información. | 200, `accion: "reparar"` → `estado: "corregido"`. |
| **Advertir** | Se puede responder, pero con un **supuesto** que podría estar equivocado. | 200, `accion: "advertir"` → `estado: "advertencia"` (domina sobre `reparar`). |
| **Rechazar** | Responder sería **inventar datos**, o **no se sabe qué se está preguntando** (tienda desconocida, fecha imposible). | 422 con `detail`. |

Si la solicitud llega bien, `estado: "ok"` y **sin** observaciones.

## D3. Tabla de políticas por tipo de problema

Códigos E01–E10 del catálogo (`data/catalogo_errores.md`). Las reglas son **generales**: el evaluador enviará problemas que no están en el catálogo, y caen en la fila de su tipo (cuya política es fija), no en una lista de casos.

### Solicitudes de predicción (`POST /v1/prediccion`) y campos comunes

| Cód. | Tipo de problema | Regla general | Política | Justificación |
|---|---|---|---|---|
| E01 | `tienda_id` como texto, prefijo o float | `int` exacto → se usa tal cual (sin observación). `float` sin decimales (`5.0`) → se usa. Texto → `normalizar_texto` y se extraen los dígitos; si queda **exactamente un número** y existe en `tiendas.csv` → se usa. Funciona con `"5"`, `"T05"`, `"tienda-5"`, `" 05"`. | **Reparar** | Un solo número que además es una tienda real: una única lectura, nada se pierde. |
| E01 | `tienda_id` con dos números (`"T05-2"`), ninguno (`"tienda"`), decimales (`5.5`), booleano, lista | — | **Rechazar** | No se sabe a cuál tienda se refiere. |
| E01 | `tienda_id` con un número que no está en el catálogo (`99`) | — | **Rechazar** | Un pronóstico para una tienda inexistente sería inventado: no se sabe para quién es. |
| E02 | Familia escrita de otra forma | `normalizar_texto` y búsqueda en el `familia_id` **o** el `nombre` de `familias.csv`: `"LÁCTEOS"`, `"lacteos "`, `"Frutas y Verduras"`, `"Snacks y dulces"`, `"cuidado-personal"`. | **Reparar** | La forma normalizada coincide con una única familia del catálogo. |
| E02 | Familia que no existe tras normalizar (`"lacteo"`, `"pescados"`), vacía o no textual | No se hace coincidencia aproximada. | **Rechazar** | Adivinar el parecido más cercano es inventar la pregunta. |
| E03 | `en_promocion` como texto o decimal entero | `"3"`, `2.0`, `" 9 "`, `"1,0"` → entero. | **Reparar** | Misma cantidad, distinto tipo. |
| E03 | `en_promocion` con parte decimal (`2.5`), negativo, no numérico o booleano | No se redondea ni se trunca. | **Rechazar** | Redondear cambiaría la entrada que el modelo usa; negativo es imposible. |
| E03b | `en_promocion` ausente o `null` en una familia | Se asume `0`. | **Advertir** | El supuesto «sin promoción» puede ser falso y afecta la predicción. |
| E04 | Fecha ISO con hora `00:00:00` (`2025-11-17T00:00:00`), con espacios o sin ceros (`2025-1-5`), o con año primero (`2025/11/17`) | Se toma la fecha. | **Reparar** | Una sola fecha posible. |
| E04 | Fecha `d/m/AAAA` (`/`, `-` o `.`) **sin ambigüedad**: un campo > 12 o ambos iguales (`17/11/2025`, `11/17/2025`, `05/05/2025`) | El campo > 12 es el día. | **Reparar** | Solo una lectura es un calendario válido. |
| E04 | Fecha `d/m/AAAA` **ambigua** (`05/06/2025`) | Se asume día/mes (convención de Guatemala). | **Advertir** | Puede ser 5 de junio o 6 de mayo. |
| E04 | Fecha con hora ≠ 00:00:00 o zona horaria | Se usa la fecha escrita. | **Advertir** | La zona horaria podría correr el día. |
| E04 | Fecha imposible (`2025-02-30`, `31/04/2025`, `13/13/2025`), formato no reconocido o no textual | — | **Rechazar** | No se sabe para qué día es el pronóstico. |
| E05 | Faltan familias que la tienda vende | Se pronostican solo las recibidas; el detalle lista las faltantes. | **Advertir** | No se inventa un `en_promocion` para las ausentes; el supuesto «solo quiero estas» puede ser un olvido. |
| E05b | Lista de familias vacía o ausente | — | **Rechazar** | No hay pregunta. |
| E06 | Familia repetida con **el mismo** valor | Se deja un registro. | **Reparar** | Nada se pierde. |
| E06 | Familia repetida con valores **distintos** | Gana el **último**. | **Advertir** | Es un supuesto (la última es una corrección); podría ser la otra. |
| E07 | Campo con otro nombre (`"tienda"`, `"Tienda ID"`, `"promo"`, `"familia"`) | La clave se normaliza y se compara con el nombre del contrato y una lista corta de sinónimos. | **Reparar** | Equivalencia clara; la lista es corta a propósito. |
| E07 | Dos claves equivalentes con valores distintos (`tienda_id: 5` y `tienda: 6`) | — | **Rechazar** | No se sabe cuál vale. |
| E07 | Campo desconocido sin equivalente (`"clima"`) | Se ignora. | **Advertir** | Podría ser un dato importante mal nombrado. |
| E07 | Falta `tienda_id`, `fecha` o la lista (y no hay sinónimo) | — | **Rechazar** | Falta la pregunta. |

### Observaciones de ventas (`POST /v1/observaciones`)

Comparte con predicción las filas de tienda (E01), familia (E02), fecha (E04), nombres de campo (E07), familia repetida (E06) y familias faltantes (E05, advertencia). Además:

| Cód. | Tipo de problema | Regla general | Política | Justificación |
|---|---|---|---|---|
| E08 | Ventas negativas (`-4`) | Se **omite el registro**; no se asume `0`. El detalle dice cuál. | **Advertir** | Es una devolución registrada como venta: poner `0` inventaría una venta y guardar `-4` contaminaría el histórico. |
| E08b | Ventas ausentes o `null` | Se omite el registro. | **Advertir** | `0` y «no se midió» no son lo mismo. |
| E08c | Todos los registros inutilizables | — | **Rechazar** | No queda nada que guardar. |
| E09 | Mismo día y tienda enviados de nuevo | No es un defecto: se resuelve en la BD con *upsert* por (tienda, familia, fecha); reenviar lo idéntico devuelve `registros_guardados = 0` (Paso 4). Dentro de una misma solicitud, la familia repetida sigue E06. | **Ok** (sin observación) | Reenviar es normal (reintentos); se resuelve con idempotencia, no con una advertencia. |
| E10 | Número con separador de miles o coma decimal | `"1,234"` → 1234; `"1,234.0"` → 1234; `"1.234,0"` → 1234; `"812,0"` → 812; `"812.0"` y `812.0` → 812. | **Reparar** | Las ventas son unidades enteras (el histórico lo confirma): `1,234` como 1.234 no tendría sentido. |
| E10 | Número ambiguo o fraccionario (`"1.234"`, `"812,5"`, `812.5`), texto no numérico, `NaN`, `inf`, booleano | Un punto con tres decimales **no se adivina** como miles. | **Rechazar** | Cualquier lectura sería inventar la cifra que se guardará como verdad. |

**Convención numérica.** La coma agrupa miles (`1,234`) y el punto es decimal (`812.0`); una coma sola que no forma grupos de tres (`812,0`) es decimal. `1.234,5` (punto de miles + coma decimal) se reconoce porque la coma decimal elimina la ambigüedad.

**Por qué ventas inválidas rechazan toda la solicitud pero un negativo no.** Un valor ilegible (`"mucho"`) no tiene lectura y obliga a decidir qué guardar; un negativo es legible y solo se descarta ese registro, así que el resto se aprovecha y se avisa.

### Reglas transversales

- **Fechas simuladas:** nunca se consulta el reloj del sistema; no se valida que la fecha sea «pasada» o «futura».
- **Sin fuga:** al predecir la fecha D solo se usan observaciones con fecha **< D** (el predictor falso lo respeta y hay una prueba).
- **Rutas de los campos:** las observaciones usan la ruta del campo (`familias[3].familia_id`) para ubicar el problema.
- **Pendiente (Paso 4):** idempotencia (E09) y validar contra el histórico existente (p. ej. una observación muy distinta a las anteriores).

## D4. Predictor (actualizado en el Paso 5)

Pasos 3–4: predictor falso (promedio histórico, `version_modelo = "0"`). **Desde el Paso 5** lo reemplaza `PredictorML` (`app/predictor.py`): lee la historia reciente de app-db, agrega las filas a pronosticar y llama al modelo cargado en memoria. El predictor falso solo sobrevive en `tests/fakes.py`. Sin modelo cargado, `POST /v1/prediccion` y `GET /modelo` responden **503** (igual que `/health`).

## D5. Esquema de app-db y creación con `create_all` (Paso 4)

| Grupo | Tabla | Contenido / decisión |
|---|---|---|
| a) Referencia | `tiendas`, `familias`, `costos`, `feriados`, `ventas_historicas` | Se cargan una vez desde los CSV. PK de `ventas_historicas` = (tienda, familia, fecha), con FK a tiendas y familias. `costos` es por familia. |
| b) Operación | `solicitudes` | Cada solicitud **aceptada** (predicción u observación): `tipo`, `cuerpo_crudo` (JSONB, tal como llegó), `estado_calidad`, `version_modelo` / `registros_guardados`. **Sin** unicidad por (tienda, fecha): una tienda puede pedir varias veces. |
| | `solicitudes_rechazadas` | Cada 422: endpoint, cuerpo crudo, campo y motivo. **Tabla aparte, sin FK a tiendas**, porque la tienda puede ser justo lo inválido. Incluye errores de estructura de Pydantic. |
| | `correcciones` | Una fila por observación de `calidad` (campo, acción, detalle), ligada a su solicitud. |
| | `predicciones` | Una fila por familia: `venta_esperada`, `cantidad_sugerida`, `version_modelo` y la `en_promocion` declarada. Copia `tienda_id` y `fecha` de la solicitud (denormalización deliberada) para indexar la consulta de promociones sin join. |
| | `observaciones` | Ventas reales. **PK (tienda, familia, fecha)**: es lo que permite el upsert. `valido` (por omisión `true`) permite excluir un registro del entrenamiento sin borrarlo; el upsert nunca lo modifica. |
| c) Control | `reentrenamientos` | Una fila por ejecución del DAG (`dag_run_id`, estado, `promovido`, versiones, motivo, métricas). La completa el DAG del Paso 6 (D14). |
| | `estado_sistema` | Clave-valor. Hoy: `carga_inicial = completada`. |

**`create_all`.** Las tablas se declaran con SQLAlchemy (`app/db/modelos.py`) y se crean con `Base.metadata.create_all()` más dos `CREATE OR REPLACE VIEW` (`app/db/esquema.py`). Es idempotente, pero **no altera tablas que ya existen**: si cambia una columna hay que reiniciar con `docker compose down -v` (aceptable en el laboratorio; en producción se usaría Alembic). Solo lo ejecuta el contenedor `init` (un único dueño del esquema, sin carreras): la API depende de `init` con `service_completed_successfully` y no crea tablas. *Alternativas descartadas:* SQL a mano en un `.sql` montado en `docker-entrypoint-initdb.d` (solo corre con el volumen vacío y duplica el esquema); Alembic (migraciones sin necesidad todavía).

**Vista `ventas_para_entrenamiento`** (insumo único para el Paso 5 y el 6): histórico + `observaciones` con `valido = true`, sin duplicar días.
- Si un día está en el histórico y también observado, **manda la observación**.
- **Promociones:** las observaciones no traen `en_promocion`. Se toma de la **última** predicción pedida para (tienda, familia, fecha) (vista `promocion_solicitada`; si la tienda pidió varias veces vale la de mayor `id`). Si ese día **no hubo solicitud**, no se conoce la promoción: se usa la del histórico si el día lo reemplaza y, si tampoco, **se supone 0**. La columna `promocion_conocida` queda en `false` en ese caso, para que el modelo pueda filtrar o ponderar esas filas. *Riesgo conocido:* si una tienda observa muchos días sin haber pedido pronóstico, el modelo aprende «sin promoción» con ventas que sí tuvieron promoción.
- La columna `origen` (`historico` / `observacion`) permite auditar.

## D6. Si app-db no responde: 503 (Paso 4)

Se elige **responder 503** en `POST /v1/prediccion` y `POST /v1/observaciones` (no predecir sin registrar).

| Opción | Por qué se descartó / eligió |
|---|---|
| Responder la predicción sin registrarla y marcar `/health` degradado | Descartada. El producto exige un registro de cada solicitud: una predicción sin registro no se puede auditar ni usar luego como promoción del día. Además el predictor lee el histórico y las ventas recientes de app-db: sin ella no se puede predecir bien (con el modelo real del Paso 5, peor), y habría que inventar un modo degradado con datos viejos. |
| **503 con `{"detail": ...}`** | Elegida. Es honesto, el cliente puede reintentar, y nunca queda una respuesta sin registro. |

Detalles: los **rechazos 422 siguen siendo 422** aunque app-db esté caída (el rechazo se registra «a mejor esfuerzo»; no se necesita la base para saber que el dato es inválido). `/health` ya reporta la base (chequeo en segundo plano) y la API se recupera sola cuando vuelve (probado: `docker compose stop app-db` → 503; `start` → 200). Con la base caída el 503 tarda unos segundos (timeout de conexión de 3 s). El modelo en memoria del Paso 7 NO cambia esto: a MLflow nunca se le contacta dentro de una predicción, pero app-db sí es parte del camino.

## D7. Transacciones e idempotencia (Paso 4)

- **Una transacción por solicitud** (`engine.begin()`): solicitud + correcciones + predicciones (o + upsert de observaciones) se guardan juntas o no se guarda nada. Probado con una violación de FK a mitad: no queda solicitud huérfana.
- **Upsert hecho por la BD:** `INSERT ... ON CONFLICT (tienda, familia, fecha) DO UPDATE SET ventas = EXCLUDED.ventas ... WHERE observaciones.ventas IS DISTINCT FROM EXCLUDED.ventas RETURNING ...`. El `WHERE` descarta las filas idénticas y `RETURNING` solo devuelve las insertadas o modificadas, así que `registros_guardados` = filas realmente nuevas o cambiadas, sin leer antes ni comparar en Python, y es seguro ante envíos concurrentes. Reenviar lo idéntico devuelve 0.
- **Cada envío queda en `solicitudes`** aunque no cambie nada (auditoría); `observaciones.solicitud_id` apunta a la última que la creó o modificó.
- Los registros omitidos por la limpieza (E08) **no** se guardan en `observaciones`; queda constancia en `correcciones` y en el `cuerpo_crudo`.
- El cuerpo crudo se sanea antes de guardarlo (JSONB rechaza `NaN` y `\u0000`).
- `recibida_en` usa el reloj de la BD solo para auditoría; nunca para validar fechas.

## D8. Carga inicial de los CSV (Paso 4)

Contenedor `init` (misma imagen de la API, `python -m app.db.carga`, `./data:/data:ro`; los datos no van en la imagen y la API ya no monta `data/`).
- **Idempotente:** si `ventas_historicas` tiene filas no hace nada (y se asegura `carga_inicial = completada`). Tras `down` + `up` (sin `-v`) no se vuelve a cargar.
- **Atómica:** todo en una transacción; si un CSV falla no queda nada a medias ni la bandera.
- **Normalización de IDs:** `familia_id` y `tienda_id` pasan por las mismas funciones de la limpieza (`a_familia`, `a_tienda`, `normalizar_texto`), de modo que coinciden exactamente con lo que usa la API. Un ID que no se resuelve **aborta** la carga (no se descarta en silencio).
- `ON CONFLICT DO NOTHING` en las tablas de referencia; carga por lotes de 5000 filas.

## D9. Qué información existe al predecir y qué features usa el modelo (Paso 5)

**Regla de información.** Al predecir el día D la API solo dispone de:
1. las **ventas reales hasta D-1** (la observación de ayer llega antes que la solicitud de hoy);
2. la **`en_promocion` del día D**, que viene en la solicitud (las observaciones no traen promoción);
3. el **calendario de D** (día de la semana, feriados, fechas comerciales y de pago): `feriados.csv` cubre fechas futuras;
4. los **atributos fijos** de tienda y familia.

Nunca se usa nada de D ni posterior. Se garantiza por construcción: toda feature derivada de ventas pasa por `shift(≥1)` sobre un **calendario diario completo** (un día sin dato queda en NaN en vez de «correrse»), y la API lee de la vista solo `fecha < D` (`ml.datos.historia_reciente`). Hay pruebas que alteran las ventas de D y posteriores y comprueban que las features de D no cambian, y que las features que arma la API son idénticas a las del entrenamiento.

**Una sola función**, `ml.features.construir_features(historia, tiendas, feriados)`: recibe un DataFrame con la historia y devuelve las features de cada fila. El entrenamiento la aplica a toda la vista `ventas_para_entrenamiento` (cada fila es «el día D» de su propia fila); la API le pasa la historia de los últimos 60 días + una fila por familia pedida con `ventas = NaN` y la promoción de la solicitud. Mismo código, sin riesgo de *training/serving skew* por reimplementar.

| Grupo | Features (29) |
|---|---|
| Calendario | `dia_semana`, `dia_mes`, `mes`, `semana_anio`, `es_fin_de_semana`, `es_feriado_nacional`, `es_feriado_local` (ámbito = ciudad de la tienda), `es_medio_dia`, `es_comercial`, `es_pago`, `es_vispera_feriado`, `es_dia_post_feriado` |
| Promoción | `en_promocion` del día D |
| Historia de la serie (tienda × familia) | `lag_1`, `lag_7`, `lag_14`, `lag_21`, `lag_28`, `media_mismo_dia_4`, `media_7`, `media_28`, `std_28`, `ratio_7_28`, `n_obs_28` (cuántos días con dato en los últimos 28) |
| Atributos fijos | `tienda_id`, `familia_id`, `tipo` (formato), `ciudad`, `departamento` |

**Otras features consideradas** (ablación en D10): la **historia de promoción** (`promo_lag_1/7`, `promo_media_7`) y la **promoción total de la tienda ese día** no mejoraron nada (WAPE 18,1 % y 18,0 % frente a 18,0 %); además la primera es peligrosa: en días observados sin solicitud la promoción es un supuesto (D5), y la segunda depende de que la solicitud traiga todas las familias. `dias_a_fin_de_mes` tampoco aportó (el árbol ya ve `dia_mes`). Se **descartaron** las tres. Quitar el calendario sí empeora (WAPE 20,0 %).

**Limitaciones.** Hay solo 2 años de datos: no se usa «mismo día del año pasado». Si una fecha cae fuera de lo que cubre `feriados.csv`, el calendario queda sin feriados (silenciosamente). En este conjunto el nivel de cada serie es estable, así que quitar toda la historia de ventas casi no cambia el error (WAPE 18,1 %, sesgo −6,5 %); se conserva porque es lo que permite adaptarse a un cambio de nivel real y porque lo pide el enunciado.

## D10. Falta de historia: plan B (Paso 5)

Puede faltar historia por: una observación que no llegó, un día excluido por inválido (E08: registro omitido o `valido = false`; la vista no lo incluye) o una combinación tienda-familia sin datos.

| Situación | Qué pasa |
|---|---|
| Faltan algunos días | Ese lag es NaN. **LightGBM maneja los NaN de forma nativa** (el entrenamiento incluye filas con huecos: las primeras semanas de cada serie). Los promedios (`media_7`, `media_28`, `media_mismo_dia_4`) ignoran los días ausentes (mínimo 3 / 7 / 1 datos). |
| **Ningún** dato en los últimos 28 días (`n_obs_28 = 0`) | El árbol nunca vio ese caso: se usa el **plan B** en lugar de LightGBM. |
| **Baseline** sin sus 4 mismos días | Usa el plan B. |

**Plan B** (`ml.modelos.PlanB`): media de las ventas de los últimos 90 días de entrenamiento por **(familia, formato de tienda, día de la semana)**; si esa celda no existe, **(familia, formato)**; si no, **familia**; si ni la familia existe, 0. Viaja dentro del modelo. *Alternativa descartada:* el promedio global por familia (ignora que un express vende cinco veces menos que un hipermercado).

## D11. Validación temporal, baseline y modelo de ML (Paso 5)

**Validación: siempre temporal, nunca al azar** (con datos autocorrelacionados, mezclar días filtra el futuro y el error sale falsamente bajo). `ml.entrenar.entrenar(datos, fecha_corte)` usa tres bloques consecutivos:

```
|------------ entrenamiento ------------|-- calibración (56 d) --|----- prueba (56 d) -----|
                                  fecha_corte                                              fin
```
El modelo se ajusta con `fecha < fecha_corte`; la calibración sirve para los factores del newsvendor (D12); **todas las métricas se miden en la prueba**, con factores que no vieron esos días. Es predicción a 1 día con la historia real al día anterior, que es lo que hace la API. El modelo publicado se **reajusta con todos los datos** (con más historia reciente) y sus factores salen de los errores out-of-sample de calibración + prueba; por tanto las métricas describen al modelo del corte, no exactamente al reajustado (es el compromiso habitual).

**Baseline elegido: seasonal naive con media de los últimos 4 mismos días de la semana** (D-7, D-14, D-21, D-28), con plan B si faltan. Es fácil de explicar, sin entrenamiento y robusto al ruido de un solo día. Frente a las alternativas (tabla siguiente) es claramente mejor que D-7 solo (WAPE 22,1 % vs 27,4 %) y que la media por familia × formato × día (24,0 %).

**Modelo de ML: LightGBM global** (un solo modelo para las 120 series; la identidad de tienda y familia entra como categorías; objetivo Tweedie 1,2, 31 hojas, 600 rondas, lr 0,05; semilla fija). Un modelo global comparte la estructura (día de la semana, promoción, feriados) entre series y arranca bien con series cortas; 120 modelos locales tendrían 700 puntos cada uno. Alternativas probadas (en un protocolo **desplazado 112 días atrás** para no afinar sobre la prueba final; 6 720 filas de prueba; reproducible con `python scripts/explorar_modelos.py`):

| Modelo | MAE | RMSE | WAPE | Sesgo rel. | Costo total* |
|---|---|---|---|---|---|
| Baseline: media 4 mismos días + plan B | 45,5 | 83,9 | 22,1 % | −0,5 % | 214 226 |
| Baseline: solo D-7 + plan B | 56,4 | 105,1 | 27,4 % | −0,2 % | 269 138 |
| Solo plan B (familia × formato × día) | 49,4 | 94,6 | 24,0 % | +0,4 % | 224 885 |
| **LightGBM Tweedie 1,2 · 31 hojas · 600 rondas (elegido)** | **37,1** | 69,2 | **18,0 %** | −2,0 % | 176 080 |
| LightGBM L2 | 38,2 | 69,8 | 18,5 % | −0,9 % | 180 294 |
| LightGBM L2 sobre log1p(ventas) | 37,5 | 69,4 | 18,2 % | −4,2 % | 177 235 |
| LightGBM Poisson | 37,4 | 68,4 | 18,2 % | −1,2 % | 177 286 |
| LightGBM Tweedie, 15 hojas | 37,3 | 68,7 | 18,1 % | −1,2 % | 176 326 |
| LightGBM Tweedie, 63 hojas | 37,3 | 69,8 | 18,1 % | −2,5 % | 176 542 |
| LightGBM Tweedie, 300 rondas | 37,4 | 69,0 | 18,2 % | −1,4 % | 178 079 |
| LightGBM Tweedie, 1200 rondas, lr 0,025 | 37,1 | 68,5 | 18,0 % | −1,8 % | 176 109 |
| *Ablación:* sin calendario | 41,2 | 75,6 | 20,0 % | −0,2 % | 195 220 |
| *Ablación:* sin historia de ventas | 37,3 | 70,1 | 18,1 % | −6,5 % | 172 156 |
| *Ablación:* sin atributos fijos (formato/ciudad/depto.) | 37,5 | 69,4 | 18,2 % | −2,0 % | 177 640 |
| *Candidata:* + historia de promoción | 37,3 | 69,3 | 18,1 % | −1,7 % | 176 371 |
| *Candidata:* + promoción total de la tienda | 37,1 | 68,9 | 18,0 % | −1,9 % | 175 876 |
| *Candidata:* + días al fin de mes | 37,2 | 69,0 | 18,1 % | −1,9 % | 176 087 |

\* Costo con la regla de cuantiles de D12 (calibrada en la calibración de ese protocolo). Las diferencias entre variantes de LightGBM (objetivo, hojas, rondas) están dentro del ruido (≤ 1 % en WAPE): se dejó la configuración simple. LightGBM mejora al baseline ≈ 4 puntos de WAPE y ≈ 18 % de costo. Las features más importantes por ganancia son las medias recientes (`media_7`, `media_mismo_dia_4`, `lag_1`, `media_28`); el calendario aporta lo que no está en la historia (feriados, medio día, fines de semana).

## D12. Cantidad sugerida: newsvendor (Paso 5)

`venta_esperada` es el centro de la distribución; `cantidad_sugerida` es cuánto conviene tener. Con costo de faltante `cf` y de sobrante `cs`, la cantidad óptima es el **cuantil τ = cf / (cf + cs)** de la demanda: si faltar cuesta 2 y sobrar 0,1 (licores, τ = 0,95) conviene cubrir el 95 % de los días; si sobrar cuesta 2,5 y faltar 1 (panadería, τ = 0,29) conviene quedarse corto.

**Método del cuantil.** No se supone una distribución: se usan los **errores relativos out-of-sample** del modelo, `r = (real − esperada) / max(esperada, 1)` (la escala del error crece con el nivel de la serie; por eso es relativo), y por cada familia × formato de tienda `q = cuantil_τ(r)`. Entonces:

`cantidad = ceil( max(0, esperada + q · max(esperada, 1)) )`

- **Redondeo:** entero ≥ 0 como exige el contrato. Se usa `ceil` porque la demanda es entera: el cuantil discreto es el menor entero con probabilidad acumulada ≥ τ, es decir el techo del cuantil continuo (y es coherente con τ alto). Frente a `round` la diferencia fue < 0,1 %.
- **Familia × formato** solo si hay datos suficientes: una celda usa su propio `q` si `n · min(τ, 1−τ) ≥ 15` observaciones en la cola relevante; si no, hereda el de la familia. Con 112 días de calibración las 36 celdas (12 familias × 3 formatos) tuvieron factor propio; con 56 días licores (τ = 0,95) no alcanzaba. El formato aporta poco (−0,1 % de costo frente a solo familia) pero no cuesta nada y los factores de express difieren (p. ej. bebé: 0,55 en express vs 0,26 en hipermercado).
- Los **factores son parte del modelo**: se recalculan en cada entrenamiento y se guardan junto con él (`factores_newsvendor.json` en el run y dentro del pyfunc), porque al reentrenar cambian.
- **Aviso:** la ventana de calibración incluye diciembre (picos de aguinaldo y Navidad), que ensancha un poco los cuantiles altos.

**Comparación de costos** (LightGBM, protocolo de exploración, costo total en la prueba con `costos.csv`):

| Regla | Costo total | % días sin faltante |
|---|---|---|
| `cantidad_sugerida` = esperada redondeada | 230 692 | 52,5 % |
| Cuantil relativo solo por familia (ceil) | 176 214 | 71,0 % |
| **Cuantil relativo familia × formato (ceil) — elegida** | **176 080** | 70,4 % |
| Cuantil relativo familia × formato (round) | 176 156 | 69,8 % |
| Cuantil **aditivo** (real − esperada) por familia | 187 938 | 70,4 % |
| Un solo factor para todas las familias (τ medio) | 238 091 | 72,6 % |

La regla basada en costos **ahorra ≈ 24 %** frente a servir la esperada; lo que aporta es usar el τ de **cada familia** (un único factor no ayuda: 238 091 es peor que no usar ninguno), y la escala relativa gana al error aditivo (−6 %).

## D13. Empaquetado, registro en MLflow y conexión a la API (Paso 5)

- **Un solo objeto, `Pronosticador`** (`ml/modelos.py`): modelo (LightGBM o baseline) + plan B + factores newsvendor + catálogos de tiendas y feriados. Se empaqueta como **modelo `pyfunc`** (`ml/empaquetado.py`): quien lo carga solo llama a `predict(historia)` y recibe `venta_esperada` y `cantidad_sugerida`. Las dependencias van fijadas (`mlflow`, `pandas`, `lightgbm`).
- **Experimento `novamercado-pronostico`:** un run por modelo (`baseline_seasonal_naive`, `lightgbm_global`), con parámetros, métricas de prueba, factores, lista de features y el modelo. **Registro:** el de menor costo de prueba se registra como una versión de `novamercado-ventas` y recibe el alias **`champion`**. (El criterio para reemplazarlo al reentrenar es el Paso 6.)
- **Entrenar:** `python -m ml.entrenar [--fuente db|csv] [--fecha-corte AAAA-MM-DD] [--sin-mlflow]`. En Docker: `docker compose run --rm init python -m ml.entrenar` (lee la vista de app-db y habla con MLflow). La función reutilizable es `ml.entrenar.entrenar(datos, fecha_corte) -> Resultado`; el DAG del Paso 6 y el `init` del Paso 8 la llamarán.
- **API:** al arrancar carga `models:/novamercado-ventas/<versión del @champion>` (por número de versión, para que alias y modelo no se desincronicen) y la guarda en memoria (después la mantiene al día el sondeo de D15). **Nunca contacta a MLflow dentro de una predicción**: probado con MLflow detenido. Si MLflow está caído o aún no hay `champion` al arrancar, la API arranca sin modelo (`/health` 503, predicciones 503) y la carga queda para el sondeo cada 30 s (Paso 7, D15). `GET /modelo` devuelve `{nombre, alias, version}`.
- La imagen de la API usa `mlflow-skinny==3.18.0` (cliente + pyfunc, sin servidor; misma versión que `mlflow` y que la imagen de Airflow), `pandas`, `numpy` y `lightgbm` fijados, y copia `ml/`.
- **Operación:** el servidor MLflow se quedaba sin memoria (OOM, `-9`) con 768 MB al registrar el modelo; se subió a 1,5 GB. El equipo tiene ≈ 3,8 GB: con Airflow también levantado conviene vigilar la memoria.
- **Consistencia de `familia_id`:** en la BD, la vista, los factores (`tau`, `q_familia`, `q_celda`), el plan B, las categorías de LightGBM y las métricas por familia se usa exactamente el id canónico de `familias.csv` (`panaderia`, `frutas_verduras`...); «Panadería» o «Frutas y Verduras» solo existen como texto de entrada y la limpieza (D1) los convierte antes de llegar al modelo. Se verificó contra la BD y el modelo registrado en MLflow. Para que un id distinto no pase en silencio: `entrenar` exige que los ids de ventas coincidan con los de `costos`/`tiendas` (`validar_ids`) y `Pronosticador.predecir` falla con error explícito si recibe una familia sin factor (si no, LightGBM la trataría como categoría desconocida y el plan B daría 0). Hay pruebas en `tests/test_ml_ids.py`.

## D14. Reentrenamiento y criterio de promoción (Paso 6)

**Flujo.** `POST /admin/reentrenar` dispara el DAG `reentrenar_modelo` en Airflow; el DAG solo orquesta y la lógica vive en `ml/reentrenamiento.py` (probada sin Airflow) y `ml/promocion.py`:

`preparar_datos → entrenar_candidato → evaluar_y_decidir → promover (solo si gana) → registrar_resultado`

- **`preparar_datos`** fija una «foto» de los datos (`fin` = última fecha de la vista `ventas_para_entrenamiento`) y las ventanas temporales (entrenamiento → calibración 56 d → prueba 56 d). Así todas las tareas ven el mismo conjunto aunque lleguen observaciones nuevas durante la ejecución. Falla si hay menos de 60 días de entrenamiento o si los ids son inconsistentes (`validar_ids`).
- **`entrenar_candidato`** entrena LightGBM con fecha < corte y lo **registra siempre** como versión nueva de `novamercado-ventas`, **sin alias**, con tags (`estado`, `dag_run_id`, `ultimo_dia`, `fecha_corte`). Gane o pierda, queda el linaje en MLflow.
- **`evaluar_y_decidir`** compara contra el vigente y deja la decisión, el motivo y las métricas como tags de la versión candidata.
- **`promover`** es **el único paso que mueve el alias `champion`**, con una sola operación del Model Registry (no hay estado «a medias»). Si falla algo antes (datos, entrenamiento, evaluación, MLflow caído), el alias no se toca. Si el candidato no gana, la tarea queda *skipped*.
- **`registrar_resultado`** corre siempre (`trigger_rule=all_done`), hace upsert en `reentrenamientos` y, si el reentrenamiento falló, termina con error para que la ejecución quede `failed`.

**Criterio de promoción.** `promover ⇔ costo_candidato < costo_vigente × (1 − 1,5 %)`, con el **costo de negocio total** (`cantidad_sugerida` contra la venta real, con `costos.csv`) en la misma ventana de prueba.
- *Por qué el costo y no MAE/WAPE:* es lo que le importa a NovaMercado y lo que optimiza el newsvendor (D12). Las métricas técnicas se registran, pero no deciden.
- *Por qué un margen de 1,5 %:* cada promoción tiene riesgo operativo; una diferencia menor es ruido de la ventana de prueba. Es una constante (`MARGEN_PROMOCION`) y se puede pasar como argumento. El 1,5 % es una elección justificada, no calibrada con datos: con más ejecuciones se podría estimar la variabilidad real.
- *Casos límite (todos documentados en pruebas):* sin vigente → el candidato se promueve; costo del candidato inválido (NaN, infinito, negativo) o del vigente inválido (≤ 0) → **no** se promueve; empate o borde exacto → no se promueve.
- *Alternativas descartadas:* promover siempre el más reciente (un modelo peor podría reemplazar al bueno); promover si mejora estrictamente (cambia de modelo por ruido); exigir mejora en todas las métricas (demasiado conservador y las métricas no son comparables entre sí).

**Comparación justa.** Candidato y vigente se evalúan en la **misma ventana** y ninguno entrenó con ella:
- si el vigente terminó de entrenar *antes* de la ventana (`ultimo_dia` < inicio de la prueba) se evalúa tal cual está desplegado (`vigente_real`);
- si ya vio parte de la ventana (p. ej. no hubo datos nuevos desde que se entrenó), compararlo sería injusto a su favor: se **reajusta su misma receta** (tipo y parámetros) con fecha < corte y se evalúa ese (`vigente_reajustado`);
- si el número de filas de prueba difiere entre ambos, el DAG falla (`ventanas distintas`) en lugar de comparar cosas distintas.

**Operación.**
- **Sin reintentos** (`retries=0`) y con timeouts por tarea y `dagrun_timeout` de 9 min: un reintento dentro de una ejecución lenta rompería el límite de 10 min. Un fallo es definitivo y se vuelve a disparar a mano.
- **`max_active_runs=1`:** un segundo `POST /admin/reentrenar` **no se rechaza**: ambos responden 202 y Airflow deja el segundo en cola (`en_curso`) hasta que termine el primero. Así no se entrenan dos modelos a la vez ni se pierde una solicitud.
- **Las tareas leen de `app-db` con su propia conexión** (`APP_DB_URL`), nunca de la base de metadatos de Airflow (prohibido en Airflow 3). La imagen de Airflow incluye `ml/`, así que DAG, API e `init` usan el mismo código y las mismas versiones.
- **La API elige el `dag_run_id`** (`reentrenar_<uuid>`) y guarda la fila `en_curso` en `reentrenamientos` **antes** de disparar; si Airflow no responde, borra la fila y responde 503 (no queda un `en_curso` eterno). Si `app-db` está caída, 503 y no se dispara nada.
- **`GET /admin/reentrenar/{id}`:** 404 para ids que la API no creó (no se pregunta a Airflow). El estado sale de Airflow (`queued`/`running` → `en_curso`, `success` → `exitoso`, `failed` → `fallido`); si Airflow no responde o no conoce la ejecución, se usa lo guardado por el DAG. `version` es la **vigente al terminar**: la nueva si se promovió, la anterior si no. Si el DAG murió antes de escribir (p. ej. por timeout), el motivo por omisión lo dice y aclara que el vigente no se tocó.
- **Adopción por la API:** el alias se mueve en MLflow y la API lo adopta sola con el sondeo del Paso 7 (D15).

**Pendiente de verificar con el sistema levantado:** ejecución real del DAG (con promoción y sin ella), el límite de 10 min y que un fallo deje el alias intacto en MLflow real. Las pruebas unitarias cubren la lógica con fakes.

## D15. Recarga del modelo sin reinicio, resiliencia y `/health` (Paso 7)

**Sondeo del alias (`api/app/recarga.py`).** Una tarea en segundo plano, cada `MODELO_SONDEO_INTERVALO` = 30 s, pregunta a MLflow a qué versión apunta `@champion` (`version_vigente`: una consulta liviana al registro). Si difiere de la versión en memoria, la carga **por número de versión** en un hilo aparte y, solo si terminó bien, reemplaza `app.state.model` con **una única asignación**:
- una predicción en curso termina con el objeto que ya tenía; la siguiente usa el nuevo; nunca hay un modelo «a medias» y no hace falta un candado;
- el peor caso para adoptar un modelo promovido es ≈ 30 s de sondeo + lo que tarde la carga, muy por debajo del límite de 2 min; la API no se reinicia;
- **las predicciones nunca contactan a MLflow**: solo esta tarea lo hace, y lo hace fuera del bucle de eventos (`asyncio.to_thread`).

**Resiliencia.**
- Si MLflow está caído, el alias no existe, el artefacto no se puede leer o la consulta/carga excede su tiempo (`MODELO_CONSULTA_TIMEOUT` 15 s, `MODELO_CARGA_TIMEOUT` 120 s), **se conserva el modelo en memoria**, se registra un aviso y se reintenta en el siguiente ciclo. Las predicciones siguen respondiendo.
- Si la API arrancó **sin modelo** (MLflow caído o sin `champion` todavía), el mismo sondeo lo carga en cuanto sea posible: ya no hace falta reiniciarla (`/health` pasa de 503 a ok/degradado).
- Si el alias se mueve a una versión *anterior* (rollback manual), también se adopta: el alias es la única fuente de verdad.
- Una versión que falla al cargar se reintenta cada ciclo; es barato (falla pronto) y se recupera sola si el problema era transitorio. *Alternativa descartada:* lista negra de versiones fallidas (más estado y se perdería la recuperación automática).
- Costo conocido: cada carga descarga los artefactos a un directorio temporal del contenedor; solo ocurre al cambiar de versión (poco frecuente), así que no se limpia.
- *Alternativas descartadas:* recargar al terminar el DAG con un webhook (acopla Airflow a la API y falla si la API estaba caída); sondear dentro de la predicción (viola «nunca contactar a MLflow en una predicción»); reiniciar el contenedor (pierde peticiones y no es lo que pide el enunciado).

**`/health` alineado con el contrato.** Cambia a `{estado, motivo?, checks}`:
- **503** `{estado: "no_disponible", motivo}` solo mientras no haya modelo cargado;
- **200** `{estado: "degradado", motivo}` si falla cualquier dependencia (BD, MLflow, Airflow); el `motivo` lista cuáles. Con la BD caída ya no es 503: las rutas que persisten responden 503 por su cuenta (D6) y `/health` informa el estado;
- **200** `{estado: "ok"}` si todo está bien. `checks` se conserva como campo extra (el contrato permite agregar).
- Se agregó el chequeo de **Airflow** (`/api/v2/monitor/health`, sin autenticación): debe responder y reportar `metadatabase`, `scheduler` y `dag_processor` como `healthy`; sin scheduler o dag-processor el reentrenamiento no correría.
- Los chequeos siguen en segundo plano cada 15 s (timeout 3 s): `/health` solo lee el último resultado y se entera de la caída de MLflow en < 20 s, y de su regreso en la misma medida (límites: < 1 min y < 2 min). Un resultado más viejo de 45 s cuenta como caído.

**Pendiente de verificar con el sistema levantado:** promover un modelo con el DAG y comprobar que `/modelo` cambia solo en ≤ 2 min; `docker compose stop mlflow` y comprobar `degradado` en < 1 min, predicciones vigentes y regreso a `ok` en < 2 min. Las pruebas unitarias (`tests/test_recarga.py`, `tests/test_health.py`) cubren la lógica con fakes.
