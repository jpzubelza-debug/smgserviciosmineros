"""Actas de Medición de Operaciones (versión simple).

Un acta junta los partes diarios (tabla horas_maquinas) de los equipos elegidos en un período y los vincula
a una orden de compra del cliente para certificar:

    acta (am_mediciones): OC del cliente + período + columnas del parte que se muestran + columna que es la cantidad
      -> equipos (am_medicion_equipos): CI + ítem de la OC donde se certifica (+ columna de cantidad propia, opcional)
         + centro de costos (opcional): solo cuentan los partes de ese CI cargados en ese centro de costos
      -> KPI (am_medicion_kpi): mínimo y objetivo por ítem de la OC + gráficos elegidos y su orden

El mínimo y el objetivo son referencias de gestión: marcan el cumplimiento de cada ítem, no cambian lo que se certifica.

El centro de costos del parte sale de Obra/Proyecto ("209 - MANTENIMIENTO DE CAMINO SSR"); los partes históricos con texto
libre no tienen centro y solo cuentan en equipos sin centro de costos elegido.

La cantidad de cada equipo es la suma de su columna de cantidad en los partes del período; la de cada ítem de la OC
es la suma de sus equipos. Un acta nueva copia equipos, ítems y columnas de la última acta de la misma OC.

Estados: BORRADOR (se calcula en vivo) -> CERRADA (guarda el resultado; se puede precargar el certificado)
-> ANULADA. Una cerrada sin certificado vigente puede reabrirse.

Las funciones reciben una conexión SQLite abierta y no hacen commit: main.py las envuelve en BEGIN IMMEDIATE.
"""
import calendar
import io
import json
import math
import re
from datetime import date, datetime, timedelta

MODULO = "operaciones"
PANEL = "actas_medicion"
ACCIONES = ("consultar", "generar", "aprobar", "anular")
TABLA_PARTES = "horas_maquinas"
CAMPO_EQUIPO = "ci"
CAMPO_FECHA = "fecha_parte"
CAMPO_CANTIDAD_DEFECTO = "total_hs"
COLUMNAS_DEFECTO = ["fecha_parte", "nro_parte", "maquinista", "descripcion_trabajos", "hs_inicio", "hs_final", "total_hs"]
OCULTAS = {"id", CAMPO_EQUIPO}  # el CI ya es la fila del equipo; id es interno


class AmError(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje


def _err(codigo, mensaje):
    raise AmError(codigo, mensaje)


# ============================================================================
# Estructura de base (solo agrega; las tablas am_* de la versión anterior quedan sin uso)
# ============================================================================
DDL = (
    """
    CREATE TABLE IF NOT EXISTS am_mediciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seguimiento_cc_id INTEGER NOT NULL,
        orden_compra_cliente_id INTEGER NOT NULL,
        numero INTEGER NOT NULL,
        periodo_desde TEXT NOT NULL,
        periodo_hasta TEXT NOT NULL,
        fecha TEXT NOT NULL,
        campo_cantidad TEXT NOT NULL,
        columnas_json TEXT NOT NULL DEFAULT '[]',
        observaciones TEXT,
        estado TEXT NOT NULL DEFAULT 'BORRADOR' CHECK (estado IN ('BORRADOR', 'CERRADA', 'ANULADA')),
        resultado_json TEXT,
        creado_por TEXT NOT NULL, creado_en TEXT NOT NULL,
        actualizado_por TEXT, actualizado_en TEXT,
        cerrada_por TEXT, cerrada_en TEXT,
        anulada_por TEXT, anulada_en TEXT, motivo_anulacion TEXT,
        CHECK (periodo_desde <= periodo_hasta),
        CHECK (estado <> 'CERRADA' OR resultado_json IS NOT NULL),
        FOREIGN KEY (seguimiento_cc_id) REFERENCES seguimiento_cc (id) ON DELETE RESTRICT,
        FOREIGN KEY (orden_compra_cliente_id) REFERENCES Orden_de_compra_cliente (id) ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_am_mediciones_oc ON am_mediciones (orden_compra_cliente_id, numero)",
    """
    CREATE TABLE IF NOT EXISTS am_medicion_equipos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicion_id INTEGER NOT NULL,
        ci TEXT NOT NULL,
        item_oc_cliente_id INTEGER,
        campo_cantidad TEXT,
        centro_costo_id INTEGER,
        orden INTEGER NOT NULL DEFAULT 0,
        UNIQUE (medicion_id, ci),
        FOREIGN KEY (medicion_id) REFERENCES am_mediciones (id) ON DELETE CASCADE,
        FOREIGN KEY (item_oc_cliente_id) REFERENCES items_oc_cliente (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_medicion_kpi (
        medicion_id INTEGER PRIMARY KEY,
        metas_json TEXT NOT NULL DEFAULT '[]',
        graficos_json TEXT,
        FOREIGN KEY (medicion_id) REFERENCES am_mediciones (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_medicion_certificados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicion_id INTEGER NOT NULL,
        certificado_id INTEGER NOT NULL UNIQUE,
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        FOREIGN KEY (medicion_id) REFERENCES am_mediciones (id) ON DELETE RESTRICT,
        FOREIGN KEY (certificado_id) REFERENCES certificados_cliente (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidad TEXT NOT NULL,
        entidad_id INTEGER,
        evento TEXT NOT NULL,
        usuario TEXT NOT NULL,
        fecha TEXT NOT NULL,
        detalle_json TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_am_eventos ON am_eventos (entidad, entidad_id)",
    """
    CREATE TRIGGER IF NOT EXISTS trg_am_medicion_cerrada
    BEFORE UPDATE ON am_mediciones WHEN OLD.estado = 'CERRADA' AND NEW.estado = 'CERRADA' AND (
        NEW.resultado_json IS NOT OLD.resultado_json OR NEW.periodo_desde IS NOT OLD.periodo_desde
        OR NEW.periodo_hasta IS NOT OLD.periodo_hasta OR NEW.orden_compra_cliente_id IS NOT OLD.orden_compra_cliente_id
        OR NEW.numero IS NOT OLD.numero OR NEW.campo_cantidad IS NOT OLD.campo_cantidad)
    BEGIN SELECT RAISE(ABORT, 'AM_INMUTABLE: un acta cerrada no se modifica; reábrala'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_am_medicion_anulada
    BEFORE UPDATE ON am_mediciones WHEN OLD.estado = 'ANULADA'
    BEGIN SELECT RAISE(ABORT, 'AM_INMUTABLE: un acta anulada no se modifica'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_am_eventos_sin_update
    BEFORE UPDATE ON am_eventos
    BEGIN SELECT RAISE(ABORT, 'AM_HISTORIAL: el historial no puede modificarse'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_am_eventos_sin_delete
    BEFORE DELETE ON am_eventos
    BEGIN SELECT RAISE(ABORT, 'AM_HISTORIAL: el historial no puede eliminarse'); END
    """,
)


def migrar(conn):
    """Solo agrega tablas, índices y triggers. Idempotente."""
    for sentencia in DDL:
        trigger = re.search(r"CREATE TRIGGER IF NOT EXISTS (\w+)", sentencia)
        if trigger:
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger.group(1)}")
        conn.execute(sentencia)
    cols = {f[1] for f in conn.execute("PRAGMA table_info(am_medicion_equipos)").fetchall()}
    if "centro_costo_id" not in cols:
        conn.execute("ALTER TABLE am_medicion_equipos ADD COLUMN centro_costo_id INTEGER")


# ============================================================================
# Utilidades
# ============================================================================
def _ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hoy():
    return date.today().isoformat()


def _filas(conn, sql, params=()):
    return [dict(f) for f in conn.execute(sql, params).fetchall()]


def _fila(conn, sql, params=()):
    f = conn.execute(sql, params).fetchone()
    return dict(f) if f else None


def _dump(valor):
    return json.dumps(valor, ensure_ascii=False, default=str)


def _texto(valor):
    return "" if valor is None else str(valor).strip()


def num(valor):
    """Convierte texto o número a float; None si no es numérico. Acepta coma decimal y separador de miles."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return None if (isinstance(valor, float) and math.isnan(valor)) else float(valor)
    s = str(valor).strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _r(valor, decimales=4):
    v = round(float(valor or 0), decimales)
    return 0.0 if v == 0 else v


_FORMATOS_FECHA = ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y")


def parse_fecha(valor):
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    s = str(valor).strip()
    if not s:
        return None
    s = s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else s.split(" ")[0]
    for fmt in _FORMATOS_FECHA:
        try:
            d = datetime.strptime(s, fmt).date()
            if 1990 <= d.year <= 2100:
                return d
        except ValueError:
            continue
    return None


def _iso(valor, campo):
    d = parse_fecha(valor)
    if d is None:
        _err("DATOS", f"{campo}: fecha inválida")
    return d.isoformat()


def _fecha_txt(iso):
    d = parse_fecha(iso)
    return d.strftime("%d/%m/%Y") if d else ""


def _sumar_meses(d, meses):
    m = d.month - 1 + meses
    anio, mes = d.year + m // 12, m % 12 + 1
    return date(anio, mes, min(d.day, calendar.monthrange(anio, mes)[1]))


def evento(conn, entidad, entidad_id, nombre, usuario, detalle=None):
    conn.execute(
        "INSERT INTO am_eventos (entidad, entidad_id, evento, usuario, fecha, detalle_json) VALUES (?, ?, ?, ?, ?, ?)",
        (entidad, entidad_id, nombre, usuario or "sistema", _ahora(), _dump(detalle) if detalle is not None else None),
    )


# ============================================================================
# Columnas del parte diario (se leen de la tabla: una columna nueva aparece sola)
# ============================================================================
def _etiqueta(campo):
    especiales = {"ci": "CI", "nro_parte": "N° parte", "hs_inicio": "Hs inicio", "hs_final": "Hs final", "total_hs": "Total hs",
                  "km_inicio": "Km inicio", "km_final": "Km final", "km_total": "Km total", "fecha_parte": "Fecha",
                  "descripcion_trabajos": "Descripción de trabajos", "legajo_empleado": "Legajo"}
    return especiales.get(campo) or campo.replace("_", " ").capitalize()


def campos_parte(conn):
    """Columnas de horas_maquinas: nombre, etiqueta y si son numéricas (por tipo o por muestra de datos)."""
    salida = []
    for f in conn.execute(f"PRAGMA table_info({TABLA_PARTES})").fetchall():
        nombre, tipo = f[1], (f[2] or "").upper()
        if nombre in OCULTAS:
            continue
        if nombre.startswith("fecha") or nombre.endswith("_at"):
            numerico = False
        elif tipo in ("REAL", "INTEGER", "NUMERIC", "FLOAT", "DOUBLE"):
            numerico = True
        else:
            muestra = [x[0] for x in conn.execute(
                f'SELECT "{nombre}" FROM {TABLA_PARTES} WHERE TRIM(COALESCE("{nombre}", "")) <> "" ORDER BY id DESC LIMIT 200'
            ).fetchall()]
            numerico = bool(muestra) and sum(1 for v in muestra if num(v) is not None) >= 0.8 * len(muestra)
        salida.append({"campo": nombre, "etiqueta": _etiqueta(nombre), "numerico": numerico})
    return salida


def _campos_validos(conn):
    return {c["campo"]: c for c in campos_parte(conn)}


# ============================================================================
# Lectura de partes
# ============================================================================
_RE_CC = re.compile(r"^\s*(\d+)\s*-")


def _centros(conn):
    return {f["id"]: _texto(f["nombre"]) for f in _filas(conn, "SELECT id, nombre FROM centros_costos")}


def _cc_txt(centros, cc_id):
    return f"{cc_id} - {centros.get(cc_id, '')}".strip(" -") if cc_id is not None else ""


def _cc_de_parte(p, centros):
    """Código del centro de costos del parte: centro_costo_id o el número al inicio de Obra/Proyecto ("209 - ...")."""
    cc = p.get("centro_costo_id")
    if cc not in (None, ""):
        try:
            return int(cc)
        except (TypeError, ValueError):
            pass
    for campo in ("obra", "proyecto"):
        m = _RE_CC.match(_texto(p.get(campo)))
        if m and int(m.group(1)) in centros:
            return int(m.group(1))
    return None


def _partes_periodo(conn, desde, hasta, cis=None):
    """Partes con fecha dentro del período (acepta fechas ISO o dd/mm/aaaa). cis: lista de CI o None (todos)."""
    d0, d1 = parse_fecha(desde), parse_fecha(hasta)
    sql = (f'SELECT * FROM {TABLA_PARTES} WHERE TRIM(COALESCE({CAMPO_EQUIPO}, "")) <> "" '
           f"AND (({CAMPO_FECHA} >= ? AND {CAMPO_FECHA} <= ?) OR {CAMPO_FECHA} NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*')")
    params = [d0.isoformat(), d1.isoformat() + "~"]
    filas = []
    if cis is None:
        filas = _filas(conn, sql, params)
    else:
        cis = sorted({c.upper() for c in cis})
        for i in range(0, len(cis), 400):
            lote = cis[i:i + 400]
            filas += _filas(conn, sql + f' AND UPPER(TRIM({CAMPO_EQUIPO})) IN ({",".join("?" for _ in lote)})', params + lote)
    salida, centros = [], _centros(conn)
    for f in filas:
        d = parse_fecha(f.get(CAMPO_FECHA))
        if d and d0 <= d <= d1:
            f["_fecha"] = d.isoformat()
            f["_ci"] = _texto(f.get(CAMPO_EQUIPO)).upper()
            f["_cc"] = _cc_de_parte(f, centros)
            salida.append(f)
    salida.sort(key=lambda p: (p["_fecha"], _texto(p.get("turno")), p["id"]))
    return salida


def _descripcion_equipos(conn):
    return {_texto(v["codigo"]).upper(): " ".join(x for x in (_texto(v["tipo"]), _texto(v["marca"]), _texto(v["modelo"])) if x)
            for v in _filas(conn, "SELECT codigo, tipo, marca, modelo FROM vehiculos WHERE TRIM(COALESCE(codigo, '')) <> ''")}


def equipos_con_partes(conn, desde, hasta):
    """CI con partes en el período, una fila por CI y centro de costos, para elegir los equipos del acta."""
    desde, hasta = _iso(desde, "desde"), _iso(hasta, "hasta")
    conteo, centros = {}, _centros(conn)
    for p in _partes_periodo(conn, desde, hasta):
        c = conteo.setdefault((p["_ci"], p["_cc"]), {"ci": p["_ci"], "centro_costo_id": p["_cc"], "centro_costo": _cc_txt(centros, p["_cc"]),
                                                     "partes": 0, "clientes": set(), "total_hs": 0.0})
        c["partes"] += 1
        c["total_hs"] += num(p.get(CAMPO_CANTIDAD_DEFECTO)) or 0
        if _texto(p.get("cliente")):
            c["clientes"].add(_texto(p.get("cliente")))
    desc = _descripcion_equipos(conn)
    return [{**c, "descripcion": desc.get(c["ci"], ""), "clientes": sorted(c["clientes"])[:3], "total_hs": _r(c["total_hs"], 2)}
            for c in sorted(conteo.values(), key=lambda x: (x["ci"], x["centro_costo_id"] is None, x["centro_costo_id"] or 0))]


# ============================================================================
# Cálculo
# ============================================================================
def _items_oc(conn, oc_id):
    return {i["id"]: i for i in _filas(conn, """SELECT i.id, i.numero, i.descripcion, i.unidad, i.cantidad, i.precio,
                                                      COALESCE(NULLIF(i.moneda, ''), o.moneda) AS moneda
                                               FROM items_oc_cliente i JOIN Orden_de_compra_cliente o ON o.id = i.orden_compra_cliente_id
                                               WHERE i.orden_compra_cliente_id = ? ORDER BY i.numero""", (oc_id,))}


# Columnas que se suman en el TOTAL del detalle de partes (N° de parte, hs/km de inicio y final no se suman).
SUMABLES = ("total_hs", "km_total", "gasoil", "aceite", "viajes", "hs_desplazamiento", "hs_mantenimiento",
            "hs_reparaciones", "hs_disposicion", "total_hs_control", "no_programadas")

# Indicadores del acta: sumas por equipo y generales, siempre (no dependen de las columnas elegidas).
# Las horas efectivas y el combustible se muestran siempre; el resto solo si tiene valores en el período.
INDICADORES = [("total_hs", "Horas efectivas"), ("gasoil", "Combustible (L)"), ("aceite", "Aceite (L)"),
               ("km_total", "Km recorridos"), ("viajes", "Viajes"), ("hs_disposicion", "Hs a disposición"),
               ("hs_mantenimiento", "Hs mantenimiento"), ("hs_reparaciones", "Hs reparaciones")]
INDICADORES_FIJOS = ("total_hs", "gasoil")


def _indicadores(campos, por_equipo):
    """por_equipo: [(ci, descripcion, partes)] → {columnas, equipos:[{ci, descripcion, dias, partes, valores, consumo_lh}], general}."""
    disponibles = [(c, t) for c, t in INDICADORES if c in campos]
    filas, general = [], {c: 0.0 for c, _ in disponibles}
    for ci, descripcion, partes in por_equipo:
        valores = {c: 0.0 for c, _ in disponibles}
        for p in partes:
            for c in valores:
                valores[c] += num(p.get(c)) or 0.0
        for c in valores:
            general[c] += valores[c]
        dias = len({p["_fecha"] for p in partes})
        filas.append({"ci": ci, "descripcion": descripcion, "dias": dias, "partes": len(partes), "valores": valores})
    columnas = [{"campo": c, "etiqueta": t} for c, t in disponibles if c in INDICADORES_FIJOS or abs(general[c]) > 1e-9]

    def consumo(v):
        hs, lt = v.get("total_hs") or 0.0, v.get("gasoil") or 0.0
        return _r(lt / hs, 2) if hs > 0 and lt > 0 else None
    for f in filas:
        f["consumo_lh"] = consumo(f["valores"])
        f["valores"] = {c["campo"]: _r(f["valores"][c["campo"]], 2) for c in columnas}
    return {"columnas": columnas, "equipos": filas,
            "general": {"consumo_lh": consumo(general),
                        "valores": {c["campo"]: _r(general[c["campo"]], 2) for c in columnas}}}


def _cumplimiento(cantidad, minimo, objetivo):
    """Estado de un ítem frente a su mínimo y objetivo (estado_meta None = sin meta)."""
    def pct(ref):
        return _r(cantidad / ref * 100, 1) if ref else None
    if minimo is None and objetivo is None:
        estado = None
    elif minimo is not None and cantidad < minimo:
        estado = "BAJO_MINIMO"
    elif objetivo is not None and cantidad >= objetivo:
        estado = "OBJETIVO"
    elif objetivo is not None:
        estado = "BAJO_OBJETIVO"
    else:
        estado = "CUMPLE_MINIMO"
    return {"minimo": minimo, "objetivo": objetivo, "pct_minimo": pct(minimo), "pct_objetivo": pct(objetivo), "estado_meta": estado}


ESTADOS_META = {"BAJO_MINIMO": "Bajo el mínimo", "BAJO_OBJETIVO": "Sobre el mínimo, bajo el objetivo",
                "CUMPLE_MINIMO": "Cumple el mínimo", "OBJETIVO": "Alcanzó el objetivo"}


def calcular(conn, a, equipos, metas=None):
    """Resultado del acta desde los partes diarios. No escribe nada. metas: [{item_oc_cliente_id, minimo, objetivo}]."""
    campos = _campos_validos(conn)
    columnas = [c for c in a["columnas"] if c in campos]
    items = _items_oc(conn, a["orden_compra_cliente_id"])
    desc = _descripcion_equipos(conn)
    centros = _centros(conn)
    partes = _partes_periodo(conn, a["periodo_desde"], a["periodo_hasta"], [e["ci"] for e in equipos])
    por_ci = {}
    for p in partes:
        por_ci.setdefault(p["_ci"], []).append(p)

    def partes_de(e):
        cc = e.get("centro_costo_id")
        return [p for p in por_ci.get(e["ci"].upper(), []) if cc is None or p["_cc"] == cc]

    salida_eq, por_item, avisos = [], {}, []
    for e in equipos:
        ci = e["ci"].upper()
        cc = e.get("centro_costo_id")
        campo = e.get("campo_cantidad") or a["campo_cantidad"]
        lista = partes_de(e)
        filas, total, totales = [], 0.0, {c: 0.0 for c in columnas if c in SUMABLES}
        vistos, duplicados = {}, []
        for p in lista:
            cantidad = num(p.get(campo)) or 0.0
            total += cantidad
            for c in totales:
                totales[c] += num(p.get(c)) or 0.0
            clave = (p["_fecha"], _texto(p.get("turno")).upper())
            if clave in vistos:
                duplicados.append(p["_fecha"])
            vistos[clave] = True
            filas.append({"id": p["id"], "fecha": p["_fecha"], "valores": {c: p.get(c) for c in columnas}, "cantidad": _r(cantidad)})
        item = items.get(e.get("item_oc_cliente_id"))
        salida_eq.append({
            "ci": ci, "descripcion": desc.get(ci, ""), "campo_cantidad": campo, "etiqueta_cantidad": _etiqueta(campo),
            "centro_costo_id": cc, "centro_costo": _cc_txt(centros, cc),
            "item_oc_cliente_id": item["id"] if item else None,
            "item_oc": f"{item['numero']} · {item['descripcion'] or ''}".strip() if item else None,
            "partes": filas, "total": _r(total), "totales": {c: _r(v) for c, v in totales.items()},
        })
        if not lista:
            avisos.append(f"{ci}: sin partes en el período" + (f" en el centro de costos {cc}." if cc is not None else "."))
        if not item:
            avisos.append(f"{ci}: no tiene ítem de la OC; su cantidad no se certifica.")
        if duplicados:
            avisos.append(f"{ci}: más de un parte el mismo día y turno ({', '.join(_fecha_txt(d) for d in sorted(set(duplicados)))}).")
        if item:
            linea = por_item.setdefault(item["id"], {**item, "item_oc_cliente_id": item["id"], "cantidad_periodo": 0.0, "equipos": []})
            linea["cantidad_periodo"] += total
            linea["equipos"].append(ci)
    metas = {m["item_oc_cliente_id"]: m for m in (metas or [])}
    lineas = []
    for it in sorted(por_item.values(), key=lambda x: x["numero"]):
        it["cantidad_periodo"] = _r(it["cantidad_periodo"])
        precio = num(it.get("precio"))
        it["importe"] = _r(it["cantidad_periodo"] * precio, 2) if precio is not None else None
        m = metas.get(it["id"], {})
        it.update(_cumplimiento(it["cantidad_periodo"], m.get("minimo"), m.get("objetivo")))
        if it["estado_meta"] == "BAJO_MINIMO":
            avisos.append(f"Ítem {it['numero']}: {_r(it['cantidad_periodo'], 2):g} está bajo el mínimo de {it['minimo']:g}.")
        lineas.append(it)
    con_obj = [x for x in lineas if x["objetivo"]]
    return {
        "columnas": [{"campo": c, "etiqueta": campos[c]["etiqueta"], "numerico": campos[c]["numerico"]} for c in columnas],
        "equipos": salida_eq, "items_oc": lineas, "avisos": avisos,
        "indicadores": _indicadores(campos, [(e["ci"].upper(), desc.get(e["ci"].upper(), ""), partes_de(e))
                                             for e in equipos]),
        "total_partes": sum(len(e["partes"]) for e in salida_eq),
        "total_importe": _r(sum(x["importe"] or 0 for x in lineas), 2),
        "kpi": {"items_con_meta": sum(1 for x in lineas if x["estado_meta"]),
                "bajo_minimo": sum(1 for x in lineas if x["estado_meta"] == "BAJO_MINIMO"),
                "en_objetivo": sum(1 for x in lineas if x["estado_meta"] == "OBJETIVO"),
                "pct_objetivo": _r(sum(x["cantidad_periodo"] for x in con_obj) / sum(x["objetivo"] for x in con_obj) * 100, 1)
                if con_obj else None},
        "calculado_en": _ahora(),
    }


# ============================================================================
# Actas
# ============================================================================
def _acta(conn, acta_id):
    a = _fila(conn, "SELECT * FROM am_mediciones WHERE id = ?", (acta_id,))
    if not a:
        _err("NO_EXISTE", "acta inexistente")
    a["columnas"] = json.loads(a.pop("columnas_json") or "[]")
    kpi = _fila(conn, "SELECT metas_json, graficos_json FROM am_medicion_kpi WHERE medicion_id = ?", (acta_id,)) or {}
    a["metas"] = json.loads(kpi.get("metas_json") or "[]")
    a["graficos"] = json.loads(kpi["graficos_json"]) if kpi.get("graficos_json") else None
    return a


def _equipos(conn, acta_id):
    return _filas(conn, """SELECT ci, item_oc_cliente_id, campo_cantidad, centro_costo_id FROM am_medicion_equipos
                           WHERE medicion_id = ? ORDER BY orden, id""", (acta_id,))


def _certificados_de(conn, acta_id):
    return _filas(conn, """SELECT c.id, c.numero, c.estado, c.seguimiento_cc_id FROM am_medicion_certificados l
                           JOIN certificados_cliente c ON c.id = l.certificado_id WHERE l.medicion_id = ? ORDER BY c.id""", (acta_id,))


def _cert_vigente(conn, acta_id):
    return next((c for c in _certificados_de(conn, acta_id) if c["estado"] != "ANULADO"), None)


def _oc(conn, oc_id):
    o = _fila(conn, """SELECT o.id, o.seguimiento_cc_id, o.numero, o.orden_compra, o.revision, o.moneda, cl.razon_social AS cliente
                       FROM Orden_de_compra_cliente o LEFT JOIN seguimiento_cc s ON s.id = o.seguimiento_cc_id
                       LEFT JOIN clientes cl ON cl.codigo = s.cliente_codigo WHERE o.id = ?""", (oc_id,))
    if not o:
        _err("NO_EXISTE", "orden de compra inexistente")
    return o


def listar(conn, seguimiento_id=None):
    sql = """SELECT m.id, m.numero, m.periodo_desde, m.periodo_hasta, m.fecha, m.estado, m.seguimiento_cc_id, m.orden_compra_cliente_id,
                    o.orden_compra, o.numero AS oc_numero, cl.razon_social AS cliente,
                    (SELECT COUNT(*) FROM am_medicion_equipos e WHERE e.medicion_id = m.id) AS equipos
             FROM am_mediciones m JOIN Orden_de_compra_cliente o ON o.id = m.orden_compra_cliente_id
             LEFT JOIN seguimiento_cc s ON s.id = m.seguimiento_cc_id LEFT JOIN clientes cl ON cl.codigo = s.cliente_codigo"""
    params = ()
    if seguimiento_id:
        sql += " WHERE m.seguimiento_cc_id = ?"
        params = (seguimiento_id,)
    filas = _filas(conn, sql + " ORDER BY m.periodo_hasta DESC, m.id DESC", params)
    for f in filas:
        f["certificado"] = _cert_vigente(conn, f["id"])
    return filas


def sugerencia(conn, oc_id):
    """Número, período y configuración (equipos, ítems, columnas) a partir de la última acta de la OC."""
    _oc(conn, oc_id)
    ultima = _fila(conn, """SELECT id FROM am_mediciones WHERE orden_compra_cliente_id = ? AND estado <> 'ANULADA'
                            ORDER BY periodo_hasta DESC, numero DESC LIMIT 1""", (oc_id,))
    hoy = date.today()
    if ultima:
        a = _acta(conn, ultima["id"])
        desde = parse_fecha(a["periodo_hasta"]) + timedelta(days=1)
        hasta = _sumar_meses(desde, 1) - timedelta(days=1)
        maximo = conn.execute("SELECT MAX(numero) FROM am_mediciones WHERE orden_compra_cliente_id = ? AND estado <> 'ANULADA'",
                              (oc_id,)).fetchone()[0] or 0
        return {"numero": maximo + 1, "periodo_desde": desde.isoformat(), "periodo_hasta": hasta.isoformat(), "fecha": hoy.isoformat(),
                "campo_cantidad": a["campo_cantidad"], "columnas": a["columnas"], "equipos": _equipos(conn, a["id"]), "desde_acta": a["numero"],
                "metas": a["metas"], "graficos": a["graficos"]}
    primero = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1)
    return {"numero": 1, "periodo_desde": primero.isoformat(), "periodo_hasta": (hoy.replace(day=1) - timedelta(days=1)).isoformat(),
            "fecha": hoy.isoformat(), "campo_cantidad": CAMPO_CANTIDAD_DEFECTO,
            "columnas": [c for c in COLUMNAS_DEFECTO if c in _campos_validos(conn)], "equipos": [], "desde_acta": None,
            "metas": [], "graficos": None}


def _normalizar(conn, datos, acta_id=None, chequear_numero=True):
    oc = _oc(conn, int(datos.get("orden_compra_cliente_id") or 0))
    try:
        numero = int(datos.get("numero"))
    except (TypeError, ValueError):
        _err("DATOS", "número de acta inválido")
    if numero < 1:
        _err("DATOS", "el número de acta debe ser mayor que cero")
    desde, hasta = _iso(datos.get("periodo_desde"), "desde"), _iso(datos.get("periodo_hasta"), "hasta")
    if desde > hasta:
        _err("DATOS", "el período termina antes de empezar")
    campos = _campos_validos(conn)
    campo = _texto(datos.get("campo_cantidad")) or CAMPO_CANTIDAD_DEFECTO
    if campo not in campos:
        _err("DATOS", f"el parte diario no tiene la columna {campo}")
    columnas = []
    for c in datos.get("columnas") or []:
        if c not in campos:
            _err("DATOS", f"el parte diario no tiene la columna {c}")
        if c not in columnas:
            columnas.append(c)
    items = _items_oc(conn, oc["id"])
    centros = _centros(conn)
    equipos, vistos = [], set()
    for e in datos.get("equipos") or []:
        ci = _texto(e.get("ci")).upper()
        if not ci:
            continue
        if ci in vistos:
            _err("DATOS", f"el equipo {ci} está dos veces")
        vistos.add(ci)
        item = e.get("item_oc_cliente_id") or None
        if item is not None:
            item = int(item)
            if item not in items:
                _err("DATOS", f"{ci}: el ítem elegido no es de la OC del acta")
        propio = _texto(e.get("campo_cantidad")) or None
        if propio and propio not in campos:
            _err("DATOS", f"{ci}: el parte diario no tiene la columna {propio}")
        cc = e.get("centro_costo_id")
        if cc in ("", None):
            cc = None
        else:
            try:
                cc = int(cc)
            except (TypeError, ValueError):
                _err("DATOS", f"{ci}: centro de costos inválido")
            if cc not in centros:
                _err("DATOS", f"{ci}: el centro de costos {cc} no existe")
        equipos.append({"ci": ci, "item_oc_cliente_id": item, "campo_cantidad": propio if propio != campo else None, "centro_costo_id": cc})
    metas = _normalizar_metas(datos.get("metas"), items)
    repetida = _fila(conn, """SELECT id FROM am_mediciones WHERE orden_compra_cliente_id = ? AND numero = ? AND estado <> 'ANULADA'
                              AND id <> ?""", (oc["id"], numero, acta_id or 0))
    if repetida and chequear_numero:
        _err("DUPLICADO", f"ya existe el acta N° {numero} para esta OC")
    return {"seguimiento_cc_id": oc["seguimiento_cc_id"], "orden_compra_cliente_id": oc["id"], "numero": numero,
            "periodo_desde": desde, "periodo_hasta": hasta, "fecha": _iso(datos.get("fecha") or _hoy(), "fecha"),
            "campo_cantidad": campo, "columnas_json": _dump(columnas), "observaciones": _texto(datos.get("observaciones")) or None}, equipos, metas


def _normalizar_metas(metas, items):
    """[{item_oc_cliente_id, minimo, objetivo}] de ítems de la OC; descarta las filas sin ningún valor."""
    salida, vistos = [], set()
    for m in metas or []:
        try:
            item = int(m.get("item_oc_cliente_id") or 0)
        except (TypeError, ValueError):
            item = 0
        if item not in items:
            _err("DATOS", "la meta es de un ítem que no pertenece a la OC del acta")
        if item in vistos:
            continue
        vistos.add(item)
        valores = {}
        for clave, nombre in (("minimo", "mínimo"), ("objetivo", "objetivo")):
            crudo = m.get(clave)
            vacio = crudo is None or _texto(crudo) == ""
            v = None if vacio else num(crudo)
            if v is None and not vacio:
                _err("DATOS", f"ítem {items[item]['numero']}: el {nombre} no es un número")
            if v is not None and v < 0:
                _err("DATOS", f"ítem {items[item]['numero']}: el {nombre} no puede ser negativo")
            valores[clave] = _r(v) if v is not None else None
        if valores["minimo"] is not None and valores["objetivo"] is not None and valores["minimo"] > valores["objetivo"]:
            _err("DATOS", f"ítem {items[item]['numero']}: el mínimo no puede superar al objetivo")
        if valores["minimo"] is not None or valores["objetivo"] is not None:
            salida.append({"item_oc_cliente_id": item, **valores})
    return salida


# Gráficos KPI que se pueden poner en un acta (el orden de la lista es el orden en pantalla).
GRAFICOS = {
    "tarjetas": "Tarjetas de resumen",
    "items_meta": "Cantidad por ítem vs. mínimo y objetivo",
    "avance_item": "Avance acumulado del ítem",
    "equipos": "Indicador por equipo",
    "diario": "Cantidad diaria",
}
INDICADORES_GRAFICO = ("cantidad", "dias", "partes", "consumo_lh") + tuple(c for c, _ in INDICADORES)
GRAFICOS_DEFECTO = [{"tipo": "tarjetas", "ancho": "completo"}, {"tipo": "items_meta", "ancho": "medio"},
                    {"tipo": "equipos", "indicador": "total_hs", "ancho": "medio"}, {"tipo": "diario", "ancho": "completo"}]


def normalizar_graficos(graficos):
    """None = usar los de defecto. Lista de {tipo, ancho, titulo?, indicador?, item_oc_cliente_id?}."""
    if graficos is None:
        return None
    if not isinstance(graficos, list):
        _err("DATOS", "los gráficos deben ser una lista")
    if len(graficos) > 20:
        _err("DATOS", "como máximo 20 gráficos por acta")
    salida = []
    for g in graficos:
        g = g if isinstance(g, dict) else {}
        tipo = _texto(g.get("tipo"))
        if tipo not in GRAFICOS:
            _err("DATOS", f"tipo de gráfico desconocido: {tipo or '(vacío)'}")
        limpio = {"tipo": tipo, "ancho": "completo" if g.get("ancho") == "completo" else "medio"}
        if _texto(g.get("titulo")):
            limpio["titulo"] = _texto(g.get("titulo"))[:80]
        if tipo == "equipos":
            ind = _texto(g.get("indicador")) or "total_hs"
            if ind not in INDICADORES_GRAFICO:
                _err("DATOS", f"indicador desconocido: {ind}")
            limpio["indicador"] = ind
        if tipo in ("avance_item", "diario"):
            try:
                limpio["item_oc_cliente_id"] = int(g.get("item_oc_cliente_id")) if g.get("item_oc_cliente_id") else None
            except (TypeError, ValueError):
                _err("DATOS", "ítem del gráfico inválido")
        salida.append(limpio)
    return salida


def _guardar_kpi(conn, acta_id, metas=None, graficos=None, solo_graficos=False):
    """metas siempre se reemplaza (salvo solo_graficos); graficos None conserva los que había."""
    actual = _fila(conn, "SELECT metas_json, graficos_json FROM am_medicion_kpi WHERE medicion_id = ?", (acta_id,)) or {}
    metas_json = actual.get("metas_json", "[]") if solo_graficos else _dump(metas or [])
    graficos_json = _dump(graficos) if graficos is not None else actual.get("graficos_json")
    conn.execute("INSERT INTO am_medicion_kpi (medicion_id, metas_json, graficos_json) VALUES (?, ?, ?) "
                 "ON CONFLICT (medicion_id) DO UPDATE SET metas_json = excluded.metas_json, graficos_json = excluded.graficos_json",
                 (acta_id, metas_json, graficos_json))


def guardar_graficos(conn, acta_id, graficos, usuario):
    """Elige y ordena los gráficos KPI del acta. Vale en borrador o cerrada: no cambia el resultado."""
    a = _acta(conn, acta_id)
    if a["estado"] == "ANULADA":
        _err("INMUTABLE", "un acta anulada no se modifica")
    graficos = normalizar_graficos(graficos)  # None = volver a los de siempre
    _guardar_kpi(conn, acta_id, solo_graficos=True)
    conn.execute("UPDATE am_medicion_kpi SET graficos_json = ? WHERE medicion_id = ?",
                 (_dump(graficos) if graficos is not None else None, acta_id))
    evento(conn, "MEDICION", acta_id, "GRAFICOS", usuario, {"cantidad": None if graficos is None else len(graficos)})
    return {"graficos": graficos}


def vista_previa(conn, datos):
    """Calcula con los datos del formulario, sin guardar."""
    fila, equipos, metas = _normalizar(conn, {**datos, "numero": datos.get("numero") or 1}, int(datos.get("id") or 0) or None,
                                       chequear_numero=False)
    return calcular(conn, {**fila, "columnas": json.loads(fila["columnas_json"])}, equipos, metas)


def _guardar_equipos(conn, acta_id, equipos):
    conn.execute("DELETE FROM am_medicion_equipos WHERE medicion_id = ?", (acta_id,))
    for i, e in enumerate(equipos):
        conn.execute("""INSERT INTO am_medicion_equipos (medicion_id, ci, item_oc_cliente_id, campo_cantidad, centro_costo_id, orden)
                        VALUES (?, ?, ?, ?, ?, ?)""", (acta_id, e["ci"], e["item_oc_cliente_id"], e["campo_cantidad"], e.get("centro_costo_id"), i))


def crear(conn, datos, usuario):
    fila, equipos, metas = _normalizar(conn, datos)
    graficos = normalizar_graficos(datos.get("graficos"))
    cols = list(fila)
    acta_id = conn.execute(
        f"INSERT INTO am_mediciones ({', '.join(cols)}, estado, creado_por, creado_en) VALUES ({', '.join('?' for _ in cols)}, 'BORRADOR', ?, ?)",
        [fila[c] for c in cols] + [usuario, _ahora()]).lastrowid
    _guardar_equipos(conn, acta_id, equipos)
    _guardar_kpi(conn, acta_id, metas, graficos)
    evento(conn, "MEDICION", acta_id, "CREADA", usuario, {"numero": fila["numero"]})
    return obtener(conn, acta_id)


def actualizar(conn, acta_id, datos, usuario):
    a = _acta(conn, acta_id)
    if a["estado"] != "BORRADOR":
        _err("INMUTABLE", "solo se edita un acta en borrador; reábrala primero")
    fila, equipos, metas = _normalizar(conn, datos, acta_id)
    graficos = normalizar_graficos(datos.get("graficos"))
    conn.execute(f"UPDATE am_mediciones SET {', '.join(f'{c} = ?' for c in fila)}, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                 list(fila.values()) + [usuario, _ahora(), acta_id])
    _guardar_equipos(conn, acta_id, equipos)
    _guardar_kpi(conn, acta_id, metas, graficos)
    evento(conn, "MEDICION", acta_id, "ACTUALIZADA", usuario)
    return obtener(conn, acta_id)


def eliminar(conn, acta_id, usuario):
    a = _acta(conn, acta_id)
    if a["estado"] != "BORRADOR":
        _err("INMUTABLE", "solo se elimina un borrador; un acta cerrada se anula")
    if _certificados_de(conn, acta_id):
        _err("INMUTABLE", "el acta tiene certificados vinculados")
    conn.execute("DELETE FROM am_medicion_kpi WHERE medicion_id = ?", (acta_id,))
    conn.execute("DELETE FROM am_mediciones WHERE id = ?", (acta_id,))
    evento(conn, "MEDICION", acta_id, "ELIMINADA", usuario, {"numero": a["numero"]})
    return {"ok": True}


def obtener(conn, acta_id):
    a = _acta(conn, acta_id)
    a["equipos"] = _equipos(conn, acta_id)
    a["oc"] = _oc(conn, a["orden_compra_cliente_id"])
    resultado = a.pop("resultado_json")
    a["resultado"] = json.loads(resultado) if resultado else calcular(conn, a, a["equipos"], a["metas"])
    a["graficos_defecto"] = GRAFICOS_DEFECTO
    a["certificados"] = _certificados_de(conn, acta_id)
    return a


def cerrar(conn, acta_id, usuario):
    a = _acta(conn, acta_id)
    if a["estado"] != "BORRADOR":
        _err("DATOS", "el acta no está en borrador")
    equipos = _equipos(conn, acta_id)
    if not equipos:
        _err("DATOS", "el acta no tiene equipos")
    resultado = calcular(conn, a, equipos, a["metas"])
    if not resultado["items_oc"]:
        _err("DATOS", "ningún equipo tiene ítem de la OC: no hay nada para certificar")
    conn.execute("UPDATE am_mediciones SET estado = 'CERRADA', resultado_json = ?, cerrada_por = ?, cerrada_en = ? WHERE id = ?",
                 (_dump(resultado), usuario, _ahora(), acta_id))
    evento(conn, "MEDICION", acta_id, "CERRADA", usuario, {"partes": resultado["total_partes"]})
    return obtener(conn, acta_id)


def reabrir(conn, acta_id, usuario):
    a = _acta(conn, acta_id)
    if a["estado"] != "CERRADA":
        _err("DATOS", "solo se reabre un acta cerrada")
    if _cert_vigente(conn, acta_id):
        _err("INMUTABLE", "el acta tiene un certificado vigente; anúlelo primero")
    conn.execute("UPDATE am_mediciones SET estado = 'BORRADOR', resultado_json = NULL, cerrada_por = NULL, cerrada_en = NULL, "
                 "actualizado_por = ?, actualizado_en = ? WHERE id = ?", (usuario, _ahora(), acta_id))
    evento(conn, "MEDICION", acta_id, "REABIERTA", usuario)
    return obtener(conn, acta_id)


def anular(conn, acta_id, motivo, usuario):
    a = _acta(conn, acta_id)
    if a["estado"] == "ANULADA":
        _err("DATOS", "el acta ya está anulada")
    if len(_texto(motivo)) < 10:
        _err("DATOS", "indique el motivo de la anulación (mínimo 10 caracteres)")
    if _cert_vigente(conn, acta_id):
        _err("INMUTABLE", "el acta tiene un certificado vigente; anúlelo primero")
    conn.execute("UPDATE am_mediciones SET estado = 'ANULADA', anulada_por = ?, anulada_en = ?, motivo_anulacion = ? WHERE id = ?",
                 (usuario, _ahora(), _texto(motivo), acta_id))
    evento(conn, "MEDICION", acta_id, "ANULADA", usuario, {"motivo": _texto(motivo)})
    return obtener(conn, acta_id)


# ============================================================================
# Excel
# ============================================================================
def exportar_excel(conn, acta_id):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    a = obtener(conn, acta_id)
    res = a["resultado"]
    oc = a["oc"]
    wb = Workbook()
    fino = Side(style="thin", color="9FB3C8")
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)
    cab = PatternFill("solid", fgColor="DCE8F7")
    tot = PatternFill("solid", fgColor="EEF4FC")
    negrita = Font(bold=True)
    usados = set()

    def encabezado(ws, titulo):
        ws.append([f"ACTA DE MEDICIÓN N° {a['numero']}" + (" (ANULADA)" if a["estado"] == "ANULADA" else "")])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append([f"Cliente: {oc.get('cliente') or ''}   ·   OC: {oc.get('orden_compra') or oc.get('numero')}   ·   "
                   f"Período: {_fecha_txt(a['periodo_desde'])} al {_fecha_txt(a['periodo_hasta'])}   ·   Fecha: {_fecha_txt(a['fecha'])}"])
        ws.append([titulo])
        ws.cell(ws.max_row, 1).font = Font(bold=True, size=12)
        ws.append([])

    def fila(ws, valores, estilo=None, formatos=None):
        ws.append(valores)
        for i, _ in enumerate(valores, 1):
            c = ws.cell(ws.max_row, i)
            c.border = borde
            if estilo is not None:
                c.fill = estilo
                c.font = negrita
            if formatos and formatos[i - 1]:
                c.number_format = formatos[i - 1]

    def anchos(ws, n):
        for i in range(1, n + 1):
            largo = max((len(str(ws.cell(f, i).value or "")) for f in range(5, ws.max_row + 1)), default=8)
            ws.column_dimensions[get_column_letter(i)].width = min(max(largo + 2, 9), 60)

    ws = wb.active
    ws.title = "Resumen"
    usados.add("Resumen")
    encabezado(ws, "Cantidades a certificar por ítem de la OC")
    fila(ws, ["Ítem OC", "Descripción", "Unidad", "Equipos", "Cantidad", "Precio", "Importe", "Mínimo", "Objetivo", "% del objetivo",
              "Cumplimiento"], cab)
    for it in res["items_oc"]:
        fila(ws, [it["numero"], it["descripcion"], it["unidad"], ", ".join(it["equipos"]), it["cantidad_periodo"], num(it.get("precio")), it["importe"],
                  it.get("minimo"), it.get("objetivo"), it.get("pct_objetivo"), ESTADOS_META.get(it.get("estado_meta"), "")],
             formatos=[None, None, None, None, "#,##0.00", "#,##0.00", "#,##0.00", "#,##0.##", "#,##0.##", "0.0", None])
    fila(ws, ["Total", "", "", "", "", "", res["total_importe"], "", "", "", ""], tot, [None] * 6 + ["#,##0.00"] + [None] * 4)
    ws.append([])
    fila(ws, ["Equipo", "Descripción", "Centro de costos", "Ítem OC", "Columna", "Partes", "Cantidad"], cab)
    for e in res["equipos"]:
        fila(ws, [e["ci"], e["descripcion"], e.get("centro_costo") or "Todos", e["item_oc"] or "— sin ítem —", e["etiqueta_cantidad"],
                  len(e["partes"]), e["total"]], formatos=[None] * 6 + ["#,##0.00"])
    ind = res.get("indicadores")
    if ind and ind["equipos"]:
        ws.append([])
        ws.append(["Indicadores"])
        ws.cell(ws.max_row, 1).font = negrita
        fila(ws, ["Equipo", "Descripción", "Días", "Partes"] + [c["etiqueta"] for c in ind["columnas"]] + ["Consumo (L/h)"], cab)
        fmt = [None, None, "0", "0"] + ["#,##0.##"] * (len(ind["columnas"]) + 1)
        for f in ind["equipos"]:
            fila(ws, [f["ci"], f["descripcion"], f["dias"], f["partes"]] + [f["valores"][c["campo"]] for c in ind["columnas"]] + [f["consumo_lh"]],
                 formatos=fmt)
        g = ind["general"]
        fila(ws, ["General", "", "", ""] + [g["valores"][c["campo"]] for c in ind["columnas"]] + [g["consumo_lh"]], tot, fmt)
    if res["avisos"]:
        ws.append([])
        ws.append(["Avisos"])
        ws.cell(ws.max_row, 1).font = negrita
        for t in res["avisos"]:
            ws.append([t])
    anchos(ws, max(11, 5 + len((res.get("indicadores") or {}).get("columnas", []))))

    cols = res["columnas"]
    for e in res["equipos"]:
        nombre = re.sub(r"[\[\]\*\?/\\:]", "-", e["ci"])[:28] or "Equipo"
        n, i = nombre, 2
        while n in usados:
            n, i = f"{nombre[:25]} {i}", i + 1
        usados.add(n)
        hs = wb.create_sheet(n)
        cc_txt = f" · CC {e['centro_costo']}" if e.get("centro_costo") else ""
        encabezado(hs, f"{e['ci']} {e['descripcion']}{cc_txt} — {e['item_oc'] or 'sin ítem de OC'}")
        fila(hs, [c["etiqueta"] for c in cols] + [f"Cantidad ({e['etiqueta_cantidad']})"], cab)
        for p in e["partes"]:
            valores = []
            for c in cols:
                v = p["valores"].get(c["campo"])
                if c["campo"].startswith("fecha"):
                    v = parse_fecha(v) or v
                elif c["numerico"]:
                    v = num(v) if num(v) is not None else v
                valores.append(v)
            fila(hs, valores + [p["cantidad"]],
                 formatos=["DD/MM/YYYY" if c["campo"].startswith("fecha") else ("#,##0.##" if c["numerico"] else None) for c in cols] + ["#,##0.##"])
        fila(hs, [("TOTAL" if i == 0 else e["totales"].get(c["campo"], "")) for i, c in enumerate(cols)] + [e["total"]], tot)
        for c in hs[5]:
            c.alignment = Alignment(wrap_text=True, vertical="center")
        anchos(hs, len(cols) + 1)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"Acta_medicion_{a['numero']}_OC_{re.sub(r'[^A-Za-z0-9-]', '_', str(oc.get('orden_compra') or oc['numero']))}.xlsx"


# ============================================================================
# Conexión con Certificados (Seguimiento de Cliente)
# ============================================================================
def actas_para_certificado(conn, seguimiento_id, orden_compra_cliente_id=None):
    """Actas cerradas del seguimiento, con su certificado vigente (si tiene)."""
    salida = []
    for a in _filas(conn, """SELECT m.id, m.numero, m.periodo_desde, m.periodo_hasta, m.estado, m.orden_compra_cliente_id, o.orden_compra
                             FROM am_mediciones m JOIN Orden_de_compra_cliente o ON o.id = m.orden_compra_cliente_id
                             WHERE m.seguimiento_cc_id = ? AND m.estado = 'CERRADA' ORDER BY m.periodo_hasta DESC""", (seguimiento_id,)):
        salida.append({"acta_id": a["id"], "contrato": f"OC {a['orden_compra'] or ''}".strip(), "numero": a["numero"], "revision": 0,
                       "periodo_desde": a["periodo_desde"], "periodo_hasta": a["periodo_hasta"], "estado": a["estado"],
                       "orden_compra_cliente_id": a["orden_compra_cliente_id"],
                       "coincide_oc": not orden_compra_cliente_id or a["orden_compra_cliente_id"] == orden_compra_cliente_id,
                       "certificado_vigente": _cert_vigente(conn, a["id"])})
    return salida


def precarga_certificado(conn, acta_id):
    a = obtener(conn, acta_id)
    if a["estado"] != "CERRADA":
        _err("DATOS", "solo se precarga desde un acta cerrada")
    res = a["resultado"]
    texto = (f"Precargado desde Acta de Medición N° {a['numero']} "
             f"({_fecha_txt(a['periodo_desde'])} al {_fecha_txt(a['periodo_hasta'])})")
    return {
        "acta_id": acta_id, "numero": a["numero"], "revision": 0, "periodo_desde": a["periodo_desde"], "periodo_hasta": a["periodo_hasta"],
        "seguimiento_cc_id": a["seguimiento_cc_id"], "orden_compra_cliente_id": a["orden_compra_cliente_id"],
        "ordenes": [a["orden_compra_cliente_id"]] if res["items_oc"] else [],
        "lineas": [{"item_oc_cliente_id": it["item_oc_cliente_id"], "cantidad_periodo": it["cantidad_periodo"],
                    "observacion": f"Acta N° {a['numero']}: {', '.join(it['equipos'])}"} for it in res["items_oc"]],
        "descuentos": [], "items_sin_vinculo": [e["ci"] for e in res["equipos"] if not e["item_oc_cliente_id"] and e["total"]],
        "conceptos_sin_precio": [], "observaciones": texto,
    }


def vincular_certificado(conn, acta_id, certificado_id, usuario):
    a = _acta(conn, acta_id)
    cert = _fila(conn, "SELECT id, seguimiento_cc_id, estado FROM certificados_cliente WHERE id = ?", (certificado_id,))
    if not cert:
        _err("NO_EXISTE", "certificado inexistente")
    if cert["seguimiento_cc_id"] != a["seguimiento_cc_id"]:
        _err("DATOS", "el certificado es de otro seguimiento")
    if cert["estado"] == "ANULADO":
        _err("DATOS", "el certificado está anulado")
    vigente = _cert_vigente(conn, acta_id)
    if vigente and vigente["id"] != certificado_id:
        _err("DUPLICADO", "el acta ya tiene otro certificado vigente")
    existente = _fila(conn, "SELECT medicion_id FROM am_medicion_certificados WHERE certificado_id = ?", (certificado_id,))
    if existente and existente["medicion_id"] != acta_id:
        _err("DUPLICADO", "el certificado ya está vinculado a otra acta")
    if not existente:
        conn.execute("INSERT INTO am_medicion_certificados (medicion_id, certificado_id, creado_por, creado_en) VALUES (?, ?, ?, ?)",
                     (acta_id, certificado_id, usuario, _ahora()))
        evento(conn, "MEDICION", acta_id, "CERTIFICADO_VINCULADO", usuario, {"certificado_id": certificado_id})
    return {"ok": True}


def acta_de_certificado(conn, certificado_id):
    return _fila(conn, """SELECT m.id AS acta_id, m.numero, 0 AS revision, 'OC ' || COALESCE(o.orden_compra, '') AS contrato
                          FROM am_medicion_certificados l JOIN am_mediciones m ON m.id = l.medicion_id
                          JOIN Orden_de_compra_cliente o ON o.id = m.orden_compra_cliente_id WHERE l.certificado_id = ?""",
                 (certificado_id,))


# ============================================================================
# Datos para la pantalla
# ============================================================================
def opciones(conn):
    return {
        "seguimientos": _filas(conn, """SELECT s.id, s.cliente_codigo, cl.razon_social AS cliente, s.centro_costo_id, cc.nombre AS centro_costo
                                        FROM seguimiento_cc s LEFT JOIN clientes cl ON cl.codigo = s.cliente_codigo
                                        LEFT JOIN centros_costos cc ON cc.id = s.centro_costo_id ORDER BY s.id"""),
        "ordenes": _filas(conn, """SELECT id, seguimiento_cc_id, numero, orden_compra, revision, moneda FROM Orden_de_compra_cliente
                                   ORDER BY seguimiento_cc_id, numero"""),
        "items_oc": _filas(conn, """SELECT i.id, i.orden_compra_cliente_id, i.numero, i.descripcion, i.unidad, i.precio
                                    FROM items_oc_cliente i ORDER BY i.orden_compra_cliente_id, i.numero"""),
        "equipos": [{"codigo": k, "descripcion": v} for k, v in sorted(_descripcion_equipos(conn).items())],
        "campos": campos_parte(conn),
        "centros_costos": _filas(conn, "SELECT id, nombre FROM centros_costos WHERE COALESCE(activo, 1) = 1 ORDER BY id"),
    }
