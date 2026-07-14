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

CREATE TABLE IF NOT EXISTS fleetcare_checklists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT,
    correo_operador TEXT,
    proyecto TEXT,
    nro_checklist TEXT,
    nro_parte_diario TEXT,
    horometro_actual TEXT,
    kilometro_actual TEXT,
    codigo_equipo TEXT,
    operador TEXT,
    supervisor TEXT,
    tipo_equipo TEXT,
    estado_general TEXT,
    observaciones TEXT,
    fotos_json TEXT,
    raw_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS fleetcare_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id INTEGER NOT NULL,
    categoria TEXT,
    item_key TEXT,
    item_label TEXT,
    estado TEXT,
    comentario TEXT,
    foto_url TEXT,
    genera_incidencia INTEGER DEFAULT 0,
    raw_json TEXT,
    FOREIGN KEY (checklist_id) REFERENCES fleetcare_checklists (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fleetcare_incidencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id INTEGER,
    fecha_deteccion TEXT,
    codigo_equipo TEXT,
    proyecto TEXT,
    categoria TEXT,
    item_key TEXT,
    item_label TEXT,
    estado_detectado TEXT,
    observacion TEXT,
    evidencia_json TEXT,
    prioridad TEXT,
    estado TEXT DEFAULT 'Pendiente',
    responsable TEXT,
    fecha_asignacion TEXT,
    fecha_resolucion TEXT,
    tiempo_respuesta TEXT,
    accion_correctiva TEXT,
    costo_reparacion REAL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    FOREIGN KEY (checklist_id) REFERENCES fleetcare_checklists (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_fleetcare_checklists_equipo ON fleetcare_checklists (codigo_equipo);
CREATE INDEX IF NOT EXISTS idx_fleetcare_checklists_proyecto ON fleetcare_checklists (proyecto);
CREATE INDEX IF NOT EXISTS idx_fleetcare_items_checklist ON fleetcare_items (checklist_id);
CREATE INDEX IF NOT EXISTS idx_fleetcare_incidencias_equipo ON fleetcare_incidencias (codigo_equipo);
CREATE INDEX IF NOT EXISTS idx_fleetcare_incidencias_estado ON fleetcare_incidencias (estado);
CREATE INDEX IF NOT EXISTS idx_fleetcare_incidencias_prioridad ON fleetcare_incidencias (prioridad);
