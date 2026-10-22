# Módulo Facturación y Cobranzas — Especificación v3

> Estado: **diseño preliminar aprobado**, con la fecha de corte pendiente de confirmar con Contabilidad. Preparación técnica y pruebas hechas **solo sobre copias** (§9).
> No se modificó `main.py`, `schema.sql`, ningún HTML real, `dashboard.db` ni Render.
> Maqueta navegable: `maquetas/facturacion_maqueta.html`. Accesos directos: `#pedido`, `#comprobante`, `#nc`, `#cobranza`, `#detalle` (FC en USD cobrada en pesos), `#detalle-si` (saldo inicial con cancelación estimada), `#revision` (revisar la estimación), `#saldos`, `#ctacte`, `#diseno` (incluye la configuración).
> Código preparado (no integrado): `maquetas/facturacion_tecnico/` — `fac_migracion.py`, `fac_servicio.py`, `test_facturacion.py`.

Tango sigue siendo el sistema contable oficial. SMG registra a mano lo que emite Tango para seguir qué falta facturar, qué se facturó, qué se cobró y qué está vencido. Si SMG y Tango no coinciden, manda Tango.

## 0. Decisiones aprobadas

| # | Decisión | Cómo queda en el diseño |
|---|---|---|
| 1 | **USD/ARS** | Cada comprobante conserva su moneda e importes originales. Un cobro en pesos de una factura en USD registra los pesos recibidos, la fecha efectiva de pago, el TC aplicado y el equivalente en USD que se asigna a la factura. Lo que falta queda como saldo en USD. **No se calculan ni se generan diferencias de cambio**: se cargan las ND/NC que emita Tango (§4.2) |
| 2 | **Histórico** | Se arranca con los comprobantes abiertos a una fecha de corte, con saldos iniciales documentados y conciliados contra Tango. No se importa el histórico (§4.3) |
| 3 | **Impuestos** | Alícuota de IVA por línea (27 / 21 / 10,5 / exento / no gravado). El IVA y el total se calculan solos. Las percepciones se cargan como tributos (§3.4–3.5) |
| 4 | **Relaciones** | Relación N:M: un comprobante puede vincularse a varios pedidos y certificados, y un pedido o certificado a varios comprobantes. Cada vínculo indica el neto asignado (§3.6) |
| 5 | **Menú** | Módulo independiente «Facturación y Cobranzas» en el Menú Principal (§2) |
| 6 | **Fecha de corte** | Pendiente de confirmar con Contabilidad. El corte se puede crear sin fecha; no se confirma ni se cargan saldos definitivos hasta tener el informe de Tango conciliado. Los saldos de la maqueta son ilustrativos (§3.10, §4.3) |
| 7 | **Cobros anteriores al corte** | Si Tango no informa la imputación, la asignación por antigüedad queda registrada como **ESTIMADA**, visible como tal y revisable. Nunca se presenta como imputación real (§3.11, §4.3) |
| 8 | **Adjuntos** | Opcionales en pedidos, FC, ND, NC, cobranzas y corte. Se pueden exigir más adelante por configuración, sin bloquear registros anteriores. Hasta resolver el almacenamiento del servidor, solo enlaces externos y ningún cambio de infraestructura (§3.13) |

---

## 1. Qué se relevó en los archivos adjuntos

### 1.1 `Pedido de Facturacion.xlsx` (42 hojas, una por pedido)
Todas las hojas usan el formulario **«SOLICITUD DE EMISION» — D.C. 1-9-14, Rev. 02, Vigencia 24/8/2017**.

| Campo | Valores observados |
|---|---|
| Comprobante solicitado (D4, en rojo) | `FACTURA` / `NOTA DE CREDITO` / `NOTA DE DEBITO` |
| Solicitud Nº (D7) | `AAAAMMDD-n` en los recientes; en los viejos hay irregularidades |
| Depto. origen | siempre `15` |
| Cotización, Cliente, CUIT, Domicilio | — |
| Moneda | mezcla moneda y TC (`USD`, `AR$`, `DÓLAR`, `1`, `USD TC Venta Divisa`) |
| Condición de pago | `30 FF`, `Al día`, `Inmediato`… |
| OC | — |
| Detalle | 1 a 7 líneas: Cantidad · Detalle · Precio unitario · Subtotal |
| Subtotal, IVA, Total | IVA siempre 21 % |
| Observaciones (en rojo) | — |
| Solicitó / Recibe y pie | fijos |

Conclusiones para el diseño:
- Un mismo formulario sirve para FC, NC y ND.
- Una NC puede corregir varias facturas (`EXAR (NC)`: 06-512 a 06-515).
- Un pedido de factura puede derivar en una NC («Emitir NC descuento combustible»).
- Hay errores de copiar y pegar: el PDF O75088 dice «OC O70754» en el detalle. Por eso la OC sale del vínculo y no se tipea.

### 1.2 `Pedido de Facturación CertNº1 OC O75088.pdf`
Es la impresión A4 vertical de la hoja `Pirquitas (75088)`. El PDF nuevo replica esa disposición.

### 1.3 `Viap RT Cuentas.xlsx`
Cuenta corriente con formato Tango:
- Columnas: Fecha · Tipo (`FAC`, `N/C`, `N/D`, `REC`) · Número (`A0000600000434` = A-0006-00000434) · Vto. · Debe · Haber · Saldo, más Comprobante · Certificado · Pedido (enlaces a Drive).
- El código de cliente coincide con `clientes.codigo`.
- **Los recibos no se asignan a facturas.** El saldo es global y está en pesos.
- **Saldos reales al 31/12/2025:** VIAP $ 7.630.941,23 · MODULAR $ 67.853.261,40. La maqueta los usa como ejemplo de saldo inicial.

### 1.4 `General de Obra SMG.xlsm`
La sección «AVANCE DE FACTURACIÓN» tiene estas columnas: Cert. Nº · Nº Factura · Fecha · Días · Neto · Total + IVA · Vence · Pagado.
Está vacía en las 45 hojas. El módulo nuevo calcula esos indicadores por OC y por seguimiento.

---

## 2. Ubicación y permisos

- **Menú Principal:** entrada nueva en `MODULOS` de `menu_principal.html` (`key: "facturacion"`, título «Facturación y Cobranzas», `href: "/facturacion"`).
- **Página `facturacion.html`:** la misma barra lateral colapsable de Operaciones, con estas secciones: Tablero · Pedidos · Facturas/ND/NC · Cobranzas · Cuenta corriente · Saldos iniciales.
- **Acceso:** el módulo se asigna por usuario en Administración (`usuarios.modulos_json`), igual que los demás.
- **Permisos dentro del módulo** (con mecanismos que ya existen):

  | Rol | Puede | Cómo se asigna |
  |---|---|---|
  | Consulta | ver todo | usuarios con el módulo y `tipo_usuario` CONSULTOR |
  | Carga | pedidos, comprobantes, cobranzas y saldos iniciales en borrador | demás usuarios con el módulo |
  | Responsable | anular registros y confirmar el corte | usuarios en `responsables_modulo` con `modulo = 'facturacion'` |

- **Integración con Operaciones › Seguimiento de Cliente:**
  - botón «Pedido de facturación» en cada certificado EMITIDO;
  - bloque «Avance de facturación» por OC: certificado, facturado, falta facturar, cobrado y saldo.

  Se leen datos de este módulo; Certificados no cambia su lógica.

---

## 3. Modelo de datos (definitivo)

Este modelo es el que crea `maquetas/facturacion_tecnico/fac_migracion.py` y el que se probó (§9). Son 16 tablas nuevas `fac_*`; no se agrega ni cambia ninguna columna de tablas existentes.

Mismas convenciones que Certificados (`CERT_DDL` en `main.py`):
- prefijo `fac_`;
- usuario y fecha en texto;
- `CHECK` en estados;
- triggers de inmutabilidad;
- snapshot JSON + PDF + SHA-256 al emitir;
- tabla de eventos;
- montos `NUMERIC` calculados en Python con `Decimal`, redondeo HALF_UP a 2 decimales.

Los importes calculados (IVA, totales, equivalentes) **siempre los recalcula el servidor**: lo que mande el navegador se ignora.

### 3.1 `fac_pedidos` — Solicitud de emisión (D.C. 1-9-14)
| Columna | Regla |
|---|---|
| id, numero_solicitud | `AAAAMMDD-n` asignado al emitir (correlativo global del día, transacción `BEGIN IMMEDIATE`); único |
| tipo_solicitado | `FACTURA` \| `NOTA_CREDITO` \| `NOTA_DEBITO` |
| estado | `BORRADOR` \| `EMITIDO` \| `ANULADO` (el avance de facturación se calcula) |
| fecha_emision, depto_origen (default 15), cotizacion | |
| cliente_codigo → clientes; snapshot razon_social, cuit, domicilio | |
| moneda | `ARS` \| `USD` \| `EUR` |
| tipo_cambio_informar | texto opcional; default «TC de la fecha efectiva de pago» para pedidos en USD |
| condicion_pago, dias_vencimiento | |
| orden_compra_cliente_id, oc_texto, centro_costo_id, seguimiento_cc_id | opcionales |
| subtotal, iva_total, total | calculados desde las líneas |
| observaciones, solicitante_texto, recibe_texto | |
| justificacion_diferencia | obligatoria si una línea con certificado no coincide con su total a facturar |
| snapshot_json, pdf_sha256 | obligatorios si estado ≠ BORRADOR |
| pdf_ruta | opcional: el PDF se puede regenerar desde el snapshot (reportlab con `invariant=1`, mismo SHA-256), así no depende del disco del servidor |
| reemplaza_a_id | |
| creado/actualizado/emitido/anulado _por/_en, motivo_anulacion | |

### 3.2 `fac_pedido_lineas`
`id, pedido_id, orden, cantidad, detalle, precio_unitario, alicuota_iva ('27','21','10.5','0','NG'), subtotal (= cantidad × precio), certificado_id NULL → certificados_cliente (solo EMITIDOS)`.

Un pedido puede incluir varios certificados (una línea por certificado). El PDF muestra una fila de IVA por alícuota y, si hay más de una, indica la alícuota en cada línea.

### 3.3 `fac_documentos` — FC / ND / NC cargadas desde Tango
| Columna | Regla |
|---|---|
| id, tipo `FC`\|`ND`\|`NC`, letra, punto_venta, numero | `UNIQUE(tipo, letra, punto_venta, numero) WHERE estado='VIGENTE'` |
| estado | `VIGENTE` \| `ANULADO` (ANULADO = error de carga en SMG, no anulación comercial) |
| origen | `NORMAL` \| `SALDO_INICIAL` |
| corte_id | → fac_cortes, obligatorio si origen = SALDO_INICIAL |
| cliente_codigo; snapshot razón social y CUIT | |
| motivo_alta_post_corte | obligatorio (≥ 20 caracteres) si es un saldo inicial agregado con el corte ya confirmado (trigger) |
| fecha_emision, fecha_vencimiento | vencimiento obligatorio en FC/ND y ≥ emisión; nulo en NC |
| moneda, tipo_cambio_emision | TC informativo, opcional |
| condicion_pago, detalle | |
| neto_gravado, exento, no_gravado, iva_total, tributos_total, total | **calculados** desde 3.4 y 3.5 |
| total_pdf_tango | opcional: total impreso por Tango, guardado como control |
| motivo_codigo, motivo_detalle | obligatorios en ND/NC (descuento de combustible, diferencia de cambio, anulación total, ajuste de precio, desacopio de anticipo, otro) |
| documento_origen_id | comprobante que corrige la ND/NC |
| orden_compra_cliente_id, centro_costo_id | opcionales; se proponen desde los pedidos vinculados |
| reemplaza_a_id, nota, creado/anulado _por/_en, motivo_anulacion | |

Lo cancelado antes del corte **no** es una columna del comprobante: está en `fac_cancelaciones_corte` (§3.11), con su criterio.

Regla de fecha: si `origen = NORMAL`, la fecha de emisión tiene que ser posterior a la fecha de corte confirmada. Así se evita cargar dos veces lo que ya entró como saldo inicial.

### 3.4 `fac_documento_lineas` — Detalle por alícuota
`id, documento_id, descripcion, alicuota_iva ('27','21','10.5','0','NG'), neto NUMERIC > 0`.

Se puede cargar línea por línea o agrupado por alícuota, como figure en el PDF de Tango. El IVA de cada línea no se guarda: se calcula por alícuota (§4.1).

### 3.5 `fac_documento_tributos` — Percepciones y otros
`id, documento_id, tipo (PERCEPCION_IIBB, PERCEPCION_IVA, PERCEPCION_GANANCIAS, IMPUESTO_INTERNO, REDONDEO, OTRO), jurisdiccion, base, alicuota, importe`.
- Si hay base y alícuota, `importe = round(base × alícuota / 100; 2)`.
- El tipo `REDONDEO` existe solo para igualar centavos con el total del PDF de Tango (tope ±0,05; ver §6).

### 3.6 `fac_documento_vinculos` — N:M con pedidos y certificados
`id, documento_id, pedido_id NULL, certificado_id NULL, neto_asignado NUMERIC > 0, CHECK ((pedido_id IS NULL) <> (certificado_id IS NULL))`, más `UNIQUE (documento_id, pedido_id)` y `UNIQUE (documento_id, certificado_id)`.

Reglas:
- Por comprobante, Σ neto_asignado a pedidos ≤ neto del comprobante, y lo mismo para certificados.
- Misma moneda que el pedido o el certificado.
- Si se supera el pendiente del pedido o del certificado, se pide confirmación y queda en el evento.

### 3.7 `fac_aplicaciones_nc`
`id, nc_id, documento_id (FC/ND), importe > 0, estado, creado/anulado`.
- Misma moneda y mismo cliente.
- Σ aplicado ≤ total de la NC.
- La aplicación ≤ saldo del destino.

### 3.8 `fac_recibos` y `fac_recibo_valores`
- **Recibo:** `id, punto_venta, numero (único entre vigentes), cliente_codigo, fecha_pago (fecha efectiva de pago), moneda (moneda recibida), total (= Σ valores), observaciones, estado, reemplaza_a_id, creado/anulado`.
- **Valores:** `id, recibo_id, medio (TRANSFERENCIA, CHEQUE, ECHEQ, EFECTIVO, RETENCION_IVA, RETENCION_GANANCIAS, RETENCION_IIBB, RETENCION_SUSS, OTRO), importe > 0, banco_emisor, referencia, fecha_acreditacion, numero_certificado_retencion`.

Las retenciones cancelan deuda igual que el dinero y van en la moneda del recibo.

### 3.9 `fac_imputaciones` — Recibo → FC/ND
| Columna | Regla |
|---|---|
| id, recibo_id, documento_id, estado | |
| importe_recibo | lo asignado en la moneda recibida (p. ej. ARS) |
| tipo_cambio, fecha_tipo_cambio | obligatorios si la moneda del recibo ≠ la del documento; default: fecha de pago del recibo |
| importe_documento | equivalente en la moneda del documento, **calculado**: `round(importe_recibo ÷ tipo_cambio; 2)`, con la regla de cancelación exacta de §4.2 |
| saldo_documento_antes, saldo_documento_despues | snapshot para auditoría |
| creado/anulado | |

### 3.10 `fac_cortes` y `fac_corte_conciliacion` — Saldos iniciales
- **Corte:** `id, fecha_corte (NULL hasta que Contabilidad la confirme), estado BORRADOR|CONFIRMADO, observaciones, creado_por/en, confirmado_por/en`.
  - Un solo corte (índice único).
  - `CONFIRMADO` exige fecha y usuario. Después de eso no se modifica (trigger).
- **Conciliación:** `corte_id, cliente_codigo, moneda, saldo_tango, nota, cargado_por/en`, única por corte + cliente + moneda. Con el corte confirmado no se modifica.
- El saldo SMG al corte se calcula con los registros `origen = SALDO_INICIAL` (comprobantes y anticipos).

### 3.11 `fac_cancelaciones_corte` — Lo cancelado antes del corte
`id, documento_id (FC/ND de saldo inicial), importe > 0, criterio, revision_de_id, nota, estado VIGENTE|ANULADO, creado/anulado _por/_en, motivo_anulacion`.

| criterio | Significado |
|---|---|
| `SEGUN_TANGO` | Tango informa a qué facturas se aplicó el cobro |
| `ESTIMADO_ANTIGUEDAD` | Tango no lo informa; el sistema lo repartió por antigüedad para igualar el saldo de Tango. **No es una imputación real** |
| `REVISADO` | Revisado con Contabilidad; exige nota de 10 caracteres o más |

- `cancelado_antes_corte(doc) = Σ importes VIGENTES`. Siempre menor que el total del comprobante: si no, el comprobante no estaba abierto.
- **Revisión:** se anula la fila anterior (queda en el historial) y se carga la nueva con `revision_de_id` apuntando a ella. No se edita ni se borra.
- Con el corte confirmado, la revisión la hace un responsable y **no cambia el total por cliente y moneda**: solo redistribuye entre comprobantes, para no alterar lo conciliado con Tango.

### 3.12 `fac_config` — Configuración
`clave, valor_json, actualizado_por/en`. La migración inserta los valores iniciales solo si la clave no existe; nunca pisa uno ya configurado. Cada cambio lo hace un responsable y genera un evento.

| Clave | Valor inicial | Uso |
|---|---|---|
| `adjuntos_requeridos` | `{PEDIDO, FC, ND, NC, RECIBO, CORTE: false}` | Exigir adjunto por entidad (§3.13) |
| `adjuntos_almacenamiento` | `ENLACE` | `ENLACE` = solo enlaces externos (Drive); `DISCO` = archivos en `DATA_DIR/Doc_Facturacion` cuando se resuelva el almacenamiento |
| `requiere_corte_confirmado` | `true` | Comprobantes y cobranzas se habilitan al confirmar el corte; los pedidos no lo necesitan |
| `tolerancia_redondeo` | `0.05` | Tope del ajuste «Redondeo» contra el total del PDF de Tango |

### 3.13 `fac_adjuntos` y `fac_eventos`
- **Adjuntos:** `entidad (PEDIDO|DOCUMENTO|RECIBO|CORTE), entidad_id, tipo, nombre, ruta + sha256 o url_externa, estado, creado/anulado`.
  - **Opcionales.** Ningún alta, emisión, cobro ni confirmación se bloquea por falta de adjuntos mientras `adjuntos_requeridos` esté en `false`.
  - Se pueden agregar en cualquier momento, también a registros ya vigentes o anulados.
  - Si más adelante una entidad pasa a obligatoria, aplica solo a las **altas nuevas**. Los registros anteriores siguen operables (se les puede cobrar o aplicar NC) y se listan como «sin adjunto» para completarlos.
  - Mientras el almacenamiento sea `ENLACE`, el servidor rechaza la subida de archivos: no se usa el disco de Render ni se cambia infraestructura.
- **Eventos:** `entidad, entidad_id, evento, usuario, fecha, detalle_json`. Solo admite inserciones (sin UPDATE ni DELETE).

### 3.14 Inmutabilidad (triggers)
- Un pedido BORRADOR es editable y se puede descartar. Uno EMITIDO solo pasa a ANULADO, con motivo de 20 caracteres o más. Sus líneas no se modifican ni se borran.
- Documentos, recibos, aplicaciones, imputaciones, cancelaciones al corte y adjuntos: el único cambio permitido es VIGENTE → ANULADO, con motivo.
- Líneas, tributos, vínculos y valores de recibo no se modifican: se anula el registro padre y se carga de nuevo.
- Corte y conciliación confirmados: no se modifican. Un saldo inicial omitido se agrega con motivo.
- No hay DELETE en ninguna tabla `fac_`, salvo descartar un pedido BORRADOR.
- La migración recrea los triggers en cada arranque (igual que `migrar_certificados_cliente`), sin tocar datos.

---

## 4. Cálculos

Siempre con registros VIGENTES, en la moneda de cada comprobante. Tolerancia: 0,01.

### 4.1 Importes del comprobante y del pedido
```
IVA(alícuota a)   = round(Σ netos de las líneas con alícuota a × a; 2)     a ∈ {27; 21; 10,5}
neto_gravado      = Σ netos de las líneas gravadas
tributos_total    = Σ importes de fac_documento_tributos
total             = neto_gravado + exento + no_gravado + Σ IVA(a) + tributos_total
```
El IVA se calcula sobre el neto agrupado por alícuota, como se discrimina en la factura. Si el total impreso por Tango difiere en centavos, ver §6 (control de total).

### 4.2 Cobros en moneda extranjera
Caso habitual: factura en USD cobrada en pesos al tipo de cambio de la fecha efectiva de pago.

```
Datos que se cargan:  pesos asignados (importe_recibo), TC de la fecha de pago, fecha de pago
Equivalente USD    =  round(pesos asignados ÷ TC; 2)
  · Si los pesos cubren todo el saldo (|pesos − round(saldo_USD × TC; 2)| ≤ round(0,01 × TC; 2)),
    el equivalente es exactamente el saldo y la factura queda CANCELADA.
Saldo restante USD =  saldo USD − equivalente USD
```

Qué se ve en el detalle de la factura (pantalla `#detalle`): saldo original en USD, cada pago con fecha, recibo, pesos recibidos, TC, equivalente en USD y saldo restante, más las retenciones del recibo.

Las **retenciones** del recibo (en pesos) forman parte del importe asignado. Se convierten con el mismo TC.

**Diferencias de cambio:** el sistema no las calcula ni las genera. Si Tango emite una ND o NC por diferencia de cambio, se carga como cualquier comprobante, con motivo «Diferencia de cambio», su moneda (normalmente ARS) y el comprobante original. En la maqueta: ND A-0005-00000025 en ARS sobre la FC 6-505 en USD.

Ejemplo de la maqueta (FC 6-514, EXAR, USD):

| Fecha | Movimiento | Recibido | TC | Asignado USD | Saldo USD |
|---|---|---|---|---|---|
| 28/02/2026 | Importe original | | | | 350.900,00 |
| 14/05/2026 | NC 5-185 aplicada | | | 290.724,57 | 60.175,43 |
| 16/10/2026 | REC 0001-472 (transferencia ARS 41.500.000 + ret. IIBB ARS 500.000) | ARS 42.000.000,00 | 1.383,00 | 30.368,76 | **29.806,67** |

### 4.3 Saldos iniciales (corte)
- **Fecha:** pendiente de Contabilidad. Se puede crear el corte sin fecha y preparar la carga. Al fijar la fecha, se rechaza si hay saldos iniciales con fecha posterior.
- Se cargan las FC/ND con saldo pendiente al corte, más las NC sin aplicar y los anticipos a cuenta a esa fecha. Los anticipos entran como recibo `origen = SALDO_INICIAL` sin asignar.
- Cada FC/ND guarda su importe original. Su saldo al corte es `total − cancelado_antes_corte` (§3.11).
- **Cancelado antes del corte:**
  - Si Tango informa la imputación, se carga `SEGUN_TANGO`.
  - Si no la informa (el caso de Viap RT Cuentas), el sistema puede **estimarla por antigüedad**:
    ```
    exceso = Σ pendientes SMG del cliente y moneda (sin cancelaciones) − saldo Tango
    se cancela primero el comprobante más antiguo, hasta cubrir el exceso
    ```
    Si el exceso no alcanza a cubrirse sin cancelar del todo un comprobante, se rechaza: ese comprobante no estaba abierto. Si el saldo de Tango supera lo cargado, faltan comprobantes.
  - Lo estimado queda `ESTIMADO_ANTIGUEDAD`. En la pantalla se ve con la marca «ESTIMADO» (lista de saldos iniciales, conciliación, detalle del comprobante y cuenta corriente) y con el aviso «no es una imputación real».
  - Se revisa con «Revisar asignación» (`#revision`): nuevo importe por comprobante, criterio `SEGUN_TANGO` o `REVISADO` y nota. Lo anterior queda anulado en el historial.
- **Conciliación:** por cliente y moneda, Σ saldos al corte de SMG vs. saldo según el informe de Tango. El corte solo se confirma con fecha definida y con todas las filas conciliadas o con una nota que justifique la diferencia. Que haya montos estimados no bloquea la confirmación, pero quedan listados en el evento de confirmación.
- **Hasta tener el informe de Tango conciliado no se cargan saldos definitivos.** Los de la maqueta (`#saldos`) son ilustrativos: EXAR USD 165.849,03 · VIAP ARS 7.630.941,23 (FC 6-445 por $ 10.101.569,13 con $ 2.470.627,90 estimados por antigüedad) · MODULAR ARS 67.853.261,40.

### 4.4 Saldos y estados
```
cancelado_antes_corte = Σ fac_cancelaciones_corte VIGENTES del comprobante (cualquier criterio)
Saldo FC/ND     = total − cancelado_antes_corte − Σ NC aplicadas − Σ importe_documento de imputaciones
Disponible NC   = total − Σ aplicaciones
A cuenta REC    = total recibo − Σ importe_recibo asignado
Saldo cliente[M]= Σ saldos FC/ND en M − Σ disponibles NC en M − Σ a cuenta de recibos en M
```
La cuenta corriente muestra los saldos iniciales al corte por su importe pendiente. Su saldo coincide con Σ Debe − Σ Haber.

**Estados de FC/ND:**
| Estado | Condición |
|---|---|
| PENDIENTE | saldo = saldo inicial y no vencido |
| PARCIAL | el saldo bajó y no está vencido |
| VENCIDA | saldo > 0 y hoy > vencimiento |
| CANCELADA | saldo = 0 con al menos un cobro |
| CANCELADA POR NC | saldo = 0 solo con NC |

**NC:** SIN APLICAR / APLICADA PARCIAL / APLICADA.

**Pedido y certificado:**
```
Facturado(P)    = Σ neto_asignado al pedido por comprobantes vigentes del tipo pedido (FC+ND para pedido de factura)
Estado(P)       = PENDIENTE DE FACTURAR | FACTURADO PARCIAL | FACTURADO
Facturado(Cert) = Σ neto asignado por FC/ND − Σ neto asignado por NC
Falta facturar  = total_a_facturar − Facturado(Cert)
Cobrado(Cert)   = Σ cobros de sus FC/ND, prorrateados por neto asignado
```

**Antigüedad de la deuda:** sin vencer · 1–30 · 31–60 · 61–90 · +90 días, por moneda.

---

## 5. Flujo

0. **Puesta en marcha (una vez).**
   1. Crear el corte (puede quedar sin fecha hasta que Contabilidad la confirme).
   2. Fijar la fecha de corte acordada con Contabilidad.
   3. Cargar los comprobantes abiertos y las NC o anticipos sin aplicar, según el informe de Tango.
   4. Cargar lo cancelado antes del corte: según Tango, o estimado por antigüedad (queda marcado y revisable).
   5. Cargar el saldo de Tango por cliente y moneda y conciliar. Adjuntar el informe es opcional.
   6. Un responsable confirma el corte. Desde ahí se habilitan comprobantes y cobranzas; los pedidos se pueden usar desde antes.
1. **Certificado EMITIDO** (Operaciones, opcional). Botón «Pedido de facturación».
2. **Pedido BORRADOR.** Se precarga lo siguiente:

   | Dato del pedido | Origen |
   |---|---|
   | Cliente y CUIT | `seguimiento_cc.cliente_codigo` → `clientes` |
   | OC | certificado / `Orden_de_compra_cliente` |
   | Moneda | moneda del certificado |
   | Condición de pago | `condiciones_comerciales` |
   | Cotización | `seguimiento_cc.cotizacion` |
   | Centro de costos | `centros_costos` |
   | Una línea por certificado | texto «Certificado N°X / obra / período / OC», neto = total a facturar, IVA 21 % editable |

   Se pueden agregar otros certificados o líneas manuales. Un **pedido independiente** se completa a mano.
3. **Emitir.**
   1. Validaciones.
   2. Número `AAAAMMDD-n`.
   3. Snapshot.
   4. PDF D.C. 1-9-14 con reportlab (`facturacion_pdf.py`).
   5. SHA-256.
   6. Evento.

   Desde ahí, el pedido queda inmutable.
4. **Envío al Depto. Contable:** se descarga el PDF. Opcional: botón «Enviar por correo» con el SMTP ya configurado y una plantilla en `email_templates`; el destinatario se configura en Administración.
5. **Tango emite** la FC/NC/ND (fuera de SMG).
6. **Registrar comprobante**, desde uno o varios pedidos (precarga líneas y vínculos) o independiente:
   - se copian número, fechas, netos por alícuota y percepciones;
   - el IVA y el total los calcula SMG;
   - control opcional contra el total del PDF;
   - en una NC, aplicación a una o varias FC/ND;
   - adjunto opcional (enlace al PDF de Tango).
7. **Registrar cobranza:**
   - recibo de Tango con fecha efectiva de pago, valores y retenciones;
   - TC de la fecha de pago si hay comprobantes en otra moneda;
   - asignación sugerida por vencimiento (el más antiguo primero), editable;
   - lo no asignado queda a cuenta.
8. **Seguimiento:** tablero, antigüedad de la deuda, cuenta corriente por cliente y moneda, avance en Seguimiento de Cliente y exportación a Excel.

**Anulaciones y correcciones:**
- Anular sirve solo para errores de carga. Exige motivo de 20 caracteres o más y registra usuario y fecha del servidor. El registro queda visible y no suma.
- Corregir es anular y cargar el correcto, vinculado con `reemplaza_a_id`.
- Si un comprobante con aplicaciones o cobros se anula, también se anulan sus aplicaciones o imputaciones (no los recibos), y esos importes vuelven a «sin aplicar» o «a cuenta».
- Una anulación comercial en Tango se registra como NC aplicada, y la factura queda CANCELADA POR NC.

---

## 6. Controles

**Monedas**
- Pedido, sus certificados y sus comprobantes vinculados: todos en la misma moneda.
- Las NC solo se aplican a FC/ND de su moneda.
- Una imputación entre monedas exige TC y guarda los dos importes.
- Saldos, KPIs, antigüedad y cuenta corriente siempre separados por moneda.
- No hay conversiones automáticas ni diferencias de cambio calculadas.

**Duplicados**
- Número único por tipo + letra + punto de venta + número.
- Recibo único por punto de venta + número.
- Aviso de posible duplicado si coinciden cliente, tipo y total.
- Token de idempotencia en cada alta.
- Número de pedido asignado en transacción.
- Un certificado no puede tener pedidos vigentes que sumen más que su total a facturar.
- Los comprobantes con fecha ≤ corte solo entran como saldo inicial.

**Importes**
- IVA y totales calculados por el servidor.
- **Control de total:** si se carga el total del PDF de Tango y difiere:
  - hasta ±0,05 se puede registrar un tributo «Redondeo»;
  - más de 0,05 bloquea.
- Aplicaciones e imputaciones ≤ saldo disponible.
- Lo asignado de un recibo ≤ su total.
- Vínculos ≤ neto del comprobante.

**Fechas**
- Vencimiento ≥ emisión.
- Aviso si el cobro es anterior a la emisión.
- El TC se fecha con la fecha efectiva de pago.

**Datos maestros**
- El cliente sale de `clientes` (código Tango), con validación del dígito verificador del CUIT.
- Snapshot de razón social y CUIT al emitir o registrar.

**Adjuntos**
- Opcionales mientras la configuración no los exija. Nunca bloquean registros anteriores a la exigencia.
- Sin subida de archivos al servidor hasta que `adjuntos_almacenamiento` pase a `DISCO`.

**Saldos iniciales**
- Lo estimado por antigüedad se distingue siempre de lo informado por Tango.
- Con el corte confirmado, una revisión no puede cambiar el total cancelado por cliente y moneda.

**Auditoría**
- Cada alta, emisión, aplicación, imputación, anulación, confirmación de corte y adjunto genera un `fac_eventos` con usuario, fecha y JSON antes/después.

---

## 7. Decisiones pendientes

### 7.1 Pendiente de Contabilidad
1. **Fecha de corte.** Bloquea solo la confirmación del corte y la carga de saldos definitivos. La programación y las pruebas pueden avanzar sin ella.

### 7.2 Resueltas con un valor por defecto
Se pueden cambiar sin rediseñar.

| # | Tema | Resolución |
|---|---|---|
| a | Moneda de cobro y TC | Decisión 1 + aclaración: TC de la fecha efectiva de pago, tomado del recibo de Tango; sin diferencias automáticas |
| b | Conciliación con Tango | Por comprobante (moneda e importes originales) y, al corte, por cliente y moneda. TC de emisión opcional, solo informativo |
| c | Numeración del pedido | `AAAAMMDD-n`, correlativo global por día (compatible con los existentes) |
| d | IVA por línea y percepciones | Decisión 3 |
| e | Redondeo frente a Tango | IVA por alícuota sobre el neto agrupado; control contra el total del PDF con tributo «Redondeo» de hasta ±0,05 |
| f | Envío al Depto. Contable | PDF descargable; correo opcional con el SMTP existente y destinatario configurable |
| g | Firma «Recibe» | En blanco para firma manuscrita |
| h | Una factura para varios pedidos o certificados | Decisión 4 |
| i | Vencimiento | Se sugiere por la condición de pago (30 FF → emisión + 30) y se corrige con el dato del PDF de Tango |
| j | Fondo de reparo y anticipos | Comprobantes con su motivo (p. ej. «Desacopio de anticipo financiero») vinculados al pedido o certificado. Los descuentos siguen en Certificados |
| k | Permisos | Consulta / Carga / Responsable según §2. Anular, confirmar el corte, revisar estimaciones con el corte confirmado y cambiar la configuración: solo responsables |
| l | Adjuntos | Decisión 8: opcionales, exigibles por configuración, solo enlaces hasta resolver el almacenamiento |
| m | Inconsistencias del Excel histórico | No aplica: el histórico no se importa |
| n | Cancelado antes del corte | Decisión 7: según Tango o estimado por antigüedad, marcado y revisable |
| o | Operación antes del corte | Pedidos sí; comprobantes y cobranzas, al confirmar el corte (`requiere_corte_confirmado`) |
| p | PDF del pedido | Se guarda el snapshot y el SHA-256; el PDF se regenera igual, sin depender del disco |

### 7.3 Datos para la puesta en marcha (no son decisiones de diseño)
- Fecha de corte e informe de Tango de comprobantes pendientes y saldos por cliente a esa fecha (Contabilidad).
- Si Tango puede informar qué facturas canceló cada recibo: reduce o elimina las estimaciones.
- Usuarios con el módulo asignado y responsables.
- Destinatario del correo al Depto. Contable, si se usa.

---

## 8. Plan de implementación (cuando se apruebe aplicar)

Se trabaja siempre en una **copia** de la base. Nunca se trabaja sobre `dashboard.db` con `uvicorn --reload`.

1. ✅ *Preparado sobre copia:* `migrar_facturacion()` (DDL `fac_*` + índices + triggers + configuración inicial), idempotente. Al aplicar se copia a `main.py` junto a `CERT_DDL` y se llama desde `init_sqlite()`.
2. ✅ *Preparado sobre copia:* servicio con `Decimal` (`fac_servicio.py`) y pruebas (§9). Al aplicar se mueve a un módulo `facturacion.py` importado por `main.py`.
3. API `/api/facturacion/...` con validaciones, idempotencia y eventos.
4. `facturacion_pdf.py` (D.C. 1-9-14 con IVA por alícuota) y test de texto con pypdf.
5. `facturacion.html` basado en la maqueta aprobada, y la entrada nueva en `menu_principal.html`.
6. Integración con Seguimiento de Cliente.
7. Pruebas e2e sobre una copia de la base; backup previo y despliegue en Render.

---

## 9. Preparación técnica y pruebas (sobre copias)

Nada de esto está conectado a la aplicación. Los archivos están en `maquetas/facturacion_tecnico/` y no los importa `main.py`.

### 9.1 Migraciones previstas
Una sola función, `migrar_facturacion(conn)`, que se ejecuta en cada arranque:

| Paso | Qué hace | Riesgo sobre datos existentes |
|---|---|---|
| 1 | `CREATE TABLE IF NOT EXISTS` de las 16 tablas `fac_*` | Ninguno: solo agrega |
| 2 | `CREATE [UNIQUE] INDEX IF NOT EXISTS` (número de comprobante y de recibo únicos entre vigentes, corte único, búsquedas) | Ninguno: solo sobre tablas nuevas |
| 3 | `DROP` + `CREATE TRIGGER` de los triggers `trg_fac_*` (inmutabilidad, sin borrados, corte) y limpieza de `trg_fac_*` obsoletos | Ninguno: solo triggers del módulo |
| 4 | `INSERT OR IGNORE` de la configuración inicial en `fac_config` | Ninguno: no pisa valores existentes |

No hay `ALTER TABLE` ni `UPDATE` sobre tablas existentes. Las claves foráneas apuntan a `clientes(codigo)`, `certificados_cliente`, `Orden_de_compra_cliente`, `centros_costos` y `seguimiento_cc` con `ON DELETE RESTRICT`. La reimportación de `Clientes.xlsx` usa `ON CONFLICT DO UPDATE`, que no borra filas, así que no choca con esas claves.

**Al aplicar en Render:** respaldo de `/var/data/dashboard.db` → despliegue → la migración corre sola al arrancar → verificación con `PRAGMA integrity_check` y `foreign_key_check`. Para volver atrás alcanza con desplegar la versión anterior: las tablas `fac_*` quedan sin uso y no afectan al resto.

### 9.2 Resultado de las pruebas
`.venv\Scripts\python.exe -m unittest maquetas.facturacion_tecnico.test_facturacion -v` → **16 pruebas, todas OK** (≈ 9 s).

Cada prueba trabaja sobre una copia nueva de `dashboard.db`, hecha con la API de backup desde una conexión de solo lectura. La última verifica que el SHA-256 de la base real no cambió.

| Prueba | Qué verifica |
|---|---|
| 01 | La migración solo agrega las 16 tablas `fac_*`; las demás tablas quedan idénticas (filas, hash del contenido y definición); ejecutarla tres veces da el mismo esquema; `integrity_check` y `foreign_key_check` OK |
| 02 | Volver a migrar no pisa una configuración cambiada |
| 10 | IVA 21 % + 10,5 % + exento + percepción IIBB; redondeo de 0,03 contra el PDF aceptado; 0,10 rechazado |
| 11 | Equivalente ARS→USD: 42.000.000 / 1.383 = 30.368,76; cancelación exacta dentro de un centavo de USD |
| 20 | Corte sin fecha: no se confirma, no se cargan comprobantes; los pedidos sí se emiten (`20261019-1`) |
| 21 | VIAP FC 6-445: estimación por antigüedad de 2.470.627,90, marcada `ESTIMADO_ANTIGUEDAD`, saldo 7.630.941,23 conciliado con Tango |
| 22 | Revisión de la estimación: con el corte confirmado no cambia el total, solo un responsable, la fila estimada queda anulada y vinculada |
| 23 | Corte confirmado: fecha ≤ corte rechazada; saldo inicial omitido sin motivo rechazado por trigger; corte y conciliación inmutables |
| 24 | No se confirma sin conciliar ni sin ser responsable |
| 30 | FC 6-514 USD 350.900 − NC 290.724,57 − cobro ARS 42.000.000 a TC 1.383 → saldo USD 29.806,67, recibo sin saldo a cuenta, saldo ARS 0; cobro final cancela exacto; sin TC no imputa |
| 31 | ND por diferencia de cambio cargada en ARS: suma solo en ARS; NC en ARS no se aplica a FC en USD |
| 32 | Duplicados: rechazo del servicio y del índice único aunque se saltee el servicio |
| 33 | Factura con dos pedidos (numeración `20261010-1` y `-2`); vínculo que excede el neto rechazado; pedido emitido inmutable y sin borrado |
| 34 | Anulación: solo responsable, motivo ≥ 20; cascada de imputaciones, el recibo vuelve a «a cuenta»; sin reactivar ni borrar; eventos inmutables; se recarga el número correcto |
| 40 | Adjuntos: opcionales por defecto; exigibles por configuración solo para altas nuevas; los registros anteriores siguen operables; sin subida de archivos mientras el almacenamiento sea `ENLACE` |
| 99 | Integridad final, eventos de estimación y confirmación (con la lista de clientes con montos estimados) y base real intacta |

Control de las pruebas: al romper a propósito la regla de cancelación exacta y la marca de «estimado», fallan 4 pruebas (11, 21, 22 y 99). Es decir, las pruebas detectan esos errores.

### 9.3 Qué falta para implementar (cuando lo autorices)
1. Copiar `FAC_DDL` y `migrar_facturacion()` a `main.py` y el servicio a `facturacion.py`, siempre trabajando en una copia (nunca con `uvicorn --reload` sobre `dashboard.db`).
2. API `/api/facturacion/...` con permisos, idempotencia y transacciones (`BEGIN IMMEDIATE`).
3. `facturacion_pdf.py` (D.C. 1-9-14) con prueba de texto con pypdf.
4. `facturacion.html` a partir de la maqueta, y la entrada en `menu_principal.html`.
5. Integración con Seguimiento de Cliente y prueba e2e sobre una copia.
6. Respaldo y despliegue en Render, con tu aprobación previa.
