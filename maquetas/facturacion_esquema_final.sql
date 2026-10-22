-- Esquema del módulo Facturación y Cobranzas, versión 2 (puesta en marcha), generado desde facturacion.migrar_facturacion.
-- Solo objetos fac_*; no se modifica ninguna tabla preexistente. Desde la versión 1 se agregan fac_estado_inicial,
-- fac_antecedentes_certificado, sus índices y triggers, y columnas de pago previo en fac_cancelaciones_corte.
-- Configuración inicial: {"adjuntos_almacenamiento": "ENLACE", "adjuntos_requeridos": {"PEDIDO": false, "FC": false, "ND": false, "NC": false, "RECIBO": false, "CORTE": false}, "requiere_corte_confirmado": true, "tolerancia_redondeo": "0.05"}

CREATE TABLE fac_config (
        clave TEXT PRIMARY KEY,
        valor_json TEXT NOT NULL CHECK (json_valid(valor_json)),
        actualizado_por TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    );

CREATE TABLE fac_cortes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha_corte TEXT CHECK (fecha_corte IS NULL OR fecha_corte GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
        estado TEXT NOT NULL DEFAULT 'BORRADOR' CHECK (estado IN ('BORRADOR', 'CONFIRMADO')),
        observaciones TEXT,
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        confirmado_por TEXT,
        confirmado_en TEXT,
        CHECK (estado = 'BORRADOR' OR (fecha_corte IS NOT NULL AND confirmado_por IS NOT NULL AND confirmado_en IS NOT NULL))
    );

CREATE TABLE fac_corte_conciliacion (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        corte_id INTEGER NOT NULL REFERENCES fac_cortes (id) ON DELETE RESTRICT,
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        moneda TEXT NOT NULL CHECK (moneda IN ('ARS', 'USD', 'EUR')),
        saldo_tango NUMERIC NOT NULL,
        nota TEXT,
        cargado_por TEXT NOT NULL,
        cargado_en TEXT NOT NULL,
        UNIQUE (corte_id, cliente_codigo, moneda)
    );

CREATE TABLE fac_pedidos (
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
        moneda TEXT NOT NULL CHECK (moneda IN ('ARS', 'USD', 'EUR')),
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
    );

CREATE TABLE fac_pedido_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pedido_id INTEGER NOT NULL REFERENCES fac_pedidos (id) ON DELETE RESTRICT,
        orden INTEGER NOT NULL,
        cantidad NUMERIC NOT NULL CHECK (cantidad > 0),
        detalle TEXT NOT NULL CHECK (length(trim(detalle)) > 0),
        precio_unitario NUMERIC NOT NULL,
        alicuota_iva TEXT NOT NULL CHECK (alicuota_iva IN ('27', '21', '10.5', '0', 'NG')),
        subtotal NUMERIC NOT NULL,
        certificado_id INTEGER REFERENCES certificados_cliente (id) ON DELETE RESTRICT,
        UNIQUE (pedido_id, orden)
    );

CREATE TABLE fac_documentos (
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
        moneda TEXT NOT NULL CHECK (moneda IN ('ARS', 'USD', 'EUR')),
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
        nota TEXT,
        estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT,
        CHECK (estado = 'VIGENTE' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                      AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20)),
        CHECK (origen = 'NORMAL' OR corte_id IS NOT NULL),
        CHECK (tipo = 'NC' OR (fecha_vencimiento IS NOT NULL AND fecha_vencimiento >= fecha_emision)),
        CHECK (tipo = 'FC' OR motivo_codigo IS NOT NULL)
    );

CREATE TABLE fac_documento_lineas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        descripcion TEXT,
        alicuota_iva TEXT NOT NULL CHECK (alicuota_iva IN ('27', '21', '10.5', '0', 'NG')),
        neto NUMERIC NOT NULL CHECK (neto > 0)
    );

CREATE TABLE fac_documento_tributos (
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
    );

CREATE TABLE fac_documento_vinculos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        pedido_id INTEGER REFERENCES fac_pedidos (id) ON DELETE RESTRICT,
        certificado_id INTEGER REFERENCES certificados_cliente (id) ON DELETE RESTRICT,
        neto_asignado NUMERIC NOT NULL CHECK (neto_asignado > 0),
        CHECK ((pedido_id IS NULL) <> (certificado_id IS NULL)),
        UNIQUE (documento_id, pedido_id),
        UNIQUE (documento_id, certificado_id)
    );

CREATE TABLE fac_aplicaciones_nc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nc_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        importe NUMERIC NOT NULL CHECK (importe > 0),
        estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT,
        CHECK (estado = 'VIGENTE' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                      AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20)),
        CHECK (nc_id <> documento_id)
    );

CREATE TABLE fac_cancelaciones_corte (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        importe NUMERIC NOT NULL CHECK (importe > 0),
        criterio TEXT NOT NULL CHECK (criterio IN ('SEGUN_TANGO', 'ESTIMADO_ANTIGUEDAD', 'REVISADO')),
        revision_de_id INTEGER REFERENCES fac_cancelaciones_corte (id) ON DELETE RESTRICT,
        nota TEXT,
        estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT, fecha_pago TEXT, moneda_pago TEXT CHECK (moneda_pago IS NULL OR moneda_pago IN ('ARS', 'USD', 'EUR')), importe_pago NUMERIC CHECK (importe_pago IS NULL OR importe_pago > 0), tipo_cambio NUMERIC CHECK (tipo_cambio IS NULL OR tipo_cambio > 0), recibo_punto_venta INTEGER CHECK (recibo_punto_venta IS NULL OR recibo_punto_venta BETWEEN 1 AND 99999), recibo_numero INTEGER CHECK (recibo_numero IS NULL OR recibo_numero BETWEEN 1 AND 99999999), referencia TEXT, recibo_total NUMERIC CHECK (recibo_total IS NULL OR recibo_total > 0), recibo_moneda TEXT CHECK (recibo_moneda IS NULL OR recibo_moneda IN ('ARS', 'USD', 'EUR')),
        CHECK (estado = 'VIGENTE' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                      AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20)),
        CHECK (criterio <> 'REVISADO' OR length(trim(COALESCE(nota, ''))) >= 10)
    );

CREATE TABLE fac_estado_inicial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        corte_id INTEGER NOT NULL REFERENCES fac_cortes (id) ON DELETE RESTRICT,
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        situacion TEXT NOT NULL CHECK (situacion IN ('SIN_ANTECEDENTES', 'CON_ANTECEDENTES')),
        resumen_json TEXT NOT NULL CHECK (json_valid(resumen_json)),
        nota TEXT,
        estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT,
        CHECK (estado = 'VIGENTE' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                      AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20))
    );

CREATE TABLE fac_antecedentes_certificado (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        corte_id INTEGER NOT NULL REFERENCES fac_cortes (id) ON DELETE RESTRICT,
        certificado_id INTEGER NOT NULL REFERENCES certificados_cliente (id) ON DELETE RESTRICT,
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        moneda TEXT NOT NULL CHECK (moneda IN ('ARS', 'USD', 'EUR')),
        neto_facturado NUMERIC NOT NULL CHECK (neto_facturado > 0),
        factura_tipo TEXT CHECK (factura_tipo IS NULL OR factura_tipo IN ('FC', 'ND')),
        factura_letra TEXT CHECK (factura_letra IS NULL OR factura_letra IN ('A', 'B', 'C', 'E', 'M')),
        factura_punto_venta INTEGER CHECK (factura_punto_venta IS NULL OR factura_punto_venta BETWEEN 1 AND 99999),
        factura_numero INTEGER CHECK (factura_numero IS NULL OR factura_numero BETWEEN 1 AND 99999999),
        fecha_factura TEXT,
        tipo_cambio NUMERIC CHECK (tipo_cambio IS NULL OR tipo_cambio > 0),
        nota TEXT,
        estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT,
        CHECK (estado = 'VIGENTE' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                      AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20)),
        CHECK ((factura_numero IS NULL) = (factura_tipo IS NULL) AND (factura_numero IS NULL) = (factura_letra IS NULL)
               AND (factura_numero IS NULL) = (factura_punto_venta IS NULL))
    );

CREATE TABLE fac_recibos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        origen TEXT NOT NULL DEFAULT 'NORMAL' CHECK (origen IN ('NORMAL', 'SALDO_INICIAL')),
        corte_id INTEGER REFERENCES fac_cortes (id) ON DELETE RESTRICT,
        punto_venta INTEGER NOT NULL CHECK (punto_venta BETWEEN 1 AND 99999),
        numero INTEGER NOT NULL CHECK (numero BETWEEN 1 AND 99999999),
        cliente_codigo TEXT NOT NULL REFERENCES clientes (codigo) ON DELETE RESTRICT,
        fecha_pago TEXT NOT NULL,
        moneda TEXT NOT NULL CHECK (moneda IN ('ARS', 'USD', 'EUR')),
        total NUMERIC NOT NULL CHECK (total > 0),
        observaciones TEXT,
        reemplaza_a_id INTEGER REFERENCES fac_recibos (id) ON DELETE RESTRICT,
        estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT,
        CHECK (estado = 'VIGENTE' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                      AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20)),
        CHECK (origen = 'NORMAL' OR corte_id IS NOT NULL)
    );

CREATE TABLE fac_recibo_valores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recibo_id INTEGER NOT NULL REFERENCES fac_recibos (id) ON DELETE RESTRICT,
        medio TEXT NOT NULL CHECK (medio IN ('TRANSFERENCIA', 'CHEQUE', 'ECHEQ', 'EFECTIVO', 'RETENCION_IVA',
                                             'RETENCION_GANANCIAS', 'RETENCION_IIBB', 'RETENCION_SUSS', 'OTRO')),
        importe NUMERIC NOT NULL CHECK (importe > 0),
        banco_emisor TEXT,
        referencia TEXT,
        fecha_acreditacion TEXT,
        numero_certificado_retencion TEXT
    );

CREATE TABLE fac_imputaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recibo_id INTEGER NOT NULL REFERENCES fac_recibos (id) ON DELETE RESTRICT,
        documento_id INTEGER NOT NULL REFERENCES fac_documentos (id) ON DELETE RESTRICT,
        importe_recibo NUMERIC NOT NULL CHECK (importe_recibo > 0),
        tipo_cambio NUMERIC CHECK (tipo_cambio IS NULL OR tipo_cambio > 0),
        fecha_tipo_cambio TEXT,
        importe_documento NUMERIC NOT NULL CHECK (importe_documento > 0),
        saldo_documento_antes NUMERIC NOT NULL,
        saldo_documento_despues NUMERIC NOT NULL CHECK (saldo_documento_despues >= 0),
        estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT,
        CHECK (estado = 'VIGENTE' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                      AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20)),
        CHECK ((tipo_cambio IS NULL) = (fecha_tipo_cambio IS NULL))
    );

CREATE TABLE fac_adjuntos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidad TEXT NOT NULL CHECK (entidad IN ('PEDIDO', 'DOCUMENTO', 'RECIBO', 'CORTE')),
        entidad_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        nombre TEXT NOT NULL,
        ruta TEXT,
        sha256 TEXT,
        url_externa TEXT,
        estado TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE', 'ANULADO')),
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        anulado_por TEXT,
        anulado_en TEXT,
        motivo_anulacion TEXT,
        CHECK (estado = 'VIGENTE' OR (anulado_por IS NOT NULL AND anulado_en IS NOT NULL
                                      AND length(trim(COALESCE(motivo_anulacion, ''))) >= 20)),
        CHECK ((ruta IS NOT NULL AND sha256 IS NOT NULL) OR url_externa IS NOT NULL)
    );

CREATE TABLE fac_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidad TEXT NOT NULL,
        entidad_id INTEGER,
        evento TEXT NOT NULL,
        usuario TEXT NOT NULL,
        fecha TEXT NOT NULL,
        detalle_json TEXT CHECK (detalle_json IS NULL OR json_valid(detalle_json))
    );

CREATE UNIQUE INDEX ux_fac_cortes_unico ON fac_cortes ((1));

CREATE UNIQUE INDEX ux_fac_documentos_numero
       ON fac_documentos (tipo, letra, punto_venta, numero) WHERE estado = 'VIGENTE';

CREATE INDEX ix_fac_documentos_cliente ON fac_documentos (cliente_codigo, moneda, estado);

CREATE UNIQUE INDEX ux_fac_estado_inicial_cliente
       ON fac_estado_inicial (corte_id, cliente_codigo) WHERE estado = 'VIGENTE';

CREATE UNIQUE INDEX ux_fac_antecedentes_factura
       ON fac_antecedentes_certificado (certificado_id, factura_tipo, factura_letra, factura_punto_venta, factura_numero)
       WHERE estado = 'VIGENTE' AND factura_numero IS NOT NULL;

CREATE UNIQUE INDEX ux_fac_recibos_numero ON fac_recibos (punto_venta, numero) WHERE estado = 'VIGENTE';

CREATE INDEX ix_fac_adjuntos_entidad ON fac_adjuntos (entidad, entidad_id, estado);

CREATE INDEX ix_fac_eventos_entidad ON fac_eventos (entidad, entidad_id);

CREATE TRIGGER trg_fac_corte_confirmado_inmutable
    BEFORE UPDATE ON fac_cortes WHEN OLD.estado = 'CONFIRMADO'
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: un corte confirmado no puede modificarse'); END;

CREATE TRIGGER trg_fac_conciliacion_cerrada
    BEFORE UPDATE ON fac_corte_conciliacion
    WHEN (SELECT estado FROM fac_cortes WHERE id = OLD.corte_id) = 'CONFIRMADO'
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: la conciliación de un corte confirmado no puede modificarse'); END;

CREATE TRIGGER trg_fac_saldo_inicial_post_corte
    BEFORE INSERT ON fac_documentos
    WHEN NEW.origen = 'SALDO_INICIAL' AND (SELECT estado FROM fac_cortes WHERE id = NEW.corte_id) = 'CONFIRMADO'
         AND length(trim(COALESCE(NEW.motivo_alta_post_corte, ''))) < 20
    BEGIN SELECT RAISE(ABORT, 'FAC_CORTE: con el corte confirmado, un saldo inicial omitido exige motivo'); END;

CREATE TRIGGER trg_fac_saldo_inicial_cliente_validado
    BEFORE INSERT ON fac_documentos
    WHEN NEW.origen = 'SALDO_INICIAL' AND EXISTS (SELECT 1 FROM fac_estado_inicial WHERE corte_id = NEW.corte_id
         AND cliente_codigo = NEW.cliente_codigo AND estado = 'VIGENTE')
    BEGIN SELECT RAISE(ABORT, 'FAC_ESTADO_INICIAL: el estado inicial del cliente está validado; reábralo para modificarlo'); END;

CREATE TRIGGER trg_fac_anticipo_cliente_validado
    BEFORE INSERT ON fac_recibos
    WHEN NEW.origen = 'SALDO_INICIAL' AND EXISTS (SELECT 1 FROM fac_estado_inicial WHERE corte_id = NEW.corte_id
         AND cliente_codigo = NEW.cliente_codigo AND estado = 'VIGENTE')
    BEGIN SELECT RAISE(ABORT, 'FAC_ESTADO_INICIAL: el estado inicial del cliente está validado; reábralo para modificarlo'); END;

CREATE TRIGGER trg_fac_antecedente_cliente_validado
    BEFORE INSERT ON fac_antecedentes_certificado
    WHEN EXISTS (SELECT 1 FROM fac_estado_inicial WHERE corte_id = NEW.corte_id AND cliente_codigo = NEW.cliente_codigo
         AND estado = 'VIGENTE')
    BEGIN SELECT RAISE(ABORT, 'FAC_ESTADO_INICIAL: el estado inicial del cliente está validado; reábralo para modificarlo'); END;

CREATE TRIGGER trg_fac_pedido_emitido_inmutable
    BEFORE UPDATE ON fac_pedidos WHEN OLD.estado <> 'BORRADOR' AND (
        OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.numero_solicitud IS NOT OLD.numero_solicitud OR NEW.tipo_solicitado IS NOT OLD.tipo_solicitado
        OR NEW.fecha_emision IS NOT OLD.fecha_emision OR NEW.cliente_codigo IS NOT OLD.cliente_codigo
        OR NEW.moneda IS NOT OLD.moneda OR NEW.subtotal IS NOT OLD.subtotal OR NEW.iva_total IS NOT OLD.iva_total
        OR NEW.total IS NOT OLD.total OR NEW.snapshot_json IS NOT OLD.snapshot_json
        OR NEW.pdf_ruta IS NOT OLD.pdf_ruta OR NEW.pdf_sha256 IS NOT OLD.pdf_sha256
        OR NEW.emitido_por IS NOT OLD.emitido_por OR NEW.emitido_en IS NOT OLD.emitido_en
        OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en)
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: un pedido emitido solo puede anularse'); END;

CREATE TRIGGER trg_fac_pedido_lineas_emitido
    BEFORE UPDATE ON fac_pedido_lineas
    WHEN (SELECT estado FROM fac_pedidos WHERE id = OLD.pedido_id) <> 'BORRADOR'
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: las líneas de un pedido emitido no pueden modificarse'); END;

CREATE TRIGGER trg_fac_pedido_lineas_alta_emitido
    BEFORE INSERT ON fac_pedido_lineas
    WHEN (SELECT estado FROM fac_pedidos WHERE id = NEW.pedido_id) <> 'BORRADOR'
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: no se agregan líneas a un pedido emitido'); END;

CREATE TRIGGER trg_fac_documentos_inmutable
    BEFORE UPDATE ON fac_documentos WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.tipo IS NOT OLD.tipo OR NEW.letra IS NOT OLD.letra OR NEW.punto_venta IS NOT OLD.punto_venta
        OR NEW.numero IS NOT OLD.numero OR NEW.origen IS NOT OLD.origen OR NEW.corte_id IS NOT OLD.corte_id
        OR NEW.cliente_codigo IS NOT OLD.cliente_codigo OR NEW.fecha_emision IS NOT OLD.fecha_emision
        OR NEW.fecha_vencimiento IS NOT OLD.fecha_vencimiento OR NEW.moneda IS NOT OLD.moneda
        OR NEW.neto_gravado IS NOT OLD.neto_gravado OR NEW.exento IS NOT OLD.exento OR NEW.no_gravado IS NOT OLD.no_gravado
        OR NEW.iva_total IS NOT OLD.iva_total OR NEW.tributos_total IS NOT OLD.tributos_total OR NEW.total IS NOT OLD.total
        OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: un comprobante solo puede anularse'); END;

CREATE TRIGGER trg_fac_recibos_inmutable
    BEFORE UPDATE ON fac_recibos WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.punto_venta IS NOT OLD.punto_venta OR NEW.numero IS NOT OLD.numero
        OR NEW.cliente_codigo IS NOT OLD.cliente_codigo OR NEW.fecha_pago IS NOT OLD.fecha_pago
        OR NEW.moneda IS NOT OLD.moneda OR NEW.total IS NOT OLD.total
        OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: un recibo solo puede anularse'); END;

CREATE TRIGGER trg_fac_aplicaciones_nc_solo_anular
    BEFORE UPDATE ON fac_aplicaciones_nc WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.id IS NOT OLD.id OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
        OR NEW.nc_id IS NOT OLD.nc_id OR NEW.documento_id IS NOT OLD.documento_id OR NEW.importe IS NOT OLD.importe
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_aplicaciones_nc solo admite VIGENTE -> ANULADO'); END;

CREATE TRIGGER trg_fac_cancelaciones_corte_solo_anular
    BEFORE UPDATE ON fac_cancelaciones_corte WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.id IS NOT OLD.id OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
        OR NEW.documento_id IS NOT OLD.documento_id OR NEW.importe IS NOT OLD.importe OR NEW.criterio IS NOT OLD.criterio OR NEW.revision_de_id IS NOT OLD.revision_de_id OR NEW.nota IS NOT OLD.nota OR NEW.fecha_pago IS NOT OLD.fecha_pago OR NEW.moneda_pago IS NOT OLD.moneda_pago OR NEW.importe_pago IS NOT OLD.importe_pago OR NEW.tipo_cambio IS NOT OLD.tipo_cambio OR NEW.recibo_punto_venta IS NOT OLD.recibo_punto_venta OR NEW.recibo_numero IS NOT OLD.recibo_numero OR NEW.referencia IS NOT OLD.referencia OR NEW.recibo_total IS NOT OLD.recibo_total OR NEW.recibo_moneda IS NOT OLD.recibo_moneda
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_cancelaciones_corte solo admite VIGENTE -> ANULADO'); END;

CREATE TRIGGER trg_fac_estado_inicial_solo_anular
    BEFORE UPDATE ON fac_estado_inicial WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.id IS NOT OLD.id OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
        OR NEW.corte_id IS NOT OLD.corte_id OR NEW.cliente_codigo IS NOT OLD.cliente_codigo OR NEW.situacion IS NOT OLD.situacion OR NEW.resumen_json IS NOT OLD.resumen_json OR NEW.nota IS NOT OLD.nota
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_estado_inicial solo admite VIGENTE -> ANULADO'); END;

CREATE TRIGGER trg_fac_antecedentes_certificado_solo_anular
    BEFORE UPDATE ON fac_antecedentes_certificado WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.id IS NOT OLD.id OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
        OR NEW.corte_id IS NOT OLD.corte_id OR NEW.certificado_id IS NOT OLD.certificado_id OR NEW.cliente_codigo IS NOT OLD.cliente_codigo OR NEW.moneda IS NOT OLD.moneda OR NEW.neto_facturado IS NOT OLD.neto_facturado OR NEW.factura_tipo IS NOT OLD.factura_tipo OR NEW.factura_letra IS NOT OLD.factura_letra OR NEW.factura_punto_venta IS NOT OLD.factura_punto_venta OR NEW.factura_numero IS NOT OLD.factura_numero OR NEW.fecha_factura IS NOT OLD.fecha_factura OR NEW.tipo_cambio IS NOT OLD.tipo_cambio OR NEW.nota IS NOT OLD.nota
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_antecedentes_certificado solo admite VIGENTE -> ANULADO'); END;

CREATE TRIGGER trg_fac_imputaciones_solo_anular
    BEFORE UPDATE ON fac_imputaciones WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.id IS NOT OLD.id OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
        OR NEW.recibo_id IS NOT OLD.recibo_id OR NEW.documento_id IS NOT OLD.documento_id OR NEW.importe_recibo IS NOT OLD.importe_recibo OR NEW.tipo_cambio IS NOT OLD.tipo_cambio OR NEW.importe_documento IS NOT OLD.importe_documento
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_imputaciones solo admite VIGENTE -> ANULADO'); END;

CREATE TRIGGER trg_fac_adjuntos_solo_anular
    BEFORE UPDATE ON fac_adjuntos WHEN OLD.estado = 'ANULADO' OR NEW.estado IS NOT 'ANULADO'
        OR NEW.id IS NOT OLD.id OR NEW.creado_por IS NOT OLD.creado_por OR NEW.creado_en IS NOT OLD.creado_en
        OR NEW.entidad IS NOT OLD.entidad OR NEW.entidad_id IS NOT OLD.entidad_id OR NEW.ruta IS NOT OLD.ruta OR NEW.sha256 IS NOT OLD.sha256 OR NEW.url_externa IS NOT OLD.url_externa
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_adjuntos solo admite VIGENTE -> ANULADO'); END;

CREATE TRIGGER trg_fac_documento_lineas_fija
    BEFORE UPDATE ON fac_documento_lineas
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_documento_lineas no se modifica; se anula el registro padre'); END;

CREATE TRIGGER trg_fac_documento_tributos_fija
    BEFORE UPDATE ON fac_documento_tributos
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_documento_tributos no se modifica; se anula el registro padre'); END;

CREATE TRIGGER trg_fac_documento_vinculos_fija
    BEFORE UPDATE ON fac_documento_vinculos
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_documento_vinculos no se modifica; se anula el registro padre'); END;

CREATE TRIGGER trg_fac_recibo_valores_fija
    BEFORE UPDATE ON fac_recibo_valores
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_recibo_valores no se modifica; se anula el registro padre'); END;

CREATE TRIGGER trg_fac_eventos_fija
    BEFORE UPDATE ON fac_eventos
    BEGIN SELECT RAISE(ABORT, 'FAC_INMUTABLE: fac_eventos no se modifica; se anula el registro padre'); END;

CREATE TRIGGER trg_fac_cortes_sin_delete
    BEFORE DELETE ON fac_cortes
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_cortes no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_corte_conciliacion_sin_delete
    BEFORE DELETE ON fac_corte_conciliacion
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_corte_conciliacion no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_documentos_sin_delete
    BEFORE DELETE ON fac_documentos
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_documentos no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_documento_lineas_sin_delete
    BEFORE DELETE ON fac_documento_lineas
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_documento_lineas no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_documento_tributos_sin_delete
    BEFORE DELETE ON fac_documento_tributos
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_documento_tributos no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_documento_vinculos_sin_delete
    BEFORE DELETE ON fac_documento_vinculos
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_documento_vinculos no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_aplicaciones_nc_sin_delete
    BEFORE DELETE ON fac_aplicaciones_nc
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_aplicaciones_nc no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_cancelaciones_corte_sin_delete
    BEFORE DELETE ON fac_cancelaciones_corte
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_cancelaciones_corte no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_recibos_sin_delete
    BEFORE DELETE ON fac_recibos
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_recibos no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_recibo_valores_sin_delete
    BEFORE DELETE ON fac_recibo_valores
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_recibo_valores no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_imputaciones_sin_delete
    BEFORE DELETE ON fac_imputaciones
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_imputaciones no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_adjuntos_sin_delete
    BEFORE DELETE ON fac_adjuntos
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_adjuntos no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_eventos_sin_delete
    BEFORE DELETE ON fac_eventos
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_eventos no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_estado_inicial_sin_delete
    BEFORE DELETE ON fac_estado_inicial
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_estado_inicial no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_antecedentes_certificado_sin_delete
    BEFORE DELETE ON fac_antecedentes_certificado
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: fac_antecedentes_certificado no admite borrados; use la anulación'); END;

CREATE TRIGGER trg_fac_pedidos_sin_delete
    BEFORE DELETE ON fac_pedidos WHEN OLD.estado <> 'BORRADOR'
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: un pedido emitido no se borra; use la anulación'); END;

CREATE TRIGGER trg_fac_pedido_lineas_sin_delete
    BEFORE DELETE ON fac_pedido_lineas
    WHEN (SELECT estado FROM fac_pedidos WHERE id = OLD.pedido_id) <> 'BORRADOR'
    BEGIN SELECT RAISE(ABORT, 'FAC_SIN_DELETE: las líneas de un pedido emitido no se borran'); END;
