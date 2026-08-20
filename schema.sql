PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS viajes (
    id INTEGER PRIMARY KEY,
    solicitante TEXT,
    area TEXT,
    origen TEXT,
    destino TEXT,
    motivo TEXT,
    fecha_salida TEXT,
    fecha_regreso TEXT,
    chofer TEXT,
    vehiculo TEXT,
    acompanantes TEXT,
    alojamiento TEXT,
    estado TEXT,
    fecha_creacion TEXT,
    orden_salida_generada INTEGER DEFAULT 0,
    nro_orden_salida TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS recursos_viaje (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_viaje INTEGER NOT NULL UNIQUE,
    fecha TEXT,
    centro_costo TEXT,
    datos_solicitante TEXT,
    area_solicitante TEXT,
    partida TEXT,
    destino TEXT,
    motivo_viaje TEXT,
    fecha_salida_viaje TEXT,
    fecha_regreso_viaje TEXT,
    hora_ingreso_base TEXT,
    hora_salida TEXT,
    hora_regreso TEXT,
    duracion_jornadas TEXT,
    itinerario TEXT,
    rutas TEXT,
    paradas TEXT,
    chofer TEXT,
    chofer_viatico REAL DEFAULT 0,
    vehiculo TEXT,
    vehiculo_fuera_flota INTEGER DEFAULT 0,
    viaticos REAL DEFAULT 0,
    medio_pago TEXT,
    alojamiento TEXT,
    otros_gastos REAL DEFAULT 0,
    verificado_administracion TEXT,
    comprobacion_operaciones_logistica_json TEXT,
    raw_json TEXT,
    FOREIGN KEY (id_viaje) REFERENCES viajes (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS recurso_acompanantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_viaje INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    viatico REAL DEFAULT 0,
    FOREIGN KEY (id_viaje) REFERENCES viajes (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ordenes_salida (
    nro_orden TEXT PRIMARY KEY,
    fecha_orden TEXT,
    id_viaje INTEGER,
    estado TEXT,
    cierre_logistica_json TEXT,
    raw_json TEXT,
    FOREIGN KEY (id_viaje) REFERENCES viajes (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS gestion_operativa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nro_orden TEXT,
    fecha_orden TEXT,
    estado_orden TEXT,
    id_viaje INTEGER,
    centro_costo TEXT,
    proyecto TEXT,
    origen TEXT,
    destino TEXT,
    fecha_salida TEXT,
    fecha_regreso TEXT,
    jornadas INTEGER,
    legajo TEXT,
    nombre TEXT,
    rol TEXT,
    vehiculo TEXT,
    chofer TEXT,
    viatico REAL DEFAULT 0,
    horas_totales REAL DEFAULT 0,
    horas_normales REAL DEFAULT 0,
    horas_compensables REAL DEFAULT 0,
    costo_total REAL DEFAULT 0,
    fecha_cierre TEXT
);

CREATE TABLE IF NOT EXISTS vehiculos (
    codigo TEXT PRIMARY KEY,
    propiedad TEXT,
    marca TEXT,
    tipo TEXT,
    modelo TEXT,
    dominio TEXT,
    anio INTEGER,
    motor TEXT,
    chasis TEXT,
    sector TEXT,
    proyecto TEXT,
    operativo TEXT,
    habilitacion_pirquitas TEXT,
    habilitacion_exar TEXT,
    habilitacion_sdj TEXT,
    habilitacion_rincon TEXT,
    habilitacion_arli TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS choferes (
    nombre TEXT PRIMARY KEY,
    estado TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS personal (
    legajo TEXT PRIMARY KEY,
    nombre TEXT,
    cuil TEXT,
    fecha_nacimiento TEXT,
    activo TEXT DEFAULT 'ACTIVO',
    habilitacion_pirquitas TEXT,
    habilitacion_exar TEXT,
    habilitacion_sdj TEXT,
    habilitacion_rincon TEXT,
    habilitacion_arli TEXT,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_viajes_estado ON viajes (estado);
CREATE INDEX IF NOT EXISTS idx_viajes_fechas ON viajes (fecha_salida, fecha_regreso);
CREATE INDEX IF NOT EXISTS idx_recursos_id_viaje ON recursos_viaje (id_viaje);
CREATE INDEX IF NOT EXISTS idx_acompanantes_id_viaje ON recurso_acompanantes (id_viaje);
CREATE INDEX IF NOT EXISTS idx_ordenes_id_viaje ON ordenes_salida (id_viaje);

CREATE TABLE IF NOT EXISTS centros_costos (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE,
    activo INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO centros_costos (id, nombre, activo) VALUES
    (1, 'ADMINISTRACION', 1),
    (2, 'CONSTRUCCION', 1),
    (3, 'EXPLORACION', 1),
    (4, 'FINCA YALA', 1),
    (5, 'LABORATORIO', 1),
    (6, 'ALTO LA TORRE', 1),
    (7, 'SNOPEK', 1),
    (8, 'SMG METALURGICA', 1),
    (9, 'LOZANO', 1),
    (10, 'OBRA CIVIL', 1),
    (11, 'IMPORTACIONES', 1),
    (13, 'OPERACIÓN COMERCIAL', 1),
    (21, 'CANALES DE GUARDA', 1),
    (27, 'TALLER ALTO LA TORRE', 1),
    (28, 'PILETAS MILENNIAL PASTOS GRANDES', 1),
    (29, 'TERRAPLEN POZUELOS', 1),
    (30, 'UTE RUCA-VIAP', 1),
    (31, 'UTE BSD - SMG PILETAS', 1),
    (66, 'INV. PLANTA HIPOCLORITO SODIO', 1),
    (101, 'INVERSION MODULOS', 1),
    (102, 'MONTAJE COCINA-COMEDOR EXAR', 1),
    (103, 'TERMOFUSION POZO ANALIA SDJ', 1),
    (104, 'OBRA EXAR MOD. SANITARIO OFICINA', 1),
    (105, 'OBRA EXAR TRASLADO PABELLON B', 1),
    (106, 'OBRA EXAR - MANT. CAMINO Y REPOS. COMB.', 1),
    (131, 'COMERCIALIZACION ODORANTE', 1),
    (200, 'ALQUILER EQUIPOS MOVIMIENTO DE SUELO', 1),
    (201, 'ALQUILER EQUIPOS EVASA CATAMARCA', 1),
    (202, 'ALQUILER EQUIPOS EVASA JUNIN', 1),
    (203, 'MONTAJE EXAR', 1),
    (204, 'MONTAJE RUCA PANEL', 1),
    (205, 'ALQUILER EQUIPOS EVASA CORDOBA', 1),
    (206, 'ALQUILER EQUIPOS EVASA BAHIA BLANCA', 1),
    (207, 'ALQUILER EQUIPOS EVASA RIO NEGRO', 1),
    (208, 'MONTAJE LINDERO', 1),
    (209, 'MANTENIMIENTO DE CAMINO SSR', 1),
    (210, 'MANT. DE CAMINO SSR. TRAMO 2', 1),
    (211, 'ALQ. CAMIONES - OBRA GUAYATAYOC', 1),
    (212, 'OBRA LÍTICA', 1),
    (213, 'GERENCIA DE OBRAS', 1),
    (214, 'OBRA SALES DE JUJUY', 1),
    (215, 'OBRA ESPIRITU DE LOS ANDES', 1),
    (216, 'MINA PIRQUITAS - CANCHA DE FUTBOL', 1),
    (217, 'OBRA LÍTICA - POCITOS', 1),
    (218, 'OBRA LÍTICA - POZUELO. ALQ DE EQUIPOS', 1),
    (219, 'OBRA SALES DE JUJUY - COSECHA', 1),
    (220, 'OBRA SALES DE JUJUY - GAVIONES Y COLCHONETAS', 1),
    (221, 'OBRA LÍTICA - POZAZ 300/500 EFLUENTES', 1),
    (222, 'OBRA LÍTICA - BERMAS', 1),
    (223, 'MINA PIRQUITAS - REP Y PROT GASODUCTO', 1),
    (224, 'OBRA LÍTICA - ALQUILER POR HS MAQUINA', 1),
    (228, 'OBRA RINCON - RIO TINTO', 1),
    (400, 'PROYECTO ARGENTINA LITIO Y ENERGIA', 1),
    (401, 'PROYECTO POZUELOS', 1),
    (402, 'PROYECTO ADY PIPING', 1),
    (500, 'LITHIUM CHILE - CAMPAÑA SALAR ARIZARO', 1),
    (800, 'ASANOA (ALEX STEWART NOA)', 1),
    (803, 'GABRIEL BERNAL', 1),
    (900, 'NORLAB', 1),
    (901, 'MANTENIMIENTO EDILICIO SMG', 1),
    (902, 'GERENCIA GENERAL NG', 1),
    (903, 'DIRECTORIO DG', 1),
    (904, 'CATEX', 1),
    (905, 'TECTRAMIN ARGENTINA SRL', 1),
    (906, 'VIAP SRL', 1),
    (907, 'ABASTECIMIENTO EN OBRAS', 1),
    (908, 'ARIZARO', 1),
    (909, 'GERENCIA  CG', 1),
    (910, 'LITIAR', 1);

CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tipos_producto (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    activo INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS unidades_medida (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    activo INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ubicaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    parent_id INTEGER,
    ruta TEXT,
    activo INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(nombre, parent_id),
    FOREIGN KEY (parent_id) REFERENCES ubicaciones (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    descripcion TEXT NOT NULL,
    marca TEXT,
    modelo TEXT,
    categoria_id INTEGER,
    tipo_producto_id INTEGER,
    unidad_medida_id INTEGER,
    stock_minimo REAL DEFAULT 0,
    stock_maximo REAL DEFAULT 0,
    punto_reposicion REAL DEFAULT 0,
    ubicacion_id INTEGER,
    observaciones TEXT,
    fecha_alta TEXT,
    usuario_alta TEXT,
    raw_json TEXT,
    FOREIGN KEY (categoria_id) REFERENCES categorias (id) ON DELETE SET NULL,
    FOREIGN KEY (tipo_producto_id) REFERENCES tipos_producto (id) ON DELETE SET NULL,
    FOREIGN KEY (unidad_medida_id) REFERENCES unidades_medida (id) ON DELETE SET NULL,
    FOREIGN KEY (ubicacion_id) REFERENCES ubicaciones (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS remitos_ingreso (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero TEXT NOT NULL UNIQUE,
    proveedor TEXT,
    nro_remito_referencia TEXT,
    responsable_legajo TEXT,
    responsable_nombre TEXT,
    fecha TEXT,
    observaciones TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT,
    FOREIGN KEY (responsable_legajo) REFERENCES personal (legajo) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS proveedores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    "NOM_PROVEE" TEXT NOT NULL,
    "DOMICILIO" TEXT,
    "TELÉFONO_1" TEXT,
    "TELÉFONO_2" TEXT,
    "DESC_COND" TEXT,
    "COND_IVA" TEXT,
    "CUIT" TEXT,
    "C_POSTAL" TEXT,
    "LOCALIDAD" TEXT
);

CREATE TABLE IF NOT EXISTS remitos_ingreso_detalle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remito_id INTEGER NOT NULL,
    producto_id INTEGER NOT NULL,
    cantidad REAL NOT NULL,
    series_json TEXT,
    observaciones TEXT,
    vehiculo_codigo TEXT,
    proyecto TEXT,
    instalacion TEXT,
    FOREIGN KEY (remito_id) REFERENCES remitos_ingreso (id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS remitos_entrega (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero TEXT NOT NULL UNIQUE,
    destinatario TEXT,
    razon_social TEXT,
    cuit_dni TEXT,
    direccion TEXT,
    localidad TEXT,
    provincia TEXT,
    telefono TEXT,
    codigo_postal TEXT,
    transporte TEXT,
    dominio TEXT,
    entrega_legajo TEXT,
    entrega_nombre TEXT,
    recibe_legajo TEXT,
    recibe_nombre TEXT,
    observaciones TEXT,
    fecha TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT,
    FOREIGN KEY (entrega_legajo) REFERENCES personal (legajo) ON DELETE SET NULL,
    FOREIGN KEY (recibe_legajo) REFERENCES personal (legajo) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS remitos_entrega_detalle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remito_id INTEGER NOT NULL,
    producto_id INTEGER NOT NULL,
    cantidad REAL NOT NULL,
    series_json TEXT,
    observaciones TEXT,
    vehiculo_codigo TEXT,
    proyecto TEXT,
    instalacion TEXT,
    FOREIGN KEY (remito_id) REFERENCES remitos_entrega (id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS movimientos_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT,
    producto_id INTEGER NOT NULL,
    documento TEXT,
    tipo TEXT NOT NULL,
    cantidad REAL NOT NULL,
    stock_anterior REAL NOT NULL,
    stock_nuevo REAL NOT NULL,
    responsable_legajo TEXT,
    responsable_nombre TEXT,
    observaciones TEXT,
    remito_id INTEGER,
    vehiculo_codigo TEXT,
    proyecto TEXT,
    instalacion TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT,
    FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE RESTRICT,
    FOREIGN KEY (responsable_legajo) REFERENCES personal (legajo) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS inventarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero TEXT NOT NULL UNIQUE,
    fecha TEXT,
    estado TEXT DEFAULT 'PENDIENTE',
    responsable_legajo TEXT,
    responsable_nombre TEXT,
    observaciones TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT,
    FOREIGN KEY (responsable_legajo) REFERENCES personal (legajo) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS inventarios_detalle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inventario_id INTEGER NOT NULL,
    producto_id INTEGER NOT NULL,
    stock_sistema REAL NOT NULL,
    stock_fisico REAL,
    diferencia REAL,
    estado TEXT DEFAULT 'PENDIENTE',
    observaciones TEXT,
    FOREIGN KEY (inventario_id) REFERENCES inventarios (id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS ajustes_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inventario_id INTEGER,
    inventario_detalle_id INTEGER,
    producto_id INTEGER NOT NULL,
    cantidad_ajuste REAL NOT NULL,
    estado TEXT DEFAULT 'PENDIENTE_APROBACION',
    solicitado_por_legajo TEXT,
    solicitado_por_nombre TEXT,
    aprobado_por_legajo TEXT,
    aprobado_por_nombre TEXT,
    fecha_solicitud TEXT,
    fecha_aprobacion TEXT,
    observaciones TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT,
    FOREIGN KEY (inventario_id) REFERENCES inventarios (id) ON DELETE SET NULL,
    FOREIGN KEY (inventario_detalle_id) REFERENCES inventarios_detalle (id) ON DELETE SET NULL,
    FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE RESTRICT,
    FOREIGN KEY (solicitado_por_legajo) REFERENCES personal (legajo) ON DELETE SET NULL,
    FOREIGN KEY (aprobado_por_legajo) REFERENCES personal (legajo) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS roles_almacen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    permisos_json TEXT,
    activo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS personal_roles_almacen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legajo TEXT NOT NULL,
    rol_id INTEGER NOT NULL,
    UNIQUE(legajo, rol_id),
    FOREIGN KEY (legajo) REFERENCES personal (legajo) ON DELETE CASCADE,
    FOREIGN KEY (rol_id) REFERENCES roles_almacen (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS proyectos_habilitacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    clave TEXT NOT NULL UNIQUE,
    activo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS personal_habilitaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legajo TEXT NOT NULL,
    proyecto_id INTEGER NOT NULL,
    fecha_vencimiento TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(legajo, proyecto_id),
    FOREIGN KEY (legajo) REFERENCES personal (legajo) ON DELETE CASCADE,
    FOREIGN KEY (proyecto_id) REFERENCES proyectos_habilitacion (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_productos_codigo ON productos (codigo);
CREATE INDEX IF NOT EXISTS idx_productos_categoria ON productos (categoria_id);
CREATE INDEX IF NOT EXISTS idx_productos_ubicacion ON productos (ubicacion_id);
CREATE INDEX IF NOT EXISTS idx_movimientos_fecha ON movimientos_stock (fecha);
CREATE INDEX IF NOT EXISTS idx_movimientos_producto ON movimientos_stock (producto_id);
CREATE INDEX IF NOT EXISTS idx_movimientos_documento ON movimientos_stock (documento);
CREATE INDEX IF NOT EXISTS idx_ri_fecha ON remitos_ingreso (fecha);
CREATE INDEX IF NOT EXISTS idx_re_fecha ON remitos_entrega (fecha);
CREATE INDEX IF NOT EXISTS idx_inv_estado ON inventarios (estado);
CREATE INDEX IF NOT EXISTS idx_ajustes_estado ON ajustes_stock (estado);

CREATE TABLE IF NOT EXISTS familias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS marcas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    activo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS modelos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_marca INTEGER,
    nombre TEXT NOT NULL,
    descripcion TEXT,
    activo INTEGER DEFAULT 1,
    UNIQUE(id_marca, nombre),
    FOREIGN KEY (id_marca) REFERENCES marcas (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS proyectos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    cliente TEXT,
    activo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS prioridades_compra (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    activo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS estados_compra (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    activo INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS solicitud_compra (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_solicitud TEXT NOT NULL UNIQUE,
    fecha_solicitud TEXT NOT NULL,
    id_solicitante INTEGER NOT NULL,
    id_proyecto INTEGER NOT NULL,
    id_prioridad INTEGER NOT NULL,
    observaciones TEXT,
    id_estado INTEGER NOT NULL,
    fecha_creacion TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_modificacion TEXT,
    FOREIGN KEY (id_solicitante) REFERENCES personal (legajo) ON DELETE RESTRICT,
    FOREIGN KEY (id_proyecto) REFERENCES proyectos (id) ON DELETE RESTRICT,
    FOREIGN KEY (id_prioridad) REFERENCES prioridades_compra (id) ON DELETE RESTRICT,
    FOREIGN KEY (id_estado) REFERENCES estados_compra (id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS solicitud_compra_detalle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_solicitud INTEGER NOT NULL,
    id_centro_costo INTEGER NOT NULL,
    id_vehiculo INTEGER,
    id_maquinaria INTEGER,
    descripcion_articulo TEXT NOT NULL,
    motivo_compra TEXT NOT NULL,
    cantidad REAL NOT NULL,
    unidad_medida TEXT,
    observacion TEXT,
    estado_aprobacion TEXT NOT NULL DEFAULT 'Pendiente',
    motivo_rechazo TEXT,
    usuario_resolucion TEXT,
    FOREIGN KEY (id_solicitud) REFERENCES solicitud_compra (id) ON DELETE CASCADE,
    FOREIGN KEY (id_centro_costo) REFERENCES centros_costos (id) ON DELETE RESTRICT,
    FOREIGN KEY (id_vehiculo) REFERENCES vehiculos (id) ON DELETE SET NULL,
    FOREIGN KEY (id_maquinaria) REFERENCES vehiculos (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS OrdenCompra (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_oc TEXT NOT NULL UNIQUE,
    fecha TEXT,
    proveedor_id INTEGER,
    cotizacion TEXT,
    moneda TEXT,
    tipo_cambio REAL,
    forma_pago TEXT,
    lugar_entrega TEXT,
    transporte TEXT,
    validez_oferta TEXT,
    observaciones TEXT,
    solicitante TEXT,
    aprobador TEXT,
    estado TEXT,
    fecha_creacion TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proveedor_id) REFERENCES proveedores (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS OrdenCompraDetalle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    orden_compra_id INTEGER NOT NULL,
    codigo_proveedor TEXT,
    articulo_id INTEGER,
    descripcion TEXT,
    cantidad REAL,
    precio_unitario REAL,
    subtotal REAL,
    centro_costo_id INTEGER,
    FOREIGN KEY (orden_compra_id) REFERENCES OrdenCompra (id) ON DELETE CASCADE,
    FOREIGN KEY (articulo_id) REFERENCES productos (id) ON DELETE SET NULL,
    FOREIGN KEY (centro_costo_id) REFERENCES centros_costos (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_orden_compra_detalle_orden
    ON OrdenCompraDetalle (orden_compra_id);

CREATE TABLE IF NOT EXISTS OrdenCompraHistorial (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    orden_compra_id INTEGER NOT NULL,
    accion TEXT NOT NULL,
    detalle TEXT NOT NULL,
    usuario TEXT NOT NULL,
    usuario_nombre TEXT NOT NULL,
    fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (orden_compra_id) REFERENCES OrdenCompra (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_orden_compra_historial_orden
    ON OrdenCompraHistorial (orden_compra_id);

CREATE TABLE IF NOT EXISTS OrdenCompraDetalleSolicitud (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    orden_compra_detalle_id INTEGER NOT NULL,
    solicitud_compra_detalle_id INTEGER NOT NULL UNIQUE,
    fecha_vinculacion TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (orden_compra_detalle_id) REFERENCES OrdenCompraDetalle (id) ON DELETE CASCADE,
    FOREIGN KEY (solicitud_compra_detalle_id) REFERENCES solicitud_compra_detalle (id) ON DELETE RESTRICT,
    UNIQUE (orden_compra_detalle_id, solicitud_compra_detalle_id)
);

CREATE INDEX IF NOT EXISTS idx_oc_detalle_solicitud_oc_detalle
    ON OrdenCompraDetalleSolicitud (orden_compra_detalle_id);

CREATE TABLE IF NOT EXISTS instalaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_proyecto INTEGER,
    codigo TEXT NOT NULL,
    nombre TEXT NOT NULL,
    activo INTEGER DEFAULT 1,
    UNIQUE(id_proyecto, codigo),
    FOREIGN KEY (id_proyecto) REFERENCES proyectos (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_apellido TEXT NOT NULL,
    dni TEXT,
    legajo TEXT,
    correo TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    estado TEXT DEFAULT 'ACTIVO',
    tipo_usuario TEXT DEFAULT 'CONSULTOR',
    modulos_json TEXT,
    bloqueado INTEGER DEFAULT 0,
    intentos_fallidos INTEGER DEFAULT 0,
    password_temporal INTEGER DEFAULT 1,
    ultimo_acceso TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS roles_funcionales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    activo INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usuario_roles_funcionales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    rol_id INTEGER NOT NULL,
    UNIQUE(usuario_id, rol_id),
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE,
    FOREIGN KEY (rol_id) REFERENCES roles_funcionales (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS historial_accesos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER,
    username_input TEXT,
    evento TEXT,
    detalle TEXT,
    ip TEXT,
    user_agent TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_usuarios_correo ON usuarios (correo);
CREATE INDEX IF NOT EXISTS idx_usuarios_estado ON usuarios (estado);
CREATE INDEX IF NOT EXISTS idx_historial_fecha ON historial_accesos (created_at);
CREATE INDEX IF NOT EXISTS idx_historial_usuario ON historial_accesos (usuario_id);

CREATE TABLE IF NOT EXISTS stock_consumibles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_producto INTEGER NOT NULL UNIQUE,
    stock_actual REAL DEFAULT 0,
    updated_at TEXT,
    FOREIGN KEY (id_producto) REFERENCES productos (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id INTEGER NOT NULL UNIQUE,
    id_familia INTEGER,
    id_marca INTEGER,
    id_modelo INTEGER,
    id_unidad INTEGER,
    id_ubicacion INTEGER,
    producto_codigo TEXT,
    producto_descripcion TEXT,
    familia_nombre TEXT,
    marca_nombre TEXT,
    modelo_nombre TEXT,
    unidad_nombre TEXT,
    stock_actual REAL NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    raw_json TEXT,
    FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE RESTRICT,
    FOREIGN KEY (id_familia) REFERENCES familias (id) ON DELETE SET NULL,
    FOREIGN KEY (id_marca) REFERENCES marcas (id) ON DELETE SET NULL,
    FOREIGN KEY (id_modelo) REFERENCES modelos (id) ON DELETE SET NULL,
    FOREIGN KEY (id_unidad) REFERENCES unidades_medida (id) ON DELETE SET NULL,
    FOREIGN KEY (id_ubicacion) REFERENCES ubicaciones (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stock_producto ON stock (producto_id);

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL,
    numero TEXT NOT NULL UNIQUE,
    fecha TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'CONFIRMADO',
    responsable TEXT,
    observaciones TEXT,
    payload_json TEXT,
    FOREIGN KEY (responsable) REFERENCES personal (legajo) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS documentos_detalle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_documento INTEGER NOT NULL,
    id_producto INTEGER,
    cantidad REAL DEFAULT 0,
    id_vehiculo TEXT,
    id_proyecto INTEGER,
    id_instalacion INTEGER,
    observaciones TEXT,
    FOREIGN KEY (id_documento) REFERENCES documentos (id) ON DELETE CASCADE,
    FOREIGN KEY (id_producto) REFERENCES productos (id) ON DELETE RESTRICT,
    FOREIGN KEY (id_vehiculo) REFERENCES vehiculos (codigo) ON DELETE SET NULL,
    FOREIGN KEY (id_proyecto) REFERENCES proyectos (id) ON DELETE SET NULL,
    FOREIGN KEY (id_instalacion) REFERENCES instalaciones (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS movimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL,
    documento TEXT,
    id_documento INTEGER,
    id_producto INTEGER,
    cantidad REAL DEFAULT 0,
    stock_anterior REAL DEFAULT 0,
    stock_nuevo REAL DEFAULT 0,
    id_personal TEXT,
    id_vehiculo TEXT,
    id_proyecto INTEGER,
    id_instalacion INTEGER,
    observaciones TEXT,
    raw_json TEXT,
    FOREIGN KEY (id_documento) REFERENCES documentos (id) ON DELETE SET NULL,
    FOREIGN KEY (id_producto) REFERENCES productos (id) ON DELETE RESTRICT,
    FOREIGN KEY (id_personal) REFERENCES personal (legajo) ON DELETE SET NULL,
    FOREIGN KEY (id_vehiculo) REFERENCES vehiculos (codigo) ON DELETE SET NULL,
    FOREIGN KEY (id_proyecto) REFERENCES proyectos (id) ON DELETE SET NULL,
    FOREIGN KEY (id_instalacion) REFERENCES instalaciones (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    usuario TEXT,
    accion TEXT NOT NULL,
    tabla TEXT NOT NULL,
    registro INTEGER,
    valor_anterior TEXT,
    valor_nuevo TEXT,
    FOREIGN KEY (usuario) REFERENCES personal (legajo) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS adjuntos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL,
    archivo TEXT NOT NULL,
    id_documento INTEGER,
    id_inventario INTEGER,
    id_ajuste INTEGER,
    FOREIGN KEY (id_documento) REFERENCES documentos (id) ON DELETE CASCADE,
    FOREIGN KEY (id_inventario) REFERENCES inventarios (id) ON DELETE CASCADE,
    FOREIGN KEY (id_ajuste) REFERENCES ajustes_stock (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_modelos_marca ON modelos (id_marca);
CREATE INDEX IF NOT EXISTS idx_instalaciones_proyecto ON instalaciones (id_proyecto);
CREATE INDEX IF NOT EXISTS idx_doc_tipo ON documentos (tipo);
CREATE INDEX IF NOT EXISTS idx_doc_fecha ON documentos (fecha);
CREATE INDEX IF NOT EXISTS idx_doc_detalle_doc ON documentos_detalle (id_documento);
CREATE INDEX IF NOT EXISTS idx_mov_v2_fecha ON movimientos (fecha);
CREATE INDEX IF NOT EXISTS idx_mov_v2_producto ON movimientos (id_producto);
CREATE INDEX IF NOT EXISTS idx_auditoria_tabla_registro ON auditoria (tabla, registro);
CREATE INDEX IF NOT EXISTS idx_adj_doc ON adjuntos (id_documento);
