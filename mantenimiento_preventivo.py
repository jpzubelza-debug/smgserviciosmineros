"""Mantenimiento › Etapa 2: preventivo por horas / km / días, repuestos de la OT y control del horómetro.

Preventivo
- Plan (mt_prev_planes): p. ej. «Programado simple» con sus tareas e intervalos por horas, km y/o días. Vence lo que
  ocurra primero. «Aviso» = % del intervalo antes del vencimiento en que pasa a «Próximo».
- Se asigna a equipos (mt_prev_equipos) con una base: la lectura del último service (por defecto, la lectura actual).
- La lectura actual sale del último parte diario con horómetro / km final (horas_maquinas), por fecha.
- Desde un vencido o próximo se crea la OT preventiva (mt_ordenes.tipo = 'PREVENTIVA'); al finalizarla, la base pasa a
  la lectura del service. También se puede registrar un service hecho fuera del sistema.

Repuestos (mt_ot_repuestos): lo usado en la OT, desde Almacén (entregas al equipo o artículo del catálogo), desde una
solicitud de Compras del equipo o cargado a mano. Suma al costo de la OT. No mueve stock: eso sigue en Almacén.

Horómetro: al cargar un parte (app o escritorio) se bloquea una jornada de más de 24 horas y se registran como
«lecturas a revisar» los retrocesos y saltos frente al parte anterior del equipo (mt_lecturas_observadas).
"""
import json
import sqlite3
from datetime import date

import mantenimiento as mt

PANEL = "preventivo"
ACCIONES = ("consultar", "gestionar")
MAX_HS_JORNADA = 24       # bloquea
SALTO_HS = 48             # aviso: horas sin parte entre el anterior y este
MAX_KM_JORNADA = 1500     # aviso
SALTO_KM = 1000           # aviso
ESTADOS = ("VENCIDO", "PROXIMO", "REVISAR", "SIN_LECTURA", "AL_DIA")

DDL = (
    """
    CREATE TABLE IF NOT EXISTS mt_prev_planes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL COLLATE NOCASE UNIQUE,
        descripcion TEXT,
        intervalo_hs REAL,
        intervalo_km REAL,
        intervalo_dias INTEGER,
        aviso_pct REAL NOT NULL DEFAULT 10,
        activo INTEGER NOT NULL DEFAULT 1,
        actualizado_por TEXT,
        actualizado_en TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt_prev_tareas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL REFERENCES mt_prev_planes (id) ON DELETE CASCADE,
        orden INTEGER NOT NULL,
        texto TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_prevt_plan ON mt_prev_tareas (plan_id)",
    """
    CREATE TABLE IF NOT EXISTS mt_prev_equipos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL REFERENCES mt_prev_planes (id),
        ci TEXT NOT NULL,
        base_hs REAL,
        base_km REAL,
        base_fecha TEXT NOT NULL,
        base_origen TEXT,
        activo INTEGER NOT NULL DEFAULT 1,
        actualizado_por TEXT,
        actualizado_en TEXT,
        UNIQUE (plan_id, ci)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt_prev_historial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asignacion_id INTEGER NOT NULL REFERENCES mt_prev_equipos (id),
        fecha TEXT NOT NULL,
        hs REAL,
        km REAL,
        origen TEXT NOT NULL,
        ot_id INTEGER,
        nota TEXT,
        usuario TEXT NOT NULL,
        creado_en TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_prevh_asig ON mt_prev_historial (asignacion_id)",
    """
    CREATE TABLE IF NOT EXISTS mt_ot_repuestos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ot_id INTEGER NOT NULL REFERENCES mt_ordenes (id) ON DELETE CASCADE,
        origen TEXT NOT NULL CHECK (origen IN ('ALMACEN', 'COMPRA', 'OTRO')),
        producto_id INTEGER,
        solicitud_detalle_id INTEGER,
        remito_detalle_id INTEGER,
        codigo TEXT,
        descripcion TEXT NOT NULL,
        cantidad REAL NOT NULL,
        unidad TEXT,
        costo_unitario REAL,
        referencia TEXT,
        cargado_por TEXT NOT NULL,
        cargado_en TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_otr_ot ON mt_ot_repuestos (ot_id)",
    """
    CREATE TABLE IF NOT EXISTS mt_lecturas_observadas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parte_id INTEGER,
        ci TEXT NOT NULL,
        fecha_parte TEXT,
        tipo TEXT NOT NULL,
        mensaje TEXT NOT NULL,
        origen TEXT,
        creada_en TEXT NOT NULL,
        revisada_por TEXT,
        revisada_en TEXT,
        nota TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mt_lect_ci ON mt_lecturas_observadas (ci)",
    """
    CREATE TABLE IF NOT EXISTS mt_ot_tareas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ot_id INTEGER NOT NULL REFERENCES mt_ordenes (id) ON DELETE CASCADE,
        orden INTEGER NOT NULL,
        texto TEXT NOT NULL,
        hecha INTEGER NOT NULL DEFAULT 0,
        hecha_por TEXT,
        hecha_en TEXT,
        UNIQUE (ot_id, orden)
    )
    """,
)
COLUMNAS_OT = {
    "tipo": "TEXT NOT NULL DEFAULT 'CORRECTIVA'",
    "prev_equipo_id": "INTEGER",
    "lectura_hs": "REAL",
    "lectura_km": "REAL",
    "tareas_json": "TEXT",
}


def migrar(conn):
    for sentencia in DDL:
        conn.execute(sentencia)
    existentes = {f[1] for f in conn.execute("PRAGMA table_info(mt_ordenes)")}
    for col, tipo in COLUMNAS_OT.items():
        if col not in existentes:
            conn.execute(f"ALTER TABLE mt_ordenes ADD COLUMN {col} {tipo}")
    existentes = {f[1] for f in conn.execute("PRAGMA table_info(mt_prev_equipos)")}
    for col in ("aviso_estado", "aviso_en"):  # último aviso por mail (Próximo / Vencido)
        if col not in existentes:
            conn.execute(f"ALTER TABLE mt_prev_equipos ADD COLUMN {col} TEXT")


def _num(valor, campo, minimo=0.0, obligatorio=False, entero=False):
    texto = str(valor if valor is not None else "").strip().replace(",", ".")
    if not texto:
        if obligatorio:
            mt._err("VALIDACION", f"Falta {campo}.")
        return None
    try:
        n = float(texto)
    except ValueError:
        mt._err("VALIDACION", f"{campo.capitalize()} debe ser un número.")
    if n < minimo:
        mt._err("VALIDACION", f"{campo.capitalize()} no puede ser menor a {minimo:g}.")
    return int(n) if entero else n


def _fecha(valor, campo):
    try:
        f = date.fromisoformat(str(valor or "").strip()[:10])
    except ValueError:
        mt._err("VALIDACION", f"{campo.capitalize()} no es una fecha válida.")
    if f > mt.hoy():
        mt._err("VALIDACION", f"{campo.capitalize()} no puede ser futura.")
    return f.isoformat()


# ============================================================================
# Lecturas (horómetro y km) desde los partes diarios
# ============================================================================
_VALOR_HS = "CAST(REPLACE(TRIM(CAST(hs_final AS TEXT)), ',', '.') AS REAL)"
_VALOR_KM = "CAST(km_final AS REAL)"
_FECHA_OK = ("fecha_parte GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' "
             "AND substr(fecha_parte, 1, 10) <= date('now', '-3 hours')")


def _ultimas(conn, expr, ci=None):
    filtro, args = ("AND UPPER(TRIM(ci)) = UPPER(?)", [ci]) if ci else ("", [])
    sql = f"""SELECT ci, valor, fecha FROM (
                SELECT UPPER(TRIM(ci)) AS ci, {expr} AS valor, substr(fecha_parte, 1, 10) AS fecha,
                       ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(ci)) ORDER BY fecha_parte DESC, id DESC) AS n
                FROM horas_maquinas WHERE TRIM(COALESCE(ci, '')) <> '' AND {_FECHA_OK} AND {expr} > 0 {filtro}
              ) WHERE n = 1"""
    return {f["ci"]: (f["valor"], f["fecha"]) for f in conn.execute(sql, args)}


def lecturas(conn, ci=None):
    """{CI: {hs, hs_fecha, km, km_fecha}} con el último parte (por fecha) que tiene horómetro / km final."""
    hs, km = _ultimas(conn, _VALOR_HS, ci), _ultimas(conn, _VALOR_KM, ci)
    salida = {}
    for clave in set(hs) | set(km):
        h, k = hs.get(clave, (None, None)), km.get(clave, (None, None))
        salida[clave] = {"hs": h[0], "hs_fecha": h[1], "km": k[0], "km_fecha": k[1]}
    return salida


def _lectura(conn, ci):
    return lecturas(conn, ci).get(ci.upper(), {"hs": None, "hs_fecha": None, "km": None, "km_fecha": None})


# ============================================================================
# Control del horómetro al cargar un parte
# ============================================================================
def _anterior(conn, ci, fecha, expr):
    return conn.execute(
        f"""SELECT {expr} AS valor, substr(fecha_parte, 1, 10) AS fecha FROM horas_maquinas
            WHERE UPPER(TRIM(ci)) = UPPER(?) AND {_FECHA_OK} AND substr(fecha_parte, 1, 10) <= ? AND {expr} > 0
            ORDER BY fecha_parte DESC, id DESC LIMIT 1""", (ci, fecha)).fetchone()


def _ddmm(fecha):
    return f"{fecha[8:10]}/{fecha[5:7]}" if fecha else ""


def revisar_lecturas(conn, ci, fecha, hs_inicio, hs_final, km_inicio, km_final):
    """(bloqueo, avisos). Bloquea solo lo imposible; el resto queda registrado para que lo revise Mantenimiento."""
    hs_inicio, hs_final, km_inicio, km_final = (float(x or 0) for x in (hs_inicio, hs_final, km_inicio, km_final))
    if hs_final > 0 and hs_final - hs_inicio > MAX_HS_JORNADA:
        return (f"El horómetro marca {hs_final - hs_inicio:g} horas en un solo parte (máximo {MAX_HS_JORNADA}). "
                "Revisá el horómetro inicial y el final."), []
    avisos = []
    fecha = str(fecha or "")[:10]
    for nombre, ini, fin, expr, salto, unidad in (("HOROMETRO", hs_inicio, hs_final, _VALOR_HS, SALTO_HS, "h"),
                                                  ("KM", km_inicio, km_final, _VALOR_KM, SALTO_KM, "km")):
        if ini <= 0:
            continue
        ant = _anterior(conn, ci, fecha, expr)
        if not ant:
            continue
        texto = "horómetro" if nombre == "HOROMETRO" else "kilometraje"
        if ini < ant["valor"] - 0.5:
            avisos.append({"tipo": f"{nombre}_RETROCESO",
                           "mensaje": f"El {texto} inicial ({ini:g}) es menor que el final del parte anterior ({ant['valor']:g} {unidad}, {_ddmm(ant['fecha'])})."})
        elif ini - ant["valor"] > salto:
            avisos.append({"tipo": f"{nombre}_SALTO",
                           "mensaje": f"Hay {ini - ant['valor']:g} {unidad} sin parte entre el anterior ({ant['valor']:g}, {_ddmm(ant['fecha'])}) y este ({ini:g})."})
    if km_final > 0 and km_final - km_inicio > MAX_KM_JORNADA:
        avisos.append({"tipo": "KM_JORNADA", "mensaje": f"El parte marca {km_final - km_inicio:g} km en un día."})
    return None, avisos


def registrar_observadas(conn, parte_id, ci, fecha, avisos, origen):
    marca = mt.ahora()
    for a in avisos:
        conn.execute(
            """INSERT INTO mt_lecturas_observadas (parte_id, ci, fecha_parte, tipo, mensaje, origen, creada_en)
               VALUES (?, ?, ?, ?, ?, ?, ?)""", (parte_id, ci, str(fecha or "")[:10], a["tipo"], a["mensaje"], origen or "WEB", marca))


def lecturas_observadas(conn, pendientes=True, ci=""):
    sql, args = "SELECT * FROM mt_lecturas_observadas WHERE 1 = 1", []
    if pendientes:
        sql += " AND revisada_en IS NULL"
    if ci:
        sql += " AND UPPER(ci) = UPPER(?)"
        args.append(ci)
    sql += " ORDER BY id DESC LIMIT 500"
    return [dict(f) for f in conn.execute(sql, args)]


def revisar_observada(conn, obs_id, nota, usuario):
    f = conn.execute("SELECT * FROM mt_lecturas_observadas WHERE id = ?", (int(obs_id),)).fetchone()
    if not f:
        mt._err("NO_EXISTE", "La lectura observada no existe.")
    if f["revisada_en"]:
        mt._err("VALIDACION", "Ya está revisada.")
    conn.execute("UPDATE mt_lecturas_observadas SET revisada_por = ?, revisada_en = ?, nota = ? WHERE id = ?",
                 (usuario, mt.ahora(), mt._texto(nota, "la nota", obligatorio=False, maximo=500) or None, f["id"]))
    return lecturas_observadas(conn)


# ============================================================================
# Planes de mantenimiento preventivo
# ============================================================================
def _plan(conn, plan_id):
    f = conn.execute("SELECT * FROM mt_prev_planes WHERE id = ?", (int(plan_id),)).fetchone()
    if not f:
        mt._err("NO_EXISTE", "El plan no existe.")
    return f


def _tareas(conn, plan_id):
    return [f["texto"] for f in conn.execute("SELECT texto FROM mt_prev_tareas WHERE plan_id = ? ORDER BY orden", (plan_id,))]


def planes(conn):
    cuenta = {f[0]: f[1] for f in conn.execute("SELECT plan_id, COUNT(*) FROM mt_prev_equipos WHERE activo = 1 GROUP BY plan_id")}
    salida = []
    for f in conn.execute("SELECT * FROM mt_prev_planes ORDER BY activo DESC, nombre"):
        d = dict(f)
        d["tareas"] = _tareas(conn, f["id"])
        d["equipos"] = cuenta.get(f["id"], 0)
        salida.append(d)
    return salida


def guardar_plan(conn, datos, usuario, plan_id=None):
    nombre = mt._texto(datos.get("nombre"), "el nombre", maximo=120)
    ih = _num(datos.get("intervalo_hs"), "el intervalo en horas", minimo=1)
    ik = _num(datos.get("intervalo_km"), "el intervalo en km", minimo=1)
    idias = _num(datos.get("intervalo_dias"), "el intervalo en días", minimo=1, entero=True)
    if not (ih or ik or idias):
        mt._err("VALIDACION", "Indicá al menos un intervalo: horas, km o días (vence lo que ocurra primero).")
    aviso = _num(datos.get("aviso_pct"), "el aviso", minimo=0)
    aviso = 10.0 if aviso is None else aviso
    if aviso > 50:
        mt._err("VALIDACION", "El aviso no puede superar el 50% del intervalo.")
    tareas = datos.get("tareas") or []
    if isinstance(tareas, str):
        tareas = tareas.splitlines()
    tareas = [mt._texto(t, "la tarea", maximo=300) for t in tareas if str(t or "").strip()][:100]
    otro = conn.execute("SELECT id FROM mt_prev_planes WHERE nombre = ? COLLATE NOCASE AND id <> ?", (nombre, plan_id or 0)).fetchone()
    if otro:
        mt._err("VALIDACION", f"Ya existe un plan llamado «{nombre}».")
    valores = (nombre, mt._texto(datos.get("descripcion"), "la descripción", obligatorio=False, maximo=500) or None,
               ih, ik, idias, aviso, 0 if datos.get("activo") is False else 1, usuario, mt.ahora())
    if plan_id:
        _plan(conn, plan_id)
        conn.execute("""UPDATE mt_prev_planes SET nombre = ?, descripcion = ?, intervalo_hs = ?, intervalo_km = ?,
                        intervalo_dias = ?, aviso_pct = ?, activo = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?""",
                     (*valores, int(plan_id)))
    else:
        plan_id = conn.execute("""INSERT INTO mt_prev_planes (nombre, descripcion, intervalo_hs, intervalo_km, intervalo_dias,
                                  aviso_pct, activo, actualizado_por, actualizado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                               valores).lastrowid
    conn.execute("DELETE FROM mt_prev_tareas WHERE plan_id = ?", (plan_id,))
    conn.executemany("INSERT INTO mt_prev_tareas (plan_id, orden, texto) VALUES (?, ?, ?)",
                     [(plan_id, i, t) for i, t in enumerate(tareas, 1)])
    return {"plan_id": plan_id, "planes": planes(conn)}


# --- asignación a equipos y base (último service) ---
def _asignacion(conn, asignacion_id):
    f = conn.execute("SELECT * FROM mt_prev_equipos WHERE id = ?", (int(asignacion_id),)).fetchone()
    if not f:
        mt._err("NO_EXISTE", "La asignación no existe.")
    return f


def _historial(conn, asignacion_id, fecha, hs, km, origen, usuario, ot_id=None, nota=None):
    conn.execute("""INSERT INTO mt_prev_historial (asignacion_id, fecha, hs, km, origen, ot_id, nota, usuario, creado_en)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (asignacion_id, fecha, hs, km, origen, ot_id, nota, usuario, mt.ahora()))


def _base_desde_datos(conn, ci, datos):
    """base = 'ACTUAL' (lectura del último parte, hoy) o 'MANUAL' (hs / km / fecha del último service)."""
    if str(datos.get("base") or "ACTUAL").upper() == "MANUAL":
        return (_num(datos.get("hs"), "el horómetro del último service"), _num(datos.get("km"), "el km del último service"),
                _fecha(datos.get("fecha"), "la fecha del último service"), "MANUAL")
    lec = _lectura(conn, ci)
    return lec["hs"], lec["km"], mt.hoy().isoformat(), "ALTA"


def asignar_equipos(conn, plan_id, datos, usuario):
    plan = _plan(conn, plan_id)
    cis = datos.get("cis")
    if not isinstance(cis, list) or not [c for c in cis if str(c).strip()]:
        mt._err("VALIDACION", "Elegí al menos un equipo.")
    for ci in dict.fromkeys(str(c).strip() for c in cis if str(c).strip()):
        ci = mt._equipo(conn, ci, solo_activo=True)["codigo"].strip()
        hs, km, fecha, origen = _base_desde_datos(conn, ci, datos)
        conn.execute(
            """INSERT INTO mt_prev_equipos (plan_id, ci, base_hs, base_km, base_fecha, base_origen, activo, actualizado_por, actualizado_en)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT (plan_id, ci) DO UPDATE SET activo = 1, base_hs = excluded.base_hs, base_km = excluded.base_km,
                 base_fecha = excluded.base_fecha, base_origen = excluded.base_origen,
                 actualizado_por = excluded.actualizado_por, actualizado_en = excluded.actualizado_en""",
            (plan["id"], ci, hs, km, fecha, origen, usuario, mt.ahora()))
        asig = conn.execute("SELECT id FROM mt_prev_equipos WHERE plan_id = ? AND ci = ?", (plan["id"], ci)).fetchone()["id"]
        _historial(conn, asig, fecha, hs, km, origen, usuario, nota="Alta en el plan")
    return estado(conn)


def quitar_equipo(conn, asignacion_id, usuario):
    a = _asignacion(conn, asignacion_id)
    if _ot_activa(conn, a["id"]):
        mt._err("VALIDACION", "Tiene una OT preventiva abierta: finalizala o anulala antes de quitar el equipo del plan.")
    conn.execute("UPDATE mt_prev_equipos SET activo = 0, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                 (usuario, mt.ahora(), a["id"]))
    return estado(conn)


def registrar_realizado(conn, asignacion_id, datos, usuario):
    """Service hecho fuera del sistema (taller externo, antes de usar el módulo): mueve la base."""
    a = _asignacion(conn, asignacion_id)
    hs, km, fecha, _ = _base_desde_datos(conn, a["ci"], {**datos, "base": "MANUAL"})
    nota = mt._texto(datos.get("nota"), "la nota", obligatorio=False, maximo=500) or None
    conn.execute("""UPDATE mt_prev_equipos SET base_hs = ?, base_km = ?, base_fecha = ?, base_origen = 'MANUAL',
                    actualizado_por = ?, actualizado_en = ? WHERE id = ?""", (hs, km, fecha, usuario, mt.ahora(), a["id"]))
    _historial(conn, a["id"], fecha, hs, km, "MANUAL", usuario, nota=nota)
    return detalle_asignacion(conn, a["id"])


# --- estado: vence lo que ocurra primero ---
def _ot_activa(conn, asignacion_id):
    return conn.execute(
        f"""SELECT id, numero, estado FROM mt_ordenes WHERE prev_equipo_id = ? AND estado IN ({', '.join('?' for _ in mt.OT_ACTIVAS)})
            ORDER BY id DESC LIMIT 1""", (asignacion_id, *mt.OT_ACTIVAS)).fetchone()


def _calcular(a, plan, lec, hoy_):
    comps = []
    for clave, intervalo, base, actual in (("hs", plan["intervalo_hs"], a["base_hs"], lec.get("hs")),
                                           ("km", plan["intervalo_km"], a["base_km"], lec.get("km"))):
        if not intervalo:
            continue
        if base is None or actual is None:
            comps.append({"clave": clave, "intervalo": intervalo, "usado": None, "restante": None, "actual": actual})
            continue
        usado = round(actual - base, 1)
        comps.append({"clave": clave, "intervalo": intervalo, "usado": usado, "restante": round(intervalo - usado, 1), "actual": actual})
    if plan["intervalo_dias"]:
        usado = (hoy_ - date.fromisoformat(a["base_fecha"][:10])).days
        comps.append({"clave": "dias", "intervalo": plan["intervalo_dias"], "usado": usado,
                      "restante": plan["intervalo_dias"] - usado, "actual": None})
    medibles = [c for c in comps if c["restante"] is not None]
    if any(c["usado"] < 0 for c in medibles):
        est = "REVISAR"
    elif not medibles:
        est = "SIN_LECTURA"
    elif any(c["restante"] <= 0 for c in medibles):
        est = "VENCIDO"
    elif any(c["restante"] <= c["intervalo"] * (plan["aviso_pct"] or 0) / 100 for c in medibles):
        est = "PROXIMO"
    else:
        est = "AL_DIA"
    critico = min(medibles, key=lambda c: c["restante"] / c["intervalo"]) if medibles else None
    return est, comps, critico


def _fila_estado(conn, a, plan, lec, hoy_, vehiculo):
    est, comps, critico = _calcular(a, plan, lec, hoy_)
    ot = _ot_activa(conn, a["id"])
    return {
        "id": a["id"], "ci": a["ci"], "display": mt._equipo_display(vehiculo) if vehiculo else a["ci"],
        "plan_id": plan["id"], "plan": plan["nombre"], "estado": est, "componentes": comps,
        "vence_por": critico["clave"] if critico else None, "restante": critico["restante"] if critico else None,
        "base_hs": a["base_hs"], "base_km": a["base_km"], "base_fecha": a["base_fecha"], "base_origen": a["base_origen"],
        "lectura": lec, "ot": dict(ot) if ot else None,
    }


def estado(conn, ci=""):
    lects = lecturas(conn)
    hoy_ = mt.hoy()
    vehiculos = {f["codigo"].strip().upper(): f for f in conn.execute("SELECT codigo, marca, modelo, tipo FROM vehiculos WHERE TRIM(COALESCE(codigo, '')) <> ''")}
    sql = """SELECT a.*, p.nombre, p.intervalo_hs, p.intervalo_km, p.intervalo_dias, p.aviso_pct, p.id AS pid
             FROM mt_prev_equipos a JOIN mt_prev_planes p ON p.id = a.plan_id WHERE a.activo = 1 AND p.activo = 1"""
    args = []
    if ci:
        sql += " AND UPPER(a.ci) = UPPER(?)"
        args.append(ci)
    filas = []
    for f in conn.execute(sql, args).fetchall():
        plan = {"id": f["pid"], "nombre": f["nombre"], "intervalo_hs": f["intervalo_hs"], "intervalo_km": f["intervalo_km"],
                "intervalo_dias": f["intervalo_dias"], "aviso_pct": f["aviso_pct"]}
        filas.append(_fila_estado(conn, f, plan, lects.get(f["ci"].upper(), {}), hoy_, vehiculos.get(f["ci"].upper())))
    orden = {e: i for i, e in enumerate(ESTADOS)}
    filas.sort(key=lambda r: (orden[r["estado"]], (r["restante"] / next(c["intervalo"] for c in r["componentes"] if c["clave"] == r["vence_por"]))
                              if r["vence_por"] else 9, r["ci"]))
    return filas


def detalle_asignacion(conn, asignacion_id):
    a = _asignacion(conn, asignacion_id)
    fila = next((r for r in estado(conn, a["ci"]) if r["id"] == a["id"]), None)
    if not fila:
        mt._err("NO_EXISTE", "El equipo ya no está en ese plan.")
    fila["tareas"] = _tareas(conn, a["plan_id"])
    fila["historial"] = [dict(h) for h in conn.execute(
        """SELECT h.*, o.numero AS ot_numero FROM mt_prev_historial h LEFT JOIN mt_ordenes o ON o.id = h.ot_id
           WHERE h.asignacion_id = ? ORDER BY h.id DESC LIMIT 100""", (a["id"],))]
    fila["observadas"] = lecturas_observadas(conn, True, a["ci"])
    return fila


def resumen(conn):
    filas = estado(conn)
    return {"vencidos": sum(1 for r in filas if r["estado"] == "VENCIDO"),
            "proximos": sum(1 for r in filas if r["estado"] == "PROXIMO"),
            "lecturas_a_revisar": conn.execute("SELECT COUNT(*) FROM mt_lecturas_observadas WHERE revisada_en IS NULL").fetchone()[0]}


# --- OT preventiva ---
def crear_ot(conn, asignacion_id, datos, usuario):
    a = _asignacion(conn, asignacion_id)
    if not a["activo"]:
        mt._err("VALIDACION", "El equipo ya no está en ese plan.")
    abierta = _ot_activa(conn, a["id"])
    if abierta:
        mt._err("VALIDACION", f"Ya tiene la {abierta['numero']} abierta para este preventivo.")
    plan = _plan(conn, a["plan_id"])
    tareas = _tareas(conn, plan["id"])
    lec = _lectura(conn, a["ci"])
    descripcion = " · ".join(tareas)[:1900] or plan["descripcion"] or ""
    ot = mt.crear_orden(conn, {"ci": a["ci"], "titulo": f"Preventivo: {plan['nombre']}", "descripcion": descripcion,
                               "prioridad": datos.get("prioridad") or "NORMAL", "mecanicos": datos.get("mecanicos") or []}, usuario)
    conn.execute("""UPDATE mt_ordenes SET tipo = 'PREVENTIVA', prev_equipo_id = ?, lectura_hs = ?, lectura_km = ?, tareas_json = ?
                    WHERE id = ?""", (a["id"], lec["hs"], lec["km"], json.dumps(tareas, ensure_ascii=False), ot["id"]))
    _sembrar_tareas(conn, ot["id"], tareas)
    return mt.detalle_orden(conn, ot["id"])


def al_finalizar_ot(conn, f, datos, usuario):
    """Llamado por mt.finalizar_orden: el service hecho pasa a ser la nueva base del plan."""
    d = dict(f)
    if d.get("tipo") != "PREVENTIVA" or not d.get("prev_equipo_id"):
        return
    hs = _num(datos.get("lectura_hs"), "el horómetro del service")
    km = _num(datos.get("lectura_km"), "el km del service")
    hs = d.get("lectura_hs") if hs is None else hs
    km = d.get("lectura_km") if km is None else km
    fecha = mt.hoy().isoformat()
    conn.execute("""UPDATE mt_ordenes SET lectura_hs = ?, lectura_km = ? WHERE id = ?""", (hs, km, d["id"]))
    conn.execute("""UPDATE mt_prev_equipos SET base_hs = ?, base_km = ?, base_fecha = ?, base_origen = ?,
                    actualizado_por = ?, actualizado_en = ? WHERE id = ?""",
                 (hs, km, fecha, d["numero"], usuario, mt.ahora(), d["prev_equipo_id"]))
    _historial(conn, d["prev_equipo_id"], fecha, hs, km, d["numero"], usuario, ot_id=d["id"])


# ============================================================================
# Repuestos de la OT
# ============================================================================
ORIGENES = ("ALMACEN", "COMPRA", "OTRO")


def repuestos(conn, ot_id):
    return [dict(f) for f in conn.execute("SELECT * FROM mt_ot_repuestos WHERE ot_id = ? ORDER BY id", (ot_id,))]


def costo_repuestos(conn, ot_id):
    fila = conn.execute("SELECT SUM(cantidad * costo_unitario), SUM(costo_unitario IS NULL) FROM mt_ot_repuestos WHERE ot_id = ?",
                        (ot_id,)).fetchone()
    return round(fila[0] or 0, 2), int(fila[1] or 0)


def _id_opcional(valor, campo):
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        mt._err("VALIDACION", f"{campo} inválido.")


def agregar_repuesto(conn, ot_id, datos, usuario):
    f = mt._orden(conn, ot_id)
    if f["estado"] in ("FINALIZADA", "ANULADA"):
        mt._err("VALIDACION", "La OT ya está cerrada.")
    origen = str(datos.get("origen") or "OTRO").upper()
    if origen not in ORIGENES:
        mt._err("VALIDACION", "Origen inválido.")
    descripcion = mt._texto(datos.get("descripcion"), "la descripción del repuesto", maximo=300)
    cantidad = _num(datos.get("cantidad"), "la cantidad", obligatorio=True)
    if cantidad <= 0:
        mt._err("VALIDACION", "La cantidad tiene que ser mayor a cero.")
    costo = _num(datos.get("costo_unitario"), "el costo unitario")
    producto_id = _id_opcional(datos.get("producto_id"), "Artículo")
    if producto_id and not conn.execute("SELECT 1 FROM productos WHERE id = ?", (producto_id,)).fetchone():
        mt._err("NO_EXISTE", "El artículo no existe en Almacén.")
    conn.execute(
        """INSERT INTO mt_ot_repuestos (ot_id, origen, producto_id, solicitud_detalle_id, remito_detalle_id, codigo, descripcion,
           cantidad, unidad, costo_unitario, referencia, cargado_por, cargado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f["id"], origen, producto_id, _id_opcional(datos.get("solicitud_detalle_id"), "Solicitud"),
         _id_opcional(datos.get("remito_detalle_id"), "Remito"),
         mt._texto(datos.get("codigo"), "el código", obligatorio=False, maximo=60) or None, descripcion, cantidad,
         mt._texto(datos.get("unidad"), "la unidad", obligatorio=False, maximo=30) or None, costo,
         mt._texto(datos.get("referencia"), "la referencia", obligatorio=False, maximo=120) or None, usuario, mt.ahora()))
    mt._evento(conn, f["id"], "REPUESTO", f"{cantidad:g} × {descripcion}", usuario)
    return mt.detalle_orden(conn, f["id"])


def quitar_repuesto(conn, ot_id, repuesto_id, usuario):
    f = mt._orden(conn, ot_id)
    if f["estado"] in ("FINALIZADA", "ANULADA"):
        mt._err("VALIDACION", "La OT ya está cerrada.")
    r = conn.execute("SELECT * FROM mt_ot_repuestos WHERE id = ? AND ot_id = ?", (int(repuesto_id), f["id"])).fetchone()
    if not r:
        mt._err("NO_EXISTE", "El repuesto no está en esta OT.")
    if r["remito_detalle_id"] and r["referencia"] and r["referencia"].startswith("Pedido a Almacén"):
        rem = conn.execute("""SELECT e.numero, e.estado_autorizacion FROM remitos_entrega_detalle d JOIN remitos_entrega e ON e.id = d.remito_id
                              WHERE d.id = ?""", (r["remito_detalle_id"],)).fetchone()
        if rem and str(rem["estado_autorizacion"] or "").upper() != "NO_AUTORIZADO":
            mt._err("VALIDACION", f"Está pedido a Almacén en {rem['numero']}: para quitarlo, Almacén tiene que rechazar ese remito.")
    conn.execute("DELETE FROM mt_ot_repuestos WHERE id = ?", (r["id"],))
    mt._evento(conn, f["id"], "REPUESTO", f"Quitado: {r['cantidad']:g} × {r['descripcion']}", usuario)
    return mt.detalle_orden(conn, f["id"])


def _consulta_segura(conn, sql, args):
    try:
        return [dict(f) for f in conn.execute(sql, args)]
    except sqlite3.OperationalError:  # instalaciones sin Almacén / Compras
        return []


def sugerencias_repuestos(conn, ot_id, q=""):
    """Entregas de Almacén y solicitudes de Compras de este equipo, y búsqueda en el catálogo de artículos."""
    f = mt._orden(conn, ot_id)
    ci = f["ci"] or ""
    usados = conn.execute("SELECT remito_detalle_id, solicitud_detalle_id FROM mt_ot_repuestos").fetchall()
    rem_usados = {u[0] for u in usados if u[0]}
    sol_usados = {u[1] for u in usados if u[1]}
    entregas = _consulta_segura(conn, """
        SELECT d.id AS remito_detalle_id, d.producto_id, d.cantidad, p.codigo, p.descripcion, e.numero AS remito, e.fecha
        FROM remitos_entrega_detalle d JOIN remitos_entrega e ON e.id = d.remito_id LEFT JOIN productos p ON p.id = d.producto_id
        WHERE ? <> '' AND UPPER(TRIM(COALESCE(d.vehiculo_codigo, ''))) = UPPER(?) ORDER BY e.fecha DESC, d.id DESC LIMIT 30""", (ci, ci))
    compras = _consulta_segura(conn, """
        SELECT d.id AS solicitud_detalle_id, d.descripcion_articulo AS descripcion, d.cantidad, d.unidad_medida AS unidad,
               d.estado_aprobacion, s.numero_solicitud, s.fecha_solicitud,
               (SELECT od.precio_unitario FROM OrdenCompraDetalleSolicitud x JOIN OrdenCompraDetalle od ON od.id = x.orden_compra_detalle_id
                 WHERE x.solicitud_compra_detalle_id = d.id ORDER BY x.id DESC LIMIT 1) AS precio_unitario,
               (SELECT oc.numero_oc FROM OrdenCompraDetalleSolicitud x JOIN OrdenCompraDetalle od ON od.id = x.orden_compra_detalle_id
                 JOIN OrdenCompra oc ON oc.id = od.orden_compra_id WHERE x.solicitud_compra_detalle_id = d.id ORDER BY x.id DESC LIMIT 1) AS numero_oc
        FROM solicitud_compra_detalle d JOIN solicitud_compra s ON s.id = d.id_solicitud
        WHERE ? <> '' AND (UPPER(TRIM(COALESCE(d.id_vehiculo, ''))) = UPPER(?) OR UPPER(TRIM(COALESCE(d.id_maquinaria, ''))) = UPPER(?))
        ORDER BY s.fecha_solicitud DESC, d.id DESC LIMIT 30""", (ci, ci, ci))
    q = str(q or "").strip()
    articulos = _consulta_segura(conn, """SELECT id AS producto_id, codigo, descripcion, tipo_control FROM productos
        WHERE ? <> '' AND (codigo LIKE ? OR descripcion LIKE ?) ORDER BY descripcion LIMIT 20""", (q, f"%{q}%", f"%{q}%"))
    for a in articulos:
        a["disponible"] = stock_disponible(conn, a["producto_id"])
        a["serializado"] = str(a.get("tipo_control") or "").upper() == "SERIALIZADO"
    for e in entregas:
        e["vinculado"] = e["remito_detalle_id"] in rem_usados
    for c in compras:
        c["vinculado"] = c["solicitud_detalle_id"] in sol_usados
    return {"entregas": entregas, "compras": compras, "articulos": articulos}


# ============================================================================
# Tareas de la OT preventiva: el mecánico las tilda una por una (no bloquea la corrección final)
# ============================================================================
def _sembrar_tareas(conn, ot_id, tareas):
    conn.executemany("INSERT INTO mt_ot_tareas (ot_id, orden, texto, hecha) VALUES (?, ?, ?, 0)",
                     [(ot_id, i, t) for i, t in enumerate(tareas, 1)])


def tareas_ot(conn, ot_id):
    filas = [dict(f) for f in conn.execute(
        "SELECT orden, texto, hecha, hecha_por, hecha_en FROM mt_ot_tareas WHERE ot_id = ? ORDER BY orden", (ot_id,))]
    if filas:
        return filas
    f = conn.execute("SELECT tareas_json FROM mt_ordenes WHERE id = ?", (ot_id,)).fetchone()
    tareas = json.loads(f["tareas_json"]) if f and f["tareas_json"] else []
    return [{"orden": i, "texto": t, "hecha": 0, "hecha_por": None, "hecha_en": None} for i, t in enumerate(tareas, 1)]


def marcar_tarea(conn, ot_id, orden, hecha, usuario, legajo=None):
    """legajo: si viene de la app, la OT tiene que ser del mecánico y estar abierta para él."""
    f = mt._orden_del_mecanico(conn, ot_id, legajo) if legajo else mt._orden(conn, ot_id)
    if f["estado"] in ("FINALIZADA", "ANULADA"):
        mt._err("VALIDACION", "La OT ya está cerrada.")
    if not conn.execute("SELECT 1 FROM mt_ot_tareas WHERE ot_id = ?", (f["id"],)).fetchone():
        _sembrar_tareas(conn, f["id"], [t["texto"] for t in tareas_ot(conn, f["id"])])
    t = conn.execute("SELECT * FROM mt_ot_tareas WHERE ot_id = ? AND orden = ?", (f["id"], int(orden))).fetchone()
    if not t:
        mt._err("NO_EXISTE", "La tarea no existe en esta OT.")
    hecha = bool(hecha)
    conn.execute("UPDATE mt_ot_tareas SET hecha = ?, hecha_por = ?, hecha_en = ? WHERE id = ?",
                 (1 if hecha else 0, usuario if hecha else None, mt.ahora() if hecha else None, t["id"]))
    return tareas_ot(conn, f["id"])


def avance_tareas(conn, ot_id):
    filas = tareas_ot(conn, ot_id)
    return sum(1 for t in filas if t["hecha"]), len(filas)


# ============================================================================
# Aviso por mail: un equipo pasa a «Próximo» o a «Vencido» (una vez por estado; al quedar al día se rearma)
# ============================================================================
def marcar_avisos(conn, ci=""):
    nuevos = []
    for r in estado(conn, ci):
        a = conn.execute("SELECT aviso_estado FROM mt_prev_equipos WHERE id = ?", (r["id"],)).fetchone()
        if r["estado"] in ("PROXIMO", "VENCIDO"):
            if a["aviso_estado"] != r["estado"]:
                conn.execute("UPDATE mt_prev_equipos SET aviso_estado = ?, aviso_en = ? WHERE id = ?", (r["estado"], mt.ahora(), r["id"]))
                nuevos.append(r)
        elif r["estado"] == "AL_DIA" and a["aviso_estado"]:
            conn.execute("UPDATE mt_prev_equipos SET aviso_estado = NULL, aviso_en = NULL WHERE id = ?", (r["id"],))
    return nuevos


def texto_aviso(filas):
    unidades = {"hs": "h", "km": "km", "dias": "días"}
    lineas = []
    for r in filas:
        critico = next((c for c in r["componentes"] if c["clave"] == r["vence_por"]), None)
        detalle = ""
        if critico and critico["restante"] is not None:
            u = unidades[critico["clave"]]
            detalle = f"pasado {-critico['restante']:g} {u}" if critico["restante"] <= 0 else f"faltan {critico['restante']:g} {u}"
        estado_txt = "VENCIDO" if r["estado"] == "VENCIDO" else "Próximo"
        linea = f"- {r['display']} · {r['plan']} · {estado_txt}"
        if detalle:
            linea += f" ({detalle})"
        if r["ot"]:
            linea += f" · {r['ot']['numero']} abierta"
        lineas.append(linea)
    return "\n".join(lineas)


# ============================================================================
# Pedido a Almacén desde la OT: genera un remito de entrega PENDIENTE (circuito normal de Almacén).
# El stock queda reservado y se descuenta cuando Almacén lo autoriza.
# ============================================================================
def stock_disponible(conn, producto_id):
    try:
        actual = conn.execute("SELECT COALESCE(stock_actual, 0) FROM stock WHERE producto_id = ?", (producto_id,)).fetchone()
        reservado = conn.execute(
            """SELECT COALESCE(SUM(d.cantidad), 0) FROM remitos_entrega_detalle d JOIN remitos_entrega r ON r.id = d.remito_id
               WHERE d.producto_id = ? AND UPPER(COALESCE(r.estado_autorizacion, 'AUTORIZADO')) = 'PENDIENTE'""", (producto_id,)).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    return round(float(actual[0] if actual else 0) - float(reservado[0] or 0), 2)


def preparar_pedido_almacen(conn, ot_id, datos):
    """Valida y arma el remito de entrega (lo graba main.crear_remito_entrega, con su número, PDF y aviso a Almacén)."""
    f = mt._orden(conn, ot_id)
    if f["estado"] in ("FINALIZADA", "ANULADA"):
        mt._err("VALIDACION", "La OT ya está cerrada.")
    if not f["ci"]:
        mt._err("VALIDACION", "Solo se piden repuestos a Almacén para una OT de equipo.")
    producto_id = _id_opcional(datos.get("producto_id"), "Artículo")
    if not producto_id:
        mt._err("VALIDACION", "Elegí el artículo de Almacén.")
    p = conn.execute("SELECT id, codigo, descripcion, tipo_control FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if not p:
        mt._err("NO_EXISTE", "El artículo no existe en Almacén.")
    cantidad = _num(datos.get("cantidad"), "la cantidad", obligatorio=True)
    if cantidad <= 0:
        mt._err("VALIDACION", "La cantidad tiene que ser mayor a cero.")
    series = [str(s).strip() for s in (datos.get("series") or []) if str(s).strip()]
    if str(p["tipo_control"] or "").upper() == "SERIALIZADO" and (cantidad != int(cantidad) or len(series) != int(cantidad)):
        mt._err("VALIDACION", f"{p['codigo']} es serializado: cargá un N.º de serie por unidad ({cantidad:g}).")
    disponible = stock_disponible(conn, producto_id)
    if disponible < cantidad:
        mt._err("VALIDACION", f"Stock insuficiente de {p['codigo']}: disponible {disponible:g}, pedido {cantidad:g}.")
    mec = conn.execute("SELECT legajo, nombre FROM mt_ot_mecanicos WHERE ot_id = ? ORDER BY nombre LIMIT 1", (f["id"],)).fetchone()
    return {
        "destinatario": f"Mantenimiento · {f['numero']}",
        "recibe_legajo": mec["legajo"] if mec else None, "recibe_nombre": mec["nombre"] if mec else "",
        "observaciones": f"{f['numero']} · {f['titulo']}"[:500],
        "items": [{"producto_id": producto_id, "cantidad": cantidad, "series": series, "vehiculo_codigo": f["ci"]}],
    }


def registrar_pedido_almacen(conn, ot_id, remito_id, numero, datos, usuario):
    f = mt._orden(conn, ot_id)
    producto_id = int(datos["producto_id"])
    det = conn.execute("SELECT id, cantidad FROM remitos_entrega_detalle WHERE remito_id = ? AND producto_id = ? ORDER BY id LIMIT 1",
                       (remito_id, producto_id)).fetchone()
    p = conn.execute("SELECT codigo, descripcion FROM productos WHERE id = ?", (producto_id,)).fetchone()
    conn.execute(
        """INSERT INTO mt_ot_repuestos (ot_id, origen, producto_id, remito_detalle_id, codigo, descripcion, cantidad, unidad,
           costo_unitario, referencia, cargado_por, cargado_en) VALUES (?, 'ALMACEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f["id"], producto_id, det["id"] if det else None, p["codigo"], p["descripcion"], float(det["cantidad"] if det else datos["cantidad"]),
         mt._texto(datos.get("unidad"), "la unidad", obligatorio=False, maximo=30) or None,
         _num(datos.get("costo_unitario"), "el costo unitario"), f"Pedido a Almacén {numero}", usuario, mt.ahora()))
    mt._evento(conn, f["id"], "REPUESTO", f"Pedido a Almacén {numero}: {det['cantidad'] if det else datos['cantidad']:g} × {p['descripcion']}", usuario)
    return mt.detalle_orden(conn, f["id"])
