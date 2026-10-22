# Certificados de cliente – Reajustes por polinómica y diferencias retroactivas

Especificación para revisión (complementa la especificación aprobada del módulo de certificados).
Estado: **maqueta aprobada (17/10/2026); implementado en una copia de trabajo y probado sobre copias de la base**. Todavía no se aplicó a la aplicación real, a `dashboard.db` ni a Render: requiere autorización expresa (sección 10).
Tema visual definido (17/10/2026): Seguimiento de Cliente y Certificados en diseño claro (maqueta aprobada), con barra lateral azul marino; el PDF conserva su diseño propio. Alcance exacto de la implementación en la sección 9.

Referencia analizada: `Cert. N° 22 Septiembre -26 - AM 470-069 - SMG - copia.xlsx` (no se modificó).

## 1. Decisiones aprobadas (preliminares)

| # | Tema | Decisión |
|---|------|----------|
| 1 | Ponderaciones y bases | Todo configurable por SEG. Ningún valor por defecto: la fórmula queda marcada **"pendiente de validación contractual"** hasta que alguien con permiso la valide. |
| 2 | Reajustes negativos | Se permiten cuando la fórmula los produce. La opción "permite negativos" es parte de cada versión de la fórmula (activada por defecto). |
| 3 | Descuentos | La base de cálculo se configura por contrato y por concepto. |
| 4 | Saldos | El saldo de ejecución y los reajustes van por separado. Los reajustes no consumen el saldo base. Hay una previsión de reajustes configurable. |
| 5 | Rebase | Factor configurable por versión de fórmula. Los precios de OC/HES no se tocan. |
| 6 | Histórico | Acumulado inicial justificado más el próximo número de certificado. También se pueden incorporar certificados anteriores de a uno. |
| 7 | HES | La exportación queda para otra etapa. Desde ya el ajuste es una línea calculada, nunca un "redondeo" manual. |

## 2. Modelo de datos (tablas nuevas; todas de solo inserción salvo lo indicado)

### `reajuste_formulas` — versiones de la fórmula por SEG
`id, seguimiento_id, version, vigente_desde (AAAA-MM), mes_base (AAAA-MM), factor_rebase (TEXT decimal, >0),
permite_negativo (0/1), decimales_coeficiente (NULL = sin redondeo), estado (BORRADOR | VIGENTE),
validacion_contractual (PENDIENTE | VALIDADA), validada_por, validada_en, referencia_contractual, motivo, creado_por, creado_en`

- Una versión **VIGENTE** no se modifica nunca. Cambiar cualquier dato crea una versión nueva con un motivo obligatorio.
- **Validación contractual:** la única actualización permitida por trigger es pasar, una sola vez, de PENDIENTE a VALIDADA completando `validada_por`, `validada_en` y `referencia_contractual` (obligatoria: contrato, adenda o cláusula). La acción exige `configurar_reajuste` con usuario nominal y queda registrada en `reajuste_auditoria`. No se puede "desvalidar": si la versión estaba mal, se crea una nueva.
- Para un período *p* se aplica la versión VIGENTE de **número más alto** con `vigente_desde ≤ p`. Una versión con `vigente_desde` anterior a la fecha de carga es una modificación retroactiva de la polinómica, como la nota (\*) del Excel: genera diferencias que se reconocen en el próximo certificado.
- Al guardar se valida que las ponderaciones sumen exactamente 1, que las bases sean mayores que 0, que el factor sea mayor que 0 y que no haya códigos repetidos.

### `reajuste_componentes`
`id, formula_id, codigo (MO, USD, GASOIL…), nombre, ponderacion, valor_base, fuente (texto contractual)`

### `reajuste_indices` — registro mensual con historial
`id, seguimiento_id, codigo, periodo (AAAA-MM), version, valor, estado (PROVISORIO | DEFINITIVO), fuente_ref, motivo, cargado_por, cargado_en`

- Clave única: `(seguimiento_id, codigo, periodo, version)`. El valor **vigente** es la versión más alta.
- Corregir un índice significa insertar una versión nueva con motivo obligatorio. UPDATE y DELETE están prohibidos por trigger.
- Un índice PROVISORIO permite emitir, con aviso. Cuando se carga el DEFINITIVO, la diferencia aparece como retroactivo pendiente.

### `certificados_cliente_reajustes` — líneas de reajuste de cada certificado
`id, certificado_id, tipo (PERIODO | RETROACTIVO), componente (REBASE | POLINOMICA), orden_compra_cliente_id,
origen_certificado_id, periodo_origen, base, coeficiente, esperado, reconocido_previo, importe, detalle_json`

- `detalle_json` registra la fórmula (id y versión), el factor, cada componente con su ponderación, valor base, **id y versión del índice**, valor y estado (provisorio o definitivo), y el coeficiente resultante.
- En las líneas PERIODO, `origen_certificado_id` es el propio certificado.
- Al emitir, las líneas quedan inmutables, con los mismos triggers que hoy protegen a los emitidos.

### `reajuste_auditoria` — registro de auditoría (solo inserción)
`id, seguimiento_id, accion, entidad, entidad_id, detalle_json, usuario, fecha`

Registra la creación y validación de versiones de fórmula, la carga y corrección de índices, los cambios de previsión, de bases de descuento, de numeración y de acumulado inicial, y la incorporación de certificados históricos. UPDATE y DELETE prohibidos por trigger.

### Configuración del contrato
- **`reajuste_previsiones`:** `seguimiento_id, monto, moneda, motivo, creado_por, creado_en`. Es un historial; la previsión vigente es la última.
- **`certificados_reglas_descuento`:** `seguimiento_id, concepto, base (EJECUCION | EJECUCION_REAJUSTADA | TOTAL_CON_RETROACTIVOS | MANUAL), porcentaje, vigente_desde, creado_por, creado_en`. Es un historial por concepto.
- **`certificados_saldo_inicial`:** `seguimiento_id, orden_compra_cliente_id, hasta_numero, base_acumulada, reajustes_acumulados, justificacion, creado_por, creado_en`. Se carga una sola vez por OC; corregirlo requiere una fila nueva con motivo.

### Cambios a tablas existentes
- **`seguimiento_cc.numero_proximo_certificado`** (INTEGER, NULL = 1). El número se asigna como `max(numero_proximo, MAX(numero)+1)`, dentro del mismo `BEGIN IMMEDIATE`.
- **`certificados_cliente`:**
  - `origen` (SISTEMA | HISTORICO) y `periodo_ejecucion` (AAAA-MM).
  - Totales: `base_ejecucion`, `reajuste_periodo`, `reajuste_retroactivo`, `total_a_facturar`.

  Los certificados HISTORICO se registran directamente como EMITIDO, con su número, sus líneas a precio de OC y los reajustes **efectivamente pagados**. Cuentan en saldos y admiten retroactivos.

## 3. Cálculos

Todos se hacen con Decimal en Python y redondeo al centavo con ROUND_HALF_UP, aplicado solo a los importes. El coeficiente no se redondea salvo que la versión lo indique.

```
C(p)        = Σ wᵢ · Iᵢ(p) / Iᵢ(base)                       (versión e índices vigentes para p)
B           = Σ cantidad × precio de OC   (por OC)           → consume saldo de ejecución
REBASE      = cent( B × (f − 1) )                            → reajuste
POLINOMICA  = cent( B × f × (C(p) − 1) )                     → reajuste (0 si la versión no permite negativos)
Total a facturar = B + reajuste del período + retroactivos − descuentos     (sin IVA)
```

**Bases de descuento por concepto:**
- EJECUCION = B.
- EJECUCION_REAJUSTADA = B más el reajuste del período.
- TOTAL_CON_RETROACTIVOS = B más el reajuste del período y los retroactivos.
- MANUAL = importe cargado a mano.

### Diferencias retroactivas (sin duplicar)
Para cada certificado **EMITIDO** *k*, por OC y por componente:

```
Esperado(k)   = cálculo de arriba con la fórmula e índices vigentes HOY para el período de k
Reconocido(k) = Σ importes de líneas (PERIODO + RETROACTIVO) con origen k en certificados EMITIDOS
Pendiente(k)  = Esperado(k) − Reconocido(k)      → si ≠ 0, línea RETROACTIVO en el próximo certificado
```

**Garantías:**
- **No hay duplicación.** Una diferencia ya reconocida pasa a formar parte de *Reconocido* y deja de estar pendiente. Si el índice se corrige otra vez, solo se cobra el incremento.
- **No se modifican emitidos.** El ajuste siempre se hace en un certificado nuevo, que guarda el `reconocido_previo` y el `esperado`, de modo que cada línea se puede verificar.
- **Concurrencia.** Los borradores no reservan diferencias. Al emitir, el servidor recalcula todo dentro de `BEGIN IMMEDIATE`. Si las líneas cambiaron desde el último cálculo (índice corregido u otro certificado emitido), la emisión se rechaza con "Recalcule".
- **Anulaciones.**
  - Las líneas de un certificado anulado dejan de contar, así que su retroactivo vuelve a quedar pendiente y lo toma el reemplazo.
  - No se puede anular un certificado cuyo reajuste ya fue reconocido como retroactivo en otro certificado emitido: primero hay que anular ese otro. Esta regla se suma a "solo el último emitido de cada OC".
- **Acumulado inicial sin detalle.** Los certificados cubiertos por el acumulado inicial agregado no generan retroactivos porque no tienen el detalle por período. Si se necesitan retroactivos sobre esos períodos, hay que incorporarlos de a uno.

## 4. Saldos

| Concepto | Cálculo |
|---|---|
| Saldo de ejecución por OC | Monto de OC − (base del acumulado inicial + Σ B de los emitidos) |
| Reajustes acumulados | Reajustes del acumulado inicial + Σ líneas de reajuste de los emitidos (incluye negativos y retroactivos) |
| Saldo de previsión | Previsión vigente − reajustes acumulados. Si el certificado la supera, requiere `autorizar_exceso` y una justificación, como el exceso de saldo. |
| Retroactivo pendiente | Σ Pendiente(k), visible en el listado y en el editor |

## 5. Interfaz (ver maqueta)

- **Navegación:** se conservan la barra lateral y la navegación actuales de `operaciones.html` (Dashboard, Carga de Partes Diarios, Centro de Costos, Seguimiento de Cliente, Menú Principal), sin entradas nuevas. Todo lo de certificados va en una **pantalla aparte**, "Certificados y reajustes", que se abre con un botón **Certificados** en el encabezado de Seguimiento de Cliente, junto a los botones actuales. La pantalla muestra el SEG seleccionado y tiene "← Volver al seguimiento"; la barra lateral mantiene activo Seguimiento de Cliente. Contiene: resumen de certificación, reajustes por polinómica, saldos por OC y la lista de certificados. Seguimiento de Cliente no cambia: sus secciones, el Resumen económico y el aviso de "Anexos y Certificados" quedan como hoy (los anexos siguen fuera de alcance).
- **Aspecto visual:** diseño claro de la maqueta para Seguimiento de Cliente y la pantalla de Certificados: fondo gris claro, tarjetas blancas, encabezados de tabla azul claro y botones azules; barra lateral azul marino con sus mismas cinco entradas. Se conserva la estructura y los nombres de clase de `operaciones.html` (`.seguimiento-panel`, `.panel-section`, `.seg-btn`, `.orders-table`, `.field`, modales); el tema claro se aplica con una clase de alcance (`tema-claro`) sobre esos paneles, sin redefinir los tokens globales. El PDF conserva su diseño propio, independiente del tema de la aplicación.
- **Permisos en pantalla:** sin el panel "Seguimiento de Cliente – Certificados", el botón Certificados no se muestra (y si se entra igual, la pantalla muestra un aviso); el seguimiento sigue igual.
- **Listado:** una tarjeta "Reajustes" con la fórmula vigente y su estado contractual, el coeficiente del último mes, los reajustes acumulados, la previsión con su saldo y un aviso de retroactivo pendiente. Tiene los botones *Fórmula polinómica*, *Índices mensuales* y *Configuración del contrato*.
- **Editor:** una sección "Reajustes del certificado", con la línea del período y las retroactivas, el coeficiente y la trazabilidad de los índices. Los descuentos indican su base y porcentaje. Los totales separan ejecución, reajuste del período, retroactivos y descuentos.
- **Emisión:** muestra los reajustes, avisa si hay índices provisorios y pide `autorizar_exceso` si se supera la previsión.
- **Validación contractual:** modal con la fórmula a validar, confirmación explícita y referencia contractual obligatoria. Muestra quién validó y cuándo; el registro de auditoría se consulta en la fórmula y en la configuración del contrato.
- **PDF:**
  - El bloque final suma "Reajuste · coef." y "Diferencias retroactivas" solo cuando el certificado tiene reajuste; sin reajuste, el PDF no lo menciona.
  - Se agrega un cuadro "Detalle de reajustes" con tipo, certificado de origen, OC, período, componente, base, coeficiente aplicado antes y vigente, reconocido antes, corresponde e importe, y los índices usados con su versión y motivo. El cuadro no se parte entre páginas.
  - Los descuentos por porcentaje informan base e importe de la base (ej. "5,00 % s/ ejecución + reajuste $ 58.767,50"). Si el certificado no tiene reajuste, la base se informa como "ejecución".
  - El recuadro de observaciones crece con el texto (exceso autorizado, motivo de anulación, observaciones largas): nunca desborda.
  - La copia de un certificado ANULADO incluye fecha, usuario y motivo de la anulación.
  - El snapshot guardado al emitir suma `reajuste` (fórmula, líneas e índices) y, en los descuentos, `base`, `porcentaje` y `base_importe`; la anulación se toma del historial.

## 5 bis. Moneda

- **Qué se guarda:** el código ISO de 3 letras de la OC (ARS, USD, EUR), igual que hoy en OC, ítems y certificados. Un certificado no mezcla monedas, y la previsión, los reajustes y los descuentos van en la moneda del certificado.
- **Qué se muestra:** el símbolo, tanto en el PDF como en pantalla, con el mapa `ARS → $`, `USD → US$`, `EUR → €`. Una moneda que no esté en el mapa se muestra con su código.
- **Leyenda del PDF:** dinámica según la moneda de la OC: "Importes en pesos argentinos ($)", "dólares estadounidenses (US$)" o "euros (€)". Se repite en la franja de cada página siguiente.
- **Formato de importes:** `$ 1.234,56`; negativos `-$ 1.234,56` (el signo delante del símbolo). Símbolo y número van unidos por un espacio que no se corta, así nunca quedan en renglones distintos. Las celdas numéricas de las tablas llevan solo el número; el símbolo figura en el encabezado de la columna o en la leyenda.
- **Sin conversiones:** todos los importes de un certificado (ejecución, reajustes, retroactivos, descuentos, excesos y totales) están en la moneda de su OC; no hay tipo de cambio ni mezcla de monedas.
- **Mensajes del servidor:** los textos de validación (exceso, saldo no ejecutado y similares) usarán el mismo símbolo.

## 6. Permisos (Administración → operaciones / seguimiento_cliente)

Se suman tres acciones nuevas, todas de usuario nominal (la cuenta de contingencia no puede ejecutarlas):

- **`configurar_reajuste`:** versiones de la fórmula, validación contractual, previsión y bases de descuento.
- **`cargar_indices`:** carga y corrección de índices, con motivo.
- **`incorporar_historico`:** acumulado inicial, certificados anteriores y próximo número.

## 7. Validación del cálculo (prototipo fuera de la aplicación)

El motor de referencia (`reajuste_motor.py`) tiene 19 escenarios de prueba (`test_reajuste_motor.py`) y todos pasan.

**Reproducción del Excel:**
- Los reajustes de los certificados 20, 21 y 22 dan −916.448,83, 17.598.186,35 y 25.674.028,85. El acumulado da 42.355.766,37 y el total a facturar del 22 da 441.498.906,11, igual que el Excel.
- Con el rebase de 1,30589…, la base a precio OC de 318.422.086,43 da 415.824.877,26.

**Escenarios de retroactivo cubiertos:**
- Corrección de índice.
- Segunda corrección: solo se cobra el incremento.
- Corrección con el mismo valor: no genera línea.
- Reversión: genera retroactivo negativo.
- Dos borradores simultáneos: no duplican.
- Corrección entre borrador y emisión: obliga a recalcular.
- Anulación del certificado que lleva el retroactivo: el reemplazo lo toma una sola vez.
- Bloqueo de anulación de un certificado ya reconocido por otro.
- Índice provisorio seguido del definitivo.
- Nueva versión retroactiva de la fórmula.
- Fórmula sin negativos.

**Otros escenarios:** bases de descuento, saldos separados con previsión, histórico con numeración desde 22 y validaciones.

**Invariante:** en 200 secuencias aleatorias de correcciones, emisiones y anulaciones, al cerrar se cumple siempre que Reconocido = Esperado y que no queda nada pendiente.

**Control de las pruebas:** se alteró el motor a propósito de tres maneras (no descontar lo reconocido, omitir el recálculo al emitir, contar anulados) y las pruebas detectaron las tres.

### Pruebas del PDF (prototipo, fuera de la aplicación)

`test_pdf_smg.py` extrae el texto de cada PDF generado y verifica 92 combinaciones: moneda (ARS, USD, EUR) × con y sin reajuste × con y sin descuentos × 5 y 30 ítems × emitido, borrador y anulado, más globales con varias OC, reajustes negativos en varias páginas, excesos con observaciones largas y los límites de página donde el cierre pasa entero a la hoja siguiente (41 casos de 1 página y 51 de 2). Comprueba:
- leyenda de moneda en la primera página y en la franja de las siguientes;
- solo el símbolo de la moneda del certificado, sin códigos ISO ni otros símbolos, y signo delante del símbolo;
- totales impresos = ejecución + reajuste + retroactivo − descuentos, y cada importe, descuento y base presente con su símbolo;
- numeración "Página X de Y", pie y encabezado de tabla en cada página; cada ítem una sola vez; cierre completo solo en la última página; detalle de reajustes sin partir;
- estado: borrador sin código de verificación, anulado con motivo, emitido sin marca.

Se alteró el prototipo a propósito de cinco maneras (símbolo fijo, franja sin moneda, alto fijo de observaciones, base de descuento con reajuste inexistente, signo después del símbolo) y las pruebas detectaron las cinco.

## 8. Problemas del Excel que el modelo evita

- **"Ajuste abonado" calculado con fórmula:** con eso el retroactivo siempre daba 0. En el modelo, lo abonado queda congelado en las líneas emitidas.
- **Constantes escritas a mano** (el acumulado anterior y las cantidades del tipo "=230+230"): se reemplazan por el acumulado inicial justificado y los certificados históricos.
- **Leyendas que no coinciden con las celdas** (ponderaciones y bases): el texto contractual (`fuente`) y los valores se cargan juntos en cada versión, y la versión requiere validación contractual.
- **"Redondeo ajuste inflación" manual en la HES:** el ajuste se obtiene de las líneas de reajuste calculadas.
- **Referencias rotas** (21 `#REF!` y 36 nombres definidos rotos): no se trasladan.
- **Excesos de cantidad ocultos por `IFERROR`:** siguen los controles de exceso del módulo.

## 9. Alcance exacto de la implementación (Etapa 3)

Implementado en una copia de trabajo del código. Nada se copió a la carpeta real: `uvicorn --reload` reiniciaría la aplicación y migraría `dashboard.db` en el acto.

| Archivo | Cambios | No cambia |
|---|---|---|
| `main.py` – migración | `migrar_reajustes` (llamada en el arranque después de `migrar_certificados_cliente`): 9 tablas nuevas, 9 columnas nuevas y 23 triggers `trg_rj_*` de inmutabilidad, historial de solo inserción, coherencia de OC y moneda, y bloqueo de anulación con retroactivos reconocidos. Solo agrega estructuras; es idempotente. | Datos existentes, las 64 tablas previas y los 19 triggers `trg_cert_*`. |
| `main.py` – cálculo y emisión | Motor de reajuste dentro de `_cert_calcular`. Total = ejecución + reajuste + retroactivos − descuentos. Descuentos con base y porcentaje. Numeración `max(próximo configurado, último + 1)`. La emisión exige índices completos y la huella del último cálculo ("Recalcule"), y trata el exceso de previsión como el exceso de saldo. | Reglas de saldo, modalidad, anulación y exceso ya aprobadas. Sin fórmula, el certificado se calcula igual que antes. |
| `main.py` – permisos | `CERT_ACCIONES_NOMINALES` suma `configurar_reajuste`, `cargar_indices` e `incorporar_historico`. | Las acciones existentes y el control de contingencia. |
| `main.py` – servicios nuevos | `GET /reajustes` (estado completo). `POST /reajustes/formulas`, `/formulas/{id}/validar`, `/indices`, `/previsiones`, `/reglas-descuento`, `/numeracion` y `/saldo-inicial`, y `POST /certificados/historico`. Todos auditados en `reajuste_auditoria`. | Servicios existentes de certificados, SEG, OC e ítems. `_seg_centro_costo_info` solo suma el campo `provincia` (lectura). |
| `main.py` – PDF y snapshot | Snapshot versión 2 con el bloque `reajuste` (fórmula, líneas, índices con versión y motivo) y la base de cada descuento. `_cert_pdf_bytes` usa el módulo nuevo. La copia sellada agrega fecha, usuario y motivo de la anulación. | Los PDF ya emitidos (se entregan tal cual, con control de integridad). Los snapshots versión 1 siguen generando su copia sellada. |
| `certificado_pdf.py` (nuevo) | Diseño aprobado del PDF, portado del prototipo sin cambios de formato. Agrega el escape de textos ingresados por usuarios ("&", "<"), las fechas DD/MM/AAAA, las observaciones de registro histórico y de exceso de previsión, y la marca "REGISTRO HISTÓRICO". | — |
| `operaciones.html` | Botón **Certificados** en el encabezado del Seguimiento (oculto sin el panel; deshabilitado si el SEG no está guardado). Panel `panel-certificados-cliente` con 10 ventanas. CSS bajo `body.tema-claro` (Seguimiento y Certificados) y clases `cert-*`. `activarPanel` suma el panel nuevo, activa el tema claro y expone `window.activarPanelOperaciones`. | Las entradas de la barra lateral; Dashboard, Carga de Partes Diarios y Centro de Costos siguen con el tema actual; el contenido de Seguimiento de Cliente. |
| `administracion.html` | Panel `seguimiento_cliente` ("Seguimiento de Cliente – Certificados") y sus nueve acciones en `ACCIONES_POR_MODULO_PANEL.operaciones`. | Los demás módulos, paneles y acciones. |
| `tests/` | `test_reajustes.py` (20 pruebas, base copiada) y `e2e_certificados.py` (navegador real con Playwright y Edge). | `test_certificados.py` y las demás pruebas, sin cambios. |

### Precisiones surgidas al implementar
- **Fórmula:** toda versión guardada rige desde su `vigente_desde`; no hay estado borrador. La validación contractual es una marca aparte. Como en la maqueta, se puede emitir con la fórmula pendiente de validación: hay un aviso y el PDF lo indica en el título del detalle de reajustes.
- **Índices faltantes:** si la fórmula aplica al período y falta algún índice, el borrador se guarda con un aviso, pero no se puede emitir.
- **Acumulado inicial:**
  - En modalidad **por ítems** se carga la cantidad certificada de cada ítem; la base se calcula al precio de la OC. En modalidad **global** se carga la base por OC.
  - Los reajustes acumulados se cargan por OC.
  - El registro va en `certificados_saldo_inicial` (cabecera) y `certificados_saldo_inicial_lineas`. Una corrección es un registro nuevo, que pasa a ser el vigente.
- **Certificado anterior (histórico):**
  - Su número debe ser mayor que el que cubre el acumulado inicial y menor que el primero emitido en el sistema.
  - Se registra EMITIDO, con PDF marcado "REGISTRO HISTÓRICO". Sus reajustes pagados cuentan como reconocidos, así que una fórmula o un índice corregido después genera solo la diferencia.
- **Certificado parcial sin avance:** se admite solo si tiene reajustes (por ejemplo, para cobrar un retroactivo pendiente).
- **Total negativo:** un certificado cuyo total resultaría negativo (retroactivo negativo mayor que la ejecución del mes) se rechaza.
- **Previsión:** se define por moneda. El exceso es la parte del reajuste de este certificado que supera la previsión vigente.

## 10. Resultados de las pruebas (copias de la base, 17/10/2026)

| Prueba | Resultado |
|---|---|
| `test_reajustes.py`: migración, fórmula, validación, índices, escenario de la maqueta (1.720,00; 3.622,50; retroactivo 1.080,00; total 51.909,12; segunda corrección solo +720), fórmula retroactiva, huella, índices faltantes, negativos, rebase, previsión, numeración, acumulado inicial, históricos, inmutabilidad y PDF de punta a punta | 20/20 OK |
| `test_certificados.py` (Etapa 2) con el código nuevo | 47/47 OK |
| `test_sesiones`, `test_admin_crear_usuario`, `test_compras_route`, `test_viajes_estado` | 20, 1, 4 y 5 OK |
| Matriz del PDF (ARS, USD, EUR; varias páginas; estados) contra `certificado_pdf.py` | 4 pruebas / 92 casos OK |
| Navegador real (Playwright + Edge): seguimiento → certificados → fórmula → validación → índices → emisión → corrección → retroactivo → detalle → contrato → PDF; contingencia; usuario sin panel | 19/19 OK, sin excepciones de JavaScript |
| Mutaciones del motor | 4 de 5 detectadas. La no detectada (control de anulación por retroactivos en el código) es redundante con "solo el último de cada OC" y con el trigger de la base, que sí está probado. |
| Ensayo de migración sobre una copia fresca de la base real | OK: integridad, claves foráneas sin cambios, 64/64 tablas intactas, idempotente; la base real no tiene certificados. |
