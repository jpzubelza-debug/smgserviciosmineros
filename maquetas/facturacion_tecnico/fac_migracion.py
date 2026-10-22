"""Migración del módulo Facturación y Cobranzas (preparación técnica, NO integrada a main.py).

Solo agrega tablas, índices, triggers y la configuración inicial de las tablas nuevas `fac_*`.
No altera tablas ni filas existentes. Es idempotente: se puede ejecutar en cada arranque.
Cuando se apruebe, FAC_DDL y migrar_facturacion() se copian a main.py junto a CERT_DDL.
"""
import json
import re

ENTIDADES_ADJUNTO = ("PEDIDO", "FC", "ND", "NC", "RECIBO", "CORTE")

# Configuración inicial: se inserta solo si la clave no existe (nunca pisa un valor ya configurado).
CONFIG_INICIAL = {
    # Adjuntos opcionales en todas las entidades. Pasar una entidad a true la vuelve obligatoria
    # solo para altas posteriores; los registros anteriores sin adjunto no se bloquean.
    "adjuntos_requeridos": {e: False for e in ENTIDADES_ADJUNTO},
    # ENLACE: solo enlaces externos (Drive). DISCO: archivos en DATA_DIR/Doc_Facturacion.
    # Queda en ENLACE hasta resolver el almacenamiento del servidor.
    "adjuntos_almacenamiento": "ENLACE",
    # Comprobantes y cobranzas se habilitan cuando hay un corte confirmado (los pedidos no lo necesitan).
    "requiere_corte_confirmado": True,
    "tolerancia_redondeo": "0.05",
}

_MONEDA = "CHECK (moneda IN ('ARS', 'USD', 'EUR'))"
_ALICUOTA = "CHECK (alicuota_iva IN ('27', '21', '10.5', '0', 'NG'))"
_ANULACION = """
        estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT,
        CHECK (estado = 'VIGENTE' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                      AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20))"""

FAC_TABLAS = (
    "fac_config", "fac_cortes", "fac_corte_conciliacion", "fac_pedidos", "fac_pedido_lineas",
    "fac_documentos", "fac_documento_lineas", "fac_documento_tributos", "fac_documento_vinculos",
    "fac_aplicaciones_nc", "fac_cancelaciones_corte", "fac_recibos", "fac_recibo_valores",
    "fac_imputaciones", "fac_adjuntos", "fac_eventos",
)

FAC_DDL = (
    """
    CREATE TABLE IF NOT EXISTS fac_config (
        clave TEXT PRIMARY KEY,
        valor_json TEXT NOT NULL CHECK (json_valid(valor_json)),
        actualizado_por TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    )
    """,
    # --- Corte de saldos iniciales: fecha a definir con Contabilidad; un solo corte vigente ---
    """
    CREATE TABLE IF NOT EXISTS fac_cortes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha_corte TEXT CHECK (fecha_corte IS NULL OR fecha_corte GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
        estado TEXT NOT NULL DEFAULT 'BORRADOR' CHECK (estado IN ('BORRADOR', 'CONFIRMADO')),
        observaciones TEXT,
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        confirmado_por TEXT,
        confirmado_en TEXT,
        CHECK (estado = 'BORRADOR' OR (fecha_corte IS NOT NULL AND confirmado_por IS NOT NULL AND confirmado_en IS NOT NULL))
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_fac_cortes_unico ON fac_cortes ((1))",
    """
    CREATE TABLE IF NOT EXISTS fac_corte_conciliacion (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        corte_id INTEGER NOT NULL REFERENCES fac_cortes (id) ON DELETE RESTRICT,
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        moneda TEXT NOT NULL """ + _MONEDA + """,
        saldo_tango NUMERIC NOT NULL,
        nota TEXT,
        cargado_por TEXT NOT NULL,
        cargado_en TEXT NOT NULL,
        UNIQUE (corte_id, cliente_codigo, moneda)
    )
    """,
    # --- Pedido de facturación (Solicitud de emisión D.C. 1-9-14) ---
    """
    CREATE TABLE IF NOT EXISTS fac_pedidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_solicitud TEXT UNIQUE,
        tipo_solicitado TEXT NOT NULL CHECK (tipo_solicitado IN ('FACTURA', 'NOTA_CREDITO', 'NOTA_DEBITO')),
        estado TEXT NOT NULL DEFAULT 'BORRADOR' CHECK (estado IN ('BORRADOR', 'EMITIDO', 'ANULADO')),
        fecha_emision TEXT NOT NULL,
        depto_origen TEXT NOT NULL DEFAULT '15',
        cotizacion TEXT,
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        cliente_razon_social TEXT,
        cliente_cuit TEXT,
        cliente_domicilio TEXT,
        moneda TEXT NOT NULL """ + _MONEDA + """,
        tipo_cambio_informar TEXT,
        condicion_pago TEXT,
        dias_vencimiento INTEGER CHECK (dias_vencimiento IS NULL OR dias_vencimiento >= 0),
        orden_compra_cliente_id INTEGER REFERENCES Orden_de_compra_cliente (id) ON DELETE RESTRICT,
        oc_texto TEXT,
        centro_costo_id INTEGER REFERENCES centros_costos (id) ON DELETE RESTRICT,
        seguimiento_cc_id INTEGER REFERENCES seguimiento_cc (id) ON DELETE RESTRICT,
        subtotal NUMERIC NOT NULL DEFAULT 0,
        iva_total NUMERIC NOT NULL DEFAULT 0,
        total NUMERIC NOT NULL DEFAULT 0,
        observaciones TEXT,
        solicitante_texto TEXT,
        recibe_texto TEXT,
        justificacion_diferencia TEXT,
        snapshot_json TEXT,
        pdf_ruta TEXT,
        pdf_sha256 TEXT,
        reemplaza_a_id INTEGER REFERENCES fac_pedidos (id) ON DELETE RESTRICT,
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        actualizado_por TEXT,
        actualizado_en TEXT,
        emitido_por TEXT,
        emitido_en TEXT,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT,
        CHECK (estado = 'BORRADOR' OR (numero_solicitud IS NOT NULL AND snapshot_json IS NOT NULL
                                       AND pdf_sha256 IS NOT NULL AND emitido_por IS NOT NULL)),
        CHECK (estado <> 'ANULADO' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                       AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fac_pedido_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pedido_id INTEGER NOT NULL REFERENCES fac_pedidos (id) ON DELETE RESTRICT,
        orden INTEGER NOT NULL,
        cantidad NUMERIC NOT NULL CHECK (cantidad > 0),
        detalle TEXT NOT NULL CHECK (length(trim(detalle)) > 0),
        precio_unitario NUMERIC NOT NULL,
        alicuota_iva TEXT NOT NULL """ + _ALICUOTA + """,
        subtotal NUMERIC NOT NULL,
        certificado_id INTEGER REFERENCES certificados_cliente (id) ON DELETE RESTRICT,
        UNIQUE (pedido_id, orden)
    )
    """,
    # --- Comprobantes cargados desde Tango ---
    """
    CREATE TABLE IF NOT EXISTS fac_documentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL CHECK (tipo IN ('FC', 'ND', 'NC')),
        letra TEXT NOT NULL CHECK (letra IN ('A', 'B', 'C', 'E', 'M')),
        punto_venta INTEGER NOT NULL CHECK (punto_venta BETWEEN 1 AND 99999),
        numero INTEGER NOT NULL CHECK (numero BETWEEN 1 AND 99999999),
        origen TEXT NOT NULL DEFAULT 'NORMAL' CHECK (origen IN ('NORMAL', 'SALDO_INICIAL')),
        corte_id INTEGER REFERENCES fac_cortes (id) ON DELETE RESTRICT,
        motivo_alta_post_corte TEXT,
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        cliente_razon_social TEXT,
        cliente_cuit TEXT,
        fecha_emision TEXT NOT NULL,
        fecha_vencimiento TEXT,
        moneda TEXT NOT NULL """ + _MONEDA + """,
        tipo_cambio_emision NUMERIC CHECK (tipo_cambio_emision IS NULL OR tipo_cambio_emision > 0),
        condicion_pago TEXT,
        detalle TEXT,
        neto_gravado NUMERIC NOT NULL DEFAULT 0,
        exento NUMERIC NOT NULL DEFAULT 0,
        no_gravado NUMERIC NOT NULL DEFAULT 0,
        iva_total NUMERIC NOT NULL DEFAULT 0,
        tributos_total NUMERIC NOT NULL DEFAULT 0,
        total NUMERIC NOT NULL CHECK (total > 0),
        total_pdf_tango NUMERIC,
        motivo_codigo TEXT CHECK (motivo_codigo IS NULL OR motivo_codigo IN
            ('DESCUENTO_COMBUSTIBLE', 'DIFERENCIA_CAMBIO', 'ANULACION_TOTAL', 'AJUSTE_PRECIO', 'DESACOPIO_ANTICIPO', 'OTRO')),
        motivo_detalle TEXT,
        documento_origen_id INTEGER REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        orden_compra_cliente_id INTEGER REFERENCES Orden_de_compra_cliente (id) ON DELETE RESTRICT,
        centro_costo_id INTEGER REFERENCES centros_costos (id) ON DELETE RESTRICT,
        reemplaza_a_id INTEGER REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        nota TEXT,""" + _ANULACION + """,
        CHECK (origen = 'NORMAL' OR corte_id IS NOT NULL),
        CHECK (tipo = 'NC' OR (fecha_vencimiento IS NOT NULL AND fecha_vencimiento >= fecha_emision)),
        CHECK (tipo = 'FC' OR motivo_codigo IS NOT NULL)
    )
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_fac_documentos_numero
       ON fac_documentos (tipo, letra, punto_venta, numero) WHERE estado = 'VIGENTE'""",
    "CREATE INDEX IF NOT EXISTS ix_fac_documentos_cliente ON fac_documentos (cliente_codigo, moneda, estado)",
    """
    CREATE TABLE IF NOT EXISTS fac_documento_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        descripcion TEXT,
        alicuota_iva TEXT NOT NULL """ + _ALICUOTA + """,
        neto NUMERIC NOT NULL CHECK (neto > 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fac_documento_tributos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        tipo TEXT NOT NULL CHECK (tipo IN ('PERCEPCION_IIBB', 'PERCEPCION_IVA', 'PERCEPCION_GANANCIAS',
                                           'IMPUESTO_INTERNO', 'REDONDEO', 'OTRO')),
        jurisdiccion TEXT,
        base NUMERIC,
        alicuota NUMERIC,
        importe NUMERIC NOT NULL,
        CHECK (tipo = 'REDONDEO' OR importe > 0),
        CHECK (tipo <> 'REDONDEO' OR abs(importe) <= 0.05)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fac_documento_vinculos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        pedido_id INTEGER REFERENCES fac_pedidos (id) ON DELETE RESTRICT,
        certificado_id INTEGER REFERENCES certificados_cliente (id) ON DELETE RESTRICT,
        neto_asignado NUMERIC NOT NULL CHECK (neto_asignado > 0),
        CHECK ((pedido_id IS NULL) <> (certificado_id IS NULL)),
        UNIQUE (documento_id, pedido_id),
        UNIQUE (documento_id, certificado_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fac_aplicaciones_nc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nc_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        importe NUMERIC NOT NULL CHECK (importe > 0),""" + _ANULACION + """,
        CHECK (nc_id <> documento_id)
    )
    """,
    # --- Lo cancelado antes del corte. Criterio explícito: lo estimado por antigüedad NO es una
    #     imputación real; se revisa anulando la fila y cargando la correcta (SEGUN_TANGO o REVISADO). ---
    """
    CREATE TABLE IF NOT EXISTS fac_cancelaciones_corte (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        importe NUMERIC NOT NULL CHECK (importe > 0),
        criterio TEXT NOT NULL CHECK (criterio IN ('SEGUN_TANGO', 'ESTIMADO_ANTIGUEDAD', 'REVISADO')),
        revision_de_id INTEGER REFERENCES fac_cancelaciones_corte (id) ON DELETE RESTRICT,
        nota TEXT,""" + _ANULACION + """,
        CHECK (criterio <> 'REVISADO' OR length(trim(COALESCE(nota, ''))) >= 10)
    )
    """,
    # --- Cobranzas ---
    """
    CREATE TABLE IF NOT EXISTS fac_recibos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        origen TEXT NOT NULL DEFAULT 'NORMAL' CHECK (origen IN ('NORMAL', 'SALDO_INICIAL')),
        corte_id INTEGER REFERENCES fac_cortes (id) ON DELETE RESTRICT,
        punto_venta INTEGER NOT NULL CHECK (punto_venta BETWEEN 1 AND 99999),
        numero INTEGER NOT NULL CHECK (numero BETWEEN 1 AND 99999999),
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        fecha_pago TEXT NOT NULL,
        moneda TEXT NOT NULL """ + _MONEDA + """,
        total NUMERIC NOT NULL CHECK (total > 0),
        observaciones TEXT,
        reemplaza_a_id INTEGER REFERENCES fac_recibos (id) ON DELETE RESTRICT,""" + _ANULACION + """,
        CHECK (origen = 'NORMAL' OR corte_id IS NOT NULL)
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_fac_recibos_numero ON fac_recibos (punto_venta, numero) WHERE estado = 'VIGENTE'",
    """
    CREATE TABLE IF NOT EXISTS fac_recibo_valores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recibo_id INTEGER NOT NULL REFERENCES fac_recibos (id) ON DELETE RESTRICT,
        medio TEXT NOT NULL CHECK (medio IN ('TRANSFERENCIA', 'CHEQUE', 'ECHEQ', 'EFECTIVO', 'RETENCION_IVA',
                                             'RETENCION_GANANCIAS', 'RETENCION_IIBB', 'RETENCION_SUSS', 'OTRO')),
        importe NUMERIC NOT NULL CHECK (importe > 0),
        banco_emisor TEXT,
        referencia TEXT,
        fecha_acreditacion TEXT,
        numero_certificado_retencion TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fac_imputaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recibo_id INTEGER NOT NULL REFERENCES fac_recibos (id) ON DELETE RESTRICT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        importe_recibo NUMERIC NOT NULL CHECK (importe_recibo > 0),
        tipo_cambio NUMERIC CHECK (tipo_cambio IS NULL OR tipo_cambio > 0),
        fecha_tipo_cambio TEXT,
        importe_documento NUMERIC NOT NULL CHECK (importe_documento > 0),
        saldo_documento_antes NUMERIC NOT NULL,
        saldo_documento_despues NUMERIC NOT NULL CHECK (saldo_documento_despues >= 0),""" + _ANULACION + """,
        CHECK ((tipo_cambio IS NULL) = (fecha_tipo_cambio IS NULL))
    )
    """,
    # --- Adjuntos (opcionales por configuración) y auditoría ---
    """
    CREATE TABLE IF NOT EXISTS fac_adjuntos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidad TEXT NOT NULL CHECK (entidad IN ('PEDIDO', 'DOCUMENTO', 'RECIBO', 'CORTE')),
        entidad_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        nombre TEXT NOT NULL,
        ruta TEXT,
        sha256 TEXT,
        url_externa TEXT,""" + _ANULACION + """,
        CHECK ((ruta IS NOT NULL AND sha256 IS NOT NULL) OR url_externa IS NOT NULL)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_fac_adjuntos_entidad ON fac_adjuntos (entidad, entidad_id, estado)",
    """
    CREATE TABLE IF NOT EXISTS fac_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidad TEXT NOT NULL,
        entidad_id INTEGER,
        evento TEXT NOT NULL,
        usuario TEXT NOT NULL,
        fecha TEXT NOT NULL,
        detalle_json TEXT CHECK (detalle_json IS NULL OR json_valid(detalle_json))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_fac_eventos_entidad ON fac_eventos (entidad, entidad_id)",
    # --- Inmutabilidad ---
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_corte_confirmado_inmutable
    BEFORE UPDATE ON fac_cortes WHEN OLD.estado = 'CONFIRMADO'
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: un corte confirmado no puede modificarse'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_conciliacion_cerrada
    BEFORE UPDATE ON fac_corte_conciliacion
    WHEN (SELECT estado FROM fac_cortes WHERE id = OLD.corte_id) = 'CONFIRMADO'
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: la conciliación de un corte confirmado no puede modificarse'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_saldo_inicial_post_corte
    BEFORE INSERT ON fac_documentos
    WHEN NEW.origen = 'SALDO_INICIAL' AND (SELECT estado FROM fac_cortes WHERE id = NEW.corte_id) = 'CONFIRMADO'
         AND length(trim(COALESCE(NEW.motivo_alta_post_corte, ''))) < 20
    BEGIN SELECT RAISE(ABORT, 'FAC_CORTE: con el corte confirmado, un saldo inicial omitido exige motivo'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_pedido_emitido_inmutable
    BEFORE UPDATE ON fac_pedidos WHEN OLD.estado <> 'BORRADOR' AND (
        OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.numero_solicitud IS NOT OLD.numero_solicitud OR NEW.tipo_solicitado IS NOT OLD.tipo_solicitado
        OR NEW.fecha_emision IS NOT OLD.fecha_emision OR NEW.cliente_codigo IS NOT OLD.cliente_codigo
        OR NEW.moneda IS NOT OLD.moneda OR NEW.subtotal IS NOT OLD.subtotal OR NEW.iva_total IS NOT OLD.iva_total
        OR NEW.total IS NOT OLD.total OR NEW.snapshot_json IS NOT OLD.snapshot_json
        OR NEW.pdf_ruta IS NOT OLD.pdf_ruta OR NEW.pdf_sha256 IS NOT OLD.pdf_sha256
        OR NEW.emitido_por IS NOT OLD.emitido_por OR NEW.emitido_en IS NOT OLD.emitido_en
        OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en)
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: un pedido emitido solo puede anularse'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_pedido_lineas_emitido
    BEFORE UPDATE ON fac_pedido_lineas
    WHEN (SELECT estado FROM fac_pedidos WHERE id = OLD.pedido_id) <> 'BORRADOR'
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: las líneas de un pedido emitido no pueden modificarse'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_pedido_lineas_alta_emitido
    BEFORE INSERT ON fac_pedido_lineas
    WHEN (SELECT estado FROM fac_pedidos WHERE id = NEW.pedido_id) <> 'BORRADOR'
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: no se agregan líneas a un pedido emitido'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_documentos_inmutable
    BEFORE UPDATE ON fac_documentos WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.tipo IS NOT OLD.tipo OR NEW.letra IS NOT OLD.letra OR NEW.punto_venta IS NOT OLD.punto_venta
        OR NEW.numero IS NOT OLD.numero OR NEW.origen IS NOT OLD.origen OR NEW.corte_id IS NOT OLD.corte_id
        OR NEW.cliente_codigo IS NOT OLD.cliente_codigo OR NEW.fecha_emision IS NOT OLD.fecha_emision
        OR NEW.fecha_vencimiento IS NOT OLD.fecha_vencimiento OR NEW.moneda IS NOT OLD.moneda
        OR NEW.neto_gravado IS NOT OLD.neto_gravado OR NEW.exento IS NOT OLD.exento OR NEW.no_gravado IS NOT OLD.no_gravado
        OR NEW.iva_total IS NOT OLD.iva_total OR NEW.tributos_total IS NOT OLD.tributos_total OR NEW.total IS NOT OLD.total
        OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: un comprobante solo puede anularse'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_recibos_inmutable
    BEFORE UPDATE ON fac_recibos WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.punto_venta IS NOT OLD.punto_venta OR NEW.numero IS NOT OLD.numero
        OR NEW.cliente_codigo IS NOT OLD.cliente_codigo OR NEW.fecha_pago IS NOT OLD.fecha_pago
        OR NEW.moneda IS NOT OLD.moneda OR NEW.total IS NOT OLD.total
        OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: un recibo solo puede anularse'); END
    """,
) + tuple(
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_{t}_solo_anular
    BEFORE UPDATE ON {t} WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.id IS NOT OLD.id OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
        OR {' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in cols)}
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: {t} solo admite VIGENTE -> ANULADO'); END
    """
    for t, cols in (
        ("fac_aplicaciones_nc", ("nc_id", "documento_id", "importe")),
        ("fac_cancelaciones_corte", ("documento_id", "importe", "criterio", "revision_de_id", "nota")),
        ("fac_imputaciones", ("recibo_id", "documento_id", "importe_recibo", "tipo_cambio", "importe_documento")),
        ("fac_adjuntos", ("entidad", "entidad_id", "ruta", "sha256", "url_externa")),
    )
) + tuple(
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_{t}_fija
    BEFORE UPDATE ON {t}
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: {t} no se modifica; se anula el registro padre'); END
    """
    for t in ("fac_documento_lineas", "fac_documento_tributos", "fac_documento_vinculos", "fac_recibo_valores", "fac_eventos")
) + tuple(
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_{t}_sin_delete
    BEFORE DELETE ON {t}
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: {t} no admite borrados; use la anulación'); END
    """
    for t in FAC_TABLAS if t not in ("fac_config", "fac_pedidos", "fac_pedido_lineas")
) + (
    # Un pedido en BORRADOR (sin número) se puede descartar y sus líneas reemplazar; uno emitido, no.
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_pedidos_sin_delete
    BEFORE DELETE ON fac_pedidos WHEN OLD.estado <> 'BORRADOR'
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: un pedido emitido no se borra; use la anulación'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_pedido_lineas_sin_delete
    BEFORE DELETE ON fac_pedido_lineas
    WHEN (SELECT estado FROM fac_pedidos WHERE id = OLD.pedido_id) <> 'BORRADOR'
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: las líneas de un pedido emitido no se borran'); END
    """,
)


def migrar_facturacion(conn, usuario="sistema", ahora=None):
    """Crea las estructuras fac_*. Solo agrega; no modifica tablas ni filas existentes."""
    definidos = set()
    for sentencia in FAC_DDL:
        trigger = re.search(r"CREATE TRIGGER IF NOT EXISTS (\w+)", sentencia)
        if trigger:
            definidos.add(trigger.group(1))
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger.group(1)}")
        conn.execute(sentencia)
    for (obsoleto,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg\\_fac\\_%' ESCAPE '\\'"
    ).fetchall():
        if obsoleto not in definidos:
            conn.execute(f"DROP TRIGGER IF EXISTS {obsoleto}")
    ahora = ahora or conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')").fetchone()[0]
    for clave, valor in CONFIG_INICIAL.items():
        conn.execute(
            "INSERT OR IGNORE INTO fac_config (clave, valor_json, actualizado_por, actualizado_en) VALUES (?, ?, ?, ?)",
            (clave, json.dumps(valor), usuario, ahora),
        )
