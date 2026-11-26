"""Mantenimiento › Etapa 1 (correctivo). Reemplaza a FleetCare.

Circuito:
- Novedad: la reporta un operador desde la app (o un usuario desde la web). Es solo de un EQUIPO (vehiculos.codigo);
  las novedades de obra se descartaron (la columna tipo/centro_costo_id queda por compatibilidad). No se duplica: si ya hay una abierta, el operador la confirma ("sigue igual") y la
  novedad muestra los días desde que se reportó.
- El usuario con la acción novedades.aprobar (asignada en Administración) la resuelve: la pasa a OT o la cierra sin OT,
  y puede cambiar el estado del equipo.
- OT: Asignada → En curso ⇄ En espera → En revisión → Finalizada. Los mecánicos (legajos con PIN en la app) cargan
  avances, usan el cronómetro y mandan la corrección final (pasa a En revisión). No pueden cerrarla.
- Solo el usuario con ordenes.finalizar (administrador de Mantenimiento) la finaliza o la devuelve con observación.
  Al finalizar, el equipo vuelve a Operativo (o al estado que elija).
- Equipo: estado OPERATIVO / FUNCIONAL (trabaja con una novedad abierta o una restricción) / FUERA_DE_SERVICIO
  (no bloquea la carga de partes, solo avisa). Ubicación: la del último parte diario, salvo que haya una manual más
  nueva (con motivo); un parte posterior la reemplaza. Todo cambio queda en mt_equipo_historial.
- Una novedad de equipo pasa el equipo de Operativo a Funcional sola. Al cerrarse la última novedad pendiente
  (finalizar la OT o cerrar sin OT) vuelve a Operativo, salvo que se elija otro estado.
- Fotos: hasta MAX_FOTOS por novedad y por OT (archivos en DATA_DIR/Doc_Mantenimiento; acá solo el registro).
- Control contra la Tarja: horas del cronómetro por mecánico y día frente al día cargado en tarja_registros.
- Etapa 2: check list (mantenimiento_checklist.py); preventivo, repuestos de la OT y control del horómetro
  (mantenimiento_preventivo.py).
"""
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

MODULO = "mantenimiento"
PANELES = {
    "novedades": ("consultar", "cargar", "aprobar"),
    "ordenes": ("consultar", "gestionar", "finalizar"),
    "checklist": ("consultar", "realizar"),
    "equipos": ("consultar", "cambiar_estado"),
    "preventivo": ("consultar", "gestionar"),
    "indicadores": ("consultar",),
    "configuracion": ("configurar",),
}
ESTADOS_EQUIPO = ("OPERATIVO", "FUNCIONAL", "FUERA_DE_SERVICIO")
ESTADOS_OT = ("ASIGNADA", "EN_CURSO", "EN_ESPERA", "EN_REVISION", "FINALIZADA", "ANULADA")
OT_ACTIVAS = ("ASIGNADA", "EN_CURSO", "EN_ESPERA", "EN_REVISION")
PRIORIDADES = ("NORMAL", "URGENTE")
ZONA_AR = timezone(timedelta(hours=-3))
CONFIG_INICIAL = {"valor_hora_mecanico": "0", "moneda": "ARS"}
MAX_TEXTO = 2000
MAX_FOTOS = 6


class MtError(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje


def _err(codigo, mensaje):
    raise MtError(codigo, mensaje)


def ahora():
    return datetime.now(ZONA_AR).strftime("%Y-%m-%d %H:%M:%S")


def hoy():
    return datetime.now(ZONA_AR).date()


def _dt(texto):
    return datetime.strptime(str(texto)[:19], "%Y-%m-%d %H:%M:%S")


def _dias_desde(texto, hasta=None):
    if not texto:
        return None
    try:
        desde = date.fromisoformat(str(texto)[:10])
    except ValueError:
        return None
    return max(0, ((hasta or hoy()) - desde).days)


def _texto(valor, campo, obligatorio=True, maximo=MAX_TEXTO):
    t = re.sub(r"\s+", " ", str(valor or "")).strip()
    if obligatorio and not t:
        _err("VALIDACION", f"Falta {campo}.")
    if len(t) > maximo:
        _err("VALIDACION", f"{campo.capitalize()}: máximo {maximo} caracteres.")
    return t


# ============================================================================
# Estructura de base (solo agrega tablas mt_*)
# ============================================================================
DDL = (
    """
    CREATE TABLE IF NOT EXISTS mt_novedades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL CHECK (tipo IN ('EQUIPO', 'OBRA')),
        ci TEXT,
        centro_costo_id INTEGER,
        descripcion TEXT NOT NULL,
        prioridad TEXT NOT NULL DEFAULT 'NORMAL' CHECK (prioridad IN ('NORMAL', 'URGENTE')),
        estado TEXT NOT NULL DEFAULT 'ABIERTA' CHECK (estado IN ('ABIERTA', 'EN_OT', 'CERRADA')),
        origen TEXT NOT NULL DEFAULT 'APP' CHECK (origen IN ('APP', 'WEB')),
        legajo_reporta TEXT,
        reportada_por TEXT NOT NULL,
        creada_en TEXT NOT NULL,
        confirmaciones INTEGER NOT NULL DEFAULT 0,
        ultima_confirmacion_en TEXT,
        resuelta_por TEXT,
        resuelta_en TEXT,
        resolucion TEXT,
        ot_id INTEGER,
        CHECK ((tipo = 'EQUIPO' AND ci IS NOT NULL) OR (tipo = 'OBRA' AND centro_costo_id IS NOT NULL))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_nov_estado ON mt_novedades (estado)",
    "CREATE INDEX IF NOT EXISTS idx_mt_nov_ci ON mt_novedades (ci)",
    """
    CREATE TABLE IF NOT EXISTS mt_novedad_confirmaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        novedad_id INTEGER NOT NULL REFERENCES mt_novedades (id) ON DELETE CASCADE,
        legajo TEXT,
        autor TEXT NOT NULL,
        comentario TEXT,
        creada_en TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt_ordenes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero TEXT NOT NULL UNIQUE,
        novedad_id INTEGER REFERENCES mt_novedades (id),
        ci TEXT,
        centro_costo_id INTEGER,
        titulo TEXT NOT NULL,
        descripcion TEXT,
        prioridad TEXT NOT NULL DEFAULT 'NORMAL' CHECK (prioridad IN ('NORMAL', 'URGENTE')),
        estado TEXT NOT NULL DEFAULT 'ASIGNADA'
            CHECK (estado IN ('ASIGNADA', 'EN_CURSO', 'EN_ESPERA', 'EN_REVISION', 'FINALIZADA', 'ANULADA')),
        creada_por TEXT NOT NULL,
        creada_en TEXT NOT NULL,
        actualizada_en TEXT NOT NULL,
        finalizada_por TEXT,
        finalizada_en TEXT,
        informe_archivo TEXT,
        informe_generado_en TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_ot_estado ON mt_ordenes (estado)",
    """
    CREATE TABLE IF NOT EXISTS mt_ot_mecanicos (
        ot_id INTEGER NOT NULL REFERENCES mt_ordenes (id) ON DELETE CASCADE,
        legajo TEXT NOT NULL,
        nombre TEXT NOT NULL,
        PRIMARY KEY (ot_id, legajo)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_otm_legajo ON mt_ot_mecanicos (legajo)",
    """
    CREATE TABLE IF NOT EXISTS mt_ot_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ot_id INTEGER NOT NULL REFERENCES mt_ordenes (id) ON DELETE CASCADE,
        tipo TEXT NOT NULL,
        texto TEXT,
        autor TEXT NOT NULL,
        legajo TEXT,
        creado_en TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_ote_ot ON mt_ot_eventos (ot_id)",
    """
    CREATE TABLE IF NOT EXISTS mt_ot_tiempos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ot_id INTEGER NOT NULL REFERENCES mt_ordenes (id) ON DELETE CASCADE,
        legajo TEXT NOT NULL,
        inicio TEXT NOT NULL,
        fin TEXT,
        minutos REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_ott_ot ON mt_ot_tiempos (ot_id)",
    """
    CREATE TABLE IF NOT EXISTS mt_equipos (
        ci TEXT PRIMARY KEY,
        estado TEXT NOT NULL DEFAULT 'OPERATIVO' CHECK (estado IN ('OPERATIVO', 'FUNCIONAL', 'FUERA_DE_SERVICIO')),
        estado_desde TEXT,
        estado_motivo TEXT,
        ubic_centro_costo_id INTEGER,
        ubic_texto TEXT,
        ubic_motivo TEXT,
        ubic_desde TEXT,
        actualizado_por TEXT,
        actualizado_en TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt_equipo_historial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ci TEXT NOT NULL,
        campo TEXT NOT NULL CHECK (campo IN ('ESTADO', 'UBICACION')),
        anterior TEXT,
        nuevo TEXT,
        motivo TEXT,
        usuario TEXT NOT NULL,
        creado_en TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_eh_ci ON mt_equipo_historial (ci)",
    """
    CREATE TABLE IF NOT EXISTS mt_fotos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        novedad_id INTEGER REFERENCES mt_novedades (id) ON DELETE CASCADE,
        ot_id INTEGER REFERENCES mt_ordenes (id) ON DELETE CASCADE,
        archivo TEXT NOT NULL,
        bytes INTEGER,
        tipo TEXT NOT NULL DEFAULT 'image/jpeg',
        subida_por TEXT NOT NULL,
        legajo TEXT,
        subida_en TEXT NOT NULL,
        CHECK ((novedad_id IS NULL) <> (ot_id IS NULL))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_fotos_nov ON mt_fotos (novedad_id)",
    "CREATE INDEX IF NOT EXISTS idx_mt_fotos_ot ON mt_fotos (ot_id)",
    """
    CREATE TABLE IF NOT EXISTS mt_config (
        clave TEXT PRIMARY KEY,
        valor TEXT,
        actualizado_por TEXT,
        actualizado_en TEXT
    )
    """,
)


def migrar(conn):
    for sentencia in DDL:
        conn.execute(sentencia)
    columnas_ot = {f[1] for f in conn.execute("PRAGMA table_info(mt_ordenes)")}
    for columna in ("informe_archivo", "informe_generado_en"):
        if columna not in columnas_ot:
            conn.execute(f"ALTER TABLE mt_ordenes ADD COLUMN {columna} TEXT")
    for clave, valor in CONFIG_INICIAL.items():
        conn.execute("INSERT OR IGNORE INTO mt_config (clave, valor) VALUES (?, ?)", (clave, valor))
    # Reporte: varias novedades cargadas juntas (p. ej. el parte del supervisor). Guarda el id de la primera del grupo.
    if "reporte_id" not in {f[1] for f in conn.execute("PRAGMA table_info(mt_novedades)")}:
        conn.execute("ALTER TABLE mt_novedades ADD COLUMN reporte_id INTEGER")
    import mantenimiento_checklist  # check list en tres capas (etapa 2); importa este módulo
    mantenimiento_checklist.migrar(conn)
    import mantenimiento_preventivo  # preventivo, repuestos y control del horómetro (etapa 2)
    mantenimiento_preventivo.migrar(conn)


# ============================================================================
# Catálogos
# ============================================================================
# Equipo activo: los inactivos (Logística › Vehículos) no se listan ni reciben novedades u OT nuevas.
VEHICULO_ACTIVO_SQL = "UPPER(TRIM(COALESCE(activo, 'SI'))) <> 'NO'"


def _equipo(conn, ci, solo_activo=False):
    ci = str(ci or "").strip()
    if not ci:
        _err("VALIDACION", "Falta el equipo.")
    fila = conn.execute("SELECT codigo, marca, modelo, tipo, COALESCE(activo, 'SI') AS activo FROM vehiculos WHERE UPPER(TRIM(codigo)) = UPPER(?)", (ci,)).fetchone()
    if not fila:
        _err("NO_EXISTE", f"El equipo {ci} no existe.")
    if solo_activo and str(fila["activo"]).strip().upper() == "NO":
        _err("VALIDACION", f"El equipo {fila['codigo']} está inactivo (dado de baja en Vehículos).")
    return fila


def _equipo_display(fila):
    desc = " ".join(p for p in [(fila["marca"] or "").strip(), (fila["modelo"] or "").strip()] if p) or (fila["tipo"] or "").strip()
    return f"{fila['codigo'].strip()} - {desc}" if desc else fila["codigo"].strip()


def _cc(conn, cc_id):
    try:
        cc_id = int(str(cc_id).split("-")[0].strip())
    except (TypeError, ValueError):
        _err("VALIDACION", "Falta el centro de costo.")
    fila = conn.execute("SELECT id, nombre FROM centros_costos WHERE id = ?", (cc_id,)).fetchone()
    if not fila:
        _err("NO_EXISTE", f"El centro de costo {cc_id} no existe.")
    return fila


def _cc_texto(fila):
    return f"{fila['id']} - {(fila['nombre'] or '').strip()}" if fila else ""


def _personal(conn, legajo):
    fila = conn.execute(
        "SELECT legajo, nombre FROM personal WHERE TRIM(CAST(legajo AS TEXT)) = ? AND UPPER(COALESCE(activo, 'ACTIVO')) = 'ACTIVO'",
        (str(legajo or "").strip(),),
    ).fetchone()
    if not fila:
        _err("VALIDACION", f"El legajo {legajo} no es un empleado activo.")
    return str(fila["legajo"]).strip(), str(fila["nombre"] or "").strip()


def config(conn):
    return {f["clave"]: f["valor"] for f in conn.execute("SELECT clave, valor FROM mt_config")}


def _valor_hora(conn):
    try:
        return float(str(config(conn).get("valor_hora_mecanico") or 0).replace(",", "."))
    except ValueError:
        return 0.0


def guardar_config(conn, datos, usuario):
    cambios = {}
    if "valor_hora_mecanico" in datos:
        try:
            valor = float(str(datos["valor_hora_mecanico"]).replace(",", "."))
        except (TypeError, ValueError):
            _err("VALIDACION", "El valor hora debe ser un número.")
        if valor < 0:
            _err("VALIDACION", "El valor hora no puede ser negativo.")
        cambios["valor_hora_mecanico"] = f"{valor:.4f}".rstrip("0").rstrip(".")  # sin notación científica (1.25e+06)
    if "moneda" in datos:
        cambios["moneda"] = _texto(datos["moneda"], "la moneda", maximo=10).upper()
    for clave, valor in cambios.items():
        conn.execute(
            """INSERT INTO mt_config (clave, valor, actualizado_por, actualizado_en) VALUES (?, ?, ?, ?)
               ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, actualizado_por = excluded.actualizado_por,
               actualizado_en = excluded.actualizado_en""",
            (clave, valor, usuario, ahora()),
        )
    return config(conn)


def opciones(conn):
    equipos = [{"ci": f["codigo"].strip(), "display": _equipo_display(f), "tipo": (f["tipo"] or "").strip()}
               for f in conn.execute(f"SELECT codigo, marca, modelo, tipo FROM vehiculos WHERE TRIM(COALESCE(codigo, '')) <> '' AND {VEHICULO_ACTIVO_SQL} ORDER BY codigo")]
    # Hoy ningún centro de costo tiene proyecto_id: si falta, se toma el proyecto cuyo código aparece en el nombre
    # ("OBRA EXAR - ..." → EXAR). Cuando se cargue el vínculo real, manda ese.
    proyectos = [((f["codigo"] or "").strip(), (f["nombre"] or f["codigo"] or "").strip())
                 for f in conn.execute("SELECT codigo, nombre FROM proyectos WHERE COALESCE(activo, 1) = 1 ORDER BY nombre")]

    def proyecto_de(f):
        if f["proyecto"]:
            return f["proyecto"].strip()
        nombre = f" {(f['nombre'] or '').upper()} "
        return next((n for cod, n in proyectos if cod and re.search(rf"\b{re.escape(cod.upper())}\b", nombre)), "")
    ccs = [{"id": f["id"], "nombre": (f["nombre"] or "").strip(), "texto": _cc_texto(f), "proyecto": proyecto_de(f)}
           for f in conn.execute(
               """SELECT c.id, c.nombre, p.nombre AS proyecto FROM centros_costos c
                  LEFT JOIN proyectos p ON p.id = c.proyecto_id
                  WHERE COALESCE(c.activo, 1) = 1 ORDER BY c.id""")]
    personal = [{"legajo": str(f["legajo"]).strip(), "nombre": (f["nombre"] or "").strip()}
                for f in conn.execute(
                    "SELECT legajo, nombre FROM personal WHERE UPPER(COALESCE(activo, 'ACTIVO')) = 'ACTIVO' ORDER BY nombre")]
    return {"equipos": equipos, "centros_costos": ccs, "personal": personal, "config": config(conn),
            "estados_equipo": ESTADOS_EQUIPO, "estados_ot": ESTADOS_OT}


# ============================================================================
# Novedades
# ============================================================================
def _novedad_dict(conn, f):
    d = dict(f)
    d["dias"] = _dias_desde(f["creada_en"], _fecha_fin_novedad(f))
    if f["tipo"] == "EQUIPO":
        eq = conn.execute("SELECT codigo, marca, modelo, tipo FROM vehiculos WHERE UPPER(TRIM(codigo)) = UPPER(?)", (f["ci"],)).fetchone()
        d["objetivo"] = _equipo_display(eq) if eq else f["ci"]
    else:
        cc = conn.execute("SELECT id, nombre FROM centros_costos WHERE id = ?", (f["centro_costo_id"],)).fetchone()
        d["objetivo"] = _cc_texto(cc) or f"CC {f['centro_costo_id']}"
    if f["ot_id"]:
        ot = conn.execute("SELECT id, numero, estado, informe_archivo FROM mt_ordenes WHERE id = ?", (f["ot_id"],)).fetchone()
        d["ot_numero"], d["ot_estado"] = (ot["numero"], ot["estado"]) if ot else (None, None)
        d["informe_url"] = f"/operador/api/mt/ordenes/{ot['id']}/informe" if ot and ot["informe_archivo"] else None
    return d


def _fecha_fin_novedad(f):
    if f["estado"] == "CERRADA" and f["resuelta_en"]:
        return date.fromisoformat(str(f["resuelta_en"])[:10])
    return None


def listar_novedades(conn, estado="ABIERTA", ci="", centro_costo_id=None, tipo=""):
    sql, args = "SELECT * FROM mt_novedades WHERE 1 = 1", []
    if estado and estado != "TODAS":
        if estado == "PENDIENTES":
            sql += " AND estado IN ('ABIERTA', 'EN_OT')"
        else:
            sql += " AND estado = ?"
            args.append(estado)
    if ci:
        sql += " AND UPPER(ci) = UPPER(?)"
        args.append(str(ci).strip())
    if centro_costo_id:
        sql += " AND centro_costo_id = ?"
        args.append(int(centro_costo_id))
    if tipo:
        sql += " AND tipo = ?"
        args.append(tipo)
    sql += " ORDER BY CASE prioridad WHEN 'URGENTE' THEN 0 ELSE 1 END, creada_en ASC LIMIT 500"
    return [_novedad_dict(conn, f) for f in conn.execute(sql, args)]


def _novedad(conn, novedad_id):
    f = conn.execute("SELECT * FROM mt_novedades WHERE id = ?", (int(novedad_id),)).fetchone()
    if not f:
        _err("NO_EXISTE", "La novedad no existe.")
    return f


def crear_novedad(conn, datos, autor, legajo=None, origen="WEB"):
    """Una novedad suelta. Para varias juntas usar crear_reporte (mismo circuito: cada ítem se aprueba por separado)."""
    tipo = str(datos.get("tipo") or "EQUIPO").upper()
    if tipo != "EQUIPO":
        _err("VALIDACION", "Las novedades son solo de equipos.")
    descripcion = _texto(datos.get("descripcion"), "la descripción")
    prioridad = str(datos.get("prioridad") or "NORMAL").upper()
    if prioridad not in PRIORIDADES:
        _err("VALIDACION", "Prioridad inválida.")
    ci = cc_id = None
    if tipo == "EQUIPO":
        ci = _equipo(conn, datos.get("ci"), solo_activo=True)["codigo"].strip()
    else:
        cc_id = _cc(conn, datos.get("centro_costo_id"))["id"]
    cur = conn.execute(
        """INSERT INTO mt_novedades (tipo, ci, centro_costo_id, descripcion, prioridad, origen, legajo_reporta,
           reportada_por, creada_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tipo, ci, cc_id, descripcion, prioridad, origen, legajo, autor, ahora()),
    )
    if ci and _asegurar_fila(conn, ci)["estado"] == "OPERATIVO":
        cambiar_estado_equipo(conn, ci, "FUNCIONAL", autor, f"Novedad #{cur.lastrowid}: {descripcion[:120]}")
    return _novedad_dict(conn, _novedad(conn, cur.lastrowid))


MAX_ITEMS_REPORTE = 80


def crear_reporte(conn, items, autor, legajo=None, origen="WEB"):
    """Varias novedades de uno o más equipos en un solo envío. Todo o nada: si un ítem falla no se carga ninguno."""
    if not isinstance(items, list) or not items:
        _err("VALIDACION", "Agregá al menos un ítem al reporte.")
    if len(items) > MAX_ITEMS_REPORTE:
        _err("VALIDACION", f"Un reporte admite hasta {MAX_ITEMS_REPORTE} ítems.")
    creadas = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            _err("VALIDACION", f"Ítem {i}: formato inválido.")
        try:
            creadas.append(crear_novedad(conn, item, autor, legajo, origen))
        except MtError as exc:
            raise MtError(exc.codigo, f"Ítem {i}: {exc.mensaje}") from None
    reporte_id = None
    if len(creadas) > 1:
        reporte_id = creadas[0]["id"]
        conn.executemany("UPDATE mt_novedades SET reporte_id = ? WHERE id = ?", [(reporte_id, n["id"]) for n in creadas])
        for n in creadas:
            n["reporte_id"] = reporte_id
    return {"reporte_id": reporte_id, "novedades": creadas}


# --- novedades parecidas: avisa (no bloquea) cuando lo que se carga se parece a otra del mismo equipo ---
# «Pinchadura cubierta 1» y «pinchadura de cubierta 2» se parecen: puede ser la misma o otra pieza; decide quien carga.
_PALABRAS_VACIAS = set("""
de del la las el los lo le les en por con sin para que se no ni y e o u un una uno unos unas al su sus mas muy ya hay
esta este esto estos estas esta estan tiene tienen presenta presentan observa observan falta faltan equipo mal estado
lado parte cada hace desde dias dia funciona funcionan anda andan problema problemas inconveniente inconvenientes
viene sigue siguen tambien otra otro
""".split())
DIAS_PARECIDAS_CERRADAS = 30


def _sin_acentos(texto):
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _raices(texto):
    """Palabras significativas recortadas a 5 letras (cubierta/cubiertas → cubie). Los números no cuentan."""
    return {p[:5] for p in re.findall(r"[a-zñ]+", _sin_acentos(texto)) if len(p) >= 3 and p not in _PALABRAS_VACIAS}


def novedades_parecidas(conn, ci, descripcion, limite=5):
    """Pendientes del equipo (y cerradas en los últimos 30 días) con palabras en común con la descripción."""
    propias = _raices(descripcion)
    if not propias or not str(ci or "").strip():
        return []
    ci = _equipo(conn, ci)["codigo"].strip()
    desde = (date.today() - timedelta(days=DIAS_PARECIDAS_CERRADAS)).isoformat()
    resultado = []
    for f in conn.execute(
            """SELECT * FROM mt_novedades WHERE UPPER(TRIM(ci)) = UPPER(?)
               AND (estado IN ('ABIERTA', 'EN_OT') OR substr(COALESCE(resuelta_en, ''), 1, 10) >= ?)
               ORDER BY id DESC LIMIT 300""", (ci, desde)):
        otras = _raices(f["descripcion"])
        comunes = propias & otras
        if comunes and len(comunes) / min(len(propias), len(otras)) >= 0.5:
            d = _novedad_dict(conn, f)
            d["coincidencia"] = round(len(comunes) / len(propias | otras), 2)
            resultado.append(d)
    resultado.sort(key=lambda d: (d["estado"] == "CERRADA", -d["coincidencia"], -d["id"]))
    return resultado[:limite]


def confirmar_novedad(conn, novedad_id, autor, legajo=None, comentario=""):
    """«Sigue igual»: no crea otra novedad. Una confirmación por persona y día."""
    f = _novedad(conn, novedad_id)
    if f["estado"] == "CERRADA":
        _err("VALIDACION", "La novedad ya está cerrada. Si el problema sigue, reportá una nueva.")
    comentario = _texto(comentario, "el comentario", obligatorio=False, maximo=500)
    marca = ahora()
    quien = legajo or autor
    ya = conn.execute(
        """SELECT 1 FROM mt_novedad_confirmaciones WHERE novedad_id = ? AND COALESCE(legajo, autor) = ?
           AND substr(creada_en, 1, 10) = ?""",
        (f["id"], quien, marca[:10]),
    ).fetchone()
    if not ya or comentario:
        conn.execute(
            "INSERT INTO mt_novedad_confirmaciones (novedad_id, legajo, autor, comentario, creada_en) VALUES (?, ?, ?, ?, ?)",
            (f["id"], legajo, autor, comentario or None, marca),
        )
        conn.execute(
            "UPDATE mt_novedades SET confirmaciones = confirmaciones + ?, ultima_confirmacion_en = ? WHERE id = ?",
            (0 if ya else 1, marca, f["id"]),
        )
    return _novedad_dict(conn, _novedad(conn, f["id"]))


def detalle_novedad(conn, novedad_id):
    d = _novedad_dict(conn, _novedad(conn, novedad_id))
    d["confirmaciones_detalle"] = [dict(f) for f in conn.execute(
        "SELECT autor, legajo, comentario, creada_en FROM mt_novedad_confirmaciones WHERE novedad_id = ? ORDER BY id DESC",
        (d["id"],))]
    d["fotos"] = fotos(conn, novedad_id=d["id"])
    if d.get("ot_id"):
        ot = conn.execute("SELECT id, numero, ci, estado, informe_archivo FROM mt_ordenes WHERE id = ?", (d["ot_id"],)).fetchone()
        if ot:
            d["ot_numero"] = ot["numero"]
            d["informe_url"] = f"/api/mantenimiento/ordenes/{ot['id']}/informe" if ot["informe_archivo"] else None
    d["reporte"] = [_novedad_dict(conn, f) for f in conn.execute(
        "SELECT * FROM mt_novedades WHERE reporte_id = ? AND id <> ? ORDER BY id", (d["reporte_id"], d["id"]))] if d.get("reporte_id") else []
    return d


def resolver_novedad(conn, novedad_id, datos, usuario):
    """decision = 'OT' (crea la OT con sus mecánicos) o 'CERRAR' (sin OT, con motivo). Puede cambiar el estado del equipo."""
    f = _novedad(conn, novedad_id)
    if f["estado"] != "ABIERTA":
        _err("VALIDACION", "La novedad ya fue resuelta.")
    decision = str(datos.get("decision") or "").upper()
    marca = ahora()
    estado_equipo = str(datos.get("estado_equipo") or "").upper()
    if estado_equipo and f["tipo"] == "EQUIPO":
        cambiar_estado_equipo(conn, f["ci"], estado_equipo, usuario,
                              datos.get("motivo_estado") or f"Novedad #{f['id']}: {f['descripcion'][:120]}", silencioso_si_igual=True)
    if decision == "CERRAR":
        motivo = _texto(datos.get("resolucion"), "el motivo del cierre")
        conn.execute(
            "UPDATE mt_novedades SET estado = 'CERRADA', resuelta_por = ?, resuelta_en = ?, resolucion = ? WHERE id = ?",
            (usuario, marca, motivo, f["id"]),
        )
        if f["tipo"] == "EQUIPO" and not estado_equipo:
            _volver_a_operativo(conn, f["ci"], usuario, f"Novedad #{f['id']} cerrada sin OT")
        return {"novedad": detalle_novedad(conn, f["id"]), "ot": None}
    if decision != "OT":
        _err("VALIDACION", "Elegí si la novedad pasa a OT o se cierra sin OT.")
    ot = crear_orden(conn, {
        "ci": f["ci"], "centro_costo_id": f["centro_costo_id"],
        "titulo": datos.get("titulo") or f["descripcion"][:120],
        "descripcion": datos.get("descripcion") or f["descripcion"],
        "prioridad": datos.get("prioridad") or f["prioridad"],
        "mecanicos": datos.get("mecanicos") or [],
    }, usuario, novedad_id=f["id"])
    conn.execute(
        "UPDATE mt_novedades SET estado = 'EN_OT', resuelta_por = ?, resuelta_en = ?, resolucion = ?, ot_id = ? WHERE id = ?",
        (usuario, marca, _texto(datos.get("resolucion"), "la resolución", obligatorio=False) or f"Pasa a {ot['numero']}", ot["id"], f["id"]),
    )
    return {"novedad": detalle_novedad(conn, f["id"]), "ot": ot}


# ============================================================================
# Órdenes de trabajo
# ============================================================================
def _siguiente_numero(conn):
    fila = conn.execute("SELECT MAX(CAST(substr(numero, 4) AS INTEGER)) AS n FROM mt_ordenes WHERE numero LIKE 'OT-%'").fetchone()
    return f"OT-{(fila['n'] or 0) + 1:05d}"


def _orden(conn, ot_id):
    f = conn.execute("SELECT * FROM mt_ordenes WHERE id = ?", (int(ot_id),)).fetchone()
    if not f:
        _err("NO_EXISTE", "La OT no existe.")
    return f


def _evento(conn, ot_id, tipo, texto, autor, legajo=None):
    marca = ahora()
    conn.execute("INSERT INTO mt_ot_eventos (ot_id, tipo, texto, autor, legajo, creado_en) VALUES (?, ?, ?, ?, ?, ?)",
                 (ot_id, tipo, texto, autor, legajo, marca))
    conn.execute("UPDATE mt_ordenes SET actualizada_en = ? WHERE id = ?", (marca, ot_id))


def _set_estado_ot(conn, ot_id, estado):
    conn.execute("UPDATE mt_ordenes SET estado = ?, actualizada_en = ? WHERE id = ?", (estado, ahora(), ot_id))


def _asignar_mecanicos(conn, ot_id, legajos):
    if not isinstance(legajos, list):
        _err("VALIDACION", "Los mecánicos deben venir en una lista de legajos.")
    personas = [_personal(conn, legajo) for legajo in dict.fromkeys(str(x).strip() for x in legajos if str(x).strip())]
    if not personas:
        _err("VALIDACION", "Elegí al menos un mecánico para la OT.")
    conn.execute("DELETE FROM mt_ot_mecanicos WHERE ot_id = ?", (ot_id,))
    for legajo, nombre in personas:
        conn.execute("INSERT INTO mt_ot_mecanicos (ot_id, legajo, nombre) VALUES (?, ?, ?)", (ot_id, legajo, nombre))
    return personas


def crear_orden(conn, datos, usuario, novedad_id=None):
    # La OT es siempre de un equipo (2026-11-25: se sacó la OT por centro de costo). Las viejas con
    # centro_costo_id se siguen mostrando; la columna queda por compatibilidad.
    if not str(datos.get("ci") or "").strip():
        _err("VALIDACION", "Elegí el equipo de la OT.")
    ci = _equipo(conn, datos["ci"], solo_activo=True)["codigo"].strip()
    cc_id = None
    prioridad = str(datos.get("prioridad") or "NORMAL").upper()
    if prioridad not in PRIORIDADES:
        _err("VALIDACION", "Prioridad inválida.")
    marca = ahora()
    numero = _siguiente_numero(conn)
    cur = conn.execute(
        """INSERT INTO mt_ordenes (numero, novedad_id, ci, centro_costo_id, titulo, descripcion, prioridad, creada_por,
           creada_en, actualizada_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (numero, novedad_id, ci, cc_id, _texto(datos.get("titulo"), "el título", maximo=200),
         _texto(datos.get("descripcion"), "la descripción", obligatorio=False), prioridad, usuario, marca, marca),
    )
    ot_id = cur.lastrowid
    personas = _asignar_mecanicos(conn, ot_id, datos.get("mecanicos") or [])
    _evento(conn, ot_id, "CREADA", "Asignada a " + (", ".join(n for _, n in personas) or "nadie todavía"), usuario)
    return detalle_orden(conn, ot_id)


def asignar_mecanicos(conn, ot_id, legajos, usuario):
    f = _orden(conn, ot_id)
    if f["estado"] in ("FINALIZADA", "ANULADA"):
        _err("VALIDACION", "La OT ya está cerrada.")
    personas = _asignar_mecanicos(conn, f["id"], legajos)
    _evento(conn, f["id"], "ASIGNACION", "Mecánicos: " + (", ".join(n for _, n in personas) or "ninguno"), usuario)
    return detalle_orden(conn, f["id"])


def _cerrar_tiempos(conn, ot_id, legajo=None):
    marca = ahora()
    sql, args = "SELECT id, inicio FROM mt_ot_tiempos WHERE ot_id = ? AND fin IS NULL", [ot_id]
    if legajo:
        sql += " AND legajo = ?"
        args.append(legajo)
    for t in conn.execute(sql, args).fetchall():
        minutos = max(0.0, (_dt(marca) - _dt(t["inicio"])).total_seconds() / 60)
        conn.execute("UPDATE mt_ot_tiempos SET fin = ?, minutos = ? WHERE id = ?", (marca, round(minutos, 2), t["id"]))


def _minutos(conn, ot_id, legajo=None):
    sql, args = "SELECT inicio, fin, minutos FROM mt_ot_tiempos WHERE ot_id = ?", [ot_id]
    if legajo:
        sql += " AND legajo = ?"
        args.append(legajo)
    total, marca = 0.0, _dt(ahora())
    for t in conn.execute(sql, args):
        total += t["minutos"] if t["fin"] else max(0.0, (marca - _dt(t["inicio"])).total_seconds() / 60)
    return total


def _resumen_orden(conn, f):
    d = dict(f)
    d["mecanicos"] = [dict(m) for m in conn.execute("SELECT legajo, nombre FROM mt_ot_mecanicos WHERE ot_id = ? ORDER BY nombre", (f["id"],))]
    if f["ci"]:
        eq = conn.execute("SELECT codigo, marca, modelo, tipo FROM vehiculos WHERE UPPER(TRIM(codigo)) = UPPER(?)", (f["ci"],)).fetchone()
        d["objetivo"] = _equipo_display(eq) if eq else f["ci"]
    else:
        d["objetivo"] = _cc_texto(conn.execute("SELECT id, nombre FROM centros_costos WHERE id = ?", (f["centro_costo_id"],)).fetchone())
    horas = round(_minutos(conn, f["id"]) / 60, 2)
    d["horas"] = horas
    d["costo_mano_obra"] = round(horas * _valor_hora(conn), 2)
    # Los pedidos a Almacén que Almacén no autorizó no suman al costo
    rep = conn.execute(
        """SELECT SUM(r.cantidad * r.costo_unitario), SUM(r.costo_unitario IS NULL), COUNT(*) FROM mt_ot_repuestos r
           LEFT JOIN remitos_entrega_detalle d ON d.id = r.remito_detalle_id LEFT JOIN remitos_entrega e ON e.id = d.remito_id
           WHERE r.ot_id = ? AND UPPER(COALESCE(e.estado_autorizacion, '')) <> 'NO_AUTORIZADO'""", (f["id"],)).fetchone()
    d["costo_repuestos"] = round(rep[0] or 0, 2)
    d["repuestos_sin_costo"] = int(rep[1] or 0)
    d["cantidad_repuestos"] = rep[2]
    d["costo_total"] = round(d["costo_mano_obra"] + d["costo_repuestos"], 2)
    d["tipo"] = d.get("tipo") or "CORRECTIVA"
    d["dias"] = _dias_desde(f["creada_en"], date.fromisoformat(f["finalizada_en"][:10]) if f["finalizada_en"] else None)
    return d


def listar_ordenes(conn, estado="ACTIVAS", ci="", legajo=""):
    sql, args = "SELECT o.* FROM mt_ordenes o WHERE 1 = 1", []
    if estado == "ACTIVAS":
        sql += f" AND o.estado IN ({', '.join('?' for _ in OT_ACTIVAS)})"
        args += list(OT_ACTIVAS)
    elif estado and estado != "TODAS":
        sql += " AND o.estado = ?"
        args.append(estado)
    if ci:
        sql += " AND UPPER(o.ci) = UPPER(?)"
        args.append(str(ci).strip())
    if legajo:
        sql += " AND EXISTS (SELECT 1 FROM mt_ot_mecanicos m WHERE m.ot_id = o.id AND m.legajo = ?)"
        args.append(str(legajo).strip())
    sql += """ ORDER BY CASE o.estado WHEN 'EN_REVISION' THEN 0 WHEN 'EN_CURSO' THEN 1 WHEN 'ASIGNADA' THEN 2
               WHEN 'EN_ESPERA' THEN 3 ELSE 4 END, CASE o.prioridad WHEN 'URGENTE' THEN 0 ELSE 1 END, o.id DESC LIMIT 500"""
    return [_resumen_orden(conn, f) for f in conn.execute(sql, args).fetchall()]


def detalle_orden(conn, ot_id):
    f = _orden(conn, ot_id)
    d = _resumen_orden(conn, f)
    d["eventos"] = [dict(e) for e in conn.execute(
        "SELECT tipo, texto, autor, legajo, creado_en FROM mt_ot_eventos WHERE ot_id = ? ORDER BY id DESC", (f["id"],))]
    d["tiempos"] = []
    for t in conn.execute(
            """SELECT t.legajo, COALESCE(m.nombre, t.legajo) AS nombre, t.inicio, t.fin, t.minutos FROM mt_ot_tiempos t
               LEFT JOIN mt_ot_mecanicos m ON m.ot_id = t.ot_id AND m.legajo = t.legajo WHERE t.ot_id = ? ORDER BY t.id DESC""",
            (f["id"],)):
        d["tiempos"].append(dict(t))
    d["novedad"] = detalle_novedad(conn, f["novedad_id"]) if f["novedad_id"] else None
    d["fotos"] = fotos(conn, ot_id=f["id"])
    d["informe_url"] = f"/api/mantenimiento/ordenes/{f['id']}/informe" if f["informe_archivo"] else None
    d["tareas"] = json.loads(f["tareas_json"]) if f["tareas_json"] else []
    d["repuestos"] = [dict(r) for r in conn.execute(
        """SELECT r.*, e.numero AS remito_numero, e.estado_autorizacion AS remito_estado FROM mt_ot_repuestos r
           LEFT JOIN remitos_entrega_detalle d ON d.id = r.remito_detalle_id LEFT JOIN remitos_entrega e ON e.id = d.remito_id
           WHERE r.ot_id = ? ORDER BY r.id""", (f["id"],))]
    import mantenimiento_preventivo
    d["tareas_estado"] = mantenimiento_preventivo.tareas_ot(conn, f["id"])
    d["control_tarja"] = control_tarja(conn, f["id"])
    d["config"] = config(conn)
    return d


def comentar_orden(conn, ot_id, texto, usuario):
    f = _orden(conn, ot_id)
    _evento(conn, f["id"], "COMENTARIO", _texto(texto, "el comentario"), usuario)
    return detalle_orden(conn, f["id"])


def finalizar_orden(conn, ot_id, datos, usuario):
    """Solo el administrador de Mantenimiento. El equipo vuelve a Operativo salvo que elija otro estado."""
    f = _orden(conn, ot_id)
    if f["estado"] in ("FINALIZADA", "ANULADA"):
        _err("VALIDACION", "La OT ya está cerrada.")
    _cerrar_tiempos(conn, f["id"])
    marca = ahora()
    conn.execute("UPDATE mt_ordenes SET estado = 'FINALIZADA', finalizada_por = ?, finalizada_en = ?, actualizada_en = ? WHERE id = ?",
                 (usuario, marca, marca, f["id"]))
    _evento(conn, f["id"], "FINALIZADA", _texto(datos.get("observacion"), "la observación", obligatorio=False) or "Finalizada", usuario)
    import mantenimiento_preventivo  # una OT preventiva mueve la base del plan al service hecho
    mantenimiento_preventivo.al_finalizar_ot(conn, f, datos, usuario)
    if f["novedad_id"]:
        conn.execute("UPDATE mt_novedades SET estado = 'CERRADA', resuelta_en = ? WHERE id = ?", (marca, f["novedad_id"]))
    if f["ci"]:
        if datos.get("estado_equipo"):
            cambiar_estado_equipo(conn, f["ci"], str(datos["estado_equipo"]).upper(), usuario,
                                  f"{f['numero']} finalizada", silencioso_si_igual=True)
        else:
            _volver_a_operativo(conn, f["ci"], usuario, f"{f['numero']} finalizada")
    return detalle_orden(conn, f["id"])


def devolver_orden(conn, ot_id, observacion, usuario):
    f = _orden(conn, ot_id)
    if f["estado"] != "EN_REVISION":
        _err("VALIDACION", "Solo se devuelve una OT que está en revisión.")
    _set_estado_ot(conn, f["id"], "EN_CURSO")
    _evento(conn, f["id"], "DEVOLUCION", _texto(observacion, "la observación"), usuario)
    return detalle_orden(conn, f["id"])


def anular_orden(conn, ot_id, motivo, usuario):
    f = _orden(conn, ot_id)
    if f["estado"] in ("FINALIZADA", "ANULADA"):
        _err("VALIDACION", "La OT ya está cerrada.")
    _cerrar_tiempos(conn, f["id"])
    _set_estado_ot(conn, f["id"], "ANULADA")
    _evento(conn, f["id"], "ANULADA", _texto(motivo, "el motivo"), usuario)
    if f["novedad_id"]:
        conn.execute("UPDATE mt_novedades SET estado = 'ABIERTA', ot_id = NULL, resuelta_por = NULL, resuelta_en = NULL, "
                     "resolucion = NULL WHERE id = ? AND estado = 'EN_OT'", (f["novedad_id"],))
    return detalle_orden(conn, f["id"])


# --- acciones del mecánico (app) ---
def _orden_del_mecanico(conn, ot_id, legajo):
    f = _orden(conn, ot_id)
    if not conn.execute("SELECT 1 FROM mt_ot_mecanicos WHERE ot_id = ? AND legajo = ?", (f["id"], legajo)).fetchone():
        _err("PERMISO", "Esta OT no está asignada a vos.")
    if f["estado"] in ("EN_REVISION", "FINALIZADA", "ANULADA"):
        _err("VALIDACION", "La OT está en revisión o cerrada: ya no se puede modificar desde la app.")
    return f


def accion_mecanico(conn, ot_id, legajo, nombre, accion, texto=""):
    """accion: iniciar | pausar | avance | espera | final."""
    f = _orden_del_mecanico(conn, ot_id, legajo)
    abierto = conn.execute("SELECT 1 FROM mt_ot_tiempos WHERE ot_id = ? AND legajo = ? AND fin IS NULL", (f["id"], legajo)).fetchone()
    if accion == "iniciar":
        otro = conn.execute(
            """SELECT o.numero FROM mt_ot_tiempos t JOIN mt_ordenes o ON o.id = t.ot_id
               WHERE t.legajo = ? AND t.fin IS NULL AND t.ot_id <> ?""", (legajo, f["id"])).fetchone()
        if otro:
            _err("VALIDACION", f"Tenés el cronómetro corriendo en {otro['numero']}. Pausalo primero.")
        if not abierto:
            conn.execute("INSERT INTO mt_ot_tiempos (ot_id, legajo, inicio) VALUES (?, ?, ?)", (f["id"], legajo, ahora()))
        if f["estado"] != "EN_CURSO":
            _set_estado_ot(conn, f["id"], "EN_CURSO")
            _evento(conn, f["id"], "INICIO", "Comenzó el trabajo", nombre, legajo)
    elif accion == "pausar":
        _cerrar_tiempos(conn, f["id"], legajo)
    elif accion == "avance":
        _evento(conn, f["id"], "AVANCE", _texto(texto, "el avance"), nombre, legajo)
    elif accion == "espera":
        _cerrar_tiempos(conn, f["id"], legajo)
        _set_estado_ot(conn, f["id"], "EN_ESPERA")
        _evento(conn, f["id"], "ESPERA", _texto(texto, "el motivo de la espera"), nombre, legajo)
    elif accion == "final":
        _cerrar_tiempos(conn, f["id"])
        _set_estado_ot(conn, f["id"], "EN_REVISION")
        hechas, total = __import__("mantenimiento_preventivo").avance_tareas(conn, f["id"])
        final = _texto(texto, "qué se corrigió") + (f" (tareas hechas: {hechas} de {total})" if total else "")
        _evento(conn, f["id"], "CORRECCION_FINAL", final, nombre, legajo)
    else:
        _err("VALIDACION", "Acción desconocida.")
    return orden_para_mecanico(conn, f["id"], legajo)


def orden_para_mecanico(conn, ot_id, legajo):
    d = detalle_orden(conn, ot_id)
    d["cronometro"] = conn.execute(
        "SELECT inicio FROM mt_ot_tiempos WHERE ot_id = ? AND legajo = ? AND fin IS NULL", (d["id"], legajo)).fetchone() is not None
    d["mis_horas"] = round(_minutos(conn, d["id"], legajo) / 60, 2)
    for clave in ("config", "costo_mano_obra", "costo_repuestos", "costo_total", "control_tarja"):
        d.pop(clave, None)
    d["repuestos"] = [{k: r[k] for k in ("descripcion", "cantidad", "unidad")} for r in d["repuestos"]]
    return d


def ordenes_del_mecanico(conn, legajo):
    activas = listar_ordenes(conn, "ACTIVAS", legajo=legajo)
    for o in activas:
        o["cronometro"] = conn.execute(
            "SELECT 1 FROM mt_ot_tiempos WHERE ot_id = ? AND legajo = ? AND fin IS NULL", (o["id"], legajo)).fetchone() is not None
        for clave in ("costo_mano_obra", "costo_repuestos", "costo_total"):
            o.pop(clave, None)
    return activas


def historial_del_mecanico(conn, legajo, dias=90):
    """OT del mecánico ya cerradas (finalizadas o anuladas) en los últimos días, con sus horas. Sin costos."""
    filas = conn.execute(
        """SELECT o.* FROM mt_ordenes o WHERE o.estado IN ('FINALIZADA', 'ANULADA')
           AND EXISTS (SELECT 1 FROM mt_ot_mecanicos m WHERE m.ot_id = o.id AND m.legajo = ?)
           AND substr(COALESCE(o.finalizada_en, o.actualizada_en), 1, 10) >= ?
           ORDER BY COALESCE(o.finalizada_en, o.actualizada_en) DESC LIMIT 100""",
        (legajo, (hoy() - timedelta(days=dias)).isoformat())).fetchall()
    salida = []
    for f in filas:
        d = _resumen_orden(conn, f)
        d["mis_horas"] = round(_minutos(conn, f["id"], legajo) / 60, 2)
        d["cerrada_en"] = f["finalizada_en"] or f["actualizada_en"]
        for clave in ("costo_mano_obra", "costo_repuestos", "costo_total"):
            d.pop(clave, None)
        salida.append(d)
    return salida


def es_mecanico(conn, legajo):
    return conn.execute("SELECT 1 FROM mt_ot_mecanicos WHERE legajo = ? LIMIT 1", (legajo,)).fetchone() is not None


# ============================================================================
# Equipos: estado y ubicación
# ============================================================================
def _estado_inicial(conn, ci):
    """Sin fila en mt_equipos: OPERATIVO, salvo que vehiculos.operativo diga NO."""
    fila = conn.execute("SELECT operativo FROM vehiculos WHERE UPPER(TRIM(codigo)) = UPPER(?)", (ci,)).fetchone()
    return "FUERA_DE_SERVICIO" if fila and str(fila["operativo"] or "").strip().upper() == "NO" else "OPERATIVO"


def _fila_equipo(conn, ci):
    return conn.execute("SELECT * FROM mt_equipos WHERE ci = ?", (ci,)).fetchone()


def _asegurar_fila(conn, ci):
    if not _fila_equipo(conn, ci):
        conn.execute("INSERT INTO mt_equipos (ci, estado) VALUES (?, ?)", (ci, _estado_inicial(conn, ci)))
    return _fila_equipo(conn, ci)


def _historial(conn, ci, campo, anterior, nuevo, motivo, usuario):
    conn.execute("INSERT INTO mt_equipo_historial (ci, campo, anterior, nuevo, motivo, usuario, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (ci, campo, anterior, nuevo, motivo, usuario, ahora()))


def cambiar_estado_equipo(conn, ci, estado, usuario, motivo, silencioso_si_igual=False):
    ci = _equipo(conn, ci)["codigo"].strip()
    estado = str(estado or "").upper()
    if estado not in ESTADOS_EQUIPO:
        _err("VALIDACION", "Estado inválido: Operativo, Funcional o Fuera de servicio.")
    motivo = _texto(motivo, "el motivo del cambio", maximo=500)
    fila = _asegurar_fila(conn, ci)
    if fila["estado"] == estado:
        if silencioso_si_igual:
            return estado_equipo(conn, ci)
        _err("VALIDACION", "El equipo ya está en ese estado.")
    marca = ahora()
    conn.execute("UPDATE mt_equipos SET estado = ?, estado_desde = ?, estado_motivo = ?, actualizado_por = ?, actualizado_en = ? WHERE ci = ?",
                 (estado, marca, motivo, usuario, marca, ci))
    _historial(conn, ci, "ESTADO", fila["estado"], estado, motivo, usuario)
    return estado_equipo(conn, ci)


def cambiar_ubicacion(conn, ci, cc_id, usuario, motivo):
    ci = _equipo(conn, ci)["codigo"].strip()
    cc = _cc(conn, cc_id)
    motivo = _texto(motivo, "el motivo del cambio de ubicación", maximo=500)
    anterior = estado_equipo(conn, ci)["ubicacion"]
    _asegurar_fila(conn, ci)
    marca = ahora()
    conn.execute("""UPDATE mt_equipos SET ubic_centro_costo_id = ?, ubic_texto = ?, ubic_motivo = ?, ubic_desde = ?,
                    actualizado_por = ?, actualizado_en = ? WHERE ci = ?""",
                 (cc["id"], _cc_texto(cc), motivo, marca, usuario, marca, ci))
    _historial(conn, ci, "UBICACION", anterior, _cc_texto(cc), motivo, usuario)
    return estado_equipo(conn, ci)


_ULTIMO_PARTE_SQL = """
    SELECT ci, obra, proyecto, fecha_parte FROM (
        SELECT UPPER(TRIM(ci)) AS ci, obra, proyecto, fecha_parte,
               ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(ci)) ORDER BY fecha_parte DESC, id DESC) AS n
        FROM horas_maquinas WHERE TRIM(COALESCE(ci, '')) <> ''
          -- Hay partes con fecha 'x', '-' o dd/mm/aaaa y alguno con fecha futura: no definen la ubicación.
          AND fecha_parte GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
          AND substr(fecha_parte, 1, 10) <= date('now', '-3 hours') {filtro}
    ) WHERE n = 1
"""


def _armar_estado(ci, fila, parte, estado_def, nov, ots):
    fila = dict(fila) if fila else {}
    fecha_parte = (parte["fecha_parte"] or "")[:10] if parte else ""
    manual = fila.get("ubic_desde") and (not fecha_parte or fila["ubic_desde"][:10] >= fecha_parte)
    if manual:
        ubicacion, origen, desde = fila["ubic_texto"], "MANUAL", fila["ubic_desde"][:10]
    elif parte:
        ubicacion = (parte["obra"] or parte["proyecto"] or "").strip() or "Sin obra en el parte"
        origen, desde = "PARTE", fecha_parte
    else:
        ubicacion, origen, desde = "", "SIN_DATOS", None
    estado = fila.get("estado") or estado_def
    return {
        "ci": ci, "estado": estado, "estado_desde": (fila.get("estado_desde") or "")[:10] or None,
        "estado_motivo": fila.get("estado_motivo"), "dias_estado": _dias_desde(fila.get("estado_desde")),
        "ubicacion": ubicacion, "ubicacion_origen": origen, "ubicacion_desde": desde,
        "ubicacion_motivo": fila.get("ubic_motivo") if manual else None,
        "dias_sin_parte": _dias_desde(fecha_parte) if fecha_parte else None,
        "novedades_abiertas": nov, "ot_activas": ots,
    }


def estado_equipo(conn, ci):
    eq = _equipo(conn, ci)
    ci = eq["codigo"].strip()
    parte = conn.execute(_ULTIMO_PARTE_SQL.format(filtro="AND UPPER(TRIM(ci)) = UPPER(?)"), (ci,)).fetchone()
    nov = conn.execute("SELECT COUNT(*) FROM mt_novedades WHERE UPPER(ci) = UPPER(?) AND estado <> 'CERRADA'", (ci,)).fetchone()[0]
    ots = conn.execute(f"SELECT COUNT(*) FROM mt_ordenes WHERE UPPER(ci) = UPPER(?) AND estado IN ({', '.join('?' for _ in OT_ACTIVAS)})",
                       (ci, *OT_ACTIVAS)).fetchone()[0]
    d = _armar_estado(ci, _fila_equipo(conn, ci), parte, _estado_inicial(conn, ci), nov, ots)
    d["display"] = _equipo_display(eq)
    d["tipo"] = (eq["tipo"] or "").strip()
    return d


def listar_equipos(conn):
    partes = {f["ci"]: f for f in conn.execute(_ULTIMO_PARTE_SQL.format(filtro=""))}
    filas = {f["ci"].upper(): f for f in conn.execute("SELECT * FROM mt_equipos")}
    nov = {f[0]: f[1] for f in conn.execute("SELECT UPPER(ci), COUNT(*) FROM mt_novedades WHERE ci IS NOT NULL AND estado <> 'CERRADA' GROUP BY 1")}
    ots = {f[0]: f[1] for f in conn.execute(
        f"SELECT UPPER(ci), COUNT(*) FROM mt_ordenes WHERE ci IS NOT NULL AND estado IN ({', '.join('?' for _ in OT_ACTIVAS)}) GROUP BY 1",
        OT_ACTIVAS)}
    salida = []
    for v in conn.execute(f"SELECT codigo, marca, modelo, tipo, operativo FROM vehiculos WHERE TRIM(COALESCE(codigo, '')) <> '' AND {VEHICULO_ACTIVO_SQL} ORDER BY codigo"):
        ci = v["codigo"].strip()
        clave = ci.upper()
        estado_def = "FUERA_DE_SERVICIO" if str(v["operativo"] or "").strip().upper() == "NO" else "OPERATIVO"
        d = _armar_estado(ci, filas.get(clave), partes.get(clave), estado_def, nov.get(clave, 0), ots.get(clave, 0))
        d["display"] = _equipo_display(v)
        d["tipo"] = (v["tipo"] or "").strip()
        salida.append(d)
    return salida


def historial_equipo(conn, ci):
    ci = _equipo(conn, ci)["codigo"].strip()
    return {
        "equipo": estado_equipo(conn, ci),
        "cambios": [dict(f) for f in conn.execute(
            "SELECT campo, anterior, nuevo, motivo, usuario, creado_en FROM mt_equipo_historial WHERE ci = ? ORDER BY id DESC LIMIT 200", (ci,))],
        "novedades": listar_novedades(conn, "TODAS", ci=ci)[:100],
        "ordenes": listar_ordenes(conn, "TODAS", ci=ci)[:100],
    }


def _volver_a_operativo(conn, ci, usuario, motivo):
    """Funcional → Operativo cuando ya no quedan novedades pendientes del equipo. Fuera de servicio no se toca solo."""
    pendientes = conn.execute("SELECT COUNT(*) FROM mt_novedades WHERE UPPER(ci) = UPPER(?) AND estado <> 'CERRADA'", (ci,)).fetchone()[0]
    if pendientes:
        return
    if _asegurar_fila(conn, ci)["estado"] == "FUNCIONAL":
        cambiar_estado_equipo(conn, ci, "OPERATIVO", usuario, motivo)


# ============================================================================
# Fotos (registro; el archivo lo guarda main.py en Doc_Mantenimiento)
# ============================================================================
def fotos(conn, novedad_id=None, ot_id=None):
    campo, valor = ("novedad_id", novedad_id) if novedad_id else ("ot_id", ot_id)
    return [dict(f) for f in conn.execute(
        f"SELECT id, subida_por, subida_en FROM mt_fotos WHERE {campo} = ? ORDER BY id", (valor,))]


def validar_destino_foto(conn, novedad_id=None, ot_id=None, legajo_mecanico=None):
    """Controla que se pueda agregar una foto (existe, no está cerrada, no superó el máximo). Devuelve (campo, id)."""
    if novedad_id:
        f = _novedad(conn, novedad_id)
        if f["estado"] == "CERRADA":
            _err("VALIDACION", "La novedad ya está cerrada.")
        campo, valor = "novedad_id", f["id"]
    else:
        f = _orden_del_mecanico(conn, ot_id, legajo_mecanico) if legajo_mecanico else _orden(conn, ot_id)
        if f["estado"] in ("FINALIZADA", "ANULADA"):
            _err("VALIDACION", "La OT ya está cerrada.")
        campo, valor = "ot_id", f["id"]
    if conn.execute(f"SELECT COUNT(*) FROM mt_fotos WHERE {campo} = ?", (valor,)).fetchone()[0] >= MAX_FOTOS:
        _err("VALIDACION", f"Ya tiene {MAX_FOTOS} fotos, que es el máximo.")
    return campo, valor


def registrar_foto(conn, campo, valor, archivo, tamanio, tipo, autor, legajo=None):
    cur = conn.execute(
        f"INSERT INTO mt_fotos ({campo}, archivo, bytes, tipo, subida_por, legajo, subida_en) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (valor, archivo, tamanio, tipo, autor, legajo, ahora()))
    if campo == "ot_id":
        _evento(conn, valor, "FOTO", "Agregó una foto", autor, legajo)
    return cur.lastrowid


def foto(conn, foto_id):
    f = conn.execute("SELECT * FROM mt_fotos WHERE id = ?", (int(foto_id),)).fetchone()
    if not f:
        _err("NO_EXISTE", "La foto no existe.")
    return f


# ============================================================================
# Control de horas contra la Tarja
# ============================================================================
def control_tarja(conn, ot_id):
    """Por mecánico y día: horas del cronómetro frente al día de la tarja (centro de costo o código de estado).
    alerta: SIN_TARJA (no hay día cargado), NO_TRABAJADO (descanso, ausencia, etc.), EXCEDE (más horas que la jornada)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tarja_registros'").fetchone():
        return []
    filas = conn.execute(
        """SELECT t.legajo, COALESCE(m.nombre, t.legajo) AS nombre, substr(t.inicio, 1, 10) AS fecha,
                  SUM(CASE WHEN t.fin IS NULL THEN 0 ELSE t.minutos END) AS minutos
           FROM mt_ot_tiempos t LEFT JOIN mt_ot_mecanicos m ON m.ot_id = t.ot_id AND m.legajo = t.legajo
           WHERE t.ot_id = ? GROUP BY t.legajo, fecha ORDER BY fecha, nombre""", (ot_id,)).fetchall()
    salida = []
    for f in filas:
        r = conn.execute(
            """SELECT r.horas_efectivas, r.horas_break, c.id AS cc_id, c.nombre AS cc_nombre,
                      e.codigo, e.descripcion, e.cuenta_como_trabajado
               FROM tarja_registros r LEFT JOIN centros_costos c ON c.id = r.centro_costo_id
               LEFT JOIN tarja_codigos_estado e ON e.id = r.codigo_estado_id
               WHERE TRIM(r.legajo) = ? AND r.fecha = ?""", (f["legajo"], f["fecha"])).fetchone()
        horas = round((f["minutos"] or 0) / 60, 2)
        fila = {"legajo": f["legajo"], "nombre": f["nombre"], "fecha": f["fecha"], "horas_cronometro": horas,
                "tarja": None, "horas_tarja": None, "alerta": None}
        if not r:
            fila["alerta"] = "SIN_TARJA"
        else:
            if r["cc_id"] is not None:
                fila["tarja"] = f"{r['cc_id']} - {(r['cc_nombre'] or '').strip()}"
                trabajado = True
            else:
                fila["tarja"] = f"{r['codigo']} - {r['descripcion']}"
                trabajado = bool(r["cuenta_como_trabajado"])
            fila["horas_tarja"] = round((r["horas_efectivas"] or 0) + (r["horas_break"] or 0), 2)
            if not trabajado:
                fila["alerta"] = "NO_TRABAJADO"
            elif fila["horas_tarja"] and horas > fila["horas_tarja"]:
                fila["alerta"] = "EXCEDE"
        salida.append(fila)
    return salida


def resumen(conn):
    """Contadores de cabecera."""
    eqs = listar_equipos(conn)
    return {
        "equipos": {e: sum(1 for x in eqs if x["estado"] == e) for e in ESTADOS_EQUIPO},
        "novedades_abiertas": conn.execute("SELECT COUNT(*) FROM mt_novedades WHERE estado = 'ABIERTA'").fetchone()[0],
        "ot_activas": conn.execute(f"SELECT COUNT(*) FROM mt_ordenes WHERE estado IN ({', '.join('?' for _ in OT_ACTIVAS)})", OT_ACTIVAS).fetchone()[0],
        "ot_en_revision": conn.execute("SELECT COUNT(*) FROM mt_ordenes WHERE estado = 'EN_REVISION'").fetchone()[0],
        "preventivo": __import__("mantenimiento_preventivo").resumen(conn),
    }
