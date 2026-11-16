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

## D4. Predictor falso del Paso 3

Hasta el Paso 5, `POST /v1/prediccion` usa `PredictorPromedio`: promedio histórico de (tienda, familia) en fechas **anteriores** a la pedida, `version_modelo = "0"` y `cantidad_sugerida = round(venta_esperada)` (provisional; el Paso 5 lo reemplaza por el cuantil *newsvendor*). Sin historia previa devuelve `0.0`. Se carga desde `historico_ventas.csv`, montado en `/data` (solo lectura) y no incluido en la imagen. `/health` sigue en 503 porque el predictor falso no cuenta como modelo.
