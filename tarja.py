"""Operaciones › Tarja General (Registro Diario de operadores).

Reemplaza la planilla «TARJA GENERAL»: una fila por operador activo (tabla personal) y una columna por día. Cada día
lleva un centro de costo (tabla centros_costos) o un código de estado (tarja_codigos_estado, editable).

Horas (valores en tarja_config, editables):
- Jornada normal: horas_efectivas + horas_break (10 + 2).
- Régimen Rotativo: el turno es una secuencia de días consecutivos trabajados. El primero (subida) y el último (bajada)
  son cambio de turno y suman la media jornada (5 + 1); si el turno dura un día, se aplica una sola vez.
  · Subida: el día anterior tiene un estado no trabajado o está vacío.
  · Bajada: el día siguiente tiene un estado no trabajado. Si el día siguiente está vacío (todavía no planificado)
    el turno queda abierto y no se marca bajada.
  · Cambiar de centro de costo dentro del turno no corta el turno.
  · Un estado con cuenta_como_trabajado (p. ej. Viaje) cuenta como día del turno y suma jornada.
- Régimen Diario: todos los días trabajados suman la jornada normal.
Los días con editado_manual conservan sus horas en cada recálculo.
"""
import io
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

MODULO = "operaciones"
PANEL = "tarja_general"
ACCIONES = ("consultar", "cargar", "editar_horas", "configurar")
REGIMENES = ("Rotativo", "Diario")
CATEGORIAS = ("DESCANSO", "VACACIONES", "AUSENCIA", "OTRO")
ZONA_AR = timezone(timedelta(hours=-3))  # Argentina no usa horario de verano; el servidor de Render está en UTC.
MAX_CAMBIOS = 20000
MAX_DIAS_PERIODO = 186  # hasta ~6 meses en pantalla

# Colores de los días trabajados: uno por centro de costo (id % len). Lo usan la pantalla y el Excel.
PALETA_OBRAS = ["#cfe3fb", "#d6f0dd", "#fde9c4", "#e4dcf7", "#cdeff0", "#fbd9e6",
                "#e2ecc4", "#f7dcc9", "#d7e0f5", "#d2ecea", "#f2e6b8", "#e8d6ef"]
COLOR_CAMBIO_TURNO = "#d97706"

CODIGOS_INICIALES = (
    # codigo, descripcion, color, categoria, cuenta_como_trabajado
    ("D", "Descanso", "#cbd5e1", "DESCANSO", 0),
    ("V", "Vacaciones", "#9be3b0", "VACACIONES", 0),
    ("F", "Falta", "#fca5a5", "AUSENCIA", 0),
    ("A", "Ausente", "#fca5a5", "AUSENCIA", 0),
    ("AJ", "Ausente justificado", "#fdba74", "AUSENCIA", 0),
    ("LIC", "Licencia", "#fdba74", "AUSENCIA", 0),
    ("PER", "Permiso", "#fcd34d", "AUSENCIA", 0),
    ("SAN", "Sanción/Suspensión", "#f87171", "AUSENCIA", 0),
    ("VJ", "Viaje", "#c4b5fd", "OTRO", 1),
    ("CAP", "Capacitación/Curso", "#c4b5fd", "OTRO", 1),
    ("COMP", "Compensación/Devolución de días", "#cbd5e1", "DESCANSO", 0),
    ("FER", "Feriado", "#f0abfc", "DESCANSO", 0),
    ("NUE", "Ingreso nuevo", "#7dd3fc", "OTRO", 0),
    ("BAJA", "Baja/Renuncia", "#475569", "OTRO", 0),
)


class TarjaError(Exception):
    def __init__(self, codigo, mensaje, detalle=None):
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje
        self.detalle = detalle


def _err(codigo, mensaje, detalle=None):
    raise TarjaError(codigo, mensaje, detalle)


# ============================================================================
# Estructura de base (solo agrega)
# ============================================================================
DDL = (
    """
    CREATE TABLE IF NOT EXISTS tarja_codigos_estado (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT NOT NULL UNIQUE,
        descripcion TEXT NOT NULL,
        color TEXT NOT NULL DEFAULT '#cbd5e1',
        categoria TEXT NOT NULL DEFAULT 'OTRO' CHECK (categoria IN ('DESCANSO', 'VACACIONES', 'AUSENCIA', 'OTRO')),
        cuenta_como_trabajado INTEGER NOT NULL DEFAULT 0 CHECK (cuenta_como_trabajado IN (0, 1)),
        activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
        orden INTEGER NOT NULL DEFAULT 0,
        creado_por TEXT, creado_en TEXT, actualizado_por TEXT, actualizado_en TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tarja_registros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        legajo TEXT NOT NULL,
        fecha TEXT NOT NULL,
        centro_costo_id INTEGER,
        codigo_estado_id INTEGER,
        horas_efectivas REAL NOT NULL DEFAULT 0,
        horas_break REAL NOT NULL DEFAULT 0,
        es_cambio_turno INTEGER NOT NULL DEFAULT 0 CHECK (es_cambio_turno IN (0, 1)),
        editado_manual INTEGER NOT NULL DEFAULT 0 CHECK (editado_manual IN (0, 1)),
        observaciones TEXT,
        creado_por TEXT NOT NULL, creado_en TEXT NOT NULL,
        actualizado_por TEXT, actualizado_en TEXT,
        UNIQUE (legajo, fecha),
        CHECK ((centro_costo_id IS NULL) <> (codigo_estado_id IS NULL)),
        FOREIGN KEY (legajo) REFERENCES personal (legajo) ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (centro_costo_id) REFERENCES centros_costos (id) ON DELETE RESTRICT,
        FOREIGN KEY (codigo_estado_id) REFERENCES tarja_codigos_estado (id) ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tarja_registros_fecha ON tarja_registros (fecha)",
    "CREATE INDEX IF NOT EXISTS idx_tarja_registros_cc ON tarja_registros (centro_costo_id, fecha)",
    """
    CREATE TABLE IF NOT EXISTS tarja_config (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        horas_efectivas REAL NOT NULL DEFAULT 10,
        horas_break REAL NOT NULL DEFAULT 2,
        horas_efectivas_cambio_turno REAL NOT NULL DEFAULT 5,
        horas_break_cambio_turno REAL NOT NULL DEFAULT 1,
        actualizado_por TEXT, actualizado_en TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tarja_feriados (
        fecha TEXT PRIMARY KEY,
        descripcion TEXT NOT NULL,
        creado_por TEXT, creado_en TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tarja_preferencias (
        usuario TEXT PRIMARY KEY,
        datos_json TEXT NOT NULL DEFAULT '{}',
        actualizado_en TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tarja_historial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        legajo TEXT,
        fecha TEXT,
        accion TEXT NOT NULL,
        anterior_json TEXT,
        nuevo_json TEXT,
        usuario TEXT NOT NULL,
        creado_en TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tarja_historial_dia ON tarja_historial (legajo, fecha, id)",
)


def migrar(conn):
    """Solo agrega tablas, índices, la columna personal.regimen_turno y los datos iniciales. Idempotente."""
    for sentencia in DDL:
        conn.execute(sentencia)
    cols = {f[1] for f in conn.execute("PRAGMA table_info(personal)").fetchall()}
    if "regimen_turno" not in cols:
        conn.execute("ALTER TABLE personal ADD COLUMN regimen_turno TEXT DEFAULT 'Rotativo'")
    ahora = _ahora()
    for orden, (codigo, desc, color, cat, trab) in enumerate(CODIGOS_INICIALES, 1):
        conn.execute("""INSERT OR IGNORE INTO tarja_codigos_estado
            (codigo, descripcion, color, categoria, cuenta_como_trabajado, activo, orden, creado_por, creado_en)
            VALUES (?, ?, ?, ?, ?, 1, ?, 'sistema', ?)""", (codigo, desc, color, cat, trab, orden, ahora))
    conn.execute("INSERT OR IGNORE INTO tarja_config (id, actualizado_por, actualizado_en) VALUES (1, 'sistema', ?)", (ahora,))


# ============================================================================
# Utilidades
# ============================================================================
def _ahora():
    return datetime.now(ZONA_AR).strftime("%Y-%m-%d %H:%M:%S")


def hoy():
    return datetime.now(ZONA_AR).date()


def _filas(conn, sql, params=()):
    return [dict(f) for f in conn.execute(sql, params).fetchall()]


def _fila(conn, sql, params=()):
    f = conn.execute(sql, params).fetchone()
    return dict(f) if f else None


def _dump(valor):
    return json.dumps(valor, ensure_ascii=False, default=str)


def _texto(valor):
    return "" if valor is None else str(valor).strip()


def _fecha(valor, campo="fecha"):
    try:
        return date.fromisoformat(_texto(valor)[:10])
    except ValueError:
        _err("DATOS", f"{campo} inválida: {valor!r}")


def _horas(valor, campo):
    try:
        v = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        _err("DATOS", f"{campo}: ingrese un número")
    if not 0 <= v <= 24:
        _err("DATOS", f"{campo}: debe estar entre 0 y 24")
    return round(v, 2)


def _periodo(anio, mes):
    try:
        anio, mes = int(anio), int(mes)
        desde = date(anio, mes, 1)
    except (TypeError, ValueError):
        _err("DATOS", "Período inválido")
    hasta = (date(anio + (mes == 12), mes % 12 + 1, 1)) - timedelta(days=1)
    return desde, hasta


def rango(desde=None, hasta=None, anio=None, mes=None):
    """Período de la grilla: desde/hasta (inclusive, p. ej. 20/10 al 20/12) o, si no vienen, el mes anio/mes."""
    if _texto(desde) or _texto(hasta):
        d, h = _fecha(desde, "Desde"), _fecha(hasta, "Hasta")
        if h < d:
            _err("DATOS", "La fecha Hasta es anterior a Desde")
        if (h - d).days + 1 > MAX_DIAS_PERIODO:
            _err("DATOS", f"El período no puede superar {MAX_DIAS_PERIODO} días")
        return d, h
    return _periodo(anio, mes)


def rango_de(datos):
    """Período pedido en el cuerpo de un POST/PUT (desde/hasta o anio/mes); None si no viene ninguno."""
    if _texto(datos.get("desde")) or _texto(datos.get("hasta")) or (datos.get("anio") and datos.get("mes")):
        return rango(datos.get("desde"), datos.get("hasta"), datos.get("anio"), datos.get("mes"))
    return None


def color_obra(cc_id):
    return PALETA_OBRAS[int(cc_id) % len(PALETA_OBRAS)]


def historial_evento(conn, legajo, fecha, accion, anterior, nuevo, usuario):
    conn.execute("""INSERT INTO tarja_historial (legajo, fecha, accion, anterior_json, nuevo_json, usuario, creado_en)
        VALUES (?, ?, ?, ?, ?, ?, ?)""", (legajo, fecha, accion, _dump(anterior) if anterior is not None else None,
                                          _dump(nuevo) if nuevo is not None else None, usuario or "sistema", _ahora()))


def _codigos(conn):
    return {f["id"]: f for f in _filas(conn, "SELECT * FROM tarja_codigos_estado ORDER BY orden, codigo")}


def _centros(conn):
    return {f["id"]: _texto(f["nombre"]) for f in _filas(conn, "SELECT id, nombre FROM centros_costos")}


def config(conn):
    return _fila(conn, "SELECT * FROM tarja_config WHERE id = 1")


# ============================================================================
# Cálculo de turnos y horas
# ============================================================================
def calcular_dias(registros, regimen, cfg, codigos):
    """registros: lista ordenable de dicts con fecha, centro_costo_id, codigo_estado_id, horas_*, editado_manual.
    Devuelve {fecha: (es_cambio_turno, horas_efectivas, horas_break)} para cada registro."""
    por_fecha = {r["fecha"]: r for r in registros}

    def trabajado(r):
        if r["centro_costo_id"] is not None:
            return True
        c = codigos.get(r["codigo_estado_id"])
        return bool(c and c["cuenta_como_trabajado"])

    salida = {}
    for r in registros:
        if not trabajado(r):
            ct, hef, hbr = 0, 0.0, 0.0
        else:
            ct = 0
            if regimen == "Rotativo":
                d = date.fromisoformat(r["fecha"])
                ant = por_fecha.get((d - timedelta(days=1)).isoformat())
                sig = por_fecha.get((d + timedelta(days=1)).isoformat())
                subida = ant is None or not trabajado(ant)
                bajada = sig is not None and not trabajado(sig)
                ct = 1 if (subida or bajada) else 0
            if ct:
                hef, hbr = float(cfg["horas_efectivas_cambio_turno"]), float(cfg["horas_break_cambio_turno"])
            else:
                hef, hbr = float(cfg["horas_efectivas"]), float(cfg["horas_break"])
        if r.get("editado_manual"):
            hef, hbr = float(r["horas_efectivas"]), float(r["horas_break"])
        salida[r["fecha"]] = (ct, hef, hbr)
    return salida


def recalcular_legajo(conn, legajo, cfg=None, codigos=None):
    """Recalcula cambio de turno y horas de todos los días del operador; actualiza solo los que cambian."""
    cfg = cfg or config(conn)
    codigos = codigos if codigos is not None else _codigos(conn)
    p = _fila(conn, "SELECT COALESCE(regimen_turno, 'Rotativo') AS regimen FROM personal WHERE legajo = ?", (legajo,))
    regimen = p["regimen"] if p else "Rotativo"
    regs = _filas(conn, """SELECT id, fecha, centro_costo_id, codigo_estado_id, horas_efectivas, horas_break,
        es_cambio_turno, editado_manual FROM tarja_registros WHERE legajo = ? ORDER BY fecha""", (legajo,))
    calc = calcular_dias(regs, regimen, cfg, codigos)
    cambios = []
    for r in regs:
        ct, hef, hbr = calc[r["fecha"]]
        if (ct, round(hef, 2), round(hbr, 2)) != (r["es_cambio_turno"], round(r["horas_efectivas"], 2), round(r["horas_break"], 2)):
            cambios.append((ct, hef, hbr, r["id"]))
    if cambios:
        conn.executemany("UPDATE tarja_registros SET es_cambio_turno = ?, horas_efectivas = ?, horas_break = ? WHERE id = ?", cambios)
    return len(cambios)


def recalcular_todo(conn, legajos=None):
    cfg, codigos = config(conn), _codigos(conn)
    if legajos is None:
        legajos = [f["legajo"] for f in _filas(conn, "SELECT DISTINCT legajo FROM tarja_registros")]
    return sum(recalcular_legajo(conn, lg, cfg, codigos) for lg in legajos)


# ============================================================================
# Consultas
# ============================================================================
def opciones(conn):
    centros = _filas(conn, "SELECT id, nombre, activo FROM centros_costos ORDER BY id")
    return {
        "hoy": hoy().isoformat(),
        "codigos": list(_codigos(conn).values()),
        "centros_costos": [{"id": c["id"], "nombre": _texto(c["nombre"]), "activo": c["activo"], "color": color_obra(c["id"])} for c in centros],
        "config": config(conn),
        "feriados": _filas(conn, "SELECT fecha, descripcion FROM tarja_feriados ORDER BY fecha"),
        "regimenes": list(REGIMENES),
        "categorias": list(CATEGORIAS),
        "color_cambio_turno": COLOR_CAMBIO_TURNO,
    }


def _operadores_activos(conn, legajos=None):
    sql = """SELECT legajo, nombre, COALESCE(NULLIF(regimen_turno, ''), 'Rotativo') AS regimen FROM personal
             WHERE UPPER(TRIM(COALESCE(activo, ''))) = 'ACTIVO'"""
    params = []
    if legajos is not None:
        legajos = [str(x) for x in legajos]
        if not legajos:
            return []
        sql += f" AND legajo IN ({','.join('?' * len(legajos))})"
        params = legajos
    return _filas(conn, sql + " ORDER BY nombre", params)


def _legajo_orden(legajo):
    s = _texto(legajo)
    return (0, int(s), s) if s.isdigit() else (1, 0, s)


def grilla(conn, desde, hasta, legajos=None):
    """desde/hasta: date (inclusive). Las celdas de cada operador van por fecha ISO."""
    h = hoy().isoformat()
    codigos, centros = _codigos(conn), _centros(conn)
    ops = _operadores_activos(conn, legajos)
    lista = [o["legajo"] for o in ops]
    marcas = ",".join("?" * len(lista))
    regs, hoy_regs, ultimos = [], {}, {}
    if lista:
        regs = _filas(conn, f"""SELECT legajo, fecha, centro_costo_id, codigo_estado_id, horas_efectivas, horas_break,
            es_cambio_turno, editado_manual, observaciones FROM tarja_registros
            WHERE fecha BETWEEN ? AND ? AND legajo IN ({marcas})""", [desde.isoformat(), hasta.isoformat()] + lista)
        hoy_regs = {f["legajo"]: f for f in _filas(conn, f"""SELECT legajo, centro_costo_id, codigo_estado_id FROM tarja_registros
            WHERE fecha = ? AND legajo IN ({marcas})""", [h] + lista)}
        ultimos = {f["legajo"]: f["centro_costo_id"] for f in _filas(conn, f"""SELECT legajo, centro_costo_id, MAX(fecha) AS f
            FROM tarja_registros WHERE centro_costo_id IS NOT NULL AND fecha < ? AND legajo IN ({marcas}) GROUP BY legajo""",
                                                                         [h] + lista)}
    por_legajo = {}
    for r in regs:
        por_legajo.setdefault(r["legajo"], []).append(r)

    salida = []
    for o in ops:
        dias, tot = {}, {"trabajados": 0, "cambios_turno": 0, "descansos": 0, "vacaciones": 0, "ausencias": 0,
                         "horas_efectivas": 0.0, "horas_break": 0.0}
        for r in por_legajo.get(o["legajo"], []):
            cel = {"h": r["horas_efectivas"], "b": r["horas_break"], "ct": r["es_cambio_turno"], "m": r["editado_manual"]}
            if r["observaciones"]:
                cel["o"] = r["observaciones"]
            if r["centro_costo_id"] is not None:
                cel["c"] = str(r["centro_costo_id"])
                tot["trabajados"] += 1
            else:
                c = codigos.get(r["codigo_estado_id"]) or {}
                cel["c"] = c.get("codigo", "?")
                cel["e"] = 1
                cat = c.get("categoria")
                if c.get("cuenta_como_trabajado"):
                    tot["trabajados"] += 1
                elif cat == "DESCANSO":
                    tot["descansos"] += 1
                elif cat == "VACACIONES":
                    tot["vacaciones"] += 1
                elif cat == "AUSENCIA":
                    tot["ausencias"] += 1
            tot["cambios_turno"] += r["es_cambio_turno"]
            tot["horas_efectivas"] += r["horas_efectivas"]
            tot["horas_break"] += r["horas_break"]
            dias[r["fecha"]] = cel
        tot["horas_efectivas"] = round(tot["horas_efectivas"], 2)
        tot["horas_break"] = round(tot["horas_break"], 2)
        salida.append({**o, "dias": dias, "totales": tot, "obra": _obra(hoy_regs.get(o["legajo"]), ultimos.get(o["legajo"]), centros, codigos)})
    salida.sort(key=lambda x: _legajo_orden(x["legajo"]))
    feriados = {f["fecha"]: f["descripcion"] for f in _filas(conn, "SELECT fecha, descripcion FROM tarja_feriados WHERE fecha BETWEEN ? AND ?",
                                                             (desde.isoformat(), hasta.isoformat()))}
    dias_mes = []
    d = desde
    while d <= hasta:
        dias_mes.append({"dia": d.day, "mes": d.month, "fecha": d.isoformat(), "dow": d.weekday(), "feriado": feriados.get(d.isoformat())})
        d += timedelta(days=1)
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "hoy": h, "dias": dias_mes, "operadores": salida}


def _obra(reg_hoy, ultimo_cc, centros, codigos):
    """Obra = centro de costo del operador HOY. Con un estado: el último centro trabajado + el estado. Sin cargar: P."""
    if reg_hoy is None:
        return {"pendiente": True, "texto": "P", "cc_id": ultimo_cc}
    if reg_hoy["centro_costo_id"] is not None:
        cc = reg_hoy["centro_costo_id"]
        return {"pendiente": False, "cc_id": cc, "texto": f"{cc} - {centros.get(cc, '')}"}
    c = codigos.get(reg_hoy["codigo_estado_id"]) or {}
    estado = f"(en {c.get('descripcion', '').lower()})" if c else ""
    base = f"{ultimo_cc} - {centros.get(ultimo_cc, '')}" if ultimo_cc is not None else "—"
    return {"pendiente": False, "cc_id": ultimo_cc, "estado": c.get("codigo"), "texto": f"{base} {estado}".strip()}


def historial(conn, legajo, fecha=None):
    if fecha:
        return _filas(conn, "SELECT * FROM tarja_historial WHERE legajo = ? AND fecha = ? ORDER BY id DESC LIMIT 100", (legajo, fecha))
    return _filas(conn, "SELECT * FROM tarja_historial WHERE legajo = ? ORDER BY id DESC LIMIT 100", (legajo,))


# ============================================================================
# Carga de días
# ============================================================================
def interpretar_codigo(texto, centros, codigos_por_txt):
    """'' → None (borrar). Número → centro de costo existente. Texto → código de estado activo."""
    t = _texto(texto).upper()
    if not t:
        return None
    if re.fullmatch(r"\d+(\.0+)?", t):
        cc = int(float(t))
        if cc not in centros:
            raise ValueError(f"el centro de costo {cc} no existe")
        return ("cc", cc)
    c = codigos_por_txt.get(t)
    if not c:
        raise ValueError(f"«{t}» no es un centro de costo ni un código de estado")
    if not c["activo"]:
        raise ValueError(f"el código {t} está inactivo")
    return ("estado", c["id"])


def guardar_celdas(conn, cambios, usuario, periodo=None):
    if not isinstance(cambios, list) or not cambios:
        _err("DATOS", "No hay cambios para guardar")
    if len(cambios) > MAX_CAMBIOS:
        _err("DATOS", f"Demasiadas celdas en una sola carga (máximo {MAX_CAMBIOS})")
    centros, codigos = _centros(conn), _codigos(conn)
    por_txt = {c["codigo"].upper(): c for c in codigos.values()}
    legajos_ok = {f["legajo"] for f in _filas(conn, "SELECT legajo FROM personal")}
    errores, validos = [], {}
    for c in cambios:
        legajo, fecha_txt = _texto(c.get("legajo")), _texto(c.get("fecha"))
        try:
            if legajo not in legajos_ok:
                raise ValueError("legajo inexistente")
            fecha = date.fromisoformat(fecha_txt[:10]).isoformat()
            validos[(legajo, fecha)] = (interpretar_codigo(c.get("codigo"), centros, por_txt), _texto(c.get("codigo")).upper())
        except ValueError as exc:
            errores.append(f"Legajo {legajo or '?'} · {fecha_txt or '?'}: {exc}")
    if errores:
        _err("VALIDACION", f"Se rechazó la carga: {len(errores)} celda(s) con código inválido. {errores[0]}", errores[:50])

    ahora = _ahora()
    actuales = {}
    claves = list(validos)
    for i in range(0, len(claves), 400):
        parte = claves[i:i + 400]
        cond = " OR ".join("(legajo = ? AND fecha = ?)" for _ in parte)
        for f in _filas(conn, f"SELECT * FROM tarja_registros WHERE {cond}", [x for k in parte for x in k]):
            actuales[(f["legajo"], f["fecha"])] = f
    afectados, n = set(), 0

    def trab(cc, est):
        return cc is not None or bool((codigos.get(est) or {}).get("cuenta_como_trabajado"))

    for (legajo, fecha), (valor, txt) in validos.items():
        act = actuales.get((legajo, fecha))
        ant_txt = None
        if act:
            ant_txt = str(act["centro_costo_id"]) if act["centro_costo_id"] is not None else (codigos.get(act["codigo_estado_id"]) or {}).get("codigo")
        if valor is None:
            if act:
                conn.execute("DELETE FROM tarja_registros WHERE id = ?", (act["id"],))
                historial_evento(conn, legajo, fecha, "BORRAR", {"codigo": ant_txt}, None, usuario)
                afectados.add(legajo)
                n += 1
            continue
        cc = valor[1] if valor[0] == "cc" else None
        est = valor[1] if valor[0] == "estado" else None
        if act and act["centro_costo_id"] == cc and act["codigo_estado_id"] == est:
            continue
        if act:
            # Las horas cargadas a mano se conservan si el día sigue siendo trabajado (p. ej. 209 → 210).
            manual = act["editado_manual"] if (trab(cc, est) and trab(act["centro_costo_id"], act["codigo_estado_id"])) else 0
            conn.execute("""UPDATE tarja_registros SET centro_costo_id = ?, codigo_estado_id = ?, editado_manual = ?,
                actualizado_por = ?, actualizado_en = ? WHERE id = ?""", (cc, est, manual, usuario, ahora, act["id"]))
            historial_evento(conn, legajo, fecha, "MODIFICAR", {"codigo": ant_txt}, {"codigo": txt}, usuario)
        else:
            conn.execute("""INSERT INTO tarja_registros (legajo, fecha, centro_costo_id, codigo_estado_id, creado_por, creado_en,
                actualizado_por, actualizado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (legajo, fecha, cc, est, usuario, ahora, usuario, ahora))
            historial_evento(conn, legajo, fecha, "CREAR", None, {"codigo": txt}, usuario)
        afectados.add(legajo)
        n += 1
    cfg = config(conn)
    for lg in afectados:
        recalcular_legajo(conn, lg, cfg, codigos)
    resultado = {"guardadas": n}
    if periodo:
        resultado["operadores"] = grilla(conn, *periodo, sorted(afectados))["operadores"] if afectados else []
    return resultado


def editar_registro(conn, datos, usuario, periodo=None):
    """Horas a mano (editado_manual) u observaciones de un día; restaurar=True vuelve al cálculo automático."""
    legajo = _texto(datos.get("legajo"))
    fecha = _fecha(datos.get("fecha")).isoformat()
    act = _fila(conn, "SELECT * FROM tarja_registros WHERE legajo = ? AND fecha = ?", (legajo, fecha))
    if not act:
        _err("NO_EXISTE", "Primero cargue un centro de costo o un estado en ese día")
    antes = {k: act[k] for k in ("horas_efectivas", "horas_break", "editado_manual", "observaciones")}
    obs = _texto(datos.get("observaciones")) if "observaciones" in datos else act["observaciones"]
    if datos.get("restaurar"):
        conn.execute("UPDATE tarja_registros SET editado_manual = 0, observaciones = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                     (obs or None, usuario, _ahora(), act["id"]))
    else:
        hef = _horas(datos.get("horas_efectivas", act["horas_efectivas"]), "Horas efectivas")
        hbr = _horas(datos.get("horas_break", act["horas_break"]), "Horas de break")
        manual = 1 if (act["editado_manual"] or (hef, hbr) != (round(act["horas_efectivas"], 2), round(act["horas_break"], 2))) else 0
        conn.execute("""UPDATE tarja_registros SET horas_efectivas = ?, horas_break = ?, editado_manual = ?, observaciones = ?,
            actualizado_por = ?, actualizado_en = ? WHERE id = ?""", (hef, hbr, manual, obs or None, usuario, _ahora(), act["id"]))
    recalcular_legajo(conn, legajo)
    nuevo = _fila(conn, "SELECT horas_efectivas, horas_break, editado_manual, observaciones FROM tarja_registros WHERE id = ?", (act["id"],))
    historial_evento(conn, legajo, fecha, "RESTAURAR_HORAS" if datos.get("restaurar") else "EDITAR_HORAS", antes, nuevo, usuario)
    salida = {"registro": nuevo}
    if periodo:
        salida["operadores"] = grilla(conn, *periodo, [legajo])["operadores"]
    return salida


def cambiar_regimen(conn, legajo, regimen, usuario):
    legajo = _texto(legajo)
    if regimen not in REGIMENES:
        _err("DATOS", "Régimen inválido: use Rotativo o Diario")
    p = _fila(conn, "SELECT COALESCE(regimen_turno, 'Rotativo') AS r FROM personal WHERE legajo = ?", (legajo,))
    if not p:
        _err("NO_EXISTE", "Operador inexistente")
    if p["r"] != regimen:
        conn.execute("UPDATE personal SET regimen_turno = ? WHERE legajo = ?", (regimen, legajo))
        historial_evento(conn, legajo, None, "REGIMEN", {"regimen": p["r"]}, {"regimen": regimen}, usuario)
        recalcular_legajo(conn, legajo)
    return {"legajo": legajo, "regimen": regimen}


# ============================================================================
# Configuración: jornada, códigos de estado, feriados, preferencias
# ============================================================================
def guardar_config(conn, datos, usuario):
    antes = config(conn)
    campos = {"horas_efectivas": "Jornada: horas efectivas", "horas_break": "Jornada: horas de break",
              "horas_efectivas_cambio_turno": "Cambio de turno: horas efectivas", "horas_break_cambio_turno": "Cambio de turno: horas de break"}
    valores = {k: _horas(datos.get(k, antes[k]), etq) for k, etq in campos.items()}
    conn.execute("""UPDATE tarja_config SET horas_efectivas = ?, horas_break = ?, horas_efectivas_cambio_turno = ?,
        horas_break_cambio_turno = ?, actualizado_por = ?, actualizado_en = ? WHERE id = 1""",
                 (*valores.values(), usuario, _ahora()))
    historial_evento(conn, None, None, "CONFIG_JORNADA", {k: antes[k] for k in campos}, valores, usuario)
    n = recalcular_todo(conn)
    return {"config": config(conn), "recalculados": n}


def _validar_codigo(conn, datos, id_actual=None):
    codigo = _texto(datos.get("codigo")).upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9\-]{0,9}", codigo):
        _err("DATOS", "Código: de 1 a 10 caracteres, empieza con letra (los números son centros de costo)")
    otro = _fila(conn, "SELECT id FROM tarja_codigos_estado WHERE UPPER(codigo) = ?", (codigo,))
    if otro and otro["id"] != id_actual:
        _err("DUPLICADO", f"El código {codigo} ya existe")
    desc = _texto(datos.get("descripcion"))
    if not desc:
        _err("DATOS", "La descripción es obligatoria")
    color = _texto(datos.get("color")) or "#cbd5e1"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        _err("DATOS", "Color inválido (formato #rrggbb)")
    cat = _texto(datos.get("categoria")).upper() or "OTRO"
    if cat not in CATEGORIAS:
        _err("DATOS", "Categoría inválida")
    return {"codigo": codigo, "descripcion": desc, "color": color.lower(), "categoria": cat,
            "cuenta_como_trabajado": 1 if datos.get("cuenta_como_trabajado") else 0,
            "activo": 0 if datos.get("activo") in (False, 0, "0") else 1}


def crear_codigo(conn, datos, usuario):
    v = _validar_codigo(conn, datos)
    orden = (_fila(conn, "SELECT COALESCE(MAX(orden), 0) + 1 AS o FROM tarja_codigos_estado") or {"o": 1})["o"]
    cur = conn.execute("""INSERT INTO tarja_codigos_estado (codigo, descripcion, color, categoria, cuenta_como_trabajado, activo, orden,
        creado_por, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (*v.values(), orden, usuario, _ahora()))
    historial_evento(conn, None, None, "CODIGO_CREAR", None, v, usuario)
    return _fila(conn, "SELECT * FROM tarja_codigos_estado WHERE id = ?", (cur.lastrowid,))


def actualizar_codigo(conn, codigo_id, datos, usuario):
    antes = _fila(conn, "SELECT * FROM tarja_codigos_estado WHERE id = ?", (codigo_id,))
    if not antes:
        _err("NO_EXISTE", "Código inexistente")
    v = _validar_codigo(conn, datos, codigo_id)
    conn.execute("""UPDATE tarja_codigos_estado SET codigo = ?, descripcion = ?, color = ?, categoria = ?, cuenta_como_trabajado = ?,
        activo = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?""", (*v.values(), usuario, _ahora(), codigo_id))
    historial_evento(conn, None, None, "CODIGO_MODIFICAR", {k: antes[k] for k in v}, v, usuario)
    n = 0
    if v["cuenta_como_trabajado"] != antes["cuenta_como_trabajado"]:
        legajos = [f["legajo"] for f in _filas(conn, "SELECT DISTINCT legajo FROM tarja_registros WHERE codigo_estado_id = ?", (codigo_id,))]
        n = recalcular_todo(conn, legajos)
    return {**_fila(conn, "SELECT * FROM tarja_codigos_estado WHERE id = ?", (codigo_id,)), "recalculados": n}


def agregar_feriado(conn, datos, usuario):
    fecha = _fecha(datos.get("fecha")).isoformat()
    desc = _texto(datos.get("descripcion")) or "Feriado"
    conn.execute("""INSERT INTO tarja_feriados (fecha, descripcion, creado_por, creado_en) VALUES (?, ?, ?, ?)
        ON CONFLICT(fecha) DO UPDATE SET descripcion = excluded.descripcion""", (fecha, desc, usuario, _ahora()))
    historial_evento(conn, None, fecha, "FERIADO_GUARDAR", None, {"descripcion": desc}, usuario)
    return {"fecha": fecha, "descripcion": desc}


def borrar_feriado(conn, fecha, usuario):
    fecha = _fecha(fecha).isoformat()
    antes = _fila(conn, "SELECT * FROM tarja_feriados WHERE fecha = ?", (fecha,))
    if not antes:
        _err("NO_EXISTE", "Ese feriado no existe")
    conn.execute("DELETE FROM tarja_feriados WHERE fecha = ?", (fecha,))
    historial_evento(conn, None, fecha, "FERIADO_BORRAR", {"descripcion": antes["descripcion"]}, None, usuario)
    return {"fecha": fecha}


def preferencias(conn, usuario):
    f = _fila(conn, "SELECT datos_json FROM tarja_preferencias WHERE usuario = ?", (usuario,))
    try:
        return json.loads(f["datos_json"]) if f else {}
    except ValueError:
        return {}


def guardar_preferencias(conn, usuario, datos):
    texto = _dump(datos if isinstance(datos, dict) else {})
    if len(texto) > 100_000:
        _err("DATOS", "Las preferencias son demasiado grandes")
    conn.execute("""INSERT INTO tarja_preferencias (usuario, datos_json, actualizado_en) VALUES (?, ?, ?)
        ON CONFLICT(usuario) DO UPDATE SET datos_json = excluded.datos_json, actualizado_en = excluded.actualizado_en""",
                 (usuario, texto, _ahora()))
    return {"ok": True}


# ============================================================================
# Resumen por centro de costo y exportación a Excel
# ============================================================================
def resumen_centros(operadores, centros):
    acc = {}
    for o in operadores:
        for cel in o["dias"].values():
            if cel.get("e"):
                continue
            cc = int(cel["c"])
            r = acc.setdefault(cc, {"cc_id": cc, "nombre": centros.get(cc, ""), "dias_hombre": 0, "operadores": set(),
                                    "horas_efectivas": 0.0, "horas_break": 0.0})
            r["dias_hombre"] += 1
            r["operadores"].add(o["legajo"])
            r["horas_efectivas"] += cel["h"]
            r["horas_break"] += cel["b"]
    return [{**r, "operadores": len(r["operadores"]), "horas_efectivas": round(r["horas_efectivas"], 2),
             "horas_break": round(r["horas_break"], 2)} for r in sorted(acc.values(), key=lambda x: x["cc_id"])]


MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
INICIALES = "LMMJVSD"


def _fecha_txt(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def exportar_excel(conn, desde, hasta, legajos=None):
    """desde/hasta: date. legajos: lista en el orden de pantalla (lo filtrado) o None = todos los operadores activos."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    g = grilla(conn, desde, hasta, legajos)
    ops = g["operadores"]
    if legajos is not None:
        orden = {str(lg): i for i, lg in enumerate(legajos)}
        ops.sort(key=lambda o: orden.get(o["legajo"], 10 ** 6))
    codigos = {c["codigo"]: c for c in _codigos(conn).values()}
    centros = _centros(conn)
    titulo = f"TARJA GENERAL · {_fecha_txt(g['desde'])} al {_fecha_txt(g['hasta'])}"
    fino = Side(style="thin", color="9FB3C8")
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)
    borde_ct = Border(left=fino, right=fino, top=fino, bottom=Side(style="thick", color=COLOR_CAMBIO_TURNO[1:]))
    cab = PatternFill("solid", fgColor="DCE8F7")
    finde = PatternFill("solid", fgColor="EEF2F7")
    feriado = PatternFill("solid", fgColor="F5D0FE")
    negrita = Font(bold=True)
    centro = Alignment(horizontal="center", vertical="center")
    fill = lambda hexa: PatternFill("solid", fgColor=hexa.lstrip("#").upper())  # noqa: E731

    wb = Workbook()
    ws = wb.active
    ws.title = "Tarja"
    ws.append([titulo])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Exportado {_ahora()} · {len(ops)} operadores" + (" (filtrado en pantalla)" if legajos is not None else "")])
    ws.append([])
    tot_cols = ["Días trab.", "Cambios turno", "Descansos", "Vacaciones", "Ausencias", "Hs efectivas", "Hs break"]
    # El primer día y cada día 1 llevan el mes (20/10 … 1/11 …), porque el período puede abarcar varios meses.
    cab1 = ["Legajo", "Apellido y Nombre", "Obra", "Régimen"] + [
        f"{d['dia']}/{d['mes']}" if (j == 0 or d["dia"] == 1) else d["dia"] for j, d in enumerate(g["dias"])] + tot_cols
    cab2 = ["", "", "", ""] + [INICIALES[d["dow"]] for d in g["dias"]] + [""] * len(tot_cols)
    for fila_cab in (cab1, cab2):
        ws.append(fila_cab)
        for i in range(1, len(fila_cab) + 1):
            c = ws.cell(ws.max_row, i)
            c.font, c.border, c.alignment, c.fill = negrita, borde, centro, cab
    for j, d in enumerate(g["dias"]):
        if d["feriado"] or d["dow"] >= 5:
            for r in (ws.max_row - 1, ws.max_row):
                ws.cell(r, 5 + j).fill = feriado if d["feriado"] else finde
    for o in ops:
        t = o["totales"]
        valores = [int(o["legajo"]) if o["legajo"].isdigit() else o["legajo"], o["nombre"], o["obra"]["texto"], o["regimen"]]
        for d in g["dias"]:
            cel = o["dias"].get(d["fecha"])
            valores.append((int(cel["c"]) if not cel.get("e") else cel["c"]) if cel else None)
        valores += [t["trabajados"], t["cambios_turno"], t["descansos"], t["vacaciones"], t["ausencias"], t["horas_efectivas"], t["horas_break"]]
        ws.append(valores)
        fila = ws.max_row
        for i in range(1, len(valores) + 1):
            ws.cell(fila, i).border = borde
        if o["obra"]["pendiente"]:
            ws.cell(fila, 3).font = Font(bold=True, color="B91C1C")
        for j, d in enumerate(g["dias"]):
            cel = o["dias"].get(d["fecha"])
            if not cel:
                continue
            c = ws.cell(fila, 5 + j)
            c.alignment = centro
            if cel.get("e"):
                color = (codigos.get(cel["c"]) or {}).get("color", "#cbd5e1")
            else:
                color = color_obra(int(cel["c"]))
            c.fill = fill(color)
            if cel["ct"]:
                c.border = borde_ct
    ws.freeze_panes = ws.cell(6, 5)
    ws.column_dimensions["A"].width = 9
    ws.column_dimensions["B"].width = 32
    ws.column_dimensions["C"].width = 40
    ws.column_dimensions["D"].width = 10
    for j in range(len(g["dias"])):
        ws.column_dimensions[get_column_letter(5 + j)].width = 5.5
    for k in range(len(tot_cols)):
        ws.column_dimensions[get_column_letter(5 + len(g["dias"]) + k)].width = 12
    ws.append([])
    ws.append(["Referencias:"])
    ws.cell(ws.max_row, 1).font = negrita
    for c in codigos.values():
        ws.append([c["codigo"], c["descripcion"]])
        ws.cell(ws.max_row, 1).fill = fill(c["color"])
    ws.append(["", "Borde inferior naranja = día de cambio de turno (subida / bajada)"])

    ws2 = wb.create_sheet("Totales por operador")
    ws2.append([titulo])
    ws2["A1"].font = Font(bold=True, size=14)
    ws2.append([])
    ws2.append(["Legajo", "Apellido y Nombre", "Régimen", "Obra (hoy)"] + tot_cols)
    for i in range(1, 12):
        ws2.cell(3, i).font, ws2.cell(3, i).fill, ws2.cell(3, i).border = negrita, cab, borde
    for o in ops:
        t = o["totales"]
        ws2.append([o["legajo"], o["nombre"], o["regimen"], o["obra"]["texto"], t["trabajados"], t["cambios_turno"], t["descansos"],
                    t["vacaciones"], t["ausencias"], t["horas_efectivas"], t["horas_break"]])
    for col, ancho in zip("ABCDEFGHIJK", (9, 32, 10, 40, 11, 13, 11, 11, 11, 13, 11)):
        ws2.column_dimensions[col].width = ancho

    ws3 = wb.create_sheet("Por centro de costo")
    ws3.append([titulo])
    ws3["A1"].font = Font(bold=True, size=14)
    ws3.append([])
    ws3.append(["Centro de costo", "Descripción", "Operadores", "Días-hombre", "Hs efectivas", "Hs break"])
    for i in range(1, 7):
        ws3.cell(3, i).font, ws3.cell(3, i).fill, ws3.cell(3, i).border = negrita, cab, borde
    res = resumen_centros(ops, centros)
    for r in res:
        ws3.append([r["cc_id"], r["nombre"], r["operadores"], r["dias_hombre"], r["horas_efectivas"], r["horas_break"]])
    ws3.append(["Total", "", "", sum(r["dias_hombre"] for r in res), round(sum(r["horas_efectivas"] for r in res), 2),
                round(sum(r["horas_break"] for r in res), 2)])
    for i in range(1, 7):
        ws3.cell(ws3.max_row, i).font = negrita
    for col, ancho in zip("ABCDEF", (15, 46, 12, 13, 13, 11)):
        ws3.column_dimensions[col].width = ancho

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"Tarja_General_{g['desde']}_a_{g['hasta']}.xlsx"


# ============================================================================
# Importación del histórico (planilla «TARJA GENERAL»): mapeo de cada celda
# ============================================================================
def _normal(texto):
    t = unicodedata.normalize("NFKD", _texto(texto)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


MAPEO_ESTADOS = {
    "d": "D", "v": "V", "f": "F",
    "ausente": "A", "aus": "A", "au": "A",
    "lic": "LIC", "permiso": "PER", "sancion": "SAN", "viaje": "VJ",
    "curso": "CAP", "capacitacion": "CAP",
    "dev": "COMP", "dev8": "COMP", "dev9": "COMP", "compensar": "COMP",
    "fin": "BAJA",
}
# Obras sin centro de costo confirmado: van al reporte de excepciones, nunca se crean centros.
SIN_CC_CONFIRMADO = {"p210-rb", "sdj-m", "riotinto", "norlab", "arizaro", "arli", "smg", "gemar", "pm", "exar"}
NUMEROS_SIN_CONFIRMAR = {0, 32, 108, 109, 230}
BASURA = {"x", "c", "`", "?"}


def mapear_celda_excel(valor, centros):
    """Devuelve (tipo, dato, observacion):
    ('cc', id, None) | ('estado', codigo, obs) | ('excepcion', motivo, None) | ('ignorar', motivo, None) | (None, None, None) vacía."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return (None, None, None)
    if isinstance(valor, bool):
        return ("ignorar", "valor sin sentido", None)
    if isinstance(valor, (int, float)) or re.fullmatch(r"\d+(\.0+)?", _texto(valor)):
        n = float(valor)
        if n != int(n):
            return ("ignorar", "número con decimales", None)
        n = int(n)
        if n in NUMEROS_SIN_CONFIRMAR:
            return ("excepcion", f"número {n} sin centro de costo confirmado", None)
        if n not in centros:
            return ("excepcion", f"centro de costo {n} inexistente en el sistema (queda vacío)", None)
        return ("cc", n, None)
    if isinstance(valor, (datetime, date)):
        return ("ignorar", "fecha en una celda de día", None)
    t = _normal(valor)
    if t in BASURA:
        return ("ignorar", "basura", None)
    if t in MAPEO_ESTADOS:
        return ("estado", MAPEO_ESTADOS[t], None)
    base = t.rstrip("?").strip()
    if base in SIN_CC_CONFIRMADO:
        return ("excepcion", f"«{_texto(valor)}» sin centro de costo confirmado", None)
    if len(t) == 1:
        return ("ignorar", "un solo carácter sin sentido", None)
    # Resto de textos (Ausente Justificado, Ausente 1/2, lugares, «PERMISO, ESTUDIOS MEDICOS»…): Permiso con justificación.
    return ("estado", "PER", _texto(valor))
