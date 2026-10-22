"""Módulo Facturación y Cobranzas: estructuras de base (fac_*), migración y servicio.

Tango sigue siendo el sistema contable oficial: acá se registra a mano lo que emite Tango para seguir
pedidos, comprobantes, cobranzas y saldos. Todos los importes se calculan con Decimal y redondeo
HALF_UP a 2 decimales; lo que mande el navegador como IVA, total o equivalente se ignora.
Las funciones reciben una conexión SQLite abierta y no hacen commit: main.py las envuelve en una
transacción BEGIN IMMEDIATE.

La migración solo agrega tablas, índices, triggers y la configuración inicial de las tablas nuevas;
no altera tablas ni filas existentes y es idempotente (se ejecuta en cada arranque desde init_sqlite).
"""
import hashlib
import json
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MODULO = "facturacion"
ENTIDADES_ADJUNTO = ("PEDIDO", "FC", "ND", "NC", "RECIBO", "CORTE")

# Configuración inicial: se inserta solo si la clave no existe (nunca pisa un valor ya configurado).
CONFIG_INICIAL = {
    # Adjuntos opcionales en todas las entidades. Pasar una entidad a true la vuelve obligatoria
    # solo para altas posteriores; los registros anteriores sin adjunto no se bloquean.
    "adjuntos_requeridos": {e: False for e in ENTIDADES_ADJUNTO},
    # ENLACE: solo enlaces externos (Drive). DISCO: archivos en DATA_DIR/Doc_Facturacion.
    # Queda en ENLACE hasta resolver el almacenamiento del servidor.
    "adjuntos_almacenamiento": "ENLACE",
    # Puesta en marcha: un comprobante o una cobranza normal exige la fecha de inicio operativo definida
    # (fecha del corte), fecha posterior a ella y el estado inicial del cliente validado por un responsable.
    # Los pedidos no lo necesitan. (El nombre de la clave se conserva por compatibilidad.)
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
    "fac_imputaciones", "fac_adjuntos", "fac_eventos", "fac_estado_inicial", "fac_antecedentes_certificado",
)

# Columnas agregadas a tablas fac_ ya creadas (versión 2: puesta en marcha). Se agregan solo si faltan.
FAC_COLUMNAS = {
    # Detalle del pago previo al inicio operativo: moneda original, tipo de cambio y recibo de Tango.
    "fac_cancelaciones_corte": (
        ("fecha_pago", "TEXT"),
        ("moneda_pago", "TEXT CHECK (moneda_pago IS NULL OR moneda_pago IN ('ARS', 'USD', 'EUR'))"),
        ("importe_pago", "NUMERIC CHECK (importe_pago IS NULL OR importe_pago > 0)"),
        ("tipo_cambio", "NUMERIC CHECK (tipo_cambio IS NULL OR tipo_cambio > 0)"),
        ("recibo_punto_venta", "INTEGER CHECK (recibo_punto_venta IS NULL OR recibo_punto_venta BETWEEN 1 AND 99999)"),
        ("recibo_numero", "INTEGER CHECK (recibo_numero IS NULL OR recibo_numero BETWEEN 1 AND 99999999)"),
        ("referencia", "TEXT"),
        # Total y moneda del recibo en Tango: obligatorios si se informa el número del recibo. Lo atribuido a ese recibo
        # (pagos previos de todos sus comprobantes + anticipo al inicio con el mismo número) no puede superarlo.
        ("recibo_total", "NUMERIC CHECK (recibo_total IS NULL OR recibo_total > 0)"),
        ("recibo_moneda", "TEXT CHECK (recibo_moneda IS NULL OR recibo_moneda IN ('ARS', 'USD', 'EUR'))"),
    ),
}

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
    # --- Puesta en marcha: estado inicial de cada cliente, validado por un responsable. Mientras está
    #     vigente congela su carga inicial; reabrir = anular con motivo (solo sin operaciones nuevas). ---
    """
    CREATE TABLE IF NOT EXISTS fac_estado_inicial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        corte_id INTEGER NOT NULL REFERENCES fac_cortes (id) ON DELETE RESTRICT,
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        situacion TEXT NOT NULL CHECK (situacion IN ('SIN_ANTECEDENTES', 'CON_ANTECEDENTES')),
        resumen_json TEXT NOT NULL CHECK (json_valid(resumen_json)),
        nota TEXT,""" + _ANULACION + """
    )
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_fac_estado_inicial_cliente
       ON fac_estado_inicial (corte_id, cliente_codigo) WHERE estado = 'VIGENTE'""",
    # --- Antecedentes: certificados ya facturados en Tango antes del inicio y sin saldo a cobrar.
    #     Bajan lo pendiente de facturar del certificado; no son comprobantes, deuda ni cobranzas. ---
    """
    CREATE TABLE IF NOT EXISTS fac_antecedentes_certificado (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        corte_id INTEGER NOT NULL REFERENCES fac_cortes (id) ON DELETE RESTRICT,
        certificado_id INTEGER NOT NULL REFERENCES certificados_cliente (id) ON DELETE RESTRICT,
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        moneda TEXT NOT NULL """ + _MONEDA + """,
        neto_facturado NUMERIC NOT NULL CHECK (neto_facturado > 0),
        factura_tipo TEXT CHECK (factura_tipo IS NULL OR factura_tipo IN ('FC', 'ND')),
        factura_letra TEXT CHECK (factura_letra IS NULL OR factura_letra IN ('A', 'B', 'C', 'E', 'M')),
        factura_punto_venta INTEGER CHECK (factura_punto_venta IS NULL OR factura_punto_venta BETWEEN 1 AND 99999),
        factura_numero INTEGER CHECK (factura_numero IS NULL OR factura_numero BETWEEN 1 AND 99999999),
        fecha_factura TEXT,
        tipo_cambio NUMERIC CHECK (tipo_cambio IS NULL OR tipo_cambio > 0),
        nota TEXT,""" + _ANULACION + """,
        CHECK ((factura_numero IS NULL) = (factura_tipo IS NULL) AND (factura_numero IS NULL) = (factura_letra IS NULL)
               AND (factura_numero IS NULL) = (factura_punto_venta IS NULL))
    )
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_fac_antecedentes_factura
       ON fac_antecedentes_certificado (certificado_id, factura_tipo, factura_letra, factura_punto_venta, factura_numero)
       WHERE estado = 'VIGENTE' AND factura_numero IS NOT NULL""",
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
    CREATE TRIGGER IF NOT EXISTS trg_fac_saldo_inicial_cliente_validado
    BEFORE INSERT ON fac_documentos
    WHEN NEW.origen = 'SALDO_INICIAL' AND EXISTS (SELECT 1 FROM fac_estado_inicial WHERE corte_id = NEW.corte_id
         AND cliente_codigo = NEW.cliente_codigo AND estado = 'VIGENTE')
    BEGIN SELECT RAISE(ABORT, 'FAC_ESTADO_INICIAL: el estado inicial del cliente está validado; reábralo para modificarlo'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_anticipo_cliente_validado
    BEFORE INSERT ON fac_recibos
    WHEN NEW.origen = 'SALDO_INICIAL' AND EXISTS (SELECT 1 FROM fac_estado_inicial WHERE corte_id = NEW.corte_id
         AND cliente_codigo = NEW.cliente_codigo AND estado = 'VIGENTE')
    BEGIN SELECT RAISE(ABORT, 'FAC_ESTADO_INICIAL: el estado inicial del cliente está validado; reábralo para modificarlo'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_fac_antecedente_cliente_validado
    BEFORE INSERT ON fac_antecedentes_certificado
    WHEN EXISTS (SELECT 1 FROM fac_estado_inicial WHERE corte_id = NEW.corte_id AND cliente_codigo = NEW.cliente_codigo
         AND estado = 'VIGENTE')
    BEGIN SELECT RAISE(ABORT, 'FAC_ESTADO_INICIAL: el estado inicial del cliente está validado; reábralo para modificarlo'); END
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
        ("fac_cancelaciones_corte", ("documento_id", "importe", "criterio", "revision_de_id", "nota", "fecha_pago", "moneda_pago",
                                     "importe_pago", "tipo_cambio", "recibo_punto_venta", "recibo_numero", "referencia",
                                     "recibo_total", "recibo_moneda")),
        ("fac_estado_inicial", ("corte_id", "cliente_codigo", "situacion", "resumen_json", "nota")),
        ("fac_antecedentes_certificado", ("corte_id", "certificado_id", "cliente_codigo", "moneda", "neto_facturado", "factura_tipo",
                                          "factura_letra", "factura_punto_venta", "factura_numero", "fecha_factura", "tipo_cambio",
                                          "nota")),
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
    """Crea las estructuras fac_*. Solo agrega; no modifica tablas ni filas existentes.

    Orden: tablas e índices, columnas nuevas de tablas fac_ (si faltan) y por último los triggers,
    que se recrean en cada arranque porque pueden referirse a esas columnas.
    """
    triggers = []
    for sentencia in FAC_DDL:
        if re.search(r"CREATE TRIGGER IF NOT EXISTS (\w+)", sentencia):
            triggers.append(sentencia)
        else:
            conn.execute(sentencia)
    for tabla, columnas in FAC_COLUMNAS.items():
        existentes = {r[1] for r in conn.execute(f'PRAGMA table_info("{tabla}")')}
        for nombre, definicion in columnas:
            if nombre not in existentes:
                conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {definicion}")
    definidos = set()
    for sentencia in triggers:
        nombre = re.search(r"CREATE TRIGGER IF NOT EXISTS (\w+)", sentencia).group(1)
        definidos.add(nombre)
        conn.execute(f"DROP TRIGGER IF EXISTS {nombre}")
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


# ============================================================================
# Servicio
# ============================================================================

CENT = Decimal("0.01")
CERO = Decimal("0")
ALICUOTAS = {"27": Decimal("0.27"), "21": Decimal("0.21"), "10.5": Decimal("0.105")}
NO_GRAVADAS = {"0": "exento", "NG": "no_gravado"}


class FacError(ValueError):
    def __init__(self, codigo, mensaje):
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo


def dec(valor):
    return CERO if valor is None or valor == "" else Decimal(str(valor))


def r2(valor):
    return dec(valor).quantize(CENT, rounding=ROUND_HALF_UP)


def _num(valor):
    """Decimal -> str para guardar en columnas NUMERIC sin pasar por float."""
    return str(r2(valor))


# ---------------------------------------------------------------- configuración y auditoría

def leer_config(conn, clave):
    fila = conn.execute("SELECT valor_json FROM fac_config WHERE clave = ?", (clave,)).fetchone()
    return json.loads(fila[0]) if fila else None


def guardar_config(conn, clave, valor, usuario, ahora, es_responsable):
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo cambia la configuración")
    anterior = leer_config(conn, clave)
    conn.execute(
        "INSERT INTO fac_config (clave, valor_json, actualizado_por, actualizado_en) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor_json = excluded.valor_json, "
        "actualizado_por = excluded.actualizado_por, actualizado_en = excluded.actualizado_en",
        (clave, json.dumps(valor), usuario, ahora),
    )
    evento(conn, "CONFIG", None, "CONFIG_CAMBIADA", usuario, ahora, {"clave": clave, "antes": anterior, "despues": valor})


def evento(conn, entidad, entidad_id, nombre, usuario, ahora, detalle=None):
    conn.execute(
        "INSERT INTO fac_eventos (entidad, entidad_id, evento, usuario, fecha, detalle_json) VALUES (?, ?, ?, ?, ?, ?)",
        (entidad, entidad_id, nombre, usuario, ahora, json.dumps(detalle, default=str) if detalle is not None else None),
    )


def validar_adjuntos(conn, entidad_cfg, adjuntos):
    """Los adjuntos son opcionales salvo que la configuración los exija para esa entidad."""
    requeridos = leer_config(conn, "adjuntos_requeridos") or {}
    if requeridos.get(entidad_cfg) and not adjuntos:
        raise FacError("ADJUNTO_REQUERIDO", f"la configuración exige un adjunto para {entidad_cfg}")
    almacenamiento = leer_config(conn, "adjuntos_almacenamiento") or "ENLACE"
    for adj in adjuntos:
        if adj.get("ruta") and almacenamiento != "DISCO":
            raise FacError("ADJUNTO_ALMACENAMIENTO", "la subida de archivos está deshabilitada; use un enlace externo")
        if not adj.get("url_externa") and not adj.get("ruta"):
            raise FacError("ADJUNTO_INVALIDO", "el adjunto necesita un enlace o un archivo")


def _guardar_adjuntos(conn, entidad, entidad_id, adjuntos, usuario, ahora):
    for adj in adjuntos:
        conn.execute(
            "INSERT INTO fac_adjuntos (entidad, entidad_id, tipo, nombre, ruta, sha256, url_externa, creado_por, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entidad, entidad_id, adj.get("tipo", "COMPROBANTE"), adj.get("nombre") or adj.get("url_externa"),
             adj.get("ruta"), adj.get("sha256"), adj.get("url_externa"), usuario, ahora),
        )


def adjuntar(conn, entidad, entidad_id, adjunto, usuario, ahora):
    """Agregar un adjunto después del alta (siempre permitido, también en registros ya vigentes)."""
    validar_adjuntos(conn, None, [adjunto])
    _guardar_adjuntos(conn, entidad, entidad_id, [adjunto], usuario, ahora)
    evento(conn, entidad, entidad_id, "ADJUNTO_AGREGADO", usuario, ahora, adjunto)


# ---------------------------------------------------------------- cálculos puros

def calcular_importes(lineas, tributos=(), total_pdf=None, tolerancia=Decimal("0.05")):
    """IVA por alícuota sobre el neto agrupado, tributos y control opcional contra el total del PDF."""
    netos = {}
    for linea in lineas:
        neto = r2(linea["neto"])
        if neto <= 0:
            raise FacError("LINEA_INVALIDA", "el neto de cada línea debe ser positivo")
        alic = str(linea["alicuota_iva"])
        if alic not in ALICUOTAS and alic not in NO_GRAVADAS:
            raise FacError("ALICUOTA_INVALIDA", f"alícuota de IVA desconocida: {alic}")
        netos[alic] = netos.get(alic, CERO) + neto
    iva = {a: r2(netos[a] * ALICUOTAS[a]) for a in ALICUOTAS if a in netos}
    trib = []
    for t in tributos:
        if t.get("tipo") == "REDONDEO":
            raise FacError("REDONDEO_MANUAL", "el redondeo lo calcula el sistema a partir del total del PDF")
        importe = r2(dec(t["base"]) * dec(t["alicuota"]) / 100) if t.get("base") and t.get("alicuota") else r2(t["importe"])
        if importe <= 0:
            raise FacError("TRIBUTO_INVALIDO", "el importe del tributo debe ser positivo")
        trib.append({**t, "importe": importe})
    res = {
        "neto_gravado": sum((netos[a] for a in ALICUOTAS if a in netos), CERO),
        "exento": netos.get("0", CERO),
        "no_gravado": netos.get("NG", CERO),
        "iva_por_alicuota": iva,
        "iva_total": sum(iva.values(), CERO),
        "tributos": trib,
    }
    res["tributos_total"] = sum((t["importe"] for t in trib), CERO)
    res["total"] = res["neto_gravado"] + res["exento"] + res["no_gravado"] + res["iva_total"] + res["tributos_total"]
    res["redondeo"] = CERO
    if total_pdf not in (None, ""):
        dif = r2(total_pdf) - res["total"]
        if abs(dif) > tolerancia:
            raise FacError("TOTAL_NO_COINCIDE", f"el total calculado {res['total']} difiere del PDF de Tango en {dif}")
        if dif:
            res["redondeo"] = dif
            res["tributos"].append({"tipo": "REDONDEO", "importe": dif})
            res["tributos_total"] += dif
            res["total"] += dif
    return res


def equivalente(importe_recibo, saldo_documento, tipo_cambio):
    """Equivalente en la moneda del documento de un cobro en otra moneda (p. ej. ARS -> USD).

    Si los pesos cubren el saldo con una tolerancia de un centavo de la moneda del documento,
    el equivalente es exactamente el saldo (cancela la factura sin dejar centavos colgados).
    """
    importe_recibo, saldo_documento, tc = r2(importe_recibo), r2(saldo_documento), dec(tipo_cambio)
    if tc <= 0:
        raise FacError("TC_INVALIDO", "el tipo de cambio debe ser positivo")
    if abs(importe_recibo - r2(saldo_documento * tc)) <= r2(CENT * tc):
        return saldo_documento
    return r2(importe_recibo / tc)


def condicion_a_dias(condicion):
    """'30 FF' -> 30; 'Al día' / 'Inmediato' -> 0; otro -> None (se carga a mano)."""
    texto = (condicion or "").strip().upper()
    if texto.split(" ")[0].isdigit():
        return int(texto.split(" ")[0])
    return 0 if texto in ("AL DÍA", "AL DIA", "INMEDIATO", "CONTADO") else None


# ---------------------------------------------------------------- corte

def corte_actual(conn):
    fila = conn.execute("SELECT id, fecha_corte, estado, confirmado_por, confirmado_en FROM fac_cortes").fetchone()
    return {"id": fila[0], "fecha_corte": fila[1], "estado": fila[2], "confirmado_por": fila[3], "confirmado_en": fila[4]} if fila else None


def crear_corte(conn, usuario, ahora, fecha_corte=None, observaciones=None):
    """La fecha puede quedar vacía hasta que Contabilidad la confirme."""
    if corte_actual(conn):
        raise FacError("CORTE_EXISTE", "ya hay un corte creado")
    cur = conn.execute(
        "INSERT INTO fac_cortes (fecha_corte, observaciones, creado_por, creado_en) VALUES (?, ?, ?, ?)",
        (fecha_corte, observaciones, usuario, ahora),
    )
    evento(conn, "CORTE", cur.lastrowid, "CORTE_CREADO", usuario, ahora, {"fecha_corte": fecha_corte})
    return cur.lastrowid


def fijar_fecha_corte(conn, fecha_corte, usuario, ahora):
    corte = corte_actual(conn)
    if not corte or corte["estado"] != "BORRADOR":
        raise FacError("CORTE_CERRADO", "solo se cambia la fecha de un corte en borrador")
    validados = conn.execute("SELECT count(*) FROM fac_estado_inicial WHERE corte_id = ? AND estado = 'VIGENTE'",
                             (corte["id"],)).fetchone()[0]
    if validados:
        raise FacError("CLIENTES_VALIDADOS", f"hay {validados} cliente(s) con estado inicial validado: la fecha de inicio "
                                             "operativo no se cambia sin reabrirlos")
    posteriores = conn.execute(
        "SELECT (SELECT count(*) FROM fac_antecedentes_certificado WHERE estado = 'VIGENTE' AND fecha_factura > ?) + "
        "(SELECT count(*) FROM fac_cancelaciones_corte WHERE estado = 'VIGENTE' AND fecha_pago > ?)", (fecha_corte, fecha_corte)
    ).fetchone()[0]
    if posteriores:
        raise FacError("CORTE_FECHA", f"{posteriores} antecedente(s) o pago(s) previo(s) tienen fecha posterior a {fecha_corte}")
    fuera = conn.execute(
        "SELECT count(*) FROM fac_documentos WHERE origen = 'SALDO_INICIAL' AND estado = 'VIGENTE' AND fecha_emision > ?",
        (fecha_corte,),
    ).fetchone()[0]
    if fuera:
        raise FacError("CORTE_FECHA", f"{fuera} saldo(s) inicial(es) tienen fecha posterior a {fecha_corte}")
    conn.execute("UPDATE fac_cortes SET fecha_corte = ? WHERE id = ?", (fecha_corte, corte["id"]))
    evento(conn, "CORTE", corte["id"], "FECHA_CORTE", usuario, ahora, {"antes": corte["fecha_corte"], "despues": fecha_corte})


def estado_inicial(conn, cliente):
    """Estado inicial VIGENTE (validado) del cliente en el corte actual, o None."""
    corte = corte_actual(conn)
    if not corte:
        return None
    fila = conn.execute("SELECT id, situacion, resumen_json, nota, creado_por, creado_en FROM fac_estado_inicial "
                        "WHERE corte_id = ? AND cliente_codigo = ? AND estado = 'VIGENTE'", (corte["id"], cliente)).fetchone()
    if not fila:
        return None
    return {"id": fila[0], "situacion": fila[1], "resumen": json.loads(fila[2]), "nota": fila[3], "validado_por": fila[4],
            "validado_en": fila[5]}


def _exigir_cliente_abierto(conn, cliente):
    if estado_inicial(conn, cliente):
        raise FacError("CLIENTE_VALIDADO", "el estado inicial del cliente ya está validado: un responsable debe reabrirlo "
                                           "para modificar su carga inicial")


def _exigir_operacion_normal(conn, fecha, cliente):
    """Operaciones nuevas (comprobantes y cobranzas). El seguimiento empieza con los certificados y pedidos que se generan
    en adelante: no hace falta una fecha de corte global. Solo se controlan los clientes con carga inicial (antecedentes):
    la carga debe estar validada por un responsable y la operación ser posterior a la fecha de inicio de esa carga."""
    corte = corte_actual(conn)
    if not corte:
        return
    e = estado_inicial(conn, cliente)
    if e:
        inicio = e["resumen"].get("fecha_inicio") or corte["fecha_corte"]
        if inicio and fecha <= inicio:
            raise FacError("ANTERIOR_AL_CORTE", f"la fecha {fecha} es del inicio de la carga inicial del cliente o anterior ({inicio}); "
                                                "solo puede entrar como saldo inicial o antecedente")
        return
    if not leer_config(conn, "requiere_corte_confirmado"):
        return
    if cliente in _clientes_con_carga_inicial(conn, corte["id"]):
        raise FacError("CLIENTE_SIN_VALIDAR", f"el cliente {cliente} tiene una carga inicial (antecedentes) sin validar: un responsable "
                                              "debe validarla en Puesta en marcha antes de registrar comprobantes o cobranzas nuevos")


def _exigir_saldo_inicial(conn, corte_id, fecha):
    corte = corte_actual(conn)
    if not corte or corte["id"] != corte_id:
        raise FacError("CORTE_INEXISTENTE", "el saldo inicial debe pertenecer al corte vigente")
    if corte["fecha_corte"] and fecha > corte["fecha_corte"]:
        raise FacError("POSTERIOR_AL_CORTE", f"un saldo inicial no puede tener fecha posterior al corte ({corte['fecha_corte']})")
    return corte


# ---------------------------------------------------------------- saldos

def _sum(conn, sql, params):
    return sum((r2(v) for (v,) in conn.execute(sql, params).fetchall()), CERO)


def documento(conn, doc_id):
    conn_row = conn.execute(
        "SELECT id, tipo, cliente_codigo, moneda, total, estado, origen, fecha_emision, fecha_vencimiento, letra, punto_venta, numero "
        "FROM fac_documentos WHERE id = ?", (doc_id,)
    ).fetchone()
    if not conn_row:
        raise FacError("NO_EXISTE", f"comprobante {doc_id} inexistente")
    claves = ("id", "tipo", "cliente_codigo", "moneda", "total", "estado", "origen", "fecha_emision", "fecha_vencimiento",
              "letra", "punto_venta", "numero")
    d = dict(zip(claves, conn_row))
    d["total"] = r2(d["total"])
    return d


def cancelado_antes_corte(conn, doc_id):
    return _sum(conn, "SELECT importe FROM fac_cancelaciones_corte WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,))


def cancelaciones_estimadas(conn, doc_id):
    return _sum(conn, "SELECT importe FROM fac_cancelaciones_corte WHERE documento_id = ? AND estado = 'VIGENTE' "
                      "AND criterio = 'ESTIMADO_ANTIGUEDAD'", (doc_id,))


def saldo_documento(conn, doc_id):
    d = documento(conn, doc_id)
    if d["estado"] != "VIGENTE":
        return CERO
    if d["tipo"] == "NC":
        return d["total"] - _sum(conn, "SELECT importe FROM fac_aplicaciones_nc WHERE nc_id = ? AND estado = 'VIGENTE'", (doc_id,))
    return (d["total"] - cancelado_antes_corte(conn, doc_id)
            - _sum(conn, "SELECT importe FROM fac_aplicaciones_nc WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,))
            - _sum(conn, "SELECT importe_documento FROM fac_imputaciones WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,)))


def a_cuenta_recibo(conn, recibo_id):
    fila = conn.execute("SELECT total, estado FROM fac_recibos WHERE id = ?", (recibo_id,)).fetchone()
    if not fila or fila[1] != "VIGENTE":
        return CERO
    return r2(fila[0]) - _sum(conn, "SELECT importe_recibo FROM fac_imputaciones WHERE recibo_id = ? AND estado = 'VIGENTE'",
                              (recibo_id,))


def saldo_cliente(conn, cliente, moneda):
    """Siempre por moneda: nunca se suman saldos de monedas distintas."""
    total = CERO
    for (doc_id, tipo) in conn.execute(
        "SELECT id, tipo FROM fac_documentos WHERE cliente_codigo = ? AND moneda = ? AND estado = 'VIGENTE'", (cliente, moneda)
    ).fetchall():
        s = saldo_documento(conn, doc_id)
        total += -s if tipo == "NC" else s
    for (rec_id,) in conn.execute(
        "SELECT id FROM fac_recibos WHERE cliente_codigo = ? AND moneda = ? AND estado = 'VIGENTE'", (cliente, moneda)
    ).fetchall():
        total -= a_cuenta_recibo(conn, rec_id)
    return total


def estado_documento(conn, doc_id, hoy):
    d = documento(conn, doc_id)
    if d["estado"] == "ANULADO":
        return "ANULADO"
    saldo = saldo_documento(conn, doc_id)
    if d["tipo"] == "NC":
        return "SIN APLICAR" if saldo == d["total"] else ("APLICADA" if saldo == 0 else "APLICADA PARCIAL")
    if saldo == 0:
        cobros = conn.execute("SELECT count(*) FROM fac_imputaciones WHERE documento_id = ? AND estado = 'VIGENTE'",
                              (doc_id,)).fetchone()[0]
        return "CANCELADA" if cobros or cancelado_antes_corte(conn, doc_id) else "CANCELADA POR NC"
    if d["fecha_vencimiento"] and hoy > d["fecha_vencimiento"]:
        return "VENCIDA"
    return "PENDIENTE" if saldo == d["total"] - cancelado_antes_corte(conn, doc_id) else "PARCIAL"


def antiguedad(conn, moneda, hoy):
    tramos = {"sin vencer": CERO, "1-30": CERO, "31-60": CERO, "61-90": CERO, "+90": CERO}
    for (doc_id, vto) in conn.execute(
        "SELECT id, fecha_vencimiento FROM fac_documentos WHERE moneda = ? AND estado = 'VIGENTE' AND tipo <> 'NC'", (moneda,)
    ).fetchall():
        s = saldo_documento(conn, doc_id)
        if not s:
            continue
        dias = (date.fromisoformat(hoy) - date.fromisoformat(vto)).days
        clave = "sin vencer" if dias <= 0 else "1-30" if dias <= 30 else "31-60" if dias <= 60 else "61-90" if dias <= 90 else "+90"
        tramos[clave] += s
    return tramos


# ---------------------------------------------------------------- pedidos

TIPOS_PEDIDO = ("FACTURA", "NOTA_CREDITO", "NOTA_DEBITO")
TIPO_PEDIDO_DE_DOCUMENTO = {"FC": "FACTURA", "ND": "NOTA_DEBITO", "NC": "NOTA_CREDITO"}
MONEDAS = ("ARS", "USD", "EUR")


def guardar_pedido_borrador(conn, datos, usuario, ahora, pedido_id=None):
    if datos.get("tipo_solicitado") not in TIPOS_PEDIDO:
        raise FacError("TIPO", "tipo de comprobante solicitado inválido")
    if datos.get("moneda") not in MONEDAS:
        raise FacError("MONEDA", "moneda inválida")
    if not datos.get("lineas"):
        raise FacError("LINEAS", "el pedido necesita al menos una línea")
    if not conn.execute("SELECT 1 FROM clientes WHERE codigo = ?", (datos.get("cliente_codigo"),)).fetchone():
        raise FacError("CLIENTE", "cliente inexistente")
    if pedido_id is not None:
        actual = conn.execute("SELECT estado FROM fac_pedidos WHERE id = ?", (pedido_id,)).fetchone()
        if not actual or actual[0] != "BORRADOR":
            raise FacError("PEDIDO_ESTADO", "solo se edita un pedido en borrador")
    cli = conn.execute("SELECT razon_social, cuit FROM clientes WHERE codigo = ?", (datos["cliente_codigo"],)).fetchone()
    lineas = []
    for i, l in enumerate(datos["lineas"], 1):
        sub = r2(dec(l["cantidad"]) * dec(l["precio_unitario"]))
        if l.get("certificado_id"):
            cert = conn.execute("SELECT estado, moneda FROM certificados_cliente WHERE id = ?", (l["certificado_id"],)).fetchone()
            if not cert or cert[0] != "EMITIDO":
                raise FacError("CERTIFICADO", "solo se vinculan certificados EMITIDOS")
            if cert[1] != datos["moneda"]:
                raise FacError("MONEDA", "el certificado está en otra moneda que el pedido")
        lineas.append({**l, "orden": i, "subtotal": sub})
    imp = calcular_importes([{"neto": l["subtotal"], "alicuota_iva": l["alicuota_iva"]} for l in lineas])
    campos = dict(
        tipo_solicitado=datos["tipo_solicitado"], fecha_emision=datos["fecha_emision"], cliente_codigo=datos["cliente_codigo"],
        moneda=datos["moneda"], condicion_pago=datos.get("condicion_pago"), cotizacion=datos.get("cotizacion"),
        dias_vencimiento=datos.get("dias_vencimiento", condicion_a_dias(datos.get("condicion_pago"))),
        orden_compra_cliente_id=datos.get("orden_compra_cliente_id"), oc_texto=datos.get("oc_texto"),
        centro_costo_id=datos.get("centro_costo_id"), seguimiento_cc_id=datos.get("seguimiento_cc_id"),
        tipo_cambio_informar=datos.get("tipo_cambio_informar") or ("TC de la fecha efectiva de pago" if datos["moneda"] != "ARS" else None),
        observaciones=datos.get("observaciones"), subtotal=_num(imp["neto_gravado"] + imp["exento"] + imp["no_gravado"]),
        iva_total=_num(imp["iva_total"]), total=_num(imp["total"]), solicitante_texto=datos.get("solicitante_texto"),
        justificacion_diferencia=datos.get("justificacion_diferencia"), recibe_texto=datos.get("recibe_texto"),
        depto_origen=str(datos.get("depto_origen") or "15"), cliente_domicilio=datos.get("cliente_domicilio"),
        cliente_razon_social=cli[0], cliente_cuit=cli[1],
    )
    if pedido_id is None:
        cols = list(campos) + ["creado_por", "creado_en"]
        cur = conn.execute(f"INSERT INTO fac_pedidos ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [*campos.values(), usuario, ahora])
        pedido_id = cur.lastrowid
    else:
        conn.execute(f"UPDATE fac_pedidos SET {', '.join(c + ' = ?' for c in campos)}, actualizado_por = ?, actualizado_en = ? "
                     "WHERE id = ? AND estado = 'BORRADOR'", [*campos.values(), usuario, ahora, pedido_id])
        conn.execute("DELETE FROM fac_pedido_lineas WHERE pedido_id = ?", (pedido_id,))
    for l in lineas:
        conn.execute(
            "INSERT INTO fac_pedido_lineas (pedido_id, orden, cantidad, detalle, precio_unitario, alicuota_iva, subtotal, certificado_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pedido_id, l["orden"], str(dec(l["cantidad"])), l["detalle"], str(dec(l["precio_unitario"])), l["alicuota_iva"],
             _num(l["subtotal"]), l.get("certificado_id")),
        )
    evento(conn, "PEDIDO", pedido_id, "BORRADOR_GUARDADO", usuario, ahora,
           {"total": imp["total"], "idempotencia": datos.get("idempotencia")})
    return pedido_id


def antecedentes_certificado(conn, certificado_id):
    """Neto ya facturado en Tango antes del inicio operativo (antecedente: no es comprobante ni deuda)."""
    return _sum(conn, "SELECT neto_facturado FROM fac_antecedentes_certificado WHERE certificado_id = ? AND estado = 'VIGENTE'",
                (certificado_id,))


def _facturado_saldo_inicial_cert(conn, certificado_id):
    """Neto de comprobantes de saldo inicial vinculados al certificado (FC/ND suman, NC resta)."""
    filas = conn.execute("SELECT d.tipo, v.neto_asignado FROM fac_documento_vinculos v JOIN fac_documentos d ON d.id = v.documento_id "
                         "WHERE v.certificado_id = ? AND d.estado = 'VIGENTE' AND d.origen = 'SALDO_INICIAL'", (certificado_id,)).fetchall()
    return sum(((-r2(n)) if t == "NC" else r2(n) for t, n in filas), CERO)


def _cert_comprometido(conn, certificado_id, excluir_pedido_id):
    """Neto ya comprometido del certificado: pedidos de FACTURA emitidos (no anulados), antecedentes facturados antes
    del inicio y comprobantes de saldo inicial vinculados. Evita volver a pedir lo que ya se facturó en Tango."""
    return (_sum(conn, "SELECT l.subtotal FROM fac_pedido_lineas l JOIN fac_pedidos p ON p.id = l.pedido_id "
                       "WHERE l.certificado_id = ? AND p.estado = 'EMITIDO' AND p.tipo_solicitado = 'FACTURA' AND p.id <> ?",
                 (certificado_id, excluir_pedido_id))
            + antecedentes_certificado(conn, certificado_id) + _facturado_saldo_inicial_cert(conn, certificado_id))


def _validar_certificados_pedido(conn, pedido_id):
    p = conn.execute("SELECT tipo_solicitado, justificacion_diferencia FROM fac_pedidos WHERE id = ?", (pedido_id,)).fetchone()
    por_cert = {}
    for (cert_id, sub) in conn.execute("SELECT certificado_id, subtotal FROM fac_pedido_lineas WHERE pedido_id = ? "
                                       "AND certificado_id IS NOT NULL", (pedido_id,)).fetchall():
        por_cert[cert_id] = por_cert.get(cert_id, CERO) + r2(sub)
    diferencias = []
    for cert_id, sub in por_cert.items():
        total = r2(conn.execute("SELECT total_a_facturar FROM certificados_cliente WHERE id = ?", (cert_id,)).fetchone()[0])
        if p[0] == "FACTURA" and _cert_comprometido(conn, cert_id, pedido_id) + sub > total:
            raise FacError("CERTIFICADO_EXCEDE", f"los pedidos vigentes del certificado {cert_id} superarían su total a facturar ({total})")
        if sub != total:
            diferencias.append({"certificado_id": cert_id, "pedido": sub, "total_a_facturar": total})
    if diferencias and len((p[1] or "").strip()) < 10:
        raise FacError("JUSTIFICACION", "el importe de un certificado no coincide con su total a facturar: cargue la justificación")
    return diferencias


def emitir_pedido(conn, pedido_id, usuario, ahora, generar_pdf=None, adjuntos=()):
    """Asigna AAAAMMDD-n, congela el snapshot y el SHA-256 del PDF. generar_pdf(snapshot) -> bytes."""
    fila = conn.execute("SELECT estado, fecha_emision FROM fac_pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not fila or fila[0] != "BORRADOR":
        raise FacError("PEDIDO_ESTADO", "solo se emite un pedido en borrador")
    validar_adjuntos(conn, "PEDIDO", adjuntos)
    diferencias = _validar_certificados_pedido(conn, pedido_id)
    prefijo = fila[1].replace("-", "")
    usados = [int(n.split("-")[1]) for (n,) in conn.execute(
        "SELECT numero_solicitud FROM fac_pedidos WHERE numero_solicitud LIKE ?", (prefijo + "-%",))]
    numero = f"{prefijo}-{max(usados, default=0) + 1}"
    cab = conn.execute("SELECT * FROM fac_pedidos WHERE id = ?", (pedido_id,))
    snapshot = dict(zip([c[0] for c in cab.description], cab.fetchone()))
    lin = conn.execute("SELECT * FROM fac_pedido_lineas WHERE pedido_id = ? ORDER BY orden", (pedido_id,))
    snapshot["lineas"] = [dict(zip([c[0] for c in lin.description], r)) for r in lin.fetchall()]
    snapshot["numero_solicitud"] = numero
    snapshot_json = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    # El PDF se genera desde el snapshot ya serializado: regenerarlo después da el mismo archivo y el mismo SHA-256.
    contenido = generar_pdf(json.loads(snapshot_json)) if generar_pdf else snapshot_json.encode()
    conn.execute(
        "UPDATE fac_pedidos SET estado = 'EMITIDO', numero_solicitud = ?, snapshot_json = ?, pdf_sha256 = ?, "
        "emitido_por = ?, emitido_en = ? WHERE id = ?",
        (numero, snapshot_json, hashlib.sha256(contenido).hexdigest(), usuario, ahora, pedido_id),
    )
    _guardar_adjuntos(conn, "PEDIDO", pedido_id, adjuntos, usuario, ahora)
    evento(conn, "PEDIDO", pedido_id, "EMITIDO", usuario, ahora, {"numero": numero, "diferencias_certificado": diferencias})
    return numero


def anular_pedido(conn, pedido_id, motivo, usuario, ahora, es_responsable):
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo puede anular")
    if len((motivo or "").strip()) < 20:
        raise FacError("MOTIVO", "el motivo de anulación debe tener al menos 20 caracteres")
    fila = conn.execute("SELECT estado FROM fac_pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not fila or fila[0] != "EMITIDO":
        raise FacError("PEDIDO_ESTADO", "solo se anula un pedido emitido (un borrador se descarta)")
    vinculados = conn.execute("SELECT count(*) FROM fac_documento_vinculos v JOIN fac_documentos d ON d.id = v.documento_id "
                              "WHERE v.pedido_id = ? AND d.estado = 'VIGENTE'", (pedido_id,)).fetchone()[0]
    if vinculados:
        raise FacError("PEDIDO_FACTURADO", "el pedido tiene comprobantes vigentes vinculados: anule primero esos comprobantes")
    conn.execute("UPDATE fac_pedidos SET estado = 'ANULADO', anulado_por = ?, anulado_en = ?, motivo_anulacion = ? WHERE id = ?",
                 (usuario, ahora, motivo, pedido_id))
    evento(conn, "PEDIDO", pedido_id, "ANULADO", usuario, ahora, {"motivo": motivo})


def descartar_pedido(conn, pedido_id, usuario, ahora):
    fila = conn.execute("SELECT estado FROM fac_pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not fila or fila[0] != "BORRADOR":
        raise FacError("PEDIDO_ESTADO", "solo se descarta un pedido en borrador")
    conn.execute("DELETE FROM fac_pedido_lineas WHERE pedido_id = ?", (pedido_id,))
    conn.execute("DELETE FROM fac_pedidos WHERE id = ?", (pedido_id,))
    evento(conn, "PEDIDO", pedido_id, "BORRADOR_DESCARTADO", usuario, ahora)


def facturado_pedido(conn, pedido_id):
    return _sum(conn, "SELECT v.neto_asignado FROM fac_documento_vinculos v JOIN fac_documentos d ON d.id = v.documento_id "
                      "WHERE v.pedido_id = ? AND d.estado = 'VIGENTE'", (pedido_id,))


def pendiente_pedido(conn, pedido_id):
    sub = conn.execute("SELECT subtotal FROM fac_pedidos WHERE id = ?", (pedido_id,)).fetchone()[0]
    return r2(sub) - facturado_pedido(conn, pedido_id)


def estado_facturacion_pedido(conn, pedido_id):
    fila = conn.execute("SELECT estado, subtotal FROM fac_pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if fila[0] != "EMITIDO":
        return fila[0]
    facturado = facturado_pedido(conn, pedido_id)
    if facturado <= 0:
        return "PENDIENTE DE FACTURAR"
    return "FACTURADO" if facturado >= r2(fila[1]) else "FACTURADO PARCIAL"


def facturado_certificado(conn, certificado_id):
    """Σ neto asignado por FC/ND − Σ neto asignado por NC (comprobantes vigentes)."""
    filas = conn.execute("SELECT d.tipo, v.neto_asignado FROM fac_documento_vinculos v JOIN fac_documentos d ON d.id = v.documento_id "
                         "WHERE v.certificado_id = ? AND d.estado = 'VIGENTE'", (certificado_id,)).fetchall()
    return sum(((-r2(n)) if t == "NC" else r2(n) for t, n in filas), CERO)


def pendiente_certificado(conn, certificado_id):
    total = conn.execute("SELECT total_a_facturar FROM certificados_cliente WHERE id = ?", (certificado_id,)).fetchone()[0]
    return r2(total) - facturado_certificado(conn, certificado_id) - antecedentes_certificado(conn, certificado_id)


# ---------------------------------------------------------------- comprobantes

def registrar_documento(conn, datos, usuario, ahora, adjuntos=()):
    tipo, moneda, origen = datos["tipo"], datos["moneda"], datos.get("origen", "NORMAL")
    if not conn.execute("SELECT 1 FROM clientes WHERE codigo = ?", (datos["cliente_codigo"],)).fetchone():
        raise FacError("CLIENTE", "cliente inexistente")
    if origen == "NORMAL":
        _exigir_operacion_normal(conn, datos["fecha_emision"], datos["cliente_codigo"])
    else:
        _exigir_saldo_inicial(conn, datos.get("corte_id"), datos["fecha_emision"])
        _exigir_cliente_abierto(conn, datos["cliente_codigo"])
    validar_adjuntos(conn, tipo, adjuntos)
    if conn.execute("SELECT 1 FROM fac_documentos WHERE tipo = ? AND letra = ? AND punto_venta = ? AND numero = ? "
                    "AND estado = 'VIGENTE'", (tipo, datos["letra"], datos["punto_venta"], datos["numero"])).fetchone():
        raise FacError("DUPLICADO", "ya existe un comprobante vigente con ese tipo, letra, punto de venta y número")
    if conn.execute("SELECT 1 FROM fac_antecedentes_certificado WHERE factura_tipo = ? AND factura_letra = ? AND factura_punto_venta = ? "
                    "AND factura_numero = ? AND estado = 'VIGENTE'", (tipo, datos["letra"], datos["punto_venta"], datos["numero"])).fetchone():
        raise FacError("DUPLICADO", "ese comprobante ya figura como antecedente facturado antes del inicio operativo")
    if datos.get("pagos_previos") and (origen != "SALDO_INICIAL" or tipo == "NC"):
        raise FacError("PAGO_PREVIO", "los pagos previos al inicio solo se cargan en FC/ND de saldo inicial")
    tol = dec(leer_config(conn, "tolerancia_redondeo") or "0.05")
    imp = calcular_importes(datos["lineas"], datos.get("tributos", ()), datos.get("total_pdf"), tol)
    neto = imp["neto_gravado"] + imp["exento"] + imp["no_gravado"]
    vinculos = datos.get("vinculos", ())
    for clave in ("pedido_id", "certificado_id"):
        asignado = sum((r2(v["neto_asignado"]) for v in vinculos if v.get(clave)), CERO)
        if asignado > neto:
            raise FacError("VINCULO_EXCEDE", f"los vínculos a {clave[:-3]}s suman {asignado} y el neto es {neto}")
    excedidos = []
    for v in vinculos:
        if v.get("pedido_id"):
            p = conn.execute("SELECT moneda, cliente_codigo, estado, tipo_solicitado FROM fac_pedidos WHERE id = ?",
                             (v["pedido_id"],)).fetchone()
            if not p or p[2] != "EMITIDO":
                raise FacError("PEDIDO", "solo se vinculan pedidos emitidos")
            if p[0] != moneda or p[1] != datos["cliente_codigo"]:
                raise FacError("MONEDA", "el pedido vinculado tiene otra moneda o cliente")
            if p[3] != TIPO_PEDIDO_DE_DOCUMENTO[tipo]:
                raise FacError("TIPO_PEDIDO", f"un comprobante {tipo} se vincula a pedidos de {TIPO_PEDIDO_DE_DOCUMENTO[tipo]}")
            pendiente = pendiente_pedido(conn, v["pedido_id"])
        else:
            c = conn.execute("SELECT moneda, estado FROM certificados_cliente WHERE id = ?", (v["certificado_id"],)).fetchone()
            if not c or c[1] != "EMITIDO" or c[0] != moneda:
                raise FacError("MONEDA", "el certificado vinculado no está emitido o tiene otra moneda")
            pendiente = pendiente_certificado(conn, v["certificado_id"]) if tipo != "NC" else None
        if pendiente is not None and r2(v["neto_asignado"]) > pendiente:
            excedidos.append({**v, "pendiente": pendiente})
    if excedidos and not datos.get("confirmar_excedente"):
        raise FacError("EXCEDE_PENDIENTE", "el neto asignado supera lo pendiente de facturar del pedido o certificado: "
                                           "confírmelo para registrarlo (queda en el historial)")
    pagos, en_carga = [], {}
    for p in datos.get("pagos_previos") or ():
        importe, pago = _normalizar_pago_previo(conn, datos["cliente_codigo"], moneda, p, en_carga)
        pagos.append((importe, pago))
    if pagos and sum((i for i, _ in pagos), CERO) >= imp["total"]:
        raise FacError("CANCELACION", "los pagos previos cubren todo el comprobante: una factura cobrada antes del inicio operativo no se carga como saldo inicial: si corresponde a un certificado, regístrela como antecedente del certificado (para que no vuelva a figurar pendiente); si no tiene certificado, no se registra")
    cli = conn.execute("SELECT razon_social, cuit FROM clientes WHERE codigo = ?", (datos["cliente_codigo"],)).fetchone()
    cur = conn.execute(
        "INSERT INTO fac_documentos (tipo, letra, punto_venta, numero, origen, corte_id, motivo_alta_post_corte, cliente_codigo, "
        "cliente_razon_social, cliente_cuit, fecha_emision, fecha_vencimiento, moneda, tipo_cambio_emision, condicion_pago, detalle, "
        "neto_gravado, exento, no_gravado, iva_total, tributos_total, total, total_pdf_tango, motivo_codigo, motivo_detalle, "
        "documento_origen_id, orden_compra_cliente_id, centro_costo_id, reemplaza_a_id, nota, creado_por, creado_en) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tipo, datos["letra"], datos["punto_venta"], datos["numero"], origen, datos.get("corte_id"),
         datos.get("motivo_alta_post_corte"), datos["cliente_codigo"], cli[0], cli[1], datos["fecha_emision"],
         datos.get("fecha_vencimiento"), moneda, datos.get("tipo_cambio_emision"), datos.get("condicion_pago"), datos.get("detalle"),
         _num(imp["neto_gravado"]), _num(imp["exento"]), _num(imp["no_gravado"]), _num(imp["iva_total"]),
         _num(imp["tributos_total"]), _num(imp["total"]), datos.get("total_pdf"), datos.get("motivo_codigo"),
         datos.get("motivo_detalle"), datos.get("documento_origen_id"), datos.get("orden_compra_cliente_id"),
         datos.get("centro_costo_id"), datos.get("reemplaza_a_id"), datos.get("nota"), usuario, ahora),
    )
    doc_id = cur.lastrowid
    for l in datos["lineas"]:
        conn.execute("INSERT INTO fac_documento_lineas (documento_id, descripcion, alicuota_iva, neto) VALUES (?, ?, ?, ?)",
                     (doc_id, l.get("descripcion"), str(l["alicuota_iva"]), _num(l["neto"])))
    for t in imp["tributos"]:
        conn.execute("INSERT INTO fac_documento_tributos (documento_id, tipo, jurisdiccion, base, alicuota, importe) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (doc_id, t["tipo"], t.get("jurisdiccion"), t.get("base"), t.get("alicuota"),
                                                   _num(t["importe"])))
    for v in vinculos:
        conn.execute("INSERT INTO fac_documento_vinculos (documento_id, pedido_id, certificado_id, neto_asignado) VALUES (?, ?, ?, ?)",
                     (doc_id, v.get("pedido_id"), v.get("certificado_id"), _num(v["neto_asignado"])))
    if origen == "SALDO_INICIAL" and datos.get("cancelado_antes_corte"):
        c = datos["cancelado_antes_corte"]
        _insertar_cancelacion(conn, doc_id, c["importe"], c.get("criterio", "SEGUN_TANGO"), c.get("nota"), usuario, ahora)
    for (importe, pago), p in zip(pagos, datos.get("pagos_previos") or ()):
        _insertar_cancelacion(conn, doc_id, importe, "SEGUN_TANGO", p.get("nota") or "Pago previo al inicio operativo según Tango",
                              usuario, ahora, pago=pago)
    _guardar_adjuntos(conn, "DOCUMENTO", doc_id, adjuntos, usuario, ahora)
    evento(conn, "DOCUMENTO", doc_id, "REGISTRADO", usuario, ahora,
           {"total": imp["total"], "redondeo": imp["redondeo"], "origen": origen, "vinculos": list(vinculos),
            "excedidos_confirmados": excedidos, "idempotencia": datos.get("idempotencia")})
    return doc_id


def advertencias_documento(conn, datos):
    """Avisos que no bloquean: posible duplicado (mismo cliente, tipo, moneda y total entre vigentes)."""
    avisos = []
    tol = dec(leer_config(conn, "tolerancia_redondeo") or "0.05")
    imp = calcular_importes(datos["lineas"], datos.get("tributos", ()), datos.get("total_pdf"), tol)
    for (doc_id, pv, num) in conn.execute(
        "SELECT id, punto_venta, numero FROM fac_documentos WHERE cliente_codigo = ? AND tipo = ? AND moneda = ? "
        "AND estado = 'VIGENTE' AND abs(total - ?) < 0.005", (datos["cliente_codigo"], datos["tipo"], datos["moneda"],
                                                             float(imp["total"]))).fetchall():
        avisos.append({"codigo": "POSIBLE_DUPLICADO", "documento_id": doc_id,
                       "mensaje": f"Ya hay un {datos['tipo']} {pv}-{num} del mismo cliente por el mismo total"})
    return imp, avisos


def aplicar_nc(conn, nc_id, documento_id, importe, usuario, ahora):
    nc, dest, importe = documento(conn, nc_id), documento(conn, documento_id), r2(importe)
    if nc["tipo"] != "NC" or dest["tipo"] == "NC":
        raise FacError("APLICACION", "una NC se aplica a una FC o ND")
    if nc["estado"] != "VIGENTE" or dest["estado"] != "VIGENTE":
        raise FacError("APLICACION", "la NC y el destino deben estar vigentes")
    if nc["moneda"] != dest["moneda"]:
        raise FacError("MONEDA", "una NC solo se aplica a comprobantes de su misma moneda")
    if nc["cliente_codigo"] != dest["cliente_codigo"]:
        raise FacError("CLIENTE", "la NC y el comprobante son de clientes distintos")
    if importe <= 0 or importe > saldo_documento(conn, nc_id):
        raise FacError("EXCEDE_NC", "el importe supera lo disponible de la NC")
    if importe > saldo_documento(conn, documento_id):
        raise FacError("EXCEDE_SALDO", "el importe supera el saldo del comprobante")
    cur = conn.execute("INSERT INTO fac_aplicaciones_nc (nc_id, documento_id, importe, creado_por, creado_en) VALUES (?, ?, ?, ?, ?)",
                       (nc_id, documento_id, _num(importe), usuario, ahora))
    evento(conn, "DOCUMENTO", documento_id, "NC_APLICADA", usuario, ahora, {"nc_id": nc_id, "importe": importe})
    return cur.lastrowid


# ---------------------------------------------------------------- cobranzas

def sugerir_asignacion(conn, cliente, moneda_recibo, importe, tipo_cambio=None):
    """Sugerencia por vencimiento (más antiguo primero). Solo sugiere; el usuario la confirma o edita."""
    resto, sugerencia = r2(importe), []
    for (doc_id, moneda) in conn.execute(
        "SELECT id, moneda FROM fac_documentos WHERE cliente_codigo = ? AND estado = 'VIGENTE' AND tipo <> 'NC' "
        "ORDER BY fecha_vencimiento, fecha_emision, id", (cliente,)
    ).fetchall():
        if resto <= 0:
            break
        if moneda != moneda_recibo and not tipo_cambio:
            continue
        saldo = saldo_documento(conn, doc_id)
        if saldo <= 0:
            continue
        necesario = saldo if moneda == moneda_recibo else r2(saldo * dec(tipo_cambio))
        asignar = min(resto, necesario)
        sugerencia.append({"documento_id": doc_id, "importe_recibo": asignar,
                           "tipo_cambio": tipo_cambio if moneda != moneda_recibo else None})
        resto -= asignar
    return sugerencia


def _imputar(conn, rec_id, cliente, moneda, fecha_pago, imputaciones, usuario, ahora):
    """Asigna importes del recibo a FC/ND del mismo cliente; entre monedas usa el TC de la fecha de pago."""
    detalle = []
    for imp in imputaciones:
        d = documento(conn, imp["documento_id"])
        if d["tipo"] == "NC" or d["estado"] != "VIGENTE" or d["cliente_codigo"] != cliente:
            raise FacError("IMPUTACION", "se imputa solo a FC/ND vigentes del mismo cliente")
        saldo, importe_recibo = saldo_documento(conn, d["id"]), r2(imp["importe_recibo"])
        if d["moneda"] == moneda:
            tc = fecha_tc = None
            importe_doc = importe_recibo
        else:
            if not imp.get("tipo_cambio"):
                raise FacError("TC_REQUERIDO", "el comprobante está en otra moneda: cargue el TC de la fecha de pago")
            tc, fecha_tc = dec(imp["tipo_cambio"]), imp.get("fecha_tipo_cambio") or fecha_pago
            importe_doc = equivalente(importe_recibo, saldo, tc)
        if importe_doc > saldo:
            raise FacError("EXCEDE_SALDO", f"el equivalente {importe_doc} supera el saldo {saldo} del comprobante")
        conn.execute(
            "INSERT INTO fac_imputaciones (recibo_id, documento_id, importe_recibo, tipo_cambio, fecha_tipo_cambio, importe_documento, "
            "saldo_documento_antes, saldo_documento_despues, creado_por, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rec_id, d["id"], _num(importe_recibo), str(tc) if tc else None, fecha_tc, _num(importe_doc), _num(saldo),
             _num(saldo - importe_doc), usuario, ahora),
        )
        detalle.append({"documento_id": d["id"], "importe_recibo": importe_recibo, "tc": tc, "importe_documento": importe_doc,
                        "saldo_despues": saldo - importe_doc})
    return detalle


def registrar_recibo(conn, datos, usuario, ahora, adjuntos=()):
    origen = datos.get("origen", "NORMAL")
    if not conn.execute("SELECT 1 FROM clientes WHERE codigo = ?", (datos["cliente_codigo"],)).fetchone():
        raise FacError("CLIENTE", "cliente inexistente")
    if origen == "NORMAL":
        _exigir_operacion_normal(conn, datos["fecha_pago"], datos["cliente_codigo"])
    else:
        _exigir_saldo_inicial(conn, datos.get("corte_id"), datos["fecha_pago"])
        _exigir_cliente_abierto(conn, datos["cliente_codigo"])
        if datos.get("imputaciones"):
            raise FacError("ANTICIPO", "un anticipo al corte entra a cuenta, sin asignar")
    validar_adjuntos(conn, "RECIBO", adjuntos)
    if conn.execute("SELECT 1 FROM fac_recibos WHERE punto_venta = ? AND numero = ? AND estado = 'VIGENTE'",
                    (datos["punto_venta"], datos["numero"])).fetchone():
        raise FacError("DUPLICADO", "ya existe un recibo vigente con ese número")
    # Un recibo de Tango ya cargado como pago previo al inicio no vuelve a entrar como cobranza nueva. La parte que
    # quedó a cuenta puede cargarse como anticipo al corte del mismo cliente.
    previo = conn.execute("SELECT d.cliente_codigo FROM fac_cancelaciones_corte c JOIN fac_documentos d ON d.id = c.documento_id "
                          "WHERE c.recibo_punto_venta = ? AND c.recibo_numero = ? AND c.estado = 'VIGENTE'",
                          (datos["punto_venta"], datos["numero"])).fetchone()
    if previo and (origen == "NORMAL" or previo[0] != datos["cliente_codigo"]):
        raise FacError("DUPLICADO", "ese recibo ya figura como pago previo al inicio operativo: no se registra como cobranza nueva")
    if previo:  # anticipo al inicio por la parte a cuenta del mismo recibo: entre ambos no superan su total en Tango
        declarados, pagado, _ = _recibo_previo(conn, datos["punto_venta"], datos["numero"])
        total_anticipo = sum((r2(v["importe"]) for v in datos["valores"]), CERO)
        for total_recibo, moneda_recibo in declarados:
            if total_recibo is None:
                continue
            if moneda_recibo != datos["moneda"]:
                raise FacError("RECIBO_MONEDA", f"el recibo figura en {moneda_recibo} en los pagos previos")
            if pagado + total_anticipo > total_recibo:
                raise FacError("RECIBO_EXCEDE", f"pagos previos ({pagado}) más el anticipo ({total_anticipo}) superan el total del "
                                                f"recibo en Tango ({total_recibo} {moneda_recibo})")
    total = sum((r2(v["importe"]) for v in datos["valores"]), CERO)
    imputaciones = datos.get("imputaciones", ())
    if sum((r2(i["importe_recibo"]) for i in imputaciones), CERO) > total:
        raise FacError("EXCEDE_RECIBO", "lo asignado supera el total del recibo")
    cur = conn.execute(
        "INSERT INTO fac_recibos (origen, corte_id, punto_venta, numero, cliente_codigo, fecha_pago, moneda, total, observaciones, "
        "creado_por, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (origen, datos.get("corte_id"), datos["punto_venta"], datos["numero"], datos["cliente_codigo"], datos["fecha_pago"],
         datos["moneda"], _num(total), datos.get("observaciones"), usuario, ahora),
    )
    rec_id = cur.lastrowid
    for v in datos["valores"]:
        conn.execute("INSERT INTO fac_recibo_valores (recibo_id, medio, importe, banco_emisor, referencia, fecha_acreditacion, "
                     "numero_certificado_retencion) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (rec_id, v["medio"], _num(v["importe"]), v.get("banco_emisor"), v.get("referencia"),
                      v.get("fecha_acreditacion"), v.get("numero_certificado_retencion")))
    detalle = _imputar(conn, rec_id, datos["cliente_codigo"], datos["moneda"], datos["fecha_pago"], imputaciones, usuario, ahora)
    _guardar_adjuntos(conn, "RECIBO", rec_id, adjuntos, usuario, ahora)
    evento(conn, "RECIBO", rec_id, "REGISTRADO", usuario, ahora,
           {"total": total, "imputaciones": detalle, "idempotencia": datos.get("idempotencia")})
    return rec_id


# ---------------------------------------------------------------- anulaciones

def _anular_fila(conn, tabla, fila_id, motivo, usuario, ahora):
    conn.execute(f"UPDATE {tabla} SET estado = 'ANULADO', anulado_por = ?, anulado_en = ?, motivo_anulacion = ? "
                 "WHERE id = ? AND estado = 'VIGENTE'", (usuario, ahora, motivo, fila_id))


def anular_documento(conn, doc_id, motivo, usuario, ahora, es_responsable):
    """Error de carga: el comprobante queda visible y deja de sumar. Sus aplicaciones, cobros y
    cancelaciones al corte se anulan en cascada; los recibos quedan y su importe vuelve a «a cuenta»."""
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo puede anular")
    if len((motivo or "").strip()) < 20:
        raise FacError("MOTIVO", "el motivo de anulación debe tener al menos 20 caracteres")
    d = documento(conn, doc_id)
    if d["origen"] == "SALDO_INICIAL":
        _exigir_cliente_abierto(conn, d["cliente_codigo"])
    cascada = {"fac_aplicaciones_nc": ("SELECT id FROM fac_aplicaciones_nc WHERE (nc_id = ? OR documento_id = ?) "
                                       "AND estado = 'VIGENTE'", (doc_id, doc_id)),
               "fac_imputaciones": ("SELECT id FROM fac_imputaciones WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,)),
               "fac_cancelaciones_corte": ("SELECT id FROM fac_cancelaciones_corte WHERE documento_id = ? AND estado = 'VIGENTE'",
                                           (doc_id,))}
    anuladas = {}
    for tabla, (sql, params) in cascada.items():
        ids = [r[0] for r in conn.execute(sql, params).fetchall()]
        for i in ids:
            _anular_fila(conn, tabla, i, f"Cascada por anulación del comprobante {doc_id}: {motivo}", usuario, ahora)
        anuladas[tabla] = ids
    _anular_fila(conn, "fac_documentos", doc_id, motivo, usuario, ahora)
    evento(conn, "DOCUMENTO", doc_id, "ANULADO", usuario, ahora, {"motivo": motivo, "cascada": anuladas})


def anular_recibo(conn, rec_id, motivo, usuario, ahora, es_responsable):
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo puede anular")
    if len((motivo or "").strip()) < 20:
        raise FacError("MOTIVO", "el motivo de anulación debe tener al menos 20 caracteres")
    rec = conn.execute("SELECT origen, cliente_codigo FROM fac_recibos WHERE id = ?", (rec_id,)).fetchone()
    if not rec:
        raise FacError("NO_EXISTE", f"recibo {rec_id} inexistente")
    if rec[0] == "SALDO_INICIAL":
        _exigir_cliente_abierto(conn, rec[1])
    ids = [r[0] for r in conn.execute("SELECT id FROM fac_imputaciones WHERE recibo_id = ? AND estado = 'VIGENTE'", (rec_id,))]
    for i in ids:
        _anular_fila(conn, "fac_imputaciones", i, f"Cascada por anulación del recibo {rec_id}: {motivo}", usuario, ahora)
    _anular_fila(conn, "fac_recibos", rec_id, motivo, usuario, ahora)
    evento(conn, "RECIBO", rec_id, "ANULADO", usuario, ahora, {"motivo": motivo, "imputaciones": ids})


# ---------------------------------------------------------------- saldos iniciales: cancelado antes del corte

def _insertar_cancelacion(conn, doc_id, importe, criterio, nota, usuario, ahora, revision_de_id=None, pago=None):
    d, importe = documento(conn, doc_id), r2(importe)
    if d["origen"] != "SALDO_INICIAL" or d["tipo"] == "NC":
        raise FacError("CANCELACION", "solo las FC/ND de saldo inicial tienen cancelado antes del corte")
    if importe <= 0:
        raise FacError("CANCELACION", "lo cancelado antes del corte debe ser mayor que cero")
    if cancelado_antes_corte(conn, doc_id) + importe >= d["total"]:
        raise FacError("CANCELACION", "lo cancelado antes del corte debe ser menor que el total: una factura cobrada antes del inicio operativo no se carga como saldo inicial: si corresponde a un certificado, regístrela como antecedente del certificado (para que no vuelva a figurar pendiente); si no tiene certificado, no se registra")
    if importe > saldo_documento(conn, doc_id):
        raise FacError("CANCELACION", "lo cancelado antes del corte supera el saldo disponible del comprobante")
    pago = pago or {}
    conn.execute("INSERT INTO fac_cancelaciones_corte (documento_id, importe, criterio, revision_de_id, nota, creado_por, creado_en, "
                 "fecha_pago, moneda_pago, importe_pago, tipo_cambio, recibo_punto_venta, recibo_numero, referencia, recibo_total, "
                 "recibo_moneda) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (doc_id, _num(importe), criterio, revision_de_id, nota, usuario, ahora, pago.get("fecha_pago"), pago.get("moneda_pago"),
                  _num(pago["importe_pago"]) if pago.get("importe_pago") is not None else None,
                  str(pago["tipo_cambio"]) if pago.get("tipo_cambio") is not None else None,
                  pago.get("recibo_punto_venta"), pago.get("recibo_numero"), pago.get("referencia"),
                  _num(pago["recibo_total"]) if pago.get("recibo_total") is not None else None, pago.get("recibo_moneda")))


def _recibo_previo(conn, pv, nro):
    """Lo ya atribuido a un recibo de Tango: total y moneda declarados, pagos previos vigentes y anticipo al inicio."""
    declarados = {(None if t is None else r2(t), m) for t, m in conn.execute(
        "SELECT recibo_total, recibo_moneda FROM fac_cancelaciones_corte WHERE recibo_punto_venta = ? AND recibo_numero = ? "
        "AND estado = 'VIGENTE'", (pv, nro)).fetchall()}
    pagado = _sum(conn, "SELECT importe_pago FROM fac_cancelaciones_corte WHERE recibo_punto_venta = ? AND recibo_numero = ? "
                        "AND estado = 'VIGENTE'", (pv, nro))
    anticipo = conn.execute("SELECT total, moneda FROM fac_recibos WHERE punto_venta = ? AND numero = ? AND origen = 'SALDO_INICIAL' "
                            "AND estado = 'VIGENTE'", (pv, nro)).fetchone()
    return declarados, pagado, (r2(anticipo[0]), anticipo[1]) if anticipo else None


def _normalizar_pago_previo(conn, cliente, moneda_doc, p, en_carga=None):
    """Pago anterior al inicio: conserva moneda, importe y tipo de cambio originales. Devuelve (importe en la moneda del
    comprobante, datos del pago). Con otra moneda exige el TC y que el importe aplicado sea coherente con él.
    Con número de recibo exige su total y moneda en Tango, iguales en todos sus registros; lo atribuido al recibo
    (pagos previos ya cargados + los de esta misma carga, en `en_carga` + anticipo al inicio) no puede superar su total."""
    d = {"moneda": moneda_doc}
    corte = corte_actual(conn)
    fecha = p.get("fecha_pago")
    if not fecha:
        raise FacError("PAGO_PREVIO", "cada pago previo necesita su fecha")
    if corte and corte["fecha_corte"] and fecha > corte["fecha_corte"]:
        raise FacError("POSTERIOR_AL_CORTE", f"un pago previo no puede tener fecha posterior al inicio operativo ({corte['fecha_corte']})")
    moneda = p.get("moneda_pago") or d["moneda"]
    if moneda not in MONEDAS:
        raise FacError("MONEDA", "moneda del pago inválida")
    importe_pago = r2(p["importe_pago"])
    if importe_pago <= 0:
        raise FacError("PAGO_PREVIO", "el importe pagado debe ser mayor que cero")
    tc = None
    if moneda == d["moneda"]:
        importe = importe_pago
        if p.get("importe") is not None and r2(p["importe"]) != importe:
            raise FacError("PAGO_INCONSISTENTE", "en la misma moneda, el importe aplicado debe ser igual al pagado")
    else:
        if not p.get("tipo_cambio") or dec(p["tipo_cambio"]) <= 0:
            raise FacError("PAGO_TC", f"un pago en {moneda} de un comprobante en {d['moneda']} necesita el tipo de cambio de Tango")
        tc = dec(p["tipo_cambio"])
        importe = r2(p["importe"]) if p.get("importe") is not None else r2(importe_pago / tc)
        if abs(importe * tc - importe_pago) > tc * CENT:
            raise FacError("PAGO_INCONSISTENTE", f"{importe_pago} {moneda} a TC {tc} no equivalen a {importe} {d['moneda']}")
    pv, nro = p.get("recibo_punto_venta"), p.get("recibo_numero")
    if (pv is None) != (nro is None):
        raise FacError("PAGO_PREVIO", "el recibo de Tango se identifica con punto de venta y número")
    recibo_total = recibo_moneda = None
    if nro is not None:
        otro = conn.execute("SELECT origen, cliente_codigo FROM fac_recibos WHERE punto_venta = ? AND numero = ? AND estado = 'VIGENTE'",
                            (pv, nro)).fetchone()
        if otro and (otro[0] == "NORMAL" or otro[1] != cliente):
            raise FacError("DUPLICADO", "ese recibo ya está registrado como cobranza: no se carga también como pago previo")
        ajeno = conn.execute("SELECT 1 FROM fac_cancelaciones_corte c JOIN fac_documentos x ON x.id = c.documento_id "
                             "WHERE c.recibo_punto_venta = ? AND c.recibo_numero = ? AND c.estado = 'VIGENTE' AND x.cliente_codigo <> ?",
                             (pv, nro, cliente)).fetchone()
        if ajeno:
            raise FacError("DUPLICADO", "ese recibo ya figura como pago previo de otro cliente")
        if p.get("recibo_total") in (None, "") or not p.get("recibo_moneda"):
            raise FacError("RECIBO_TOTAL", f"informe el total y la moneda del recibo {pv}-{nro} según Tango")
        recibo_total, recibo_moneda = r2(p["recibo_total"]), p["recibo_moneda"]
        if recibo_moneda not in MONEDAS or recibo_total <= 0:
            raise FacError("RECIBO_TOTAL", "total o moneda del recibo inválidos")
        if moneda != recibo_moneda:
            raise FacError("RECIBO_MONEDA", f"el pago está en {moneda} y el recibo {pv}-{nro} en {recibo_moneda}: deben coincidir")
        declarados, pagado, anticipo = _recibo_previo(conn, pv, nro)
        clave = (pv, nro)
        previo_carga = (en_carga or {}).get(clave)
        for total_otro, moneda_otro in declarados | ({previo_carga[:2]} if previo_carga else set()):
            if total_otro is None:
                continue
            if (total_otro, moneda_otro) != (recibo_total, recibo_moneda):
                raise FacError("RECIBO_INCONSISTENTE", f"el recibo {pv}-{nro} ya figura con total {total_otro} {moneda_otro}: "
                                                       "todos sus pagos deben informar el mismo total y moneda")
        if anticipo and anticipo[1] != recibo_moneda:
            raise FacError("RECIBO_MONEDA", f"el anticipo al inicio del recibo {pv}-{nro} está en {anticipo[1]}")
        atribuido = pagado + (previo_carga[2] if previo_carga else CERO) + (anticipo[0] if anticipo else CERO) + importe_pago
        if atribuido > recibo_total:
            raise FacError("RECIBO_EXCEDE", f"lo atribuido al recibo {pv}-{nro} ({atribuido} {recibo_moneda}: pagos previos"
                                            f"{' y anticipo' if anticipo else ''}) supera su total en Tango ({recibo_total})")
        if en_carga is not None:
            en_carga[clave] = (recibo_total, recibo_moneda, (previo_carga[2] if previo_carga else CERO) + importe_pago)
    return importe, {"fecha_pago": fecha, "moneda_pago": moneda, "importe_pago": importe_pago, "tipo_cambio": tc,
                     "recibo_punto_venta": pv, "recibo_numero": nro, "referencia": p.get("referencia"),
                     "recibo_total": recibo_total, "recibo_moneda": recibo_moneda}


def estimar_por_antiguedad(conn, corte_id, cliente, moneda, saldo_tango, usuario, ahora):
    """Cuando Tango no informa qué facturas cubrió cada recibo: reparte la diferencia entre lo facturado
    abierto y el saldo de Tango, cancelando primero lo más antiguo. Queda marcado ESTIMADO_ANTIGUEDAD:
    no es una imputación real y se revisa con revisar_cancelaciones()."""
    corte = _exigir_saldo_inicial(conn, corte_id, "0000-00-00")
    if corte["estado"] != "BORRADOR":
        raise FacError("CORTE_CERRADO", "con el corte confirmado se usa revisar_cancelaciones()")
    _exigir_cliente_abierto(conn, cliente)
    docs = conn.execute(
        "SELECT id FROM fac_documentos WHERE corte_id = ? AND origen = 'SALDO_INICIAL' AND cliente_codigo = ? AND moneda = ? "
        "AND tipo <> 'NC' AND estado = 'VIGENTE' ORDER BY fecha_emision, id", (corte_id, cliente, moneda)
    ).fetchall()
    for (doc_id,) in docs:
        if cancelado_antes_corte(conn, doc_id):
            raise FacError("CANCELACION_EXISTE", "ya hay cancelaciones cargadas para este cliente y moneda; revíselas")
    # Saldo SMG sin cancelaciones (las NC y anticipos al corte ya restan) contra el saldo según Tango.
    exceso = saldo_al_corte(conn, corte_id, cliente, moneda)[0] - r2(saldo_tango)
    if exceso < 0:
        raise FacError("FALTAN_COMPROBANTES", f"el saldo de Tango supera lo cargado en {-exceso}: falta cargar comprobantes")
    resto, filas = exceso, []
    for (doc_id,) in docs:
        if resto <= 0:
            break
        d = documento(conn, doc_id)
        importe = min(resto, d["total"])
        if importe >= d["total"]:
            raise FacError("DOCUMENTO_CANCELADO", f"{d['tipo']} {d['punto_venta']}-{d['numero']} quedaría cancelado al corte: "
                                                  "no debería cargarse como abierto")
        _insertar_cancelacion(conn, doc_id, importe, "ESTIMADO_ANTIGUEDAD",
                              "Estimado por antigüedad: Tango no informa la imputación de los recibos", usuario, ahora)
        filas.append({"documento_id": doc_id, "importe": importe})
        resto -= importe
    evento(conn, "CORTE", corte_id, "CANCELADO_ESTIMADO", usuario, ahora,
           {"cliente": cliente, "moneda": moneda, "saldo_tango": r2(saldo_tango), "filas": filas})
    return filas


def revisar_cancelaciones(conn, cliente, moneda, nuevas, criterio, nota, usuario, ahora, es_responsable):
    """Reemplaza las cancelaciones al corte de los comprobantes indicados ({doc_id: importe}; 0 = sin cancelación).
    Con el corte confirmado solo un responsable puede hacerlo y el total por cliente y moneda no cambia
    (se redistribuye entre comprobantes, no se altera el saldo conciliado con Tango)."""
    if criterio not in ("SEGUN_TANGO", "REVISADO"):
        raise FacError("CRITERIO", "una revisión queda como SEGUN_TANGO o REVISADO")
    if len((nota or "").strip()) < 10:
        raise FacError("NOTA", "la revisión requiere una nota de al menos 10 caracteres")
    corte = corte_actual(conn)
    # Con el corte confirmado o el estado inicial del cliente validado, solo se redistribuye (mismo total).
    confirmado = (corte and corte["estado"] == "CONFIRMADO") or estado_inicial(conn, cliente) is not None
    if confirmado and not es_responsable:
        raise FacError("PERMISO", "con el corte confirmado o el cliente validado solo un responsable revisa las cancelaciones")
    antes = despues = CERO
    for doc_id, importe in nuevas.items():
        d = documento(conn, doc_id)
        if d["cliente_codigo"] != cliente or d["moneda"] != moneda:
            raise FacError("CLIENTE", "todos los comprobantes de la revisión deben ser del mismo cliente y moneda")
        antes += cancelado_antes_corte(conn, doc_id)
        despues += r2(importe)
    if confirmado and antes != despues:
        raise FacError("TOTAL_CONCILIADO", f"con el corte confirmado el total cancelado debe seguir siendo {antes} (queda {despues})")
    for doc_id, importe in nuevas.items():
        previas = [r[0] for r in conn.execute("SELECT id FROM fac_cancelaciones_corte WHERE documento_id = ? AND estado = 'VIGENTE'",
                                              (doc_id,))]
        for p in previas:
            _anular_fila(conn, "fac_cancelaciones_corte", p, f"Revisión de lo cancelado antes del corte: {nota}", usuario, ahora)
        if r2(importe) > 0:
            _insertar_cancelacion(conn, doc_id, importe, criterio, nota, usuario, ahora, previas[0] if previas else None)
    evento(conn, "CORTE", corte["id"] if corte else None, "CANCELADO_REVISADO", usuario, ahora,
           {"cliente": cliente, "moneda": moneda, "criterio": criterio, "nuevas": nuevas, "nota": nota})


def cargar_conciliacion(conn, corte_id, cliente, moneda, saldo_tango, nota, usuario, ahora):
    _exigir_cliente_abierto(conn, cliente)
    conn.execute(
        "INSERT INTO fac_corte_conciliacion (corte_id, cliente_codigo, moneda, saldo_tango, nota, cargado_por, cargado_en) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(corte_id, cliente_codigo, moneda) DO UPDATE SET "
        "saldo_tango = excluded.saldo_tango, nota = excluded.nota, cargado_por = excluded.cargado_por, cargado_en = excluded.cargado_en",
        (corte_id, cliente, moneda, _num(saldo_tango), nota, usuario, ahora),
    )


def saldo_al_corte(conn, corte_id, cliente, moneda):
    """Saldo SMG al corte (solo registros SALDO_INICIAL) y cuánto de lo cancelado es estimado."""
    smg = estimado = CERO
    for (doc_id, tipo) in conn.execute("SELECT id, tipo FROM fac_documentos WHERE corte_id = ? AND cliente_codigo = ? "
                                       "AND moneda = ? AND estado = 'VIGENTE'", (corte_id, cliente, moneda)).fetchall():
        pendiente = documento(conn, doc_id)["total"] - cancelado_antes_corte(conn, doc_id)
        smg += -pendiente if tipo == "NC" else pendiente
        estimado += cancelaciones_estimadas(conn, doc_id)
    smg -= _sum(conn, "SELECT total FROM fac_recibos WHERE corte_id = ? AND cliente_codigo = ? AND moneda = ? "
                      "AND estado = 'VIGENTE'", (corte_id, cliente, moneda))
    return smg, estimado


def conciliacion(conn, corte_id):
    """Por cliente y moneda: saldo SMG al corte vs. saldo Tango; señala si incluye montos estimados."""
    claves = {tuple(r) for r in conn.execute(
        "SELECT cliente_codigo, moneda FROM fac_documentos WHERE corte_id = ? AND estado = 'VIGENTE' "
        "UNION SELECT cliente_codigo, moneda FROM fac_recibos WHERE corte_id = ? AND estado = 'VIGENTE' "
        "UNION SELECT cliente_codigo, moneda FROM fac_corte_conciliacion WHERE corte_id = ?", (corte_id, corte_id, corte_id))}
    filas = []
    for cliente, moneda in sorted(claves):
        smg, estimado = saldo_al_corte(conn, corte_id, cliente, moneda)
        tango = conn.execute("SELECT saldo_tango, nota FROM fac_corte_conciliacion WHERE corte_id = ? AND cliente_codigo = ? "
                             "AND moneda = ?", (corte_id, cliente, moneda)).fetchone()
        dif = None if tango is None else smg - r2(tango[0])
        filas.append({"cliente": cliente, "moneda": moneda, "saldo_smg": smg, "saldo_tango": None if tango is None else r2(tango[0]),
                      "diferencia": dif, "nota": tango[1] if tango else None, "cancelado_estimado": estimado,
                      "conciliada": dif is not None and (abs(dif) <= CENT or bool((tango[1] or "").strip()))})
    return filas


def confirmar_corte(conn, usuario, ahora, es_responsable):
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable confirma el corte")
    corte = corte_actual(conn)
    if not corte or corte["estado"] != "BORRADOR":
        raise FacError("CORTE_ESTADO", "no hay un corte en borrador")
    if not corte["fecha_corte"]:
        raise FacError("CORTE_FECHA", "la fecha de corte está pendiente de confirmar con Contabilidad")
    validar_adjuntos(conn, "CORTE", conn.execute("SELECT id FROM fac_adjuntos WHERE entidad = 'CORTE' AND entidad_id = ? "
                                                 "AND estado = 'VIGENTE'", (corte["id"],)).fetchall())
    filas = conciliacion(conn, corte["id"])
    pendientes = [f"{f['cliente']} {f['moneda']}" for f in filas if not f["conciliada"]]
    if pendientes:
        raise FacError("SIN_CONCILIAR", "faltan conciliar o justificar: " + ", ".join(pendientes))
    sin_validar = sorted(c for c in _clientes_con_carga_inicial(conn, corte["id"]) if not estado_inicial(conn, c))
    if sin_validar:
        raise FacError("SIN_VALIDAR", "faltan validar el estado inicial de: " + ", ".join(sin_validar))
    conn.execute("UPDATE fac_cortes SET estado = 'CONFIRMADO', confirmado_por = ?, confirmado_en = ? WHERE id = ?",
                 (usuario, ahora, corte["id"]))
    evento(conn, "CORTE", corte["id"], "CONFIRMADO", usuario, ahora,
           {"fecha_corte": corte["fecha_corte"], "conciliacion": filas,
            "con_estimados": [f"{f['cliente']} {f['moneda']}" for f in filas if f["cancelado_estimado"]]})


# ---------------------------------------------------------------- puesta en marcha: antecedentes y estado inicial por cliente

def _clientes_con_carga_inicial(conn, corte_id):
    return {r[0] for r in conn.execute(
        "SELECT cliente_codigo FROM fac_documentos WHERE corte_id = ? AND estado = 'VIGENTE' "
        "UNION SELECT cliente_codigo FROM fac_recibos WHERE corte_id = ? AND estado = 'VIGENTE' "
        "UNION SELECT cliente_codigo FROM fac_antecedentes_certificado WHERE corte_id = ? AND estado = 'VIGENTE' "
        "UNION SELECT cliente_codigo FROM fac_corte_conciliacion WHERE corte_id = ?", (corte_id,) * 4)}


def registrar_antecedente(conn, datos, usuario, ahora):
    """Certificado ya facturado en Tango antes del inicio operativo y sin saldo a cobrar (factura cobrada). No crea
    comprobante, deuda ni cobranza: solo baja lo pendiente de facturar del certificado para no volver a pedirlo."""
    corte = corte_actual(conn)
    if not corte:
        raise FacError("CORTE_INEXISTENTE", "cree la puesta en marcha (fecha de inicio operativo) antes de cargar antecedentes")
    cert = conn.execute("SELECT c.estado, c.moneda, c.total_a_facturar, s.cliente_codigo FROM certificados_cliente c "
                        "JOIN seguimiento_cc s ON s.id = c.seguimiento_cc_id WHERE c.id = ?", (datos["certificado_id"],)).fetchone()
    if not cert or cert[0] != "EMITIDO":
        raise FacError("CERTIFICADO", "solo se registran antecedentes de certificados EMITIDOS")
    cliente, moneda = cert[3], cert[1]
    if datos.get("cliente_codigo") and datos["cliente_codigo"] != cliente:
        raise FacError("CLIENTE", "el certificado pertenece a otro cliente")
    _exigir_cliente_abierto(conn, cliente)
    if corte["estado"] == "CONFIRMADO" and len((datos.get("nota") or "").strip()) < 20:
        raise FacError("MOTIVO", "con el corte confirmado, un antecedente omitido exige una nota de al menos 20 caracteres")
    fecha = datos.get("fecha_factura")
    if fecha and corte["fecha_corte"] and fecha > corte["fecha_corte"]:
        raise FacError("POSTERIOR_AL_CORTE", f"un antecedente no puede tener fecha posterior al inicio operativo ({corte['fecha_corte']})")
    neto = r2(datos["neto_facturado"])
    if neto <= 0:
        raise FacError("DATOS", "el neto facturado debe ser mayor que cero")
    total = r2(cert[2])
    usado = max(_cert_comprometido(conn, datos["certificado_id"], 0),
                facturado_certificado(conn, datos["certificado_id"]) + antecedentes_certificado(conn, datos["certificado_id"]))
    if usado + neto > total:
        raise FacError("CERTIFICADO_EXCEDE", f"el certificado ya tiene {usado} facturado o comprometido de {total}: "
                                             f"el antecedente de {neto} lo superaría (posible duplicado)")
    tipo, letra, pv, nro = (datos.get("factura_tipo") or ("FC" if datos.get("factura_numero") else None)), datos.get("factura_letra"), \
        datos.get("factura_punto_venta"), datos.get("factura_numero")
    if nro is not None:
        if not letra or pv is None:
            raise FacError("DATOS", "la factura de Tango se identifica con letra, punto de venta y número")
        if conn.execute("SELECT 1 FROM fac_documentos WHERE tipo = ? AND letra = ? AND punto_venta = ? AND numero = ? AND estado = 'VIGENTE'",
                        (tipo, letra, pv, nro)).fetchone():
            raise FacError("DUPLICADO", "esa factura ya está registrada como comprobante: si tiene saldo es un saldo inicial, "
                                        "no un antecedente")
        if conn.execute("SELECT 1 FROM fac_antecedentes_certificado WHERE certificado_id = ? AND factura_tipo = ? AND factura_letra = ? "
                        "AND factura_punto_venta = ? AND factura_numero = ? AND estado = 'VIGENTE'",
                        (datos["certificado_id"], tipo, letra, pv, nro)).fetchone():
            raise FacError("DUPLICADO", "ese antecedente ya está registrado para el certificado")
    tc = datos.get("tipo_cambio")
    if tc is not None and dec(tc) <= 0:
        raise FacError("DATOS", "el tipo de cambio debe ser mayor que cero")
    cur = conn.execute(
        "INSERT INTO fac_antecedentes_certificado (corte_id, certificado_id, cliente_codigo, moneda, neto_facturado, factura_tipo, "
        "factura_letra, factura_punto_venta, factura_numero, fecha_factura, tipo_cambio, nota, creado_por, creado_en) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (corte["id"], datos["certificado_id"], cliente, moneda, _num(neto), tipo, letra, pv, nro, fecha,
         str(dec(tc)) if tc is not None else None, datos.get("nota"), usuario, ahora))
    evento(conn, "ANTECEDENTE", cur.lastrowid, "REGISTRADO", usuario, ahora,
           {"certificado_id": datos["certificado_id"], "cliente": cliente, "moneda": moneda, "neto_facturado": neto,
            "factura": f"{tipo} {letra}-{pv}-{nro}" if nro is not None else None, "idempotencia": datos.get("idempotencia")})
    return cur.lastrowid


def anular_antecedente(conn, antecedente_id, motivo, usuario, ahora, es_responsable):
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo puede anular")
    if len((motivo or "").strip()) < 20:
        raise FacError("MOTIVO", "el motivo de anulación debe tener al menos 20 caracteres")
    fila = conn.execute("SELECT cliente_codigo, estado FROM fac_antecedentes_certificado WHERE id = ?", (antecedente_id,)).fetchone()
    if not fila or fila[1] != "VIGENTE":
        raise FacError("NO_EXISTE", "antecedente inexistente o ya anulado")
    _exigir_cliente_abierto(conn, fila[0])
    _anular_fila(conn, "fac_antecedentes_certificado", antecedente_id, motivo, usuario, ahora)
    evento(conn, "ANTECEDENTE", antecedente_id, "ANULADO", usuario, ahora, {"motivo": motivo})


def _resumen_inicial(conn, corte_id, cliente):
    docs = conn.execute("SELECT count(*) FROM fac_documentos WHERE corte_id = ? AND cliente_codigo = ? AND estado = 'VIGENTE'",
                        (corte_id, cliente)).fetchone()[0]
    recs = conn.execute("SELECT count(*) FROM fac_recibos WHERE corte_id = ? AND cliente_codigo = ? AND estado = 'VIGENTE'",
                        (corte_id, cliente)).fetchone()[0]
    ants = {}
    for mon, neto in conn.execute("SELECT moneda, neto_facturado FROM fac_antecedentes_certificado WHERE corte_id = ? "
                                  "AND cliente_codigo = ? AND estado = 'VIGENTE'", (corte_id, cliente)).fetchall():
        ants[mon] = ants.get(mon, CERO) + r2(neto)
    filas = [f for f in conciliacion(conn, corte_id) if f["cliente"] == cliente]
    return {"comprobantes": docs, "anticipos": recs, "antecedentes": sum(1 for _ in conn.execute(
                "SELECT 1 FROM fac_antecedentes_certificado WHERE corte_id = ? AND cliente_codigo = ? AND estado = 'VIGENTE'",
                (corte_id, cliente))),
            "neto_antecedentes": ants, "saldos": filas}


def validar_estado_inicial(conn, cliente, situacion, nota, usuario, ahora, es_responsable):
    """Control explícito de puesta en marcha por cliente. SIN_ANTECEDENTES: saldo inicial cero (sin cargas iniciales).
    CON_ANTECEDENTES: deuda, anticipos y/o antecedentes cargados, con el saldo de cada moneda conciliado con Tango.
    Congela la carga inicial del cliente y habilita sus comprobantes y cobranzas posteriores al inicio."""
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo valida el estado inicial de un cliente")
    if situacion not in ("SIN_ANTECEDENTES", "CON_ANTECEDENTES"):
        raise FacError("SITUACION", "situación inicial inválida")
    if not conn.execute("SELECT 1 FROM clientes WHERE codigo = ?", (cliente,)).fetchone():
        raise FacError("CLIENTE", "cliente inexistente")
    corte = corte_actual(conn)
    if not corte or not corte["fecha_corte"]:
        raise FacError("SIN_CORTE", "defina la fecha de inicio operativo antes de validar clientes")
    if estado_inicial(conn, cliente):
        raise FacError("DUPLICADO", "el estado inicial del cliente ya está validado")
    res = _resumen_inicial(conn, corte["id"], cliente)
    if situacion == "SIN_ANTECEDENTES":
        if res["comprobantes"] or res["anticipos"] or res["antecedentes"]:
            raise FacError("TIENE_ANTECEDENTES", "el cliente tiene cargas iniciales: valídelo con antecedentes")
        if any(f["saldo_tango"] for f in res["saldos"]):
            raise FacError("SALDO_NO_CERO", "el saldo según Tango no es cero: cargue la deuda inicial y valídelo con antecedentes")
    else:
        if not (res["comprobantes"] or res["anticipos"] or res["antecedentes"]):
            raise FacError("SIN_REGISTROS", "no hay antecedentes cargados: si no tiene deuda ni historia, declárelo sin antecedentes")
        pendientes = [f["moneda"] for f in res["saldos"] if not f["conciliada"]]
        if pendientes:
            raise FacError("SIN_CONCILIAR", "falta el saldo según Tango (o justificar la diferencia) en: " + ", ".join(pendientes))
    res.update({"fecha_inicio": corte["fecha_corte"], "situacion": situacion,
                "con_estimados": [f["moneda"] for f in res["saldos"] if f["cancelado_estimado"]]})
    cur = conn.execute("INSERT INTO fac_estado_inicial (corte_id, cliente_codigo, situacion, resumen_json, nota, creado_por, creado_en) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (corte["id"], cliente, situacion, json.dumps(res, default=str, ensure_ascii=False), nota, usuario, ahora))
    evento(conn, "ESTADO_INICIAL", cur.lastrowid, "VALIDADO", usuario, ahora, {"cliente": cliente, **res})
    return cur.lastrowid


def reabrir_estado_inicial(conn, cliente, motivo, usuario, ahora, es_responsable):
    """Anula la validación (queda en el historial) para corregir la carga inicial. Solo si el cliente todavía no tiene
    comprobantes ni cobranzas posteriores al inicio: con operaciones nuevas se corrige con NC/ND o anulaciones."""
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo reabre el estado inicial")
    if len((motivo or "").strip()) < 20:
        raise FacError("MOTIVO", "el motivo de la reapertura debe tener al menos 20 caracteres")
    e = estado_inicial(conn, cliente)
    if not e:
        raise FacError("NO_EXISTE", "el cliente no tiene un estado inicial validado")
    operaciones = conn.execute(
        "SELECT (SELECT count(*) FROM fac_documentos WHERE cliente_codigo = ? AND origen = 'NORMAL' AND estado = 'VIGENTE') + "
        "(SELECT count(*) FROM fac_recibos WHERE cliente_codigo = ? AND origen = 'NORMAL' AND estado = 'VIGENTE')", (cliente, cliente)
    ).fetchone()[0]
    if operaciones:
        raise FacError("CON_OPERACIONES", "el cliente ya tiene comprobantes o cobranzas posteriores al inicio: no se reabre; "
                                          "corrija con NC/ND o anulaciones")
    _anular_fila(conn, "fac_estado_inicial", e["id"], motivo, usuario, ahora)
    evento(conn, "ESTADO_INICIAL", e["id"], "REABIERTO", usuario, ahora, {"cliente": cliente, "motivo": motivo})


def puesta_en_marcha(conn):
    """Listado de todos los clientes con su estado inicial y lo cargado (sin sumar monedas distintas)."""
    corte = corte_actual(conn)
    clientes = _filas(conn, "SELECT codigo, razon_social, cuit FROM clientes ORDER BY razon_social")
    if not corte:
        return {"corte": None, "clientes": [{**c, "estado_inicial": None} for c in clientes], "resumen": {"clientes": len(clientes)}}
    cid = corte["id"]

    def contar(sql):
        return dict(conn.execute(sql, (cid,)).fetchall())
    docs = contar("SELECT cliente_codigo, count(*) FROM fac_documentos WHERE corte_id = ? AND estado = 'VIGENTE' GROUP BY cliente_codigo")
    recs = contar("SELECT cliente_codigo, count(*) FROM fac_recibos WHERE corte_id = ? AND estado = 'VIGENTE' GROUP BY cliente_codigo")
    ants = contar("SELECT cliente_codigo, count(*) FROM fac_antecedentes_certificado WHERE corte_id = ? AND estado = 'VIGENTE' "
                  "GROUP BY cliente_codigo")
    estados = {r["cliente_codigo"]: r for r in _filas(conn, "SELECT cliente_codigo, id, situacion, creado_por AS validado_por, "
                                                            "creado_en AS validado_en FROM fac_estado_inicial WHERE corte_id = ? "
                                                            "AND estado = 'VIGENTE'", (cid,))}
    ops = dict(conn.execute("SELECT cliente_codigo, count(*) FROM (SELECT cliente_codigo FROM fac_documentos WHERE origen = 'NORMAL' "
                            "AND estado = 'VIGENTE' UNION ALL SELECT cliente_codigo FROM fac_recibos WHERE origen = 'NORMAL' "
                            "AND estado = 'VIGENTE') GROUP BY cliente_codigo").fetchall())
    saldos = {}
    for f in conciliacion(conn, cid):
        saldos.setdefault(f["cliente"], []).append(f)
    filas = [{**c, "estado_inicial": estados.get(c["codigo"]), "comprobantes": docs.get(c["codigo"], 0),
              "anticipos": recs.get(c["codigo"], 0), "antecedentes": ants.get(c["codigo"], 0), "operaciones": ops.get(c["codigo"], 0),
              "saldos": saldos.get(c["codigo"], [])} for c in clientes]
    validados = [f for f in filas if f["estado_inicial"]]
    return {"corte": corte, "clientes": filas, "resumen": {
        "clientes": len(filas), "validados": len(validados),
        "sin_antecedentes": sum(1 for f in validados if f["estado_inicial"]["situacion"] == "SIN_ANTECEDENTES"),
        "con_antecedentes": sum(1 for f in validados if f["estado_inicial"]["situacion"] == "CON_ANTECEDENTES"),
        "con_carga_sin_validar": sum(1 for f in filas if not f["estado_inicial"] and (f["comprobantes"] or f["anticipos"] or f["antecedentes"]))}}


def detalle_inicial_cliente(conn, cliente, hoy):
    corte = corte_actual(conn)
    if not conn.execute("SELECT 1 FROM clientes WHERE codigo = ?", (cliente,)).fetchone():
        raise FacError("NO_EXISTE", "cliente inexistente")
    certs = _filas(conn, "SELECT c.id, c.seguimiento_cc_id, c.numero, c.moneda, c.total_a_facturar, c.fecha_certificado, c.periodo_desde, "
                         "c.periodo_hasta FROM certificados_cliente c JOIN seguimiento_cc s ON s.id = c.seguimiento_cc_id "
                         "WHERE s.cliente_codigo = ? AND c.estado = 'EMITIDO' ORDER BY c.seguimiento_cc_id, c.numero", (cliente,))
    for c in certs:
        c["facturado"] = facturado_certificado(conn, c["id"])
        c["antecedentes"] = antecedentes_certificado(conn, c["id"])
        c["pendiente"] = pendiente_certificado(conn, c["id"])
        c["comprometido"] = _cert_comprometido(conn, c["id"], 0)
    base = {"cliente": cliente, "corte": corte, "estado_inicial": estado_inicial(conn, cliente), "certificados": certs}
    if not corte:
        return {**base, "comprobantes": [], "anticipos": [], "antecedentes": [], "saldos": [], "eventos": []}
    cid = corte["id"]
    docs = [detalle_documento(conn, d["id"], hoy) for d in _filas(conn, "SELECT id FROM fac_documentos WHERE corte_id = ? "
                                                                         "AND cliente_codigo = ? ORDER BY fecha_emision, id", (cid, cliente))]
    ants = _filas(conn, "SELECT a.*, c.numero AS certificado_numero, c.seguimiento_cc_id FROM fac_antecedentes_certificado a "
                        "JOIN certificados_cliente c ON c.id = a.certificado_id WHERE a.corte_id = ? AND a.cliente_codigo = ? ORDER BY a.id",
                (cid, cliente))
    ids = [r[0] for r in conn.execute("SELECT id FROM fac_estado_inicial WHERE corte_id = ? AND cliente_codigo = ?", (cid, cliente))]
    eventos = sorted([e for i in ids for e in eventos_de(conn, "ESTADO_INICIAL", i)], key=lambda e: e["fecha"])
    return {**base, "comprobantes": docs, "anticipos": [r for r in listar_recibos(conn, cliente) if r["origen"] == "SALDO_INICIAL"],
            "antecedentes": ants, "saldos": [f for f in conciliacion(conn, cid) if f["cliente"] == cliente], "eventos": eventos}


# ---------------------------------------------------------------- anulaciones parciales e idempotencia

def anular_aplicacion(conn, aplicacion_id, motivo, usuario, ahora, es_responsable):
    """Desaplica una NC de un comprobante (error de carga): el importe vuelve a «sin aplicar»."""
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo puede anular")
    if len((motivo or "").strip()) < 20:
        raise FacError("MOTIVO", "el motivo de anulación debe tener al menos 20 caracteres")
    fila = conn.execute("SELECT documento_id, nc_id, estado FROM fac_aplicaciones_nc WHERE id = ?", (aplicacion_id,)).fetchone()
    if not fila or fila[2] != "VIGENTE":
        raise FacError("NO_EXISTE", "aplicación inexistente o ya anulada")
    _anular_fila(conn, "fac_aplicaciones_nc", aplicacion_id, motivo, usuario, ahora)
    evento(conn, "DOCUMENTO", fila[0], "NC_DESAPLICADA", usuario, ahora, {"aplicacion_id": aplicacion_id, "nc_id": fila[1], "motivo": motivo})


def anular_imputacion(conn, imputacion_id, motivo, usuario, ahora, es_responsable):
    """Quita la asignación de un cobro a un comprobante: el importe vuelve «a cuenta» del recibo."""
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo puede anular")
    if len((motivo or "").strip()) < 20:
        raise FacError("MOTIVO", "el motivo de anulación debe tener al menos 20 caracteres")
    fila = conn.execute("SELECT documento_id, recibo_id, estado FROM fac_imputaciones WHERE id = ?", (imputacion_id,)).fetchone()
    if not fila or fila[2] != "VIGENTE":
        raise FacError("NO_EXISTE", "imputación inexistente o ya anulada")
    _anular_fila(conn, "fac_imputaciones", imputacion_id, motivo, usuario, ahora)
    evento(conn, "RECIBO", fila[1], "IMPUTACION_ANULADA", usuario, ahora,
           {"imputacion_id": imputacion_id, "documento_id": fila[0], "motivo": motivo})


def imputar_a_cuenta(conn, recibo_id, imputaciones, usuario, ahora):
    """Asigna después lo que quedó a cuenta de un recibo vigente (mismas reglas que al registrarlo)."""
    rec = conn.execute("SELECT cliente_codigo, moneda, fecha_pago, estado, origen FROM fac_recibos WHERE id = ?", (recibo_id,)).fetchone()
    if not rec or rec[3] != "VIGENTE":
        raise FacError("NO_EXISTE", "recibo inexistente o anulado")
    if sum((r2(i["importe_recibo"]) for i in imputaciones), CERO) > a_cuenta_recibo(conn, recibo_id):
        raise FacError("EXCEDE_RECIBO", "lo asignado supera lo que queda a cuenta del recibo")
    detalle = _imputar(conn, recibo_id, rec[0], rec[1], rec[2], imputaciones, usuario, ahora)
    evento(conn, "RECIBO", recibo_id, "IMPUTADO_A_CUENTA", usuario, ahora, {"imputaciones": detalle})


def buscar_idempotente(conn, entidad, clave):
    """Si ya se registró un alta con esa clave de idempotencia, devuelve su id (reintento del navegador)."""
    if not clave:
        return None
    fila = conn.execute(
        "SELECT entidad_id FROM fac_eventos WHERE entidad = ? AND evento IN ('REGISTRADO', 'BORRADOR_GUARDADO') "
        "AND json_extract(detalle_json, '$.idempotencia') = ? ORDER BY id LIMIT 1", (entidad, clave)).fetchone()
    return fila[0] if fila else None


# ---------------------------------------------------------------- lecturas para la pantalla

def _filas(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def numero_documento(d):
    return f"{d['letra']}-{int(d['punto_venta']):04d}-{int(d['numero']):08d}"


def numero_recibo(r):
    return f"{int(r['punto_venta']):04d}-{int(r['numero']):08d}"


def eventos_de(conn, entidad, entidad_id):
    filas = _filas(conn, "SELECT id, evento, usuario, fecha, detalle_json FROM fac_eventos WHERE entidad = ? AND entidad_id = ? "
                         "ORDER BY id", (entidad, entidad_id))
    for f in filas:
        f["detalle"] = json.loads(f.pop("detalle_json")) if f.get("detalle_json") else None
    return filas


def adjuntos_de(conn, entidad, entidad_id):
    return _filas(conn, "SELECT id, tipo, nombre, url_externa, ruta, estado, creado_por, creado_en FROM fac_adjuntos "
                        "WHERE entidad = ? AND entidad_id = ? ORDER BY id", (entidad, entidad_id))


def resumen_documento(conn, d, hoy):
    """Fila de listado: importes, aplicado, cobrado, saldo y estado en la moneda del comprobante."""
    doc_id = d["id"]
    aplicado = _sum(conn, "SELECT importe FROM fac_aplicaciones_nc WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,))
    cobrado = _sum(conn, "SELECT importe_documento FROM fac_imputaciones WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,))
    cancelado = cancelado_antes_corte(conn, doc_id)
    estimado = cancelaciones_estimadas(conn, doc_id)
    vinc = _filas(conn, "SELECT v.pedido_id, v.certificado_id, v.neto_asignado, p.numero_solicitud FROM fac_documento_vinculos v "
                        "LEFT JOIN fac_pedidos p ON p.id = v.pedido_id WHERE v.documento_id = ? ORDER BY v.id", (doc_id,))
    return {
        **d, "numero_completo": numero_documento(d), "total": r2(d["total"]), "aplicado_nc": aplicado, "cobrado": cobrado,
        "cancelado_antes_corte": cancelado, "cancelado_estimado": estimado,
        "saldo": saldo_documento(conn, doc_id), "estado_saldo": estado_documento(conn, doc_id, hoy),
        "dias_vencimiento": (date.fromisoformat(d["fecha_vencimiento"]) - date.fromisoformat(hoy)).days if d.get("fecha_vencimiento") else None,
        "sin_adjunto": not conn.execute("SELECT 1 FROM fac_adjuntos WHERE entidad = 'DOCUMENTO' AND entidad_id = ? AND estado = 'VIGENTE'",
                                        (doc_id,)).fetchone(),
        "vinculos": vinc,
    }


_COLS_DOC = ("id, tipo, letra, punto_venta, numero, origen, corte_id, cliente_codigo, cliente_razon_social, cliente_cuit, "
             "fecha_emision, fecha_vencimiento, moneda, tipo_cambio_emision, condicion_pago, detalle, neto_gravado, exento, "
             "no_gravado, iva_total, tributos_total, total, total_pdf_tango, motivo_codigo, motivo_detalle, documento_origen_id, "
             "orden_compra_cliente_id, centro_costo_id, reemplaza_a_id, nota, estado, creado_por, creado_en, anulado_por, "
             "anulado_en, motivo_anulacion")


def listar_documentos(conn, hoy, cliente=None, tipo=None, moneda=None, estado=None, desde=None, hasta=None, solo_pendientes=False):
    sql, params = f"SELECT {_COLS_DOC} FROM fac_documentos WHERE 1 = 1", []
    for col, val in (("cliente_codigo", cliente), ("tipo", tipo), ("moneda", moneda), ("estado", estado)):
        if val:
            sql += f" AND {col} = ?"
            params.append(val)
    if desde:
        sql += " AND fecha_emision >= ?"
        params.append(desde)
    if hasta:
        sql += " AND fecha_emision <= ?"
        params.append(hasta)
    filas = [resumen_documento(conn, d, hoy) for d in _filas(conn, sql + " ORDER BY fecha_emision DESC, id DESC", params)]
    if solo_pendientes:
        filas = [f for f in filas if f["estado"] == "VIGENTE" and f["saldo"] > 0]
    return filas


def detalle_documento(conn, doc_id, hoy):
    d = _filas(conn, f"SELECT {_COLS_DOC} FROM fac_documentos WHERE id = ?", (doc_id,))
    if not d:
        raise FacError("NO_EXISTE", f"comprobante {doc_id} inexistente")
    res = resumen_documento(conn, d[0], hoy)
    res["lineas"] = _filas(conn, "SELECT descripcion, alicuota_iva, neto FROM fac_documento_lineas WHERE documento_id = ? ORDER BY id", (doc_id,))
    res["tributos"] = _filas(conn, "SELECT tipo, jurisdiccion, base, alicuota, importe FROM fac_documento_tributos WHERE documento_id = ? "
                                   "ORDER BY id", (doc_id,))
    netos = {}
    for l in res["lineas"]:
        netos[l["alicuota_iva"]] = netos.get(l["alicuota_iva"], CERO) + r2(l["neto"])
    res["iva_por_alicuota"] = {a: r2(netos[a] * ALICUOTAS[a]) for a in ALICUOTAS if a in netos}
    # Movimientos con saldo restante, siempre en la moneda del comprobante.
    movs = []
    if res["tipo"] == "NC":
        for a in _filas(conn, "SELECT a.id, a.documento_id, a.importe, a.estado, a.creado_en, a.anulado_en, a.motivo_anulacion, "
                              "d.letra, d.punto_venta, d.numero, d.tipo FROM fac_aplicaciones_nc a JOIN fac_documentos d ON d.id = a.documento_id "
                              "WHERE a.nc_id = ? ORDER BY a.id", (doc_id,)):
            movs.append({"tipo": "APLICACION", "id": a["id"], "fecha": a["creado_en"][:10], "documento_id": a["documento_id"],
                         "referencia": f"{a['tipo']} {numero_documento(a)}", "importe": r2(a["importe"]), "estado": a["estado"],
                         "motivo_anulacion": a["motivo_anulacion"]})
    else:
        for a in _filas(conn, "SELECT a.id, a.nc_id, a.importe, a.estado, a.motivo_anulacion, d.fecha_emision, d.letra, d.punto_venta, "
                              "d.numero, d.motivo_codigo FROM fac_aplicaciones_nc a JOIN fac_documentos d ON d.id = a.nc_id "
                              "WHERE a.documento_id = ? ORDER BY a.id", (doc_id,)):
            movs.append({"tipo": "NC", "id": a["id"], "fecha": a["fecha_emision"], "documento_id": a["nc_id"],
                         "referencia": f"NC {numero_documento(a)}", "detalle": a["motivo_codigo"], "importe": r2(a["importe"]),
                         "estado": a["estado"], "motivo_anulacion": a["motivo_anulacion"]})
        for i in _filas(conn, "SELECT i.id, i.recibo_id, i.importe_recibo, i.tipo_cambio, i.fecha_tipo_cambio, i.importe_documento, "
                              "i.saldo_documento_antes, i.saldo_documento_despues, i.estado, i.motivo_anulacion, r.fecha_pago, r.moneda, "
                              "r.punto_venta, r.numero FROM fac_imputaciones i JOIN fac_recibos r ON r.id = i.recibo_id "
                              "WHERE i.documento_id = ? ORDER BY i.id", (doc_id,)):
            valores = _filas(conn, "SELECT medio, importe FROM fac_recibo_valores WHERE recibo_id = ? ORDER BY id", (i["recibo_id"],))
            movs.append({"tipo": "COBRO", "id": i["id"], "fecha": i["fecha_pago"], "recibo_id": i["recibo_id"],
                         "referencia": f"REC {numero_recibo(i)}", "detalle": " + ".join(v["medio"] for v in valores),
                         "importe_recibido": r2(i["importe_recibo"]), "moneda_recibida": i["moneda"],
                         "tipo_cambio": dec(i["tipo_cambio"]) if i["tipo_cambio"] else None, "importe": r2(i["importe_documento"]),
                         "estado": i["estado"], "motivo_anulacion": i["motivo_anulacion"]})
    movs.sort(key=lambda m: (m["fecha"], m["id"]))
    saldo = res["total"] - res["cancelado_antes_corte"]
    for m in movs:
        if m["estado"] == "VIGENTE":
            saldo -= m["importe"]
        m["saldo_restante"] = saldo
    res["movimientos"] = movs
    res["cancelaciones_corte"] = _filas(conn, "SELECT id, importe, criterio, revision_de_id, nota, estado, creado_por, creado_en, "
                                              "anulado_por, anulado_en, motivo_anulacion, fecha_pago, moneda_pago, importe_pago, "
                                              "tipo_cambio, recibo_punto_venta, recibo_numero, referencia, recibo_total, recibo_moneda FROM fac_cancelaciones_corte "
                                              "WHERE documento_id = ? ORDER BY id", (doc_id,))
    res["adjuntos"] = adjuntos_de(conn, "DOCUMENTO", doc_id)
    res["eventos"] = eventos_de(conn, "DOCUMENTO", doc_id)
    res["reemplazado_por"] = [r[0] for r in conn.execute("SELECT id FROM fac_documentos WHERE reemplaza_a_id = ?", (doc_id,))]
    return res


def listar_pedidos(conn, cliente=None, estado=None):
    sql, params = ("SELECT id, numero_solicitud, tipo_solicitado, estado, fecha_emision, cliente_codigo, cliente_razon_social, moneda, "
                   "condicion_pago, oc_texto, centro_costo_id, subtotal, iva_total, total, creado_por, emitido_en, anulado_en, "
                   "motivo_anulacion FROM fac_pedidos WHERE 1 = 1"), []
    if cliente:
        sql += " AND cliente_codigo = ?"
        params.append(cliente)
    if estado:
        sql += " AND estado = ?"
        params.append(estado)
    filas = _filas(conn, sql + " ORDER BY id DESC", params)
    for f in filas:
        f["subtotal"], f["total"] = r2(f["subtotal"]), r2(f["total"])
        f["facturado"] = facturado_pedido(conn, f["id"])
        f["pendiente"] = f["subtotal"] - f["facturado"]
        f["estado_facturacion"] = estado_facturacion_pedido(conn, f["id"])
        f["documentos"] = _filas(conn, "SELECT d.id, d.tipo, d.letra, d.punto_venta, d.numero, v.neto_asignado FROM fac_documento_vinculos v "
                                       "JOIN fac_documentos d ON d.id = v.documento_id WHERE v.pedido_id = ? AND d.estado = 'VIGENTE'", (f["id"],))
        f["certificados"] = [r[0] for r in conn.execute("SELECT DISTINCT certificado_id FROM fac_pedido_lineas WHERE pedido_id = ? "
                                                         "AND certificado_id IS NOT NULL", (f["id"],))]
    return filas


def detalle_pedido(conn, pedido_id):
    p = _filas(conn, "SELECT * FROM fac_pedidos WHERE id = ?", (pedido_id,))
    if not p:
        raise FacError("NO_EXISTE", f"pedido {pedido_id} inexistente")
    res = p[0]
    res.pop("snapshot_json", None)
    res["lineas"] = _filas(conn, "SELECT orden, cantidad, detalle, precio_unitario, alicuota_iva, subtotal, certificado_id "
                                 "FROM fac_pedido_lineas WHERE pedido_id = ? ORDER BY orden", (pedido_id,))
    res["facturado"] = facturado_pedido(conn, pedido_id)
    res["estado_facturacion"] = estado_facturacion_pedido(conn, pedido_id)
    res["adjuntos"] = adjuntos_de(conn, "PEDIDO", pedido_id)
    res["eventos"] = eventos_de(conn, "PEDIDO", pedido_id)
    return res


def listar_recibos(conn, cliente=None):
    sql, params = ("SELECT id, origen, punto_venta, numero, cliente_codigo, fecha_pago, moneda, total, observaciones, estado, "
                   "creado_por, creado_en, anulado_por, anulado_en, motivo_anulacion FROM fac_recibos WHERE 1 = 1"), []
    if cliente:
        sql += " AND cliente_codigo = ?"
        params.append(cliente)
    filas = _filas(conn, sql + " ORDER BY fecha_pago DESC, id DESC", params)
    for f in filas:
        f["numero_completo"] = numero_recibo(f)
        f["total"] = r2(f["total"])
        f["a_cuenta"] = a_cuenta_recibo(conn, f["id"])
        f["valores"] = _filas(conn, "SELECT medio, importe, banco_emisor, referencia, fecha_acreditacion, numero_certificado_retencion "
                                    "FROM fac_recibo_valores WHERE recibo_id = ? ORDER BY id", (f["id"],))
        f["imputaciones"] = _filas(conn, "SELECT i.id, i.documento_id, i.importe_recibo, i.tipo_cambio, i.importe_documento, "
                                         "i.saldo_documento_despues, i.estado, d.tipo, d.letra, d.punto_venta, d.numero, d.moneda "
                                         "FROM fac_imputaciones i JOIN fac_documentos d ON d.id = i.documento_id WHERE i.recibo_id = ? "
                                         "ORDER BY i.id", (f["id"],))
        f["adjuntos"] = adjuntos_de(conn, "RECIBO", f["id"])
        f["eventos"] = eventos_de(conn, "RECIBO", f["id"])
    return filas


def cuenta_corriente(conn, cliente, moneda, hoy):
    """Movimientos en una sola moneda; Σ Debe − Σ Haber coincide con saldo_cliente()."""
    corte = corte_actual(conn)
    fecha_corte = corte["fecha_corte"] if corte else None
    mov = []
    for d in _filas(conn, f"SELECT {_COLS_DOC} FROM fac_documentos WHERE cliente_codigo = ? AND moneda = ? AND estado = 'VIGENTE'",
                    (cliente, moneda)):
        inicial = d["origen"] == "SALDO_INICIAL"
        importe = r2(d["total"]) - (cancelado_antes_corte(conn, d["id"]) if inicial else CERO)
        detalle = d["detalle"] or ""
        if inicial:
            estimado = cancelaciones_estimadas(conn, d["id"])
            detalle = (f"Saldo inicial · emitida {d['fecha_emision']} por {r2(d['total'])}"
                       + (f", cancelado antes del corte {cancelado_antes_corte(conn, d['id'])}" if importe != r2(d["total"]) else "")
                       + (" (estimado por antigüedad, revisable)" if estimado else "") + (f" · {detalle}" if detalle else ""))
        mov.append({"fecha": (fecha_corte or d["fecha_emision"]) if inicial else d["fecha_emision"], "orden": 0, "saldo_inicial": inicial,
                    "tipo": d["tipo"], "documento_id": d["id"], "numero": numero_documento(d), "vencimiento": d["fecha_vencimiento"],
                    "detalle": detalle, "estimado": bool(inicial and cancelaciones_estimadas(conn, d["id"])),
                    "debe": CERO if d["tipo"] == "NC" else importe, "haber": importe if d["tipo"] == "NC" else CERO})
    for r in _filas(conn, "SELECT id, origen, punto_venta, numero, fecha_pago, total FROM fac_recibos WHERE cliente_codigo = ? "
                          "AND moneda = ? AND estado = 'VIGENTE'", (cliente, moneda)):
        # Lo asignado a comprobantes de otra moneda no baja esta cuenta: figura en la cuenta de esa moneda.
        otra_moneda = _sum(conn, "SELECT i.importe_recibo FROM fac_imputaciones i JOIN fac_documentos d ON d.id = i.documento_id "
                                 "WHERE i.recibo_id = ? AND i.estado = 'VIGENTE' AND d.moneda <> ?", (r["id"], moneda))
        haber = r2(r["total"]) - otra_moneda
        if haber <= 0:
            continue
        mov.append({"fecha": r["fecha_pago"], "orden": 1, "saldo_inicial": r["origen"] == "SALDO_INICIAL", "tipo": "REC",
                    "recibo_id": r["id"], "numero": numero_recibo(r), "vencimiento": None,
                    "detalle": "Cobranza" + (f" (de {r2(r['total'])}; el resto se aplicó a comprobantes en otra moneda)" if otra_moneda else ""),
                    "debe": CERO, "haber": haber})
    # Cobros en otra moneda aplicados a comprobantes de esta moneda: bajan la deuda por su equivalente.
    for i in _filas(conn, "SELECT i.importe_documento, i.importe_recibo, i.tipo_cambio, r.id AS recibo_id, r.punto_venta, r.numero, "
                          "r.fecha_pago, r.moneda FROM fac_imputaciones i JOIN fac_recibos r ON r.id = i.recibo_id "
                          "JOIN fac_documentos d ON d.id = i.documento_id WHERE d.cliente_codigo = ? AND d.moneda = ? AND r.moneda <> ? "
                          "AND i.estado = 'VIGENTE' AND r.estado = 'VIGENTE' AND d.estado = 'VIGENTE'", (cliente, moneda, moneda)):
        mov.append({"fecha": i["fecha_pago"], "orden": 1, "saldo_inicial": False, "tipo": "REC", "recibo_id": i["recibo_id"],
                    "numero": numero_recibo(i), "vencimiento": None,
                    "detalle": f"Cobro en {i['moneda']} {r2(i['importe_recibo'])} a TC {dec(i['tipo_cambio'])}",
                    "debe": CERO, "haber": r2(i["importe_documento"])})
    mov.sort(key=lambda m: (not m["saldo_inicial"], m["fecha"], m["orden"]))
    saldo = CERO
    for m in mov:
        saldo += m["debe"] - m["haber"]
        m["saldo"] = saldo
    return {"cliente": cliente, "moneda": moneda, "movimientos": mov, "saldo": saldo, "saldo_calculado": saldo_cliente(conn, cliente, moneda),
            "fecha_corte": fecha_corte}


def tablero(conn, hoy, cliente=None):
    monedas = {}
    for (cli, mon) in conn.execute("SELECT DISTINCT cliente_codigo, moneda FROM fac_documentos WHERE estado = 'VIGENTE' "
                                   "UNION SELECT DISTINCT cliente_codigo, moneda FROM fac_recibos WHERE estado = 'VIGENTE'").fetchall():
        if cliente and cli != cliente:
            continue
        monedas.setdefault(mon, CERO)
        monedas[mon] += saldo_cliente(conn, cli, mon)
    docs = [d for d in listar_documentos(conn, hoy, cliente=cliente, estado="VIGENTE") if d["tipo"] != "NC" and d["saldo"] > 0]
    tramos = {}
    for d in docs:
        dias = -d["dias_vencimiento"] if d["dias_vencimiento"] is not None else 0
        clave = "sin vencer" if dias <= 0 else "1-30" if dias <= 30 else "31-60" if dias <= 60 else "61-90" if dias <= 90 else "+90"
        tramos.setdefault(d["moneda"], {k: CERO for k in ("sin vencer", "1-30", "31-60", "61-90", "+90")})
        tramos[d["moneda"]][clave] += d["saldo"]
    vencimientos = sorted([d for d in docs if d["dias_vencimiento"] is not None and d["dias_vencimiento"] <= 30],
                          key=lambda d: d["fecha_vencimiento"])
    pedidos = [p for p in listar_pedidos(conn, cliente=cliente, estado="EMITIDO") if p["estado_facturacion"] != "FACTURADO"]
    return {"saldos_por_moneda": monedas, "antiguedad": tramos, "vencimientos": vencimientos, "pedidos_pendientes": pedidos,
            "corte": corte_actual(conn)}


def resumen_corte(conn, hoy):
    corte = corte_actual(conn)
    if not corte:
        return {"corte": None, "conciliacion": [], "documentos": [], "anticipos": []}
    docs = [resumen_documento(conn, d, hoy) for d in _filas(conn, f"SELECT {_COLS_DOC} FROM fac_documentos WHERE corte_id = ? "
                                                                  "ORDER BY cliente_codigo, moneda, fecha_emision, id", (corte["id"],))]
    for d in docs:
        d["criterios"] = sorted({r[0] for r in conn.execute("SELECT criterio FROM fac_cancelaciones_corte WHERE documento_id = ? "
                                                             "AND estado = 'VIGENTE'", (d["id"],))})
        d["nota_cancelacion"] = (conn.execute("SELECT nota FROM fac_cancelaciones_corte WHERE documento_id = ? AND estado = 'VIGENTE' "
                                              "ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone() or [None])[0]
    anticipos = [r for r in listar_recibos(conn) if r["origen"] == "SALDO_INICIAL"]
    return {"corte": corte, "conciliacion": conciliacion(conn, corte["id"]), "documentos": docs, "anticipos": anticipos,
            "adjuntos": adjuntos_de(conn, "CORTE", corte["id"]), "eventos": eventos_de(conn, "CORTE", corte["id"])}

