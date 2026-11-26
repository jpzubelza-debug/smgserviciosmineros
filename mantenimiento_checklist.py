"""Mantenimiento › Check list de novedades (web), armado en tres capas.

CHECK LIST = BASE (todo equipo autopropulsado) + FAMILIA (vial / maquinaria / remolque) + MÓDULOS (según el tipo de equipo).
- Remolque no usa la Base.
- Escala Bueno / Regular / Malo; N/A solo en ítems opcionales. La documentación se marca Vigente / Vencida.
- Malo o Regular exige comentario y foto. Vencida exige comentario. Algunos ítems piden foto siempre (nivel de aceite).
- Cada Malo (o Vencida) entra a Novedades con el mismo circuito; si ya hay una pendiente del mismo ítem, se confirma.
  Un ítem crítico en Malo da resultado No apto y la novedad entra como URGENTE.
- El tipo de equipo es un catálogo (mt_tipos_equipo) y vehiculos.tipo guarda su nombre: se estandariza y se configura en
  Logística › Vehículos › Tipos de equipo. El texto libre anterior queda en vehiculos.tipo_original. Desde el texto se
  propone un tipo por palabras clave. Cada equipo puede tener sus neumáticos y sumar o quitar módulos (mt_equipos).
- Cada respuesta guarda el texto del módulo y del ítem de ese día: editar la plantilla no cambia los check list viejos.
Las fotos las guarda main.py en Doc_Mantenimiento; acá se registra la ruta.
"""
import json
import re
import unicodedata

import mantenimiento as mt

CAPAS = ("BASE", "FAMILIA", "MODULO")
FAMILIAS = ("VIAL", "MAQUINARIA", "REMOLQUE")
VALORES_ESTADO = ("BUENO", "REGULAR", "MALO", "NA")
VALORES_VIGENCIA = ("VIGENTE", "VENCIDA", "NA")

DDL = (
    """
    CREATE TABLE IF NOT EXISTS mt_ck_modulos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT NOT NULL UNIQUE,
        nombre TEXT NOT NULL,
        capa TEXT NOT NULL CHECK (capa IN ('BASE', 'FAMILIA', 'MODULO')),
        familia TEXT CHECK (familia IS NULL OR familia IN ('VIAL', 'MAQUINARIA', 'REMOLQUE')),
        es_neumaticos INTEGER NOT NULL DEFAULT 0,
        orden INTEGER NOT NULL DEFAULT 0,
        activo INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt_ck_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        modulo_id INTEGER NOT NULL REFERENCES mt_ck_modulos (id),
        texto TEXT NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'ESTADO' CHECK (tipo IN ('ESTADO', 'VIGENCIA')),
        opcional INTEGER NOT NULL DEFAULT 0,
        critico INTEGER NOT NULL DEFAULT 0,
        foto_obligatoria INTEGER NOT NULL DEFAULT 0,
        orden INTEGER NOT NULL DEFAULT 0,
        activo INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_ck_items_mod ON mt_ck_items (modulo_id)",
    """
    CREATE TABLE IF NOT EXISTS mt_tipos_equipo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE,
        familia TEXT NOT NULL CHECK (familia IN ('VIAL', 'MAQUINARIA', 'REMOLQUE')),
        neumaticos INTEGER NOT NULL DEFAULT 0,
        auxilios INTEGER NOT NULL DEFAULT 0,
        palabras_clave TEXT,
        activo INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt_tipo_equipo_modulos (
        tipo_equipo_id INTEGER NOT NULL REFERENCES mt_tipos_equipo (id) ON DELETE CASCADE,
        modulo_id INTEGER NOT NULL REFERENCES mt_ck_modulos (id),
        PRIMARY KEY (tipo_equipo_id, modulo_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt_equipo_modulos (
        ci TEXT NOT NULL,
        modulo_id INTEGER NOT NULL REFERENCES mt_ck_modulos (id),
        accion TEXT NOT NULL CHECK (accion IN ('SUMAR', 'QUITAR')),
        PRIMARY KEY (ci, modulo_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt_checklists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ci TEXT NOT NULL,
        tipo_equipo TEXT NOT NULL,
        familia TEXT NOT NULL,
        fecha TEXT NOT NULL,
        horometro REAL,
        km REAL,
        operador TEXT,
        ubicacion TEXT,
        resultado TEXT NOT NULL CHECK (resultado IN ('APTO', 'NO_APTO')),
        observaciones TEXT,
        realizado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        total_items INTEGER NOT NULL DEFAULT 0,
        malos INTEGER NOT NULL DEFAULT 0,
        regulares INTEGER NOT NULL DEFAULT 0,
        novedades INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_ck_ci ON mt_checklists (ci, fecha)",
    """
    CREATE TABLE IF NOT EXISTS mt_checklist_respuestas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        checklist_id INTEGER NOT NULL REFERENCES mt_checklists (id) ON DELETE CASCADE,
        orden INTEGER NOT NULL,
        clave TEXT NOT NULL,
        modulo TEXT NOT NULL,
        item TEXT NOT NULL,
        tipo TEXT NOT NULL,
        critico INTEGER NOT NULL DEFAULT 0,
        valor TEXT NOT NULL,
        comentario TEXT,
        foto_archivo TEXT,
        foto_tipo TEXT,
        novedad_id INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_ckr_ck ON mt_checklist_respuestas (checklist_id)",
)

# ----------------------------------------------------------------------------
# Plantilla inicial (la que propuso el usuario el 2026-11-24). Se carga una sola vez; después se edita en pantalla.
# Formato del ítem: texto, o (texto, banderas) con banderas: C = crítico, O = opcional (permite N/A),
# F = foto siempre, V = vigencia (Vigente / Vencida). Los críticos son una propuesta inicial, editable.
# ----------------------------------------------------------------------------
PLANTILLA = [
    # codigo, nombre, capa, familia, es_neumaticos, items
    ("motor", "Motor", "BASE", None, 0, ["Facilidad de arranque", ("Nivel de aceite", "F"), "Pérdida de aceite", ("Pérdida de combustible", "C")]),
    ("refrigeracion", "Refrigeración", "BASE", None, 0, [("Nivel de refrigerante", "F"), "Mangueras", "Radiador", "Temperatura"]),
    ("cabina", "Cabina", "BASE", None, 0, [("Aire acondicionado", "O"), ("Calefacción", "O"), "Asientos", "Limpieza interior"]),
    ("carroceria", "Carrocería", "BASE", None, 0, ["Pintura", "Espejos laterales", ("Espejo interior", "O"), "Parabrisas", "Limpiaparabrisas",
                                                  "Ventanillas", "Cierre de puertas", "Cinta reflectiva"]),
    ("frenos", "Frenos", "BASE", None, 0, [("Frenado general", "C"), ("Freno de mano o de estacionamiento", "C")]),
    ("transmision", "Transmisión", "BASE", None, 0, ["Adelante / atrás", "Nivel de aceite", "Palanca de cambio"]),
    ("luces", "Luces", "BASE", None, 0, ["Intermitentes", "Luz de freno", "Luz de retroceso", "Baliza ámbar", "Batería"]),
    ("direccion", "Dirección y chasis", "BASE", None, 0, [("Dirección", "C"), "Bujes y perno", "Barra de dirección", "Estribos", "Engrase general"]),
    ("senales", "Señales sonoras", "BASE", None, 0, ["Bocina", ("Alarma de retroceso", "C")]),
    ("seguridad", "Seguridad", "BASE", None, 0, [("Cinturón de seguridad", "C"), "Estado de asientos", ("Extintor", "C"), "Botiquín",
                                                "Comunicación (radio o handy)"]),
    ("documentacion", "Documentación", "FAMILIA", "VIAL", 0, [("Póliza de seguro", "V"), ("RTV", "V"), ("Cédula verde", "V")]),
    ("vial", "Vial", "FAMILIA", "VIAL", 0, ["Luces altas y bajas", ("Pedal de freno", "C"), "Pastillas delanteras", "Cintas traseras",
                                           "Paquete elástico", "Barra estabilizadora"]),
    ("kit_vial", "Kit vial", "FAMILIA", "VIAL", 0, ["Llave de rueda", "Gato", "Calzas", "Pértiga y luz LED", "Triángulos"]),
    ("maquinaria", "Maquinaria vial", "FAMILIA", "MAQUINARIA", 0, [("Mandos de accionamiento", "C"), ("Frenos delanteros y traseros", "C"),
                                                                  "Compuertas de motor", "Pasamanos"]),
    ("remolque", "Remolque", "FAMILIA", "REMOLQUE", 0, ["Luces de posición", "Intermitentes", "Luz de freno", "Luz de retroceso",
                                                       ("Punto y perno de enganche", "C"), "Lanza", ("Cadenas de seguridad", "C"),
                                                       "Pies de apoyo", "Paragolpe", "Paquete elástico", "Cartel de seguridad"]),
    ("neumaticos", "Neumáticos", "MODULO", None, 1, []),
    ("tren_rodante", "Tren rodante", "MODULO", None, 0, ["Corona de tracción", "Tensión de cadena", "Cadena", "Pérdida de fluidos",
                                                        "Rolos superiores", "Rolos inferiores simples y dobles", "Ruidos"]),
    ("traccion", "Tracción", "MODULO", None, 0, ["Perilla 4x4 / 6x4 o traba diferencial"]),
    ("aguilon", "Aguilón", "MODULO", None, 0, ["Alemites", "Canales de lubricación", "Anclaje al chasis", "Juego perno-buje",
                                              ("Pérdida hidráulica", "C"), "Cilindro"]),
    ("brazo", "Brazo de elevación y volteo", "MODULO", None, 0, ["Alemites", "Canales de lubricación", "Anclaje al chasis", "Juego perno-buje",
                                                                ("Pérdida hidráulica", "C"), "Cilindros de elevación", "Cilindros de volteo"]),
    ("balde", "Balde", "MODULO", None, 0, ["Bujes", "Cantoneras", "Engrase", "Fisuras", "Juego de anclaje", "Uñas"]),
    ("hoja", "Hoja vertedera", "MODULO", None, 0, ["Pernos", "Cuchilla", "Fisuras", "Cantoneras", "Barra de tiro", "Círculo",
                                                  "Cilindro de elevación", "Pérdidas", "Vertedera", ("Escarificador", "O"), "Tándem"]),
    ("rodillo", "Rodillo vibratorio", "MODULO", None, 0, ["Vibración", "Fisuras del rodillo", "Pérdidas hidráulicas", "Perno de articulación",
                                                         "Cilindro de dirección", "Balancín"]),
    ("volcador", "Volcador", "MODULO", None, 0, ["Cilindro de elevación", "Pérdidas hidráulicas", "Toma de fuerza", "Ganchos de compuerta",
                                                "Compuerta", "Bomba hidráulica"]),
    ("tanque_agua", "Tanque de agua", "MODULO", None, 0, ['Llave de 3"', "Cañería de riego", "Sujeción", "Tapa", "Pérdidas"]),
    ("tanque_combustible", "Tanque de combustible", "MODULO", None, 0, ["Bomba", "Caudalímetro", "Pistola", "Carrete y manguera", "Sujeción",
                                                                       "Estado del tanque", ("Pérdidas en tanque y cañerías", "C"),
                                                                       "Válvula de corte", ("Puesta a tierra (pinzas, jabalina, aislamiento)", "C"),
                                                                       ("Kit antiderrame", "C"), "Baldes de arena"]),
    ("semirremolque", "Semirremolque", "MODULO", None, 0, [("Plato de enganche", "C"), "Conexión de luces y de aire", "Estado del semi",
                                                          "Cubiertas", "Luces", "Cartelería", "Banderines", ("Documentación del semi", "V")]),
]

# nombre, familia, neumáticos, auxilios, módulos, palabras clave (para proponer el tipo desde vehiculos.tipo; la primera que coincide gana)
TIPOS = [
    ("Regador", "VIAL", 10, 1, ["neumaticos", "traccion", "tanque_agua"], "regador"),
    ("Camión fuera de ruta", "MAQUINARIA", 6, 0, ["neumaticos", "traccion", "volcador"], "fuera de ruta,wide body"),
    ("Camión con plato", "VIAL", 10, 1, ["neumaticos", "traccion", "semirremolque"], "plato,tractor"),
    ("Tolva", "VIAL", 10, 1, ["neumaticos", "traccion", "volcador"], "tolva,volcador"),
    ("Cisterna", "VIAL", 10, 1, ["neumaticos", "traccion", "tanque_combustible"], "cisterna,combustible"),
    ("Camión mixer", "VIAL", 10, 1, ["neumaticos", "traccion"], "mixer"),
    ("Camión hidrogrúa", "VIAL", 10, 1, ["neumaticos", "traccion"], "hidrogrua,grua"),
    ("Camión caja plana", "VIAL", 6, 1, ["neumaticos", "traccion"], "caja plana,camion"),
    ("Camioneta", "VIAL", 4, 2, ["neumaticos", "traccion"], "camioneta,pick up,pickup"),
    ("Minibús", "VIAL", 6, 1, ["neumaticos"], "minibus,colectivo"),
    ("Auto / utilitario", "VIAL", 4, 1, ["neumaticos"], "sedan,suv,utilitario,auto"),
    ("Motoniveladora", "MAQUINARIA", 6, 0, ["neumaticos", "traccion", "hoja"], "motoniveladora"),
    ("Retropala", "MAQUINARIA", 4, 0, ["neumaticos", "traccion", "aguilon", "brazo", "balde"], "retroexcavadora,retropala"),
    ("Excavadora", "MAQUINARIA", 0, 0, ["tren_rodante", "aguilon", "balde"], "excavadora"),
    ("Cargadora", "MAQUINARIA", 4, 0, ["neumaticos", "brazo", "balde"], "cargadora,pala"),
    ("Rodillo", "MAQUINARIA", 2, 0, ["neumaticos", "rodillo"], "rodillo,compactador,vibrocompactador"),
    ("Topadora", "MAQUINARIA", 0, 0, ["tren_rodante"], "topadora"),
    ("Chulengo", "REMOLQUE", 2, 0, ["neumaticos", "tanque_combustible"], "chulengo"),
    ("Carretón", "REMOLQUE", 8, 0, ["neumaticos"], "carreton,acoplado,semi"),
]


def migrar(conn):
    for sentencia in DDL:
        conn.execute(sentencia)
    cols = {f[1] for f in conn.execute("PRAGMA table_info(mt_equipos)")}
    # tipo_equipo_id quedó sin uso: el tipo vive en vehiculos.tipo (Logística › Vehículos). neumáticos/auxilios sí se usan.
    for col, tipo in (("tipo_equipo_id", "INTEGER"), ("neumaticos", "INTEGER"), ("auxilios", "INTEGER")):
        if col not in cols:
            conn.execute(f"ALTER TABLE mt_equipos ADD COLUMN {col} {tipo}")
    if "checklist_id" not in {f[1] for f in conn.execute("PRAGMA table_info(mt_novedades)")}:
        conn.execute("ALTER TABLE mt_novedades ADD COLUMN checklist_id INTEGER")
    # Texto libre que tenía vehiculos.tipo antes de estandarizarlo (p. ej. «camion con tolva 6x4 17 m3»).
    if "tipo_original" not in {f[1] for f in conn.execute("PRAGMA table_info(vehiculos)")}:
        conn.execute("ALTER TABLE vehiculos ADD COLUMN tipo_original TEXT")
    if conn.execute("SELECT COUNT(*) FROM mt_ck_modulos").fetchone()[0] == 0:
        _sembrar(conn)


def _sembrar(conn):
    ids = {}
    for orden, (codigo, nombre, capa, familia, neum, items) in enumerate(PLANTILLA, 1):
        cur = conn.execute("INSERT INTO mt_ck_modulos (codigo, nombre, capa, familia, es_neumaticos, orden) VALUES (?, ?, ?, ?, ?, ?)",
                           (codigo, nombre, capa, familia, neum, orden * 10))
        ids[codigo] = cur.lastrowid
        for i, item in enumerate(items, 1):
            texto, banderas = (item, "") if isinstance(item, str) else item
            conn.execute(
                """INSERT INTO mt_ck_items (modulo_id, texto, tipo, opcional, critico, foto_obligatoria, orden)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cur.lastrowid, texto, "VIGENCIA" if "V" in banderas else "ESTADO", int("O" in banderas),
                 int("C" in banderas), int("F" in banderas), i * 10))
    if conn.execute("SELECT COUNT(*) FROM mt_tipos_equipo").fetchone()[0] == 0:
        for nombre, familia, neum, aux, modulos, palabras in TIPOS:
            cur = conn.execute("INSERT INTO mt_tipos_equipo (nombre, familia, neumaticos, auxilios, palabras_clave) VALUES (?, ?, ?, ?, ?)",
                               (nombre, familia, neum, aux, palabras))
            for m in modulos:
                conn.execute("INSERT INTO mt_tipo_equipo_modulos (tipo_equipo_id, modulo_id) VALUES (?, ?)", (cur.lastrowid, ids[m]))


# ============================================================================
# Tipos de equipo: catálogo estándar. vehiculos.tipo guarda el nombre del tipo; se configura en Logística › Vehículos.
# ============================================================================
def _norm(texto):
    t = unicodedata.normalize("NFD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def _tipos(conn):
    return conn.execute("SELECT * FROM mt_tipos_equipo WHERE activo = 1 ORDER BY id").fetchall()


def tipo_por_nombre(conn, nombre, tipos=None):
    clave = _norm(nombre)
    if not clave:
        return None
    return next((t for t in (tipos if tipos is not None else _tipos(conn)) if _norm(t["nombre"]) == clave), None)


def proponer_tipo(conn, texto_vehiculo, tipos=None):
    """Primer tipo cuya palabra clave aparece en el texto (el orden de la tabla importa: «Tractor regador» → Regador)."""
    t = f" {_norm(texto_vehiculo)} "
    for tipo in tipos if tipos is not None else _tipos(conn):
        for palabra in (tipo["palabras_clave"] or "").split(","):
            palabra = _norm(palabra)
            if palabra and re.search(rf"\b{re.escape(palabra)}\b", t):
                return tipo
    return None


def equipos_y_tipos(conn):
    """Cada vehículo con su tipo: estandarizado (vehiculos.tipo es un nombre del catálogo) o propuesto desde el texto libre."""
    tipos = _tipos(conn)
    extras = {f["ci"].upper(): f for f in conn.execute("SELECT ci, neumaticos, auxilios FROM mt_equipos")}
    salida = []
    for v in conn.execute(f"SELECT codigo, marca, modelo, tipo, tipo_original FROM vehiculos WHERE TRIM(COALESCE(codigo, '')) <> '' AND {mt.VEHICULO_ACTIVO_SQL} ORDER BY codigo"):
        ci = v["codigo"].strip()
        fila = extras.get(ci.upper())
        estandar = tipo_por_nombre(conn, v["tipo"], tipos)
        propuesto = None if estandar else proponer_tipo(conn, v["tipo"], tipos)
        salida.append({
            "ci": ci, "display": mt._equipo_display(v), "tipo": (v["tipo"] or "").strip(), "tipo_original": v["tipo_original"],
            "tipo_equipo_id": (estandar or propuesto or {"id": None})["id"], "estandarizado": bool(estandar),
            "neumaticos": fila["neumaticos"] if fila else None, "auxilios": fila["auxilios"] if fila else None,
            "modulos": {f["modulo_id"]: f["accion"] for f in conn.execute("SELECT modulo_id, accion FROM mt_equipo_modulos WHERE ci = ?", (ci,))},
        })
    return salida


def _poner_tipo_vehiculo(conn, ci, nombre):
    """Escribe el tipo estándar en vehiculos.tipo (y en raw_json). La primera vez guarda el texto anterior en tipo_original."""
    v = conn.execute("SELECT tipo, tipo_original, raw_json FROM vehiculos WHERE codigo = ?", (ci,)).fetchone()
    if (v["tipo"] or "") == nombre:
        return
    original = v["tipo_original"]
    if not original and v["tipo"] and not tipo_por_nombre(conn, v["tipo"]):
        original = v["tipo"]
    try:
        raw = json.loads(v["raw_json"] or "{}")
    except ValueError:
        raw = {}
    raw["tipo"] = nombre
    conn.execute("UPDATE vehiculos SET tipo = ?, tipo_original = ?, raw_json = ? WHERE codigo = ?",
                 (nombre, original, json.dumps(raw, ensure_ascii=False), ci))


def validar_tipo_vehiculo(conn, nuevo, actual=None):
    """Para el alta y la edición de vehículos: el tipo tiene que ser uno del catálogo (se devuelve escrito como en el catálogo).
    Si no cambia, se acepta aunque todavía no esté estandarizado."""
    if nuevo is None:
        return None
    if actual is not None and _norm(nuevo) == _norm(actual):
        return actual
    if not str(nuevo).strip():
        return ""
    tipo = tipo_por_nombre(conn, nuevo)
    if not tipo:
        mt._err("VALIDACION", f"El tipo «{str(nuevo).strip()}» no está en el catálogo de tipos de equipo. Elegí uno de la lista "
                              "o agregalo en Vehículos › Tipos de equipo.")
    return tipo["nombre"]


def asignar_tipo(conn, ci, datos, usuario):
    ci = mt._equipo(conn, ci)["codigo"].strip()
    tipo = conn.execute("SELECT * FROM mt_tipos_equipo WHERE id = ? AND activo = 1", (int(datos.get("tipo_equipo_id") or 0),)).fetchone()
    if not tipo:
        mt._err("VALIDACION", "Elegí un tipo de equipo.")

    def entero(campo):
        v = datos.get(campo)
        if v in (None, ""):
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            mt._err("VALIDACION", "Neumáticos y auxilios son números enteros.")
        if not 0 <= n <= 30:
            mt._err("VALIDACION", "Neumáticos y auxilios: entre 0 y 30.")
        return n
    _poner_tipo_vehiculo(conn, ci, tipo["nombre"])
    mt._asegurar_fila(conn, ci)
    conn.execute("UPDATE mt_equipos SET neumaticos = ?, auxilios = ?, actualizado_por = ?, actualizado_en = ? WHERE ci = ?",
                 (entero("neumaticos"), entero("auxilios"), usuario, mt.ahora(), ci))
    if "modulos" in datos:
        conn.execute("DELETE FROM mt_equipo_modulos WHERE ci = ?", (ci,))
        for modulo_id, accion in (datos.get("modulos") or {}).items():
            if accion in ("SUMAR", "QUITAR"):
                conn.execute("INSERT INTO mt_equipo_modulos (ci, modulo_id, accion) VALUES (?, ?, ?)", (ci, int(modulo_id), accion))
    return next(e for e in equipos_y_tipos(conn) if e["ci"] == ci)


def estandarizar_propuestos(conn):
    """Pasa a tipo estándar, de una vez, los vehículos que todavía tienen texto libre y para los que hay propuesta."""
    tipos = {t["id"]: t for t in _tipos(conn)}
    n = 0
    for e in equipos_y_tipos(conn):
        # También corrige la escritura de los que ya coinciden («motoniveladora» → «Motoniveladora»).
        if e["tipo_equipo_id"] and e["tipo"] != tipos[e["tipo_equipo_id"]]["nombre"]:
            _poner_tipo_vehiculo(conn, e["ci"], tipos[e["tipo_equipo_id"]]["nombre"])
            n += 1
    return {"estandarizados": n}


def guardar_tipo(conn, datos, tipo_id=None):
    nombre = mt._texto(datos.get("nombre"), "el nombre", maximo=80)
    familia = str(datos.get("familia") or "").upper()
    if familia not in FAMILIAS:
        mt._err("VALIDACION", "Familia inválida: vial, maquinaria o remolque.")
    try:
        neum, aux = int(datos.get("neumaticos") or 0), int(datos.get("auxilios") or 0)
    except (TypeError, ValueError):
        mt._err("VALIDACION", "Neumáticos y auxilios son números enteros.")
    palabras = mt._texto(datos.get("palabras_clave"), "las palabras clave", obligatorio=False, maximo=300)
    repetido = next((t for t in conn.execute("SELECT id, nombre FROM mt_tipos_equipo") if _norm(t["nombre"]) == _norm(nombre)), None)
    if repetido and (not tipo_id or repetido["id"] != int(tipo_id)):
        mt._err("VALIDACION", f"Ya existe el tipo «{repetido['nombre']}».")
    if tipo_id:
        anterior = conn.execute("SELECT nombre FROM mt_tipos_equipo WHERE id = ?", (int(tipo_id),)).fetchone()
        if not anterior:
            mt._err("NO_EXISTE", "El tipo de equipo no existe.")
        conn.execute("UPDATE mt_tipos_equipo SET nombre = ?, familia = ?, neumaticos = ?, auxilios = ?, palabras_clave = ? WHERE id = ?",
                     (nombre, familia, neum, aux, palabras, int(tipo_id)))
        if anterior["nombre"] != nombre:  # renombrar: los vehículos de ese tipo pasan al nombre nuevo
            for v in conn.execute("SELECT codigo, tipo FROM vehiculos").fetchall():
                if _norm(v["tipo"]) == _norm(anterior["nombre"]):
                    _poner_tipo_vehiculo(conn, v["codigo"], nombre)
    else:
        tipo_id = conn.execute("INSERT INTO mt_tipos_equipo (nombre, familia, neumaticos, auxilios, palabras_clave) VALUES (?, ?, ?, ?, ?)",
                               (nombre, familia, neum, aux, palabras)).lastrowid
    if "modulos" in datos:
        conn.execute("DELETE FROM mt_tipo_equipo_modulos WHERE tipo_equipo_id = ?", (tipo_id,))
        for m in datos.get("modulos") or []:
            if conn.execute("SELECT 1 FROM mt_ck_modulos WHERE id = ? AND capa = 'MODULO'", (int(m),)).fetchone():
                conn.execute("INSERT INTO mt_tipo_equipo_modulos (tipo_equipo_id, modulo_id) VALUES (?, ?)", (tipo_id, int(m)))
    return catalogo(conn)


def catalogo(conn):
    """Lo que necesita la pantalla Vehículos › Tipos de equipo."""
    p = plantilla(conn)
    return {"tipos": p["tipos"], "modulos": [m for m in p["modulos"] if m["capa"] == "MODULO"], "equipos": equipos_y_tipos(conn)}


# ============================================================================
# Plantilla (módulos e ítems)
# ============================================================================
def plantilla(conn):
    modulos = []
    for m in conn.execute("SELECT * FROM mt_ck_modulos ORDER BY CASE capa WHEN 'BASE' THEN 0 WHEN 'FAMILIA' THEN 1 ELSE 2 END, orden, id"):
        d = dict(m)
        d["items"] = [dict(i) for i in conn.execute("SELECT * FROM mt_ck_items WHERE modulo_id = ? ORDER BY orden, id", (m["id"],))]
        modulos.append(d)
    tipos = []
    for t in conn.execute("SELECT * FROM mt_tipos_equipo ORDER BY nombre"):
        d = dict(t)
        d["modulos"] = [f[0] for f in conn.execute("SELECT modulo_id FROM mt_tipo_equipo_modulos WHERE tipo_equipo_id = ?", (t["id"],))]
        tipos.append(d)
    return {"modulos": modulos, "tipos": tipos}


def guardar_item(conn, datos, item_id=None):
    texto = mt._texto(datos.get("texto"), "el texto del ítem", maximo=150)
    tipo = str(datos.get("tipo") or "ESTADO").upper()
    if tipo not in ("ESTADO", "VIGENCIA"):
        mt._err("VALIDACION", "Tipo de ítem inválido.")
    banderas = [int(bool(datos.get(k))) for k in ("opcional", "critico", "foto_obligatoria")]
    activo = int(bool(datos.get("activo", True)))
    if item_id:
        if not conn.execute("SELECT 1 FROM mt_ck_items WHERE id = ?", (int(item_id),)).fetchone():
            mt._err("NO_EXISTE", "El ítem no existe.")
        conn.execute("UPDATE mt_ck_items SET texto = ?, tipo = ?, opcional = ?, critico = ?, foto_obligatoria = ?, activo = ? WHERE id = ?",
                     (texto, tipo, *banderas, activo, int(item_id)))
    else:
        modulo = conn.execute("SELECT id, es_neumaticos FROM mt_ck_modulos WHERE id = ?", (int(datos.get("modulo_id") or 0),)).fetchone()
        if not modulo:
            mt._err("NO_EXISTE", "El módulo no existe.")
        if modulo["es_neumaticos"]:
            mt._err("VALIDACION", "Las filas de Neumáticos salen de las posiciones del equipo.")
        orden = conn.execute("SELECT COALESCE(MAX(orden), 0) + 10 FROM mt_ck_items WHERE modulo_id = ?", (modulo["id"],)).fetchone()[0]
        conn.execute("""INSERT INTO mt_ck_items (modulo_id, texto, tipo, opcional, critico, foto_obligatoria, orden, activo)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (modulo["id"], texto, tipo, *banderas, orden, activo))
    return plantilla(conn)


def guardar_modulo(conn, datos, modulo_id=None):
    nombre = mt._texto(datos.get("nombre"), "el nombre del módulo", maximo=80)
    activo = int(bool(datos.get("activo", True)))
    if modulo_id:
        conn.execute("UPDATE mt_ck_modulos SET nombre = ?, activo = ? WHERE id = ?", (nombre, activo, int(modulo_id)))
        return plantilla(conn)
    capa = str(datos.get("capa") or "MODULO").upper()
    familia = str(datos.get("familia") or "").upper() or None
    if capa not in CAPAS or (capa == "FAMILIA" and familia not in FAMILIAS):
        mt._err("VALIDACION", "Capa o familia inválida.")
    codigo = re.sub(r"[^a-z0-9]+", "_", _norm(nombre)).strip("_") or "modulo"
    base, n = codigo, 2
    while conn.execute("SELECT 1 FROM mt_ck_modulos WHERE codigo = ?", (codigo,)).fetchone():
        codigo, n = f"{base}_{n}", n + 1
    orden = conn.execute("SELECT COALESCE(MAX(orden), 0) + 10 FROM mt_ck_modulos").fetchone()[0]
    conn.execute("INSERT INTO mt_ck_modulos (codigo, nombre, capa, familia, orden) VALUES (?, ?, ?, ?, ?)",
                 (codigo, nombre, capa, familia if capa == "FAMILIA" else None, orden))
    return plantilla(conn)


# ============================================================================
# Armado y carga del check list
# ============================================================================
def _tipo_de_equipo(conn, ci):
    v = conn.execute("SELECT tipo FROM vehiculos WHERE codigo = ?", (ci,)).fetchone()
    tipo = tipo_por_nombre(conn, v["tipo"] if v else "")
    if not tipo:
        mt._err("VALIDACION", f"El tipo del equipo {ci} («{(v['tipo'] if v else '') or 'sin tipo'}») no está estandarizado. "
                              "Corregilo en Logística › Vehículos › Tipos de equipo.")
    fila = conn.execute("SELECT * FROM mt_equipos WHERE ci = ?", (ci,)).fetchone()
    return (fila or {"neumaticos": None, "auxilios": None}), tipo


def armar(conn, ci):
    """Secciones del check list del equipo: Base (salvo remolque) + su familia + módulos del tipo ± excepciones del equipo."""
    eq = mt._equipo(conn, ci)
    ci = eq["codigo"].strip()
    fila, tipo = _tipo_de_equipo(conn, ci)
    ids = {f[0] for f in conn.execute("SELECT modulo_id FROM mt_tipo_equipo_modulos WHERE tipo_equipo_id = ?", (tipo["id"],))}
    for f in conn.execute("SELECT modulo_id, accion FROM mt_equipo_modulos WHERE ci = ?", (ci,)):
        (ids.add if f["accion"] == "SUMAR" else ids.discard)(f["modulo_id"])
    modulos = conn.execute(
        """SELECT * FROM mt_ck_modulos WHERE activo = 1 AND (
               (capa = 'BASE' AND ? <> 'REMOLQUE') OR (capa = 'FAMILIA' AND familia = ?) OR id IN (%s))
           ORDER BY CASE capa WHEN 'BASE' THEN 0 WHEN 'FAMILIA' THEN 1 ELSE 2 END, orden, id""" % (",".join("?" * len(ids)) or "NULL"),
        (tipo["familia"], tipo["familia"], *ids)).fetchall()
    neum = fila["neumaticos"] if fila["neumaticos"] is not None else tipo["neumaticos"]
    aux = fila["auxilios"] if fila["auxilios"] is not None else tipo["auxilios"]
    secciones = []
    for m in modulos:
        if m["es_neumaticos"]:
            items = [{"clave": f"neu:{k}", "texto": f"Posición {k}", "tipo": "ESTADO", "opcional": 0, "critico": 0, "foto_obligatoria": 0}
                     for k in range(1, (neum or 0) + 1)]
            items += [{"clave": f"aux:{k}", "texto": f"Auxilio {k}", "tipo": "ESTADO", "opcional": 0, "critico": 0, "foto_obligatoria": 0}
                      for k in range(1, (aux or 0) + 1)]
        else:
            items = [{"clave": f"it:{i['id']}", "texto": i["texto"], "tipo": i["tipo"], "opcional": i["opcional"],
                      "critico": i["critico"], "foto_obligatoria": i["foto_obligatoria"]}
                     for i in conn.execute("SELECT * FROM mt_ck_items WHERE modulo_id = ? AND activo = 1 ORDER BY orden, id", (m["id"],))]
        if items:
            secciones.append({"modulo": m["nombre"], "capa": m["capa"], "items": items})
    estado = mt.estado_equipo(conn, ci)
    ultimo = conn.execute(
        """SELECT hs_final, km_final, maquinista, obra FROM horas_maquinas WHERE UPPER(TRIM(ci)) = UPPER(?)
             AND fecha_parte GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' ORDER BY fecha_parte DESC, id DESC LIMIT 1""",
        (ci,)).fetchone()
    return {
        "ci": ci, "display": mt._equipo_display(eq), "tipo_equipo": tipo["nombre"], "familia": tipo["familia"],
        "secciones": secciones, "estado": estado["estado"], "ubicacion": estado["ubicacion"],
        "cabecera": {
            "horometro": _numero(ultimo["hs_final"]) if ultimo else None,
            "km": _numero(ultimo["km_final"]) if ultimo else None,
            "operador": (ultimo["maquinista"] or "").strip() if ultimo else "",
        },
    }


def _numero(v):
    try:
        n = float(str(v).replace(",", "."))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _descripcion_novedad(modulo, item):
    return f"Check list · {modulo} · {item}"


def guardar(conn, ci, datos, fotos, usuario):
    """datos: {horometro, km, operador, observaciones, respuestas: {clave: {valor, comentario}}}; fotos: {clave: (archivo, tipo)}.
    Devuelve el check list guardado, con las novedades creadas (para avisar por email)."""
    armado = armar(conn, ci)
    respuestas = datos.get("respuestas") or {}
    if not isinstance(respuestas, dict):
        mt._err("VALIDACION", "Respuestas inválidas.")
    faltan, errores, filas = [], [], []
    for s in armado["secciones"]:
        for it in s["items"]:
            r = respuestas.get(it["clave"]) or {}
            valor = str(r.get("valor") or "").upper()
            nombre = f"{s['modulo']} · {it['texto']}"
            validos = VALORES_VIGENCIA if it["tipo"] == "VIGENCIA" else VALORES_ESTADO
            if not valor:
                faltan.append(nombre)
                continue
            if valor not in validos or (valor == "NA" and not it["opcional"]):
                errores.append(f"{nombre}: respuesta inválida{' (no admite N/A)' if valor == 'NA' else ''}.")
                continue
            comentario = mt._texto(r.get("comentario"), "el comentario", obligatorio=False, maximo=500)
            foto = fotos.get(it["clave"])
            if valor in ("MALO", "REGULAR", "VENCIDA") and not comentario:
                errores.append(f"{nombre}: falta el comentario.")
            if (valor in ("MALO", "REGULAR") or (it["foto_obligatoria"] and valor != "NA")) and not foto:
                errores.append(f"{nombre}: falta la foto.")
            filas.append((s["modulo"], it, valor, comentario, foto))
    if faltan:
        mt._err("VALIDACION", f"Faltan {len(faltan)} ítems por responder: " + "; ".join(faltan[:5]) + ("…" if len(faltan) > 5 else ""))
    if errores:
        mt._err("VALIDACION", " ".join(errores[:6]) + (" …" if len(errores) > 6 else ""))

    def num(campo):
        v = datos.get(campo)
        if v in (None, ""):
            return None
        try:
            return float(str(v).replace(",", "."))
        except ValueError:
            mt._err("VALIDACION", f"{campo.capitalize()} inválido.")
    malos = [f for f in filas if f[2] in ("MALO", "VENCIDA")]
    no_apto = any(f[2] == "MALO" and f[1]["critico"] for f in filas)
    marca = mt.ahora()
    ck_id = conn.execute(
        """INSERT INTO mt_checklists (ci, tipo_equipo, familia, fecha, horometro, km, operador, ubicacion, resultado, observaciones,
           realizado_por, creado_en, total_items, malos, regulares) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (armado["ci"], armado["tipo_equipo"], armado["familia"], marca[:10], num("horometro"), num("km"),
         mt._texto(datos.get("operador"), "el operador", obligatorio=False, maximo=120), armado["ubicacion"],
         "NO_APTO" if no_apto else "APTO", mt._texto(datos.get("observaciones"), "las observaciones", obligatorio=False),
         usuario, marca, len(filas), sum(1 for f in filas if f[2] == "MALO"), sum(1 for f in filas if f[2] == "REGULAR"))).lastrowid
    nuevas = []
    for orden, (modulo, it, valor, comentario, foto) in enumerate(filas, 1):
        novedad_id = None
        if valor in ("MALO", "VENCIDA"):
            novedad_id, es_nueva = _novedad_desde_item(conn, armado["ci"], modulo, it, valor, comentario, foto, usuario, ck_id)
            if es_nueva:
                nuevas.append(novedad_id)
        conn.execute(
            """INSERT INTO mt_checklist_respuestas (checklist_id, orden, clave, modulo, item, tipo, critico, valor, comentario,
               foto_archivo, foto_tipo, novedad_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ck_id, orden, it["clave"], modulo, it["texto"], it["tipo"], it["critico"], valor, comentario or None,
             foto[0] if foto else None, foto[1] if foto else None, novedad_id))
    conn.execute("UPDATE mt_checklists SET novedades = ? WHERE id = ?", (len(malos), ck_id))
    resultado = detalle(conn, ck_id)
    resultado["novedades_nuevas"] = [mt._novedad_dict(conn, mt._novedad(conn, n)) for n in nuevas]
    return resultado


def _novedad_desde_item(conn, ci, modulo, it, valor, comentario, foto, usuario, ck_id):
    """Una novedad por ítem en Malo / Vencida. Si ya hay una pendiente del mismo ítem, se confirma (no se repite)."""
    base = _descripcion_novedad(modulo, it["texto"])
    previa = conn.execute(
        "SELECT id FROM mt_novedades WHERE UPPER(ci) = UPPER(?) AND estado <> 'CERRADA' AND descripcion LIKE ? ESCAPE '\\' ORDER BY id LIMIT 1",
        (ci, base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")).fetchone()
    if previa:
        mt.confirmar_novedad(conn, previa["id"], usuario, comentario=f"Check list #{ck_id}: {comentario}"[:500])
        novedad_id, nueva = previa["id"], False
    else:
        texto = f"{base}{' (VENCIDA)' if valor == 'VENCIDA' else ''}: {comentario}"
        nov = mt.crear_novedad(conn, {"tipo": "EQUIPO", "ci": ci, "descripcion": texto[:2000],
                                      "prioridad": "URGENTE" if it["critico"] and valor == "MALO" else "NORMAL"}, usuario, origen="WEB")
        conn.execute("UPDATE mt_novedades SET checklist_id = ? WHERE id = ?", (ck_id, nov["id"]))
        novedad_id, nueva = nov["id"], True
    if foto and conn.execute("SELECT COUNT(*) FROM mt_fotos WHERE novedad_id = ?", (novedad_id,)).fetchone()[0] < mt.MAX_FOTOS:
        mt.registrar_foto(conn, "novedad_id", novedad_id, foto[0], None, foto[1], usuario)
    return novedad_id, nueva


def listar(conn, ci="", resultado=""):
    sql, args = "SELECT * FROM mt_checklists WHERE 1 = 1", []
    if ci:
        sql += " AND UPPER(ci) = UPPER(?)"
        args.append(ci.strip())
    if resultado:
        sql += " AND resultado = ?"
        args.append(resultado)
    filas = []
    for f in conn.execute(sql + " ORDER BY id DESC LIMIT 300", args):
        d = dict(f)
        eq = conn.execute("SELECT codigo, marca, modelo, tipo FROM vehiculos WHERE UPPER(TRIM(codigo)) = UPPER(?)", (f["ci"],)).fetchone()
        d["display"] = mt._equipo_display(eq) if eq else f["ci"]
        filas.append(d)
    return filas


def detalle(conn, ck_id):
    f = conn.execute("SELECT * FROM mt_checklists WHERE id = ?", (int(ck_id),)).fetchone()
    if not f:
        mt._err("NO_EXISTE", "El check list no existe.")
    d = dict(f)
    eq = conn.execute("SELECT codigo, marca, modelo, tipo FROM vehiculos WHERE UPPER(TRIM(codigo)) = UPPER(?)", (f["ci"],)).fetchone()
    d["display"] = mt._equipo_display(eq) if eq else f["ci"]
    d["respuestas"] = [{k: r[k] for k in r.keys() if k != "foto_archivo"} | {"tiene_foto": bool(r["foto_archivo"])}
                       for r in conn.execute("SELECT * FROM mt_checklist_respuestas WHERE checklist_id = ? ORDER BY orden", (f["id"],))]
    return d


def foto_respuesta(conn, respuesta_id):
    r = conn.execute("SELECT foto_archivo, foto_tipo FROM mt_checklist_respuestas WHERE id = ?", (int(respuesta_id),)).fetchone()
    if not r or not r["foto_archivo"]:
        mt._err("NO_EXISTE", "La respuesta no tiene foto.")
    return r
