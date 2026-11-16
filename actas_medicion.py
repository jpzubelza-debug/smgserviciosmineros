"""Actas de Medición (fojas de medición / certificación) de Operaciones.

Motor genérico dirigido por configuración. Todo lo que define cómo se mide, se calcula, se agrupa y se
presenta vive en datos (tablas am_* y JSON de configuración), nunca en este código:

    plantilla (am_plantillas.config_json)
      -> contrato (am_contratos.config_json: pisa claves de la plantilla)
        -> ítem (am_items.config_json)
          -> asignación de equipo/operador (am_asignaciones.config_json)
            -> acta (am_actas.overrides_json, incluye "items": {codigo: {...}})

El nivel más específico pisa al más general (merge recursivo de diccionarios; las listas se reemplazan).

Fuente de datos: SOLO los partes diarios (tabla horas_maquinas). Todas sus columnas, actuales y futuras,
se leen con PRAGMA y quedan disponibles para medidas, fórmulas, filtros, columnas de reporte y validaciones.
Los equipos son los CI de `vehiculos`, los operadores son legajos de `personal` y los precios pueden venir de
los ítems de la OC del cliente (items_oc_cliente) del Seguimiento de Cliente.

Un acta "congelada" (estado del flujo con congela=true) guarda su resultado, la copia de la configuración usada
y una copia de los partes que la componen; los triggers impiden modificarla. Para corregirla se emite una revisión.

Las funciones reciben una conexión SQLite abierta y no hacen commit: main.py las envuelve en BEGIN IMMEDIATE.
"""
import ast
import calendar
import copy
import hashlib
import io
import json
import math
import re
from datetime import date, datetime, timedelta

MODULO = "operaciones"
PANEL = "actas_medicion"
ACCIONES = ("consultar", "generar", "configurar", "aprobar", "emitir", "anular")
SALIDAS = ("registrado", "minimas", "extras", "certificar")
NIVELES_REPORTE = ("ITEM", "UNIDAD", "EQUIPO", "PARTE")
TABLA_PARTES = "horas_maquinas"


class AmError(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje


def _err(codigo, mensaje):
    raise AmError(codigo, mensaje)


# ============================================================================
# Estructura de base
# ============================================================================
DDL = (
    """
    CREATE TABLE IF NOT EXISTS am_plantillas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE,
        descripcion TEXT,
        config_json TEXT NOT NULL DEFAULT '{}',
        activa INTEGER NOT NULL DEFAULT 1,
        creado_por TEXT, creado_en TEXT, actualizado_por TEXT, actualizado_en TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_contratos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plantilla_id INTEGER NOT NULL,
        seguimiento_cc_id INTEGER,
        orden_compra_cliente_id INTEGER,
        nombre TEXT NOT NULL,
        numero_contrato TEXT,
        proyecto TEXT,
        contratista TEXT,
        cuit TEXT,
        comitente TEXT,
        fecha_inicio TEXT,
        duracion_meses REAL,
        saldo_hasta_numero INTEGER NOT NULL DEFAULT 0,
        config_json TEXT NOT NULL DEFAULT '{}',
        activo INTEGER NOT NULL DEFAULT 1,
        creado_por TEXT, creado_en TEXT, actualizado_por TEXT, actualizado_en TEXT,
        FOREIGN KEY (plantilla_id) REFERENCES am_plantillas (id) ON DELETE RESTRICT,
        FOREIGN KEY (seguimiento_cc_id) REFERENCES seguimiento_cc (id) ON DELETE RESTRICT,
        FOREIGN KEY (orden_compra_cliente_id) REFERENCES Orden_de_compra_cliente (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_secciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contrato_id INTEGER NOT NULL,
        codigo TEXT NOT NULL,
        titulo TEXT NOT NULL,
        orden INTEGER NOT NULL DEFAULT 0,
        UNIQUE (contrato_id, codigo),
        FOREIGN KEY (contrato_id) REFERENCES am_contratos (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contrato_id INTEGER NOT NULL,
        seccion_id INTEGER,
        codigo TEXT NOT NULL,
        descripcion TEXT NOT NULL,
        unidad TEXT,
        unidades REAL NOT NULL DEFAULT 1,
        cantidad_mensual REAL,
        cantidad_contratada REAL,
        saldo_inicial REAL NOT NULL DEFAULT 0,
        meta REAL,
        meta_medida TEXT,
        config_json TEXT NOT NULL DEFAULT '{}',
        orden INTEGER NOT NULL DEFAULT 0,
        activo INTEGER NOT NULL DEFAULT 1,
        UNIQUE (contrato_id, codigo),
        FOREIGN KEY (contrato_id) REFERENCES am_contratos (id) ON DELETE CASCADE,
        FOREIGN KEY (seccion_id) REFERENCES am_secciones (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_item_oc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        salida TEXT NOT NULL CHECK (salida IN ('registrado', 'minimas', 'extras', 'certificar')),
        item_oc_cliente_id INTEGER NOT NULL,
        UNIQUE (item_id, salida),
        FOREIGN KEY (item_id) REFERENCES am_items (id) ON DELETE CASCADE,
        FOREIGN KEY (item_oc_cliente_id) REFERENCES items_oc_cliente (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_asignaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        recurso_tipo TEXT NOT NULL DEFAULT 'EQUIPO' CHECK (recurso_tipo IN ('EQUIPO', 'OPERADOR')),
        recurso_codigo TEXT NOT NULL,
        unidad INTEGER NOT NULL DEFAULT 1 CHECK (unidad >= 1),
        turno TEXT,
        desde TEXT NOT NULL,
        hasta TEXT,
        config_json TEXT NOT NULL DEFAULT '{}',
        CHECK (hasta IS NULL OR hasta >= desde),
        FOREIGN KEY (item_id) REFERENCES am_items (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_am_asig_item ON am_asignaciones (item_id)",
    """
    CREATE TABLE IF NOT EXISTS am_factores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contrato_id INTEGER NOT NULL,
        recurso_codigo TEXT NOT NULL,
        codigo TEXT NOT NULL,
        valor REAL NOT NULL,
        desde TEXT NOT NULL,
        hasta TEXT,
        CHECK (hasta IS NULL OR hasta >= desde),
        FOREIGN KEY (contrato_id) REFERENCES am_contratos (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_precios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contrato_id INTEGER NOT NULL,
        item_id INTEGER,
        salida TEXT,
        concepto TEXT,
        precio REAL NOT NULL,
        moneda TEXT,
        desde TEXT NOT NULL,
        hasta TEXT,
        CHECK ((item_id IS NULL) <> (concepto IS NULL)),
        CHECK (hasta IS NULL OR hasta >= desde),
        FOREIGN KEY (contrato_id) REFERENCES am_contratos (id) ON DELETE CASCADE,
        FOREIGN KEY (item_id) REFERENCES am_items (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_actas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contrato_id INTEGER NOT NULL,
        numero INTEGER NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0,
        periodo_desde TEXT NOT NULL,
        periodo_hasta TEXT NOT NULL,
        fecha TEXT NOT NULL,
        estado TEXT NOT NULL,
        overrides_json TEXT NOT NULL DEFAULT '{}',
        observaciones TEXT,
        congelada INTEGER NOT NULL DEFAULT 0 CHECK (congelada IN (0, 1)),
        resultado_json TEXT,
        config_snapshot_json TEXT,
        hash TEXT,
        reemplaza_a_id INTEGER,
        creado_por TEXT NOT NULL, creado_en TEXT NOT NULL,
        actualizado_por TEXT, actualizado_en TEXT,
        congelada_por TEXT, congelada_en TEXT,
        anulada_por TEXT, anulada_en TEXT, motivo_anulacion TEXT,
        UNIQUE (contrato_id, numero, revision),
        CHECK (periodo_desde <= periodo_hasta),
        CHECK (congelada = 0 OR (resultado_json IS NOT NULL AND config_snapshot_json IS NOT NULL AND hash IS NOT NULL)),
        FOREIGN KEY (contrato_id) REFERENCES am_contratos (id) ON DELETE RESTRICT,
        FOREIGN KEY (reemplaza_a_id) REFERENCES am_actas (id) ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_am_actas_contrato ON am_actas (contrato_id, numero)",
    """
    CREATE TABLE IF NOT EXISTS am_acta_certificados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        acta_id INTEGER NOT NULL,
        certificado_id INTEGER NOT NULL UNIQUE,
        creado_por TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        FOREIGN KEY (acta_id) REFERENCES am_actas (id) ON DELETE RESTRICT,
        FOREIGN KEY (certificado_id) REFERENCES certificados_cliente (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS am_campos (
        campo TEXT PRIMARY KEY,
        etiqueta TEXT,
        formato TEXT
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
    # --- Inmutabilidad de actas congeladas ---
    """
    CREATE TRIGGER IF NOT EXISTS trg_am_acta_congelada
    BEFORE UPDATE ON am_actas WHEN OLD.congelada = 1 AND (
        NEW.congelada IS NOT 1 OR NEW.contrato_id IS NOT OLD.contrato_id OR NEW.numero IS NOT OLD.numero
        OR NEW.revision IS NOT OLD.revision OR NEW.periodo_desde IS NOT OLD.periodo_desde
        OR NEW.periodo_hasta IS NOT OLD.periodo_hasta OR NEW.fecha IS NOT OLD.fecha
        OR NEW.overrides_json IS NOT OLD.overrides_json OR NEW.resultado_json IS NOT OLD.resultado_json
        OR NEW.config_snapshot_json IS NOT OLD.config_snapshot_json OR NEW.hash IS NOT OLD.hash
        OR NEW.reemplaza_a_id IS NOT OLD.reemplaza_a_id OR NEW.creado_por IS NOT OLD.creado_por
        OR NEW.creado_en IS NOT OLD.creado_en OR NEW.congelada_por IS NOT OLD.congelada_por
        OR NEW.congelada_en IS NOT OLD.congelada_en OR OLD.estado = 'ANULADA')
    BEGIN SELECT RAISE(ABORT, 'AM_INMUTABLE: un acta congelada solo cambia de estado; emita una revisión'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_am_acta_borrar
    BEFORE DELETE ON am_actas WHEN OLD.congelada = 1
    BEGIN SELECT RAISE(ABORT, 'AM_INMUTABLE: un acta congelada no se elimina; anúlela'); END
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
    """Solo agrega tablas, índices, triggers y la plantilla inicial (si no existe). Idempotente."""
    definidos = set()
    for sentencia in DDL:
        trigger = re.search(r"CREATE TRIGGER IF NOT EXISTS (\w+)", sentencia)
        if trigger:
            definidos.add(trigger.group(1))
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger.group(1)}")
        conn.execute(sentencia)
    for (obsoleto,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg\\_am\\_%' ESCAPE '\\'"
    ).fetchall():
        if obsoleto not in definidos:
            conn.execute(f"DROP TRIGGER IF EXISTS {obsoleto}")
    if not conn.execute("SELECT 1 FROM am_plantillas WHERE nombre = ?", (PLANTILLA_INICIAL_NOMBRE,)).fetchone():
        conn.execute(
            "INSERT INTO am_plantillas (nombre, descripcion, config_json, activa, creado_por, creado_en) VALUES (?, ?, ?, 1, ?, ?)",
            (PLANTILLA_INICIAL_NOMBRE, PLANTILLA_INICIAL_DESCRIPCION, json.dumps(PLANTILLA_INICIAL, ensure_ascii=False),
             "sistema", _ahora()),
        )


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


def _json(texto, defecto=None):
    if texto is None or texto == "":
        return copy.deepcopy(defecto) if defecto is not None else {}
    if isinstance(texto, (dict, list)):
        return texto
    try:
        return json.loads(texto)
    except (TypeError, ValueError):
        return copy.deepcopy(defecto) if defecto is not None else {}


def _dump(valor):
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, default=str)


def fusionar(base, encima):
    """Merge recursivo: los dict se combinan, el resto (listas incluidas) se reemplaza. None no pisa."""
    if not isinstance(base, dict) or not isinstance(encima, dict):
        return copy.deepcopy(encima if encima is not None else base)
    salida = copy.deepcopy(base)
    for clave, valor in encima.items():
        if valor is None:
            continue
        if isinstance(valor, dict) and isinstance(salida.get(clave), dict):
            salida[clave] = fusionar(salida[clave], valor)
        else:
            salida[clave] = copy.deepcopy(valor)
    return salida


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


def r(valor, decimales=4):
    if valor is None:
        return None
    v = round(float(valor), decimales)
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
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        s = s[:10]
    else:
        s = s.split(" ")[0]
    for fmt in _FORMATOS_FECHA:
        try:
            d = datetime.strptime(s, fmt).date()
            if 1990 <= d.year <= 2100:
                return d
        except ValueError:
            continue
    return None


def _fecha_txt(d):
    return d.strftime("%d/%m/%Y") if d else ""


def _iso(valor, campo):
    d = parse_fecha(valor)
    if d is None:
        _err("DATOS", f"{campo}: fecha inválida")
    return d.isoformat()


def _texto(valor):
    return "" if valor is None else str(valor).strip()


def _norm(valor):
    return _texto(valor).upper()


def _sumar_meses(d, meses):
    m = d.month - 1 + meses
    anio, mes = d.year + m // 12, m % 12 + 1
    return date(anio, mes, min(d.day, calendar.monthrange(anio, mes)[1]))


# ============================================================================
# Fórmulas: intérprete seguro (solo aritmética, comparaciones y funciones de una lista cerrada)
# ============================================================================
def _f_si(cond, a, b=0):
    return a if cond else b


def _f_redondear(x, n=0):
    return round(x or 0, int(n or 0))


FUNCIONES = {
    "min": lambda *a: min(x or 0 for x in a) if a else 0,
    "max": lambda *a: max(x or 0 for x in a) if a else 0,
    "abs": lambda x: abs(x or 0),
    "si": _f_si,
    "redondear": _f_redondear,
    "entero": lambda x: math.trunc(x or 0),
    "piso": lambda x: math.floor(x or 0),
    "techo": lambda x: math.ceil(x or 0),
    "nulo": lambda x, d=0: d if x in (None, "") else x,
    "vacio": lambda x: x in (None, "", 0),
}
_OPS = {
    ast.Add: lambda a, b: (a or 0) + (b or 0),
    ast.Sub: lambda a, b: (a or 0) - (b or 0),
    ast.Mult: lambda a, b: (a or 0) * (b or 0),
    ast.Div: lambda a, b: (a or 0) / b if b else 0.0,
    ast.Mod: lambda a, b: (a or 0) % b if b else 0.0,
    ast.Pow: lambda a, b: (a or 0) ** (b or 0),
    ast.FloorDiv: lambda a, b: (a or 0) // b if b else 0.0,
}
_CMP = {
    ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: (a or 0) < (b or 0), ast.LtE: lambda a, b: (a or 0) <= (b or 0),
    ast.Gt: lambda a, b: (a or 0) > (b or 0), ast.GtE: lambda a, b: (a or 0) >= (b or 0),
}
_CACHE_FORMULAS = {}


def _compilar(texto):
    texto = _texto(texto)
    if texto.startswith("="):
        texto = texto[1:]
    if texto in _CACHE_FORMULAS:
        return _CACHE_FORMULAS[texto]
    try:
        arbol = ast.parse(texto.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        _err("FORMULA", f"fórmula inválida «{texto}»: {exc.msg}")
    for nodo in ast.walk(arbol):
        permitido = isinstance(nodo, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
                                      ast.Call, ast.Name, ast.Attribute, ast.Constant, ast.Load, ast.operator,
                                      ast.unaryop, ast.boolop, ast.cmpop))
        if not permitido:
            _err("FORMULA", f"elemento no permitido en «{texto}»")
        if isinstance(nodo, ast.Call) and not (isinstance(nodo.func, ast.Name) and nodo.func.id in FUNCIONES):
            _err("FORMULA", f"función no permitida en «{texto}» (permitidas: {', '.join(sorted(FUNCIONES))})")
        if isinstance(nodo, ast.Attribute) and not isinstance(nodo.value, ast.Name):
            _err("FORMULA", f"referencia inválida en «{texto}»")
        if isinstance(nodo, ast.Constant) and not isinstance(nodo.value, (int, float, str)):
            _err("FORMULA", f"constante no permitida en «{texto}»")
    _CACHE_FORMULAS[texto] = arbol
    return arbol


def nombres_formula(texto):
    arbol = _compilar(texto)
    nombres, prefijos = set(), set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Attribute):
            nombres.add(f"{nodo.value.id}.{nodo.attr}")
            prefijos.add(id(nodo.value))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Name) and nodo.id not in FUNCIONES and id(nodo) not in prefijos:
            nombres.add(nodo.id)
    return nombres


def evaluar(texto, variables):
    """Evalúa una fórmula. Nombres desconocidos valen 0; la división por cero da 0 (como SI.ERROR)."""
    arbol = _compilar(texto)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return variables.get(n.id, 0)
        if isinstance(n, ast.Attribute):
            return variables.get(f"{n.value.id}.{n.attr}", 0)
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            a = num(a) if isinstance(a, str) else a
            b = num(b) if isinstance(b, str) else b
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.Not):
                return not v
            v = num(v) or 0
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BoolOp):
            valores = [ev(v) for v in n.values]
            return all(valores) if isinstance(n.op, ast.And) else any(valores)
        if isinstance(n, ast.Compare):
            izq = ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                der = ev(comp)
                if not _CMP[type(op)](izq, der):
                    return False
                izq = der
            return True
        if isinstance(n, ast.IfExp):
            return ev(n.body) if ev(n.test) else ev(n.orelse)
        if isinstance(n, ast.Call):
            return FUNCIONES[n.func.id](*[ev(a) for a in n.args])
        _err("FORMULA", "elemento no permitido")

    try:
        resultado = ev(arbol)
    except AmError:
        raise
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        return 0.0
    if isinstance(resultado, bool):
        return 1.0 if resultado else 0.0
    return num(resultado) if not isinstance(resultado, (int, float)) else float(resultado)


def validar_formula(texto, conocidos=None):
    """Devuelve {ok, nombres, desconocidos}. Lanza AmError si la sintaxis no es válida."""
    nombres = sorted(nombres_formula(texto))
    desconocidos = [n for n in nombres if conocidos is not None and n not in conocidos and not n.startswith("f.")]
    return {"ok": True, "nombres": nombres, "desconocidos": desconocidos}


# ============================================================================
# Catálogo de campos del parte diario (dinámico)
# ============================================================================
def _columnas_parte(conn):
    return [(f[1], (f[2] or "").upper()) for f in conn.execute(f"PRAGMA table_info({TABLA_PARTES})").fetchall()]


def campos_parte(conn):
    """Todas las columnas de horas_maquinas, con etiqueta editable (am_campos) y si son numéricas."""
    meta = {f["campo"]: f for f in _filas(conn, "SELECT campo, etiqueta, formato FROM am_campos")}
    columnas = _columnas_parte(conn)
    salida = []
    for nombre, tipo in columnas:
        if tipo in ("REAL", "INTEGER", "NUMERIC", "FLOAT", "DOUBLE"):
            numerico = nombre != "id"
        else:
            muestra = [x[0] for x in conn.execute(
                f'SELECT "{nombre}" FROM {TABLA_PARTES} WHERE TRIM(COALESCE("{nombre}", "")) <> "" ORDER BY id DESC LIMIT 200'
            ).fetchall()]
            numericos = sum(1 for v in muestra if num(v) is not None)
            numerico = bool(muestra) and numericos >= 0.8 * len(muestra) and not nombre.startswith("fecha")
        m = meta.get(nombre) or {}
        salida.append({
            "campo": nombre, "tipo_sql": tipo, "numerico": numerico,
            "etiqueta": m.get("etiqueta") or nombre.replace("_", " ").capitalize(),
            "formato": m.get("formato") or ("decimal" if numerico else ("fecha" if nombre.startswith("fecha") else "texto")),
        })
    return salida


def guardar_campo(conn, campo, etiqueta, formato):
    if campo not in {c for c, _ in _columnas_parte(conn)}:
        _err("NO_EXISTE", f"el parte diario no tiene el campo {campo}")
    conn.execute(
        "INSERT INTO am_campos (campo, etiqueta, formato) VALUES (?, ?, ?) "
        "ON CONFLICT(campo) DO UPDATE SET etiqueta = excluded.etiqueta, formato = excluded.formato",
        (campo, _texto(etiqueta) or None, _texto(formato) or None),
    )
    return {"ok": True}


# ============================================================================
# Plantilla inicial "Foja SMG 470-069" (DATOS de configuración, editables desde pantalla)
# ============================================================================
PLANTILLA_INICIAL_NOMBRE = "Foja SMG 470-069"
PLANTILLA_INICIAL_DESCRIPCION = (
    "Foja de medición mensual de mantenimiento de caminos y distribución de combustible tal como se trabaja hoy en Excel: "
    "período 21 al 20, horas por horómetro, 230 hs mínimas por unidad con tope, personal fijo, gasoil a descontar."
)
_ITEM_EQUIPO = {"medida": "horas", "modo": "PARTE", "regla": {"tipo": "TOPE", "minimo": 230, "ambito": "UNIDAD"}}
_ITEM_PERSONAL = {"modo": "FIJO", "fijo": {"valor": 300, "por": "UNIDAD"}, "regla": {"tipo": "SIN_MINIMO", "ambito": "ITEM"}}
PLANTILLA_INICIAL = {
    "periodo": {"dia_corte": 20, "duracion": "MENSUAL", "dias": 30},
    "contrato_defecto": {"duracion_meses": 14, "contratista": "SMG S.R.L.", "cuit": "30-71102383-2"},
    "fuente": {
        "campo_fecha": "fecha_parte", "campo_equipo": "ci", "campo_operador": "legajo_empleado",
        "campo_turno": "turno", "campo_nro_parte": "nro_parte",
        # Filtros adicionales sobre los partes (además de los equipos asignados). Ej.: {"campo": "cliente", "op": "contiene", "valor": "EXAR"}
        "filtros": [],
    },
    "turnos": {"DIA": ["DIA", "DÍA", "D", "DIURNO", "TD", "MAÑANA", ""], "NOCHE": ["NOCHE", "N", "NOCTURNO", "TN"]},
    "medidas": {
        "horas": {"titulo": "Horas (horómetro)", "unidad": "hs", "tipo": "DIFERENCIA", "inicio": "hs_inicio", "fin": "hs_final"},
        "horas_cargadas": {"titulo": "Horas cargadas en el parte", "unidad": "hs", "tipo": "CAMPO", "campo": "total_hs"},
        "jornada": {"titulo": "Jornada fija", "unidad": "hs", "tipo": "FIJO", "valor": 10},
        "km": {"titulo": "Kilómetros", "unidad": "km", "tipo": "DIFERENCIA", "inicio": "km_inicio", "fin": "km_final"},
        "viajes": {"titulo": "Viajes", "unidad": "viajes", "tipo": "CAMPO", "campo": "viajes"},
        "litros": {"titulo": "Gasoil", "unidad": "L", "tipo": "CAMPO", "campo": "gasoil"},
        "m3": {"titulo": "Material transportado", "unidad": "m³", "tipo": "FORMULA", "formula": "viajes * f.carga_m3"},
        "agua_lts": {"titulo": "Agua regada", "unidad": "L", "tipo": "FORMULA", "formula": "viajes * f.capacidad_lts"},
        "dias": {"titulo": "Días trabajados", "unidad": "días", "tipo": "FIJO", "valor": 1},
    },
    "factores": [
        {"codigo": "carga_m3", "titulo": "Índice de carga (m³ por viaje)", "unidad": "m³"},
        {"codigo": "capacidad_lts", "titulo": "Capacidad de riego (litros por viaje)", "unidad": "L"},
    ],
    "item_defecto": {"medida": "horas", "modo": "PARTE", "regla": {"tipo": "SIN_MINIMO", "ambito": "ITEM"},
                     "fijo": {"valor": 0, "por": "UNIDAD"}, "facturar": ["minimas", "extras"]},
    "acumulados": {"permite_superar_100": True},
    "emitir_importes": False,
    "conceptos": [
        {"codigo": "GASOIL", "titulo": "Gasoil a descontar", "base": "litros", "unidad": "L", "signo": -1,
         "precio": None, "activo": True, "aplica_secciones": [], "aplica_items": []},
    ],
    "indicadores": [
        {"codigo": "consumo", "titulo": "Consumo L/h", "formula": "litros / registrado", "decimales": 2},
    ],
    "validaciones": [
        {"codigo": "FINAL_MENOR_INICIO", "activa": True, "severidad": "AVISO", "param": {"pares": [["hs_inicio", "hs_final"], ["km_inicio", "km_final"]]}},
        {"codigo": "INICIO_DISTINTO_ANTERIOR", "activa": True, "severidad": "AVISO", "param": {"pares": [["hs_inicio", "hs_final"]], "tolerancia": 0}},
        {"codigo": "DUPLICADO_EQUIPO_FECHA_TURNO", "activa": True, "severidad": "AVISO", "param": {}},
        {"codigo": "DIAS_SIN_PARTE", "activa": True, "severidad": "AVISO", "param": {}},
        {"codigo": "NRO_PARTE_REPETIDO", "activa": True, "severidad": "AVISO", "param": {}},
        {"codigo": "CANTIDAD_MAXIMA", "activa": True, "severidad": "AVISO", "param": {"maximo": 24}},
        {"codigo": "EQUIPO_SIN_ITEM", "activa": True, "severidad": "AVISO", "param": {}},
        {"codigo": "EQUIPO_INACTIVO", "activa": True, "severidad": "AVISO", "param": {"campo": "operativo", "activos": ["SI", "SÍ", ""]}},
        {"codigo": "FECHA_INVALIDA", "activa": True, "severidad": "AVISO", "param": {}},
        {"codigo": "SUPERA_CONTRATO", "activa": False, "severidad": "AVISO", "param": {}},
    ],
    "flujo": {
        "estados": [
            {"codigo": "BORRADOR", "titulo": "Borrador", "permiso": "generar", "congela": False},
            {"codigo": "EN_REVISION", "titulo": "En revisión", "permiso": "generar", "congela": False},
            {"codigo": "APROBADA", "titulo": "Aprobada (Gerencia de Operaciones)", "permiso": "aprobar", "congela": True},
            {"codigo": "EMITIDA", "titulo": "Emitida", "permiso": "emitir", "congela": True},
        ],
        "precarga_desde": ["APROBADA", "EMITIDA"],
    },
    "encabezado": {
        "titulo": "FOJA DE MEDICIÓN N°: {numero}",
        "logo": "/Imagenes/09-smg.png",
        "campos": [
            {"etiqueta": "PROYECTO", "valor": "{contrato.proyecto}"},
            {"etiqueta": "CONTRATO", "valor": "{contrato.numero_contrato}"},
            {"etiqueta": "CONTRATISTA", "valor": "{contrato.contratista}"},
            {"etiqueta": "CUIT", "valor": "{contrato.cuit}"},
            {"etiqueta": "Fecha", "valor": "{fecha}"},
            {"etiqueta": "Rev.", "valor": "{revision}"},
            {"etiqueta": "Período", "valor": "{periodo}"},
        ],
    },
    "firmas": [
        {"titulo": "V°B°", "cargo": "CONTRATISTA", "empresa": "{contrato.contratista}"},
        {"titulo": "APROBACIÓN", "cargo": "GERENCIA DE OPERACIONES", "empresa": "{contrato.comitente}"},
    ],
    "reportes": [
        {"codigo": "certificado", "titulo": "Certificado", "activo": True, "nivel": "ITEM", "agrupar": "seccion",
         "titulo_seccion": "{seccion_titulo}", "una_tabla": True, "encabezado": True, "firmas": True, "mostrar_conceptos": False,
         "columnas": [
             {"valor": "codigo", "titulo": "Ítem", "formato": "texto"},
             {"valor": "descripcion", "titulo": "Descripción", "formato": "texto"},
             {"valor": "unidad", "titulo": "Unidad", "formato": "texto"},
             {"valor": "contratado", "titulo": "Cantidad", "grupo": "Contrato", "formato": "entero"},
             {"valor": "acumulado", "titulo": "Cantidad", "grupo": "Acumulado a la fecha", "formato": "entero"},
             {"valor": "acumulado_pct", "titulo": "%", "grupo": "Acumulado a la fecha", "formato": "porcentaje"},
             {"valor": "anterior", "titulo": "Cantidad", "grupo": "Acumulado anterior", "formato": "entero"},
             {"valor": "anterior_pct", "titulo": "%", "grupo": "Acumulado anterior", "formato": "porcentaje"},
             {"valor": "certificar", "titulo": "Cantidad total a certificar", "grupo": "Certificado actual", "formato": "entero"},
             {"valor": "actual_pct", "titulo": "%", "grupo": "Certificado actual", "formato": "porcentaje"},
             {"valor": "minimas", "titulo": "Hs mínimas", "grupo": "Composición del total de las hs", "formato": "entero"},
             {"valor": "extras", "titulo": "Hs extras", "grupo": "Composición del total de las hs", "formato": "entero"},
         ]},
        {"codigo": "resumen", "titulo": "Resumen", "activo": True, "nivel": "UNIDAD", "agrupar": "", "una_tabla": True,
         "encabezado": True, "firmas": False, "mostrar_conceptos": True,
         "subtitulo": "RESUMEN HS Y COMBUSTIBLE",
         "columnas": [
             {"valor": "item_descripcion", "titulo": "Descripción", "formato": "texto"},
             {"valor": "equipos", "titulo": "Equipo(s)", "formato": "texto"},
             {"valor": "item_unidad", "titulo": "Unidad", "formato": "texto"},
             {"valor": "registrado", "titulo": "Hs registradas", "grupo": "Certificado actual", "formato": "entero", "total": True},
             {"valor": "minimas", "titulo": "Hs mínimas", "grupo": "Certificado actual", "formato": "entero", "total": True},
             {"valor": "extras", "titulo": "Hs extras", "grupo": "Certificado actual", "formato": "entero", "total": True},
             {"valor": "certificar", "titulo": "Certificar hs", "grupo": "Certificado actual", "formato": "entero", "total": True},
             {"valor": "med_litros", "titulo": "Combustible", "formato": "entero", "total": True},
             {"valor": "ind_consumo", "titulo": "Consumo L/h", "formato": "decimal2"},
         ]},
        {"codigo": "detalle", "titulo": "Detalle por equipo", "activo": True, "nivel": "PARTE", "agrupar": "unidad",
         "titulo_seccion": "{item_codigo} · {item_descripcion} — {equipos}", "una_tabla": False, "hoja_por_seccion": True,
         "encabezado": True, "firmas": True, "mostrar_conceptos": False,
         "columnas": [
             {"valor": "fecha", "titulo": "Fecha parte", "formato": "fecha"},
             {"valor": "nro_parte", "titulo": "N° parte", "formato": "texto"},
             {"valor": "maquinista", "titulo": "Maquinista", "formato": "texto"},
             {"valor": "descripcion_trabajos", "titulo": "Descripción de trabajos realizados", "formato": "texto"},
             {"valor": "hs_inicio", "titulo": "Hs inicio", "formato": "entero"},
             {"valor": "hs_final", "titulo": "Hs final", "formato": "entero"},
             {"valor": "cantidad", "titulo": "Total hs", "formato": "entero", "total": True},
             {"valor": "med_litros", "titulo": "Gasoil", "formato": "entero", "total": True},
         ]},
        {"codigo": "avance", "titulo": "Avance de obra", "activo": True, "nivel": "EQUIPO", "agrupar": "", "una_tabla": True,
         "encabezado": True, "firmas": False, "mostrar_conceptos": False,
         "columnas": [
             {"valor": "item_codigo", "titulo": "Ítem", "formato": "texto"},
             {"valor": "equipo", "titulo": "Equipo", "formato": "texto"},
             {"valor": "equipo_tipo", "titulo": "Tipo", "formato": "texto"},
             {"valor": "registrado", "titulo": "Cantidad", "formato": "decimal2", "total": True},
             {"valor": "med_viajes", "titulo": "Viajes", "formato": "entero", "total": True},
             {"valor": "med_m3", "titulo": "m³ transportados", "formato": "decimal2", "total": True},
             {"valor": "med_agua_lts", "titulo": "Litros de agua", "formato": "entero", "total": True},
             {"valor": "med_km", "titulo": "Km", "formato": "entero", "total": True},
             {"valor": "med_litros", "titulo": "Gasoil", "formato": "entero", "total": True},
         ]},
    ],
    # Estructura que se copia al crear un contrato desde esta plantilla (luego se edita en el contrato).
    "estructura": [
        {"codigo": "I", "titulo": "I - EQUIPOS PARA MANTENIMIENTO DE CAMINOS Y DISTRIBUCIÓN DE COMBUSTIBLE", "items": [
            {"codigo": "I.1", "descripcion": "Motoniveladora 180 HP", "unidad": "hs", "unidades": 1, "cantidad_mensual": 230, "config": _ITEM_EQUIPO},
            {"codigo": "I.2", "descripcion": "Camión Regador de 17.000 L (6 x 4)", "unidad": "hs", "unidades": 1, "cantidad_mensual": 230, "config": _ITEM_EQUIPO},
            {"codigo": "I.3", "descripcion": "Compactador de 11Tn", "unidad": "hs", "unidades": 1, "cantidad_mensual": 230, "config": _ITEM_EQUIPO},
            {"codigo": "I.4", "descripcion": "Cargador Frontal 160 HP", "unidad": "hs", "unidades": 1, "cantidad_mensual": 230, "config": _ITEM_EQUIPO},
            {"codigo": "I.5", "descripcion": "Camión Volquete 6x4", "unidad": "hs", "unidades": 1, "cantidad_mensual": 230, "config": _ITEM_EQUIPO},
            {"codigo": "I.6", "descripcion": "Excavadora PC 200 o similar", "unidad": "hs", "unidades": 1, "cantidad_mensual": 230, "config": _ITEM_EQUIPO},
            {"codigo": "I.7", "descripcion": "Camioneta de Apoyo (4 x 4) (2 unidades)", "unidad": "hs", "unidades": 2, "cantidad_mensual": 230,
             "cantidad_contratada": 8400, "config": fusionar(_ITEM_EQUIPO, {"medida": "horas_cargadas"})},
            {"codigo": "I.8", "descripcion": "Camión cisterna para reparto de combustible de 10.000 L (4 x 4)", "unidad": "hs", "unidades": 1, "cantidad_mensual": 230, "config": _ITEM_EQUIPO},
            {"codigo": "I.9", "descripcion": "Camión regador de 10.000 L (Turno día)", "unidad": "hs", "unidades": 1, "cantidad_mensual": 230, "config": _ITEM_EQUIPO},
            {"codigo": "I.10", "descripcion": "Camión regador de 10.000 L (Turno noche)", "unidad": "hs", "unidades": 1, "cantidad_mensual": 230, "config": _ITEM_EQUIPO},
        ]},
        {"codigo": "II", "titulo": "II - PERSONAL DE APOYO A MANTENIMIENTO DE CAMINO", "items": [
            {"codigo": "II.1", "descripcion": "Supervisión", "unidad": "hs", "unidades": 1, "cantidad_mensual": 300, "config": _ITEM_PERSONAL},
            {"codigo": "II.2", "descripcion": "Técnico HyS", "unidad": "hs", "unidades": 1, "cantidad_mensual": 300, "config": _ITEM_PERSONAL},
            {"codigo": "II.3", "descripcion": "Ayudante (2)", "unidad": "hs", "unidades": 2, "cantidad_mensual": 300, "config": _ITEM_PERSONAL},
        ]},
    ],
}


# ============================================================================
# Eventos
# ============================================================================
def evento(conn, entidad, entidad_id, nombre, usuario, detalle=None):
    conn.execute(
        "INSERT INTO am_eventos (entidad, entidad_id, evento, usuario, fecha, detalle_json) VALUES (?, ?, ?, ?, ?, ?)",
        (entidad, entidad_id, nombre, usuario or "sistema", _ahora(), _dump(detalle) if detalle is not None else None),
    )


# ============================================================================
# Plantillas
# ============================================================================
def listar_plantillas(conn):
    filas = _filas(conn, "SELECT id, nombre, descripcion, activa, actualizado_en, creado_en FROM am_plantillas ORDER BY nombre")
    for f in filas:
        f["contratos"] = conn.execute("SELECT COUNT(*) FROM am_contratos WHERE plantilla_id = ?", (f["id"],)).fetchone()[0]
    return filas


def obtener_plantilla(conn, plantilla_id):
    p = _fila(conn, "SELECT * FROM am_plantillas WHERE id = ?", (plantilla_id,))
    if not p:
        _err("NO_EXISTE", "plantilla inexistente")
    p["config"] = _json(p.pop("config_json"))
    return p


def _validar_config(cfg):
    if not isinstance(cfg, dict):
        _err("DATOS", "la configuración debe ser un objeto")
    for codigo, m in (cfg.get("medidas") or {}).items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", codigo):
            _err("DATOS", f"código de medida inválido «{codigo}» (letras, números y _)")
        if (m or {}).get("tipo") == "FORMULA":
            _compilar(m.get("formula"))
    for ind in cfg.get("indicadores") or []:
        _compilar(ind.get("formula"))
    for c in cfg.get("conceptos") or []:
        _compilar(c.get("base") or "0")
    estados = ((cfg.get("flujo") or {}).get("estados")) or []
    if estados:
        codigos = [e.get("codigo") for e in estados]
        if len(set(codigos)) != len(codigos) or "ANULADA" in codigos:
            _err("DATOS", "los estados del flujo deben ser únicos y no pueden llamarse ANULADA")
    for rep in cfg.get("reportes") or []:
        if rep.get("nivel") not in NIVELES_REPORTE:
            _err("DATOS", f"reporte {rep.get('codigo')}: nivel debe ser uno de {', '.join(NIVELES_REPORTE)}")
        for col in rep.get("columnas") or []:
            if _texto(col.get("valor")).startswith("="):
                _compilar(col["valor"])


def guardar_plantilla(conn, datos, usuario, plantilla_id=None):
    nombre = _texto(datos.get("nombre"))
    if not nombre:
        _err("DATOS", "la plantilla necesita un nombre")
    cfg = datos.get("config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except ValueError as exc:
            _err("DATOS", f"configuración JSON inválida: {exc}")
    _validar_config(cfg or {})
    if plantilla_id:
        obtener_plantilla(conn, plantilla_id)
        conn.execute(
            "UPDATE am_plantillas SET nombre = ?, descripcion = ?, config_json = ?, activa = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
            (nombre, _texto(datos.get("descripcion")), _dump(cfg or {}), 1 if datos.get("activa", True) else 0, usuario, _ahora(), plantilla_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO am_plantillas (nombre, descripcion, config_json, activa, creado_por, creado_en) VALUES (?, ?, ?, ?, ?, ?)",
            (nombre, _texto(datos.get("descripcion")), _dump(cfg or {}), 1 if datos.get("activa", True) else 0, usuario, _ahora()),
        )
        plantilla_id = cur.lastrowid
    evento(conn, "PLANTILLA", plantilla_id, "GUARDADA", usuario)
    return obtener_plantilla(conn, plantilla_id)


def copiar_plantilla(conn, plantilla_id, nombre, usuario):
    p = obtener_plantilla(conn, plantilla_id)
    return guardar_plantilla(conn, {"nombre": nombre or f"{p['nombre']} (copia)", "descripcion": p["descripcion"],
                                    "config": p["config"], "activa": True}, usuario)


# ============================================================================
# Contratos y estructura
# ============================================================================
CAMPOS_CONTRATO = ("nombre", "numero_contrato", "proyecto", "contratista", "cuit", "comitente", "fecha_inicio",
                   "duracion_meses", "saldo_hasta_numero")


def listar_contratos(conn):
    return _filas(conn, """
        SELECT c.id, c.nombre, c.numero_contrato, c.proyecto, c.activo, c.plantilla_id, p.nombre AS plantilla,
               c.seguimiento_cc_id, c.orden_compra_cliente_id, o.orden_compra AS oc_texto,
               (SELECT COUNT(*) FROM am_actas a WHERE a.contrato_id = c.id) AS actas
        FROM am_contratos c JOIN am_plantillas p ON p.id = c.plantilla_id
        LEFT JOIN Orden_de_compra_cliente o ON o.id = c.orden_compra_cliente_id
        ORDER BY c.activo DESC, c.nombre""")


def _contrato_base(conn, contrato_id):
    c = _fila(conn, "SELECT * FROM am_contratos WHERE id = ?", (contrato_id,))
    if not c:
        _err("NO_EXISTE", "contrato inexistente")
    c["config"] = _json(c.pop("config_json"))
    return c


def obtener_contrato(conn, contrato_id):
    c = _contrato_base(conn, contrato_id)
    plantilla = obtener_plantilla(conn, c["plantilla_id"])
    c["plantilla"] = {"id": plantilla["id"], "nombre": plantilla["nombre"]}
    c["config_efectiva"] = fusionar(plantilla["config"], c["config"])
    c["secciones"] = _filas(conn, "SELECT * FROM am_secciones WHERE contrato_id = ? ORDER BY orden, id", (contrato_id,))
    items = _filas(conn, "SELECT * FROM am_items WHERE contrato_id = ? ORDER BY orden, id", (contrato_id,))
    for it in items:
        it["config"] = _json(it.pop("config_json"))
        it["asignaciones"] = _filas(conn, "SELECT * FROM am_asignaciones WHERE item_id = ? ORDER BY unidad, desde, id", (it["id"],))
        for a in it["asignaciones"]:
            a["config"] = _json(a.pop("config_json"))
        it["oc"] = _filas(conn, """
            SELECT l.salida, l.item_oc_cliente_id, i.numero, i.descripcion, i.unidad, i.precio, i.cantidad, o.orden_compra
            FROM am_item_oc l JOIN items_oc_cliente i ON i.id = l.item_oc_cliente_id
            JOIN Orden_de_compra_cliente o ON o.id = i.orden_compra_cliente_id WHERE l.item_id = ?""", (it["id"],))
    c["items"] = items
    c["factores"] = _filas(conn, "SELECT * FROM am_factores WHERE contrato_id = ? ORDER BY recurso_codigo, codigo, desde", (contrato_id,))
    c["precios"] = _filas(conn, "SELECT * FROM am_precios WHERE contrato_id = ? ORDER BY item_id, salida, concepto, desde", (contrato_id,))
    c["seguimiento"] = _fila(conn, """
        SELECT s.id, s.cliente_codigo, s.cotizacion, s.centro_costo_id, cc.nombre AS centro_costo, cl.razon_social AS cliente
        FROM seguimiento_cc s LEFT JOIN centros_costos cc ON cc.id = s.centro_costo_id
        LEFT JOIN clientes cl ON cl.codigo = s.cliente_codigo WHERE s.id = ?""", (c["seguimiento_cc_id"],)) if c["seguimiento_cc_id"] else None
    return c


def _verificar_oc(conn, seguimiento_id, oc_id):
    if oc_id:
        o = _fila(conn, "SELECT id, seguimiento_cc_id FROM Orden_de_compra_cliente WHERE id = ?", (oc_id,))
        if not o:
            _err("NO_EXISTE", "orden de compra inexistente")
        if seguimiento_id and o["seguimiento_cc_id"] != seguimiento_id:
            _err("DATOS", "la orden de compra no pertenece al seguimiento elegido")
        return o["seguimiento_cc_id"]
    return seguimiento_id


def guardar_contrato(conn, datos, usuario, contrato_id=None):
    nombre = _texto(datos.get("nombre"))
    if not nombre:
        _err("DATOS", "el contrato necesita un nombre")
    plantilla_id = int(datos.get("plantilla_id") or 0)
    plantilla = obtener_plantilla(conn, plantilla_id)
    seguimiento_id = int(datos["seguimiento_cc_id"]) if datos.get("seguimiento_cc_id") else None
    if seguimiento_id and not _fila(conn, "SELECT id FROM seguimiento_cc WHERE id = ?", (seguimiento_id,)):
        _err("NO_EXISTE", "seguimiento inexistente")
    oc_id = int(datos["orden_compra_cliente_id"]) if datos.get("orden_compra_cliente_id") else None
    seguimiento_id = _verificar_oc(conn, seguimiento_id, oc_id)
    cfg = datos.get("config") or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg or "{}")
        except ValueError as exc:
            _err("DATOS", f"ajustes JSON inválidos: {exc}")
    _validar_config(fusionar(plantilla["config"], cfg))
    defecto = plantilla["config"].get("contrato_defecto") or {}
    valores = {k: datos.get(k) if datos.get(k) not in (None, "") else (defecto.get(k) if not contrato_id else None)
               for k in CAMPOS_CONTRATO}
    valores["nombre"] = nombre
    valores["fecha_inicio"] = _iso(valores["fecha_inicio"], "fecha de inicio") if valores.get("fecha_inicio") else None
    valores["duracion_meses"] = num(valores.get("duracion_meses"))
    valores["saldo_hasta_numero"] = int(num(valores.get("saldo_hasta_numero")) or 0)
    if contrato_id:
        _contrato_base(conn, contrato_id)
        sets = ", ".join(f"{k} = ?" for k in CAMPOS_CONTRATO)
        conn.execute(
            f"UPDATE am_contratos SET {sets}, plantilla_id = ?, seguimiento_cc_id = ?, orden_compra_cliente_id = ?, config_json = ?, "
            "activo = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
            [valores[k] for k in CAMPOS_CONTRATO] + [plantilla_id, seguimiento_id, oc_id, _dump(cfg),
                                                     1 if datos.get("activo", True) else 0, usuario, _ahora(), contrato_id],
        )
        evento(conn, "CONTRATO", contrato_id, "ACTUALIZADO", usuario)
    else:
        cols = ", ".join(CAMPOS_CONTRATO)
        cur = conn.execute(
            f"INSERT INTO am_contratos ({cols}, plantilla_id, seguimiento_cc_id, orden_compra_cliente_id, config_json, activo, creado_por, creado_en) "
            f"VALUES ({', '.join('?' for _ in CAMPOS_CONTRATO)}, ?, ?, ?, ?, 1, ?, ?)",
            [valores[k] for k in CAMPOS_CONTRATO] + [plantilla_id, seguimiento_id, oc_id, _dump(cfg), usuario, _ahora()],
        )
        contrato_id = cur.lastrowid
        if datos.get("copiar_estructura", True):
            for orden_s, sec in enumerate(plantilla["config"].get("estructura") or []):
                sid = conn.execute("INSERT INTO am_secciones (contrato_id, codigo, titulo, orden) VALUES (?, ?, ?, ?)",
                                   (contrato_id, sec["codigo"], sec.get("titulo") or sec["codigo"], orden_s)).lastrowid
                for orden_i, it in enumerate(sec.get("items") or []):
                    conn.execute(
                        "INSERT INTO am_items (contrato_id, seccion_id, codigo, descripcion, unidad, unidades, cantidad_mensual, "
                        "cantidad_contratada, config_json, orden) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (contrato_id, sid, it["codigo"], it["descripcion"], it.get("unidad"), it.get("unidades") or 1,
                         it.get("cantidad_mensual"), it.get("cantidad_contratada"), _dump(it.get("config") or {}),
                         orden_s * 1000 + orden_i))
        evento(conn, "CONTRATO", contrato_id, "CREADO", usuario, {"plantilla_id": plantilla_id})
    return obtener_contrato(conn, contrato_id)


def guardar_seccion(conn, contrato_id, datos, usuario, seccion_id=None):
    _contrato_base(conn, contrato_id)
    codigo, titulo = _texto(datos.get("codigo")), _texto(datos.get("titulo"))
    if not codigo or not titulo:
        _err("DATOS", "la sección necesita código y título")
    orden = int(num(datos.get("orden")) or 0)
    if seccion_id:
        conn.execute("UPDATE am_secciones SET codigo = ?, titulo = ?, orden = ? WHERE id = ? AND contrato_id = ?",
                     (codigo, titulo, orden, seccion_id, contrato_id))
    else:
        seccion_id = conn.execute("INSERT INTO am_secciones (contrato_id, codigo, titulo, orden) VALUES (?, ?, ?, ?)",
                                  (contrato_id, codigo, titulo, orden)).lastrowid
    evento(conn, "CONTRATO", contrato_id, "SECCION_GUARDADA", usuario, {"seccion_id": seccion_id})
    return {"ok": True, "id": seccion_id}


def eliminar_seccion(conn, contrato_id, seccion_id, usuario):
    conn.execute("DELETE FROM am_secciones WHERE id = ? AND contrato_id = ?", (seccion_id, contrato_id))
    evento(conn, "CONTRATO", contrato_id, "SECCION_ELIMINADA", usuario, {"seccion_id": seccion_id})
    return {"ok": True}


CAMPOS_ITEM = ("seccion_id", "codigo", "descripcion", "unidad", "unidades", "cantidad_mensual", "cantidad_contratada",
               "saldo_inicial", "meta", "meta_medida", "orden", "activo")


def guardar_item(conn, contrato_id, datos, usuario, item_id=None):
    contrato = _contrato_base(conn, contrato_id)
    if not _texto(datos.get("codigo")) or not _texto(datos.get("descripcion")):
        _err("DATOS", "el ítem necesita código y descripción")
    cfg = datos.get("config") or {}
    if isinstance(cfg, str):
        cfg = _json(cfg)
    regla = cfg.get("regla") or {}
    if regla.get("tipo") == "FORMULA":
        for f in (regla.get("formulas") or {}).values():
            _compilar(f)
    v = {
        "seccion_id": int(datos["seccion_id"]) if datos.get("seccion_id") else None,
        "codigo": _texto(datos.get("codigo")), "descripcion": _texto(datos.get("descripcion")),
        "unidad": _texto(datos.get("unidad")) or None, "unidades": num(datos.get("unidades")) or 1,
        "cantidad_mensual": num(datos.get("cantidad_mensual")), "cantidad_contratada": num(datos.get("cantidad_contratada")),
        "saldo_inicial": num(datos.get("saldo_inicial")) or 0, "meta": num(datos.get("meta")),
        "meta_medida": _texto(datos.get("meta_medida")) or None, "orden": int(num(datos.get("orden")) or 0),
        "activo": 0 if datos.get("activo") in (False, 0, "0") else 1,
    }
    if v["seccion_id"] and not _fila(conn, "SELECT id FROM am_secciones WHERE id = ? AND contrato_id = ?", (v["seccion_id"], contrato["id"])):
        _err("DATOS", "la sección no pertenece al contrato")
    if item_id:
        _item(conn, contrato_id, item_id)
        sets = ", ".join(f"{k} = ?" for k in CAMPOS_ITEM)
        conn.execute(f"UPDATE am_items SET {sets}, config_json = ? WHERE id = ?", [v[k] for k in CAMPOS_ITEM] + [_dump(cfg), item_id])
    else:
        item_id = conn.execute(
            f"INSERT INTO am_items ({', '.join(CAMPOS_ITEM)}, contrato_id, config_json) VALUES ({', '.join('?' for _ in CAMPOS_ITEM)}, ?, ?)",
            [v[k] for k in CAMPOS_ITEM] + [contrato_id, _dump(cfg)]).lastrowid
    if "oc" in datos:
        conn.execute("DELETE FROM am_item_oc WHERE item_id = ?", (item_id,))
        for salida, oc_item in (datos.get("oc") or {}).items():
            if not oc_item:
                continue
            if salida not in SALIDAS:
                _err("DATOS", f"salida inválida {salida}")
            _verificar_item_oc(conn, contrato, int(oc_item))
            conn.execute("INSERT INTO am_item_oc (item_id, salida, item_oc_cliente_id) VALUES (?, ?, ?)", (item_id, salida, int(oc_item)))
    evento(conn, "CONTRATO", contrato_id, "ITEM_GUARDADO", usuario, {"item_id": item_id, "codigo": v["codigo"]})
    return {"ok": True, "id": item_id}


def _verificar_item_oc(conn, contrato, item_oc_id):
    fila = _fila(conn, """SELECT i.id, o.seguimiento_cc_id, o.id AS oc_id FROM items_oc_cliente i
                          JOIN Orden_de_compra_cliente o ON o.id = i.orden_compra_cliente_id WHERE i.id = ?""", (item_oc_id,))
    if not fila:
        _err("NO_EXISTE", "ítem de OC inexistente")
    if contrato.get("seguimiento_cc_id") and fila["seguimiento_cc_id"] != contrato["seguimiento_cc_id"]:
        _err("DATOS", "el ítem de OC no pertenece al seguimiento del contrato")


def _item(conn, contrato_id, item_id):
    it = _fila(conn, "SELECT * FROM am_items WHERE id = ? AND contrato_id = ?", (item_id, contrato_id))
    if not it:
        _err("NO_EXISTE", "ítem inexistente")
    return it


def eliminar_item(conn, contrato_id, item_id, usuario):
    _item(conn, contrato_id, item_id)
    conn.execute("DELETE FROM am_items WHERE id = ?", (item_id,))
    evento(conn, "CONTRATO", contrato_id, "ITEM_ELIMINADO", usuario, {"item_id": item_id})
    return {"ok": True}


def guardar_asignacion(conn, contrato_id, datos, usuario, asignacion_id=None):
    item_id = int(datos.get("item_id") or 0)
    _item(conn, contrato_id, item_id)
    tipo = _norm(datos.get("recurso_tipo")) or "EQUIPO"
    codigo = _texto(datos.get("recurso_codigo"))
    if tipo not in ("EQUIPO", "OPERADOR") or not codigo:
        _err("DATOS", "la asignación necesita tipo (EQUIPO u OPERADOR) y código")
    if tipo == "EQUIPO" and not conn.execute("SELECT 1 FROM vehiculos WHERE UPPER(TRIM(codigo)) = ?", (codigo.upper(),)).fetchone():
        _err("NO_EXISTE", f"el equipo {codigo} no existe en Vehículos/Equipos")
    if tipo == "OPERADOR" and not conn.execute("SELECT 1 FROM personal WHERE TRIM(legajo) = ?", (codigo,)).fetchone():
        _err("NO_EXISTE", f"el legajo {codigo} no existe en Personal")
    desde = _iso(datos.get("desde"), "desde")
    hasta = _iso(datos.get("hasta"), "hasta") if datos.get("hasta") else None
    if hasta and hasta < desde:
        _err("DATOS", "la vigencia termina antes de empezar")
    cfg = datos.get("config") or {}
    if isinstance(cfg, str):
        cfg = _json(cfg)
    turno = _norm(datos.get("turno")) or None
    unidad = int(num(datos.get("unidad")) or 1)
    if asignacion_id:
        conn.execute("""UPDATE am_asignaciones SET item_id = ?, recurso_tipo = ?, recurso_codigo = ?, unidad = ?, turno = ?, desde = ?,
                        hasta = ?, config_json = ? WHERE id = ? AND item_id IN (SELECT id FROM am_items WHERE contrato_id = ?)""",
                     (item_id, tipo, codigo.upper() if tipo == "EQUIPO" else codigo, unidad, turno, desde, hasta, _dump(cfg),
                      asignacion_id, contrato_id))
    else:
        asignacion_id = conn.execute(
            "INSERT INTO am_asignaciones (item_id, recurso_tipo, recurso_codigo, unidad, turno, desde, hasta, config_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, tipo, codigo.upper() if tipo == "EQUIPO" else codigo, unidad, turno, desde, hasta, _dump(cfg))).lastrowid
    evento(conn, "CONTRATO", contrato_id, "ASIGNACION_GUARDADA", usuario, {"asignacion_id": asignacion_id, "item_id": item_id, "recurso": codigo})
    return {"ok": True, "id": asignacion_id}


def mover_asignacion(conn, contrato_id, asignacion_id, item_destino_id, fecha, usuario):
    """Mueve un equipo a otro ítem desde una fecha: cierra la asignación vigente el día anterior y abre una nueva."""
    a = _fila(conn, """SELECT a.* FROM am_asignaciones a JOIN am_items i ON i.id = a.item_id
                       WHERE a.id = ? AND i.contrato_id = ?""", (asignacion_id, contrato_id))
    if not a:
        _err("NO_EXISTE", "asignación inexistente")
    _item(conn, contrato_id, item_destino_id)
    desde = _iso(fecha, "fecha")
    if desde <= a["desde"]:
        _err("DATOS", "la fecha debe ser posterior al inicio de la asignación actual")
    if a["hasta"] and desde > a["hasta"]:
        _err("DATOS", "la asignación ya terminó antes de esa fecha")
    cierre = (date.fromisoformat(desde) - timedelta(days=1)).isoformat()
    conn.execute("UPDATE am_asignaciones SET hasta = ? WHERE id = ?", (cierre, asignacion_id))
    nueva = conn.execute(
        "INSERT INTO am_asignaciones (item_id, recurso_tipo, recurso_codigo, unidad, turno, desde, hasta, config_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item_destino_id, a["recurso_tipo"], a["recurso_codigo"], a["unidad"], a["turno"], desde, a["hasta"], a["config_json"])).lastrowid
    evento(conn, "CONTRATO", contrato_id, "ASIGNACION_MOVIDA", usuario,
           {"desde_asignacion": asignacion_id, "nueva": nueva, "item_destino": item_destino_id, "fecha": desde})
    return {"ok": True, "id": nueva}


def eliminar_asignacion(conn, contrato_id, asignacion_id, usuario):
    conn.execute("DELETE FROM am_asignaciones WHERE id = ? AND item_id IN (SELECT id FROM am_items WHERE contrato_id = ?)",
                 (asignacion_id, contrato_id))
    evento(conn, "CONTRATO", contrato_id, "ASIGNACION_ELIMINADA", usuario, {"asignacion_id": asignacion_id})
    return {"ok": True}


def guardar_factor(conn, contrato_id, datos, usuario, factor_id=None):
    _contrato_base(conn, contrato_id)
    recurso, codigo, valor = _texto(datos.get("recurso_codigo")).upper(), _texto(datos.get("codigo")), num(datos.get("valor"))
    if not recurso or not codigo or valor is None:
        _err("DATOS", "el factor necesita equipo, código y valor")
    desde = _iso(datos.get("desde"), "desde")
    hasta = _iso(datos.get("hasta"), "hasta") if datos.get("hasta") else None
    if factor_id:
        conn.execute("UPDATE am_factores SET recurso_codigo = ?, codigo = ?, valor = ?, desde = ?, hasta = ? WHERE id = ? AND contrato_id = ?",
                     (recurso, codigo, valor, desde, hasta, factor_id, contrato_id))
    else:
        factor_id = conn.execute("INSERT INTO am_factores (contrato_id, recurso_codigo, codigo, valor, desde, hasta) VALUES (?, ?, ?, ?, ?, ?)",
                                 (contrato_id, recurso, codigo, valor, desde, hasta)).lastrowid
    evento(conn, "CONTRATO", contrato_id, "FACTOR_GUARDADO", usuario, {"factor_id": factor_id, "recurso": recurso, "codigo": codigo, "valor": valor})
    return {"ok": True, "id": factor_id}


def eliminar_factor(conn, contrato_id, factor_id, usuario):
    conn.execute("DELETE FROM am_factores WHERE id = ? AND contrato_id = ?", (factor_id, contrato_id))
    evento(conn, "CONTRATO", contrato_id, "FACTOR_ELIMINADO", usuario, {"factor_id": factor_id})
    return {"ok": True}


def guardar_precio(conn, contrato_id, datos, usuario, precio_id=None):
    _contrato_base(conn, contrato_id)
    item_id = int(datos["item_id"]) if datos.get("item_id") else None
    concepto = _texto(datos.get("concepto")) or None
    if (item_id is None) == (concepto is None):
        _err("DATOS", "el precio es de un ítem o de un concepto")
    if item_id:
        _item(conn, contrato_id, item_id)
    precio = num(datos.get("precio"))
    if precio is None:
        _err("DATOS", "precio inválido")
    salida = _texto(datos.get("salida")) or ("certificar" if item_id else None)
    desde = _iso(datos.get("desde"), "desde")
    hasta = _iso(datos.get("hasta"), "hasta") if datos.get("hasta") else None
    valores = (item_id, salida if item_id else None, concepto, precio, _texto(datos.get("moneda")) or None, desde, hasta)
    if precio_id:
        conn.execute("UPDATE am_precios SET item_id = ?, salida = ?, concepto = ?, precio = ?, moneda = ?, desde = ?, hasta = ? "
                     "WHERE id = ? AND contrato_id = ?", valores + (precio_id, contrato_id))
    else:
        precio_id = conn.execute("INSERT INTO am_precios (item_id, salida, concepto, precio, moneda, desde, hasta, contrato_id) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", valores + (contrato_id,)).lastrowid
    evento(conn, "CONTRATO", contrato_id, "PRECIO_GUARDADO", usuario, {"precio_id": precio_id})
    return {"ok": True, "id": precio_id}


def eliminar_precio(conn, contrato_id, precio_id, usuario):
    conn.execute("DELETE FROM am_precios WHERE id = ? AND contrato_id = ?", (precio_id, contrato_id))
    evento(conn, "CONTRATO", contrato_id, "PRECIO_ELIMINADO", usuario, {"precio_id": precio_id})
    return {"ok": True}


# ============================================================================
# Período y numeración
# ============================================================================
def _actas_vigentes(conn, contrato_id, congeladas=True):
    """Última revisión no anulada por número (opcionalmente solo congeladas)."""
    filas = _filas(conn, "SELECT * FROM am_actas WHERE contrato_id = ? AND estado <> 'ANULADA' ORDER BY numero, revision", (contrato_id,))
    por_numero = {}
    for f in filas:
        if congeladas and not f["congelada"]:
            continue
        por_numero[f["numero"]] = f
    return [por_numero[n] for n in sorted(por_numero)]


def sugerir_periodo(conn, contrato_id):
    contrato = obtener_contrato(conn, contrato_id)
    cfg = contrato["config_efectiva"]
    per = cfg.get("periodo") or {}
    ultimo = _fila(conn, "SELECT MAX(numero) AS n, MAX(periodo_hasta) AS h FROM am_actas WHERE contrato_id = ? AND estado <> 'ANULADA'", (contrato_id,))
    numero = max(int(ultimo["n"] or 0), int(contrato["saldo_hasta_numero"] or 0)) + 1
    duracion = _norm(per.get("duracion")) or "MENSUAL"
    corte = int(num(per.get("dia_corte")) or 0)
    if ultimo["h"]:
        desde = date.fromisoformat(ultimo["h"]) + timedelta(days=1)
    else:
        hoy = date.today()
        if duracion == "MENSUAL" and corte:
            fin = date(hoy.year, hoy.month, min(corte, calendar.monthrange(hoy.year, hoy.month)[1]))
            if fin >= hoy:
                fin = _sumar_meses(fin, -1)
            desde = _sumar_meses(fin, -1) + timedelta(days=1)
        else:
            desde = date(hoy.year, hoy.month, 1)
    if duracion == "MENSUAL":
        if corte:
            base = desde if desde.day <= corte else _sumar_meses(desde, 1)
            hasta = date(base.year, base.month, min(corte, calendar.monthrange(base.year, base.month)[1]))
            if hasta < desde:
                hasta = _sumar_meses(hasta, 1)
        else:
            hasta = _sumar_meses(desde, 1) - timedelta(days=1)
    elif duracion == "QUINCENAL":
        hasta = desde + timedelta(days=14)
    else:
        hasta = desde + timedelta(days=int(num(per.get("dias")) or 30) - 1)
    return {"numero": numero, "periodo_desde": desde.isoformat(), "periodo_hasta": hasta.isoformat(), "fecha": _hoy()}


# ============================================================================
# Motor de cálculo
# ============================================================================
def _vigente(desde, hasta, d):
    return desde <= d.isoformat() and (not hasta or d.isoformat() <= hasta)


def _solapa(desde, hasta, p_desde, p_hasta):
    return desde <= p_hasta and (not hasta or hasta >= p_desde)


def _mapa_turnos(cfg):
    mapa = {}
    for codigo, alias in (cfg.get("turnos") or {}).items():
        mapa[_norm(codigo)] = _norm(codigo)
        for a in alias or []:
            mapa[_norm(a)] = _norm(codigo)
    return mapa


def _medir(medidas_cfg, variables):
    """Calcula todas las medidas configuradas para un parte. Devuelve {codigo: valor|None}."""
    salida = {}
    for codigo, m in medidas_cfg.items():
        tipo = _norm((m or {}).get("tipo"))
        valor = None
        if tipo == "DIFERENCIA":
            ini, fin = num(variables.get(m.get("inicio"))), num(variables.get(m.get("fin")))
            if ini is not None and fin is not None and not (ini == 0 and fin == 0):
                valor = fin - ini
        elif tipo == "CAMPO":
            valor = num(variables.get(m.get("campo")))
        elif tipo == "FIJO":
            valor = num(m.get("valor"))
        elif tipo == "FORMULA":
            valor = evaluar(m.get("formula") or "0", {**variables, **{k: v for k, v in salida.items() if v is not None}})
        if valor is None and m.get("alternativa") and m["alternativa"] in salida:
            valor = salida[m["alternativa"]]
        salida[codigo] = r(valor, 6) if valor is not None else None
        variables = {**variables, codigo: salida[codigo] if salida[codigo] is not None else variables.get(codigo)}
    return salida


def aplicar_regla(registrado, regla, ctx):
    """Reglas de cálculo parametrizables. Devuelve registrado, minimas, extras y certificar."""
    tipo = _norm(regla.get("tipo")) or "SIN_MINIMO"
    R = registrado or 0.0
    M = num(regla.get("minimo")) or 0.0
    if _norm(regla.get("prorrateo")) == "DIAS":
        M = M * ctx.get("dias_periodo", 0) / (num(regla.get("dias_base")) or 30)
    certifica_extras = regla.get("certifica_extras", True) is not False
    if tipo == "TOPE":
        mn, ex = min(R, M), max(R - M, 0.0)
        cert = R if certifica_extras else mn
    elif tipo == "MINIMO_GARANTIZADO":
        mn, ex = M, max(R - M, 0.0)
        cert = max(R, M) if certifica_extras else M
    elif tipo == "TRAMOS":
        acumulado = {s: 0.0 for s in SALIDAS}
        restante, piso, cert = R, 0.0, 0.0
        for tramo in regla.get("tramos") or []:
            hasta = num(tramo.get("hasta"))
            capacidad = restante if hasta is None else max(min(restante, hasta - piso), 0.0)
            salida = tramo.get("salida") if tramo.get("salida") in ("minimas", "extras") else "minimas"
            factor = num(tramo.get("factor"))
            factor = 1.0 if factor is None else factor
            acumulado[salida] += capacidad * factor
            cert += capacidad * factor
            restante -= capacidad
            if hasta is not None:
                piso = hasta
            if restante <= 0:
                break
        mn, ex = acumulado["minimas"], acumulado["extras"]
    elif tipo == "FORMULA":
        variables = {**ctx, "R": R, "M": M, "registrado": R, "minimo": M}
        formulas = regla.get("formulas") or {}
        mn = evaluar(formulas.get("minimas") or "R", variables)
        variables["minimas"] = mn
        ex = evaluar(formulas.get("extras") or "0", variables)
        variables["extras"] = ex
        cert = evaluar(formulas.get("certificar") or "minimas + extras", variables)
    else:  # SIN_MINIMO
        mn, ex, cert = R, 0.0, R
    return {"registrado": r(R), "minimas": r(mn), "extras": r(ex), "certificar": r(cert)}


def _sumar_salidas(destino, origen):
    for s in SALIDAS:
        destino[s] = r((destino.get(s) or 0) + (origen.get(s) or 0))


def _cfg_item(cfg, item, overrides_items):
    base = fusionar(cfg.get("item_defecto") or {}, _json(item.get("config_json") if "config_json" in item else item.get("config")))
    return fusionar(base, (overrides_items or {}).get(item["codigo"]) or {})


def _cumple_filtro(parte, filtro):
    campo, op, valor = filtro.get("campo"), _norm(filtro.get("op")) or "IGUAL", filtro.get("valor")
    actual = _norm(parte.get(campo))
    lista = [_norm(v) for v in (valor if isinstance(valor, list) else [valor])]
    if op == "IGUAL":
        return actual in lista
    if op == "DISTINTO":
        return actual not in lista
    if op == "CONTIENE":
        return any(v and v in actual for v in lista)
    if op == "EMPIEZA":
        return any(actual.startswith(v) for v in lista)
    if op == "NO_VACIO":
        return actual != ""
    if op == "VACIO":
        return actual == ""
    return True


def _leer_partes(conn, cfg, codigos_equipo, codigos_operador):
    fuente = cfg.get("fuente") or {}
    campo_eq, campo_op = fuente.get("campo_equipo") or "ci", fuente.get("campo_operador") or "legajo_empleado"
    columnas = {c for c, _ in _columnas_parte(conn)}
    filas = []
    for campo, codigos in ((campo_eq, codigos_equipo), (campo_op, codigos_operador)):
        if not codigos or campo not in columnas:
            continue
        codigos = sorted(codigos)
        for i in range(0, len(codigos), 400):
            lote = codigos[i:i + 400]
            filas += _filas(conn, f'SELECT * FROM {TABLA_PARTES} WHERE UPPER(TRIM("{campo}")) IN ({",".join("?" for _ in lote)})',
                            [c.upper() for c in lote])
    vistos, unicas = set(), []
    for f in filas:
        if f["id"] not in vistos:
            vistos.add(f["id"])
            unicas.append(f)
    return unicas


def _variables_parte(parte, factores):
    variables = {}
    for k, v in parte.items():
        n = num(v) if not (isinstance(v, str) and k.startswith("fecha")) else None
        variables[k] = n if n is not None else v
    variables.update(factores)
    return variables


def calcular(conn, contrato_id, desde, hasta, numero=None, overrides=None, acta_id=None):
    """Calcula un acta a partir de los partes diarios. No escribe nada."""
    contrato = _contrato_base(conn, contrato_id)
    plantilla = obtener_plantilla(conn, contrato["plantilla_id"])
    cfg = fusionar(fusionar(plantilla["config"], contrato["config"]), {k: v for k, v in (overrides or {}).items() if k != "items"})
    overrides_items = (overrides or {}).get("items") or {}
    d_desde, d_hasta = parse_fecha(desde), parse_fecha(hasta)
    if not d_desde or not d_hasta or d_desde > d_hasta:
        _err("DATOS", "período inválido")
    p_desde, p_hasta = d_desde.isoformat(), d_hasta.isoformat()
    dias_periodo = (d_hasta - d_desde).days + 1
    fuente = cfg.get("fuente") or {}
    c_fecha, c_turno = fuente.get("campo_fecha") or "fecha_parte", fuente.get("campo_turno") or "turno"
    c_nro, c_eq, c_op = fuente.get("campo_nro_parte") or "nro_parte", fuente.get("campo_equipo") or "ci", fuente.get("campo_operador") or "legajo_empleado"
    medidas_cfg = cfg.get("medidas") or {}
    turnos = _mapa_turnos(cfg)

    secciones = {s["id"]: s for s in _filas(conn, "SELECT * FROM am_secciones WHERE contrato_id = ? ORDER BY orden, id", (contrato_id,))}
    items = _filas(conn, """SELECT i.*, s.orden AS s_orden FROM am_items i LEFT JOIN am_secciones s ON s.id = i.seccion_id
                            WHERE i.contrato_id = ? AND i.activo = 1 ORDER BY COALESCE(s.orden, 999999), i.orden, i.id""", (contrato_id,))
    asignaciones = {}
    for it in items:
        it["cfg"] = _cfg_item(cfg, it, overrides_items)
        asignaciones[it["id"]] = [a for a in _filas(conn, "SELECT * FROM am_asignaciones WHERE item_id = ? ORDER BY unidad, desde, id", (it["id"],))
                                  if _solapa(a["desde"], a["hasta"], p_desde, p_hasta)]
        for a in asignaciones[it["id"]]:
            a["cfg"] = _json(a["config_json"])
    factores = _filas(conn, "SELECT * FROM am_factores WHERE contrato_id = ?", (contrato_id,))
    equipos_cod = {a["recurso_codigo"].upper() for lista in asignaciones.values() for a in lista if a["recurso_tipo"] == "EQUIPO"}
    operadores_cod = {a["recurso_codigo"] for lista in asignaciones.values() for a in lista if a["recurso_tipo"] == "OPERADOR"}
    equipos_info = {}
    if equipos_cod:
        for v in _filas(conn, f"SELECT * FROM vehiculos WHERE UPPER(TRIM(codigo)) IN ({','.join('?' for _ in equipos_cod)})", sorted(equipos_cod)):
            v.pop("raw_json", None)
            equipos_info[_norm(v["codigo"])] = v

    todos = _leer_partes(conn, cfg, equipos_cod, operadores_cod)
    filtros = fuente.get("filtros") or []
    fechas_invalidas, en_periodo, historial = [], [], {}
    for p in todos:
        f = parse_fecha(p.get(c_fecha))
        p["_fecha"] = f
        if f is None:
            fechas_invalidas.append(p)
            continue
        historial.setdefault(_norm(p.get(c_eq)), []).append(p)
        if d_desde <= f <= d_hasta and all(_cumple_filtro(p, flt) for flt in filtros):
            en_periodo.append(p)
    en_periodo.sort(key=lambda p: (p["_fecha"], _texto(p.get(c_nro)), p["id"]))

    def factores_de(recurso, d):
        salida = {}
        for fct in factores:
            if fct["recurso_codigo"].upper() == recurso.upper() and _vigente(fct["desde"], fct["hasta"], d):
                salida[f"f.{fct['codigo']}"] = fct["valor"]
        return salida

    partes_out, asignados, multiples, sin_item = {}, {}, [], []
    for p in en_periodo:
        turno = turnos.get(_norm(p.get(c_turno)), _norm(p.get(c_turno)))
        candidatas = []
        for it in items:
            for a in asignaciones[it["id"]]:
                campo = c_eq if a["recurso_tipo"] == "EQUIPO" else c_op
                if _norm(p.get(campo)) != _norm(a["recurso_codigo"]) or not _vigente(a["desde"], a["hasta"], p["_fecha"]):
                    continue
                if a["turno"] and a["turno"] != turno:
                    continue
                candidatas.append((it, a))
        if not candidatas:
            sin_item.append(p)
            continue
        if len({c[0]["id"] for c in candidatas}) > 1:
            multiples.append((p, [c[0]["codigo"] for c in candidatas]))
        it, a = candidatas[0]
        recurso = a["recurso_codigo"]
        variables = _variables_parte(p, factores_de(recurso, p["_fecha"]))
        medidas = _medir(medidas_cfg, variables)
        cfg_a = fusionar(it["cfg"], {k: v for k, v in a["cfg"].items() if k in ("medida", "regla", "fijo")})
        medida = cfg_a.get("medida") or "horas"
        cantidad = medidas.get(medida)
        datos = {k: v for k, v in p.items() if not k.startswith("_")}
        partes_out[p["id"]] = {
            "id": p["id"], "datos": datos, "fecha": p["_fecha"].isoformat(), "turno_norm": turno, "item_id": it["id"],
            "item_codigo": it["codigo"], "unidad": a["unidad"], "recurso": recurso, "asignacion_id": a["id"],
            "medida": medida, "cantidad": r(cantidad or 0), "cantidad_nula": cantidad is None, "medidas": medidas,
            "factores": {k[2:]: v for k, v in variables.items() if k.startswith("f.")},
        }
        asignados.setdefault(it["id"], []).append(partes_out[p["id"]])

    anteriores = [a for a in _actas_vigentes(conn, contrato_id) if a["id"] != acta_id and (numero is None or a["numero"] < numero)]
    acum_prev = {}
    for a in anteriores:
        res = _json(a["resultado_json"])
        for it in res.get("items") or []:
            dst = acum_prev.setdefault(it["item_id"], {"salidas": {s: 0.0 for s in SALIDAS}, "medidas": {}})
            _sumar_salidas(dst["salidas"], it["salidas"])
            for k, v in (it.get("medidas") or {}).items():
                dst["medidas"][k] = r((dst["medidas"].get(k) or 0) + (v or 0))

    precios_oc = {}
    for l in _filas(conn, """SELECT l.item_id, l.salida, l.item_oc_cliente_id, i.precio, i.numero, i.descripcion, COALESCE(NULLIF(i.moneda, ''), o.moneda) AS moneda
                             FROM am_item_oc l JOIN items_oc_cliente i ON i.id = l.item_oc_cliente_id
                             JOIN Orden_de_compra_cliente o ON o.id = i.orden_compra_cliente_id
                             WHERE l.item_id IN (SELECT id FROM am_items WHERE contrato_id = ?)""", (contrato_id,)):
        precios_oc[(l["item_id"], l["salida"])] = l
    precios_man = _filas(conn, "SELECT * FROM am_precios WHERE contrato_id = ?", (contrato_id,))

    def precio_manual(item_id=None, salida=None, concepto=None):
        candidatos = [pr for pr in precios_man if pr["item_id"] == item_id and (pr["salida"] or "certificar") == (salida or "certificar")
                      and pr["concepto"] == concepto and _vigente(pr["desde"], pr["hasta"], d_hasta)] if item_id else \
                     [pr for pr in precios_man if pr["concepto"] == concepto and _vigente(pr["desde"], pr["hasta"], d_hasta)]
        candidatos.sort(key=lambda pr: pr["desde"])
        return candidatos[-1] if candidatos else None

    permite_100 = (cfg.get("acumulados") or {}).get("permite_superar_100", True) is not False
    indicadores = cfg.get("indicadores") or []
    codigos_medidas = list(medidas_cfg.keys())

    def indicadores_de(variables):
        return {ind["codigo"]: r(evaluar(ind.get("formula") or "0", variables), int(num(ind.get("decimales")) or 4))
                for ind in indicadores if ind.get("codigo")}

    def resumen_partes(lista):
        meds = {k: r(sum((pp["medidas"].get(k) or 0) for pp in lista)) for k in codigos_medidas}
        return meds, len(lista), len({pp["fecha"] for pp in lista})

    items_out, unidades_out, equipos_out = [], [], []
    for it in items:
        icfg = it["cfg"]
        modo = _norm(icfg.get("modo")) or "PARTE"
        regla_item = icfg.get("regla") or {}
        ambito = _norm(regla_item.get("ambito")) or "ITEM"
        unidades = num(it["unidades"]) or 1
        n_unidades = max(1, int(round(unidades)))
        lista = asignados.get(it["id"], [])
        fijo = icfg.get("fijo") or {}
        fijo_valor = num(fijo.get("valor")) or 0.0
        fijo_por_unidad = _norm(fijo.get("por")) != "ITEM"
        ctx_base = {"dias_periodo": dias_periodo, "unidades": unidades}

        def registrado_de(partes_grupo, cuota_fija):
            base = sum(pp["cantidad"] for pp in partes_grupo) if modo in ("PARTE", "MIXTO") else 0.0
            return base + (cuota_fija if modo in ("FIJO", "MIXTO") else 0.0)

        def regla_de(partes_grupo):
            regla = regla_item
            for pp in partes_grupo:
                asig = next((a for a in asignaciones[it["id"]] if a["id"] == pp["asignacion_id"]), None)
                if asig and asig["cfg"].get("regla"):
                    return fusionar(regla_item, asig["cfg"]["regla"])
            return regla

        salidas = {s: 0.0 for s in SALIDAS}
        grupos_unidad = []
        if ambito == "UNIDAD":
            ids_unidad = sorted(set(range(1, n_unidades + 1)) | {pp["unidad"] for pp in lista})
            for u in ids_unidad:
                pg = [pp for pp in lista if pp["unidad"] == u]
                cuota = fijo_valor if fijo_por_unidad else (fijo_valor / n_unidades)
                res = aplicar_regla(registrado_de(pg, cuota), regla_de(pg), ctx_base)
                _sumar_salidas(salidas, res)
                grupos_unidad.append((u, pg, res))
        elif ambito == "EQUIPO":
            recursos = sorted({pp["recurso"] for pp in lista}) or [None]
            cuota_total = fijo_valor * (n_unidades if fijo_por_unidad else 1)
            for rec in recursos:
                pg = [pp for pp in lista if pp["recurso"] == rec]
                cuota = cuota_total / len(recursos)
                res = aplicar_regla(registrado_de(pg, cuota), regla_de(pg), ctx_base)
                _sumar_salidas(salidas, res)
            for u in sorted(set(range(1, n_unidades + 1)) | {pp["unidad"] for pp in lista}):
                pg = [pp for pp in lista if pp["unidad"] == u]
                grupos_unidad.append((u, pg, None))
        else:  # ITEM
            regla = dict(regla_de(lista))
            regla["minimo"] = (num(regla.get("minimo")) or 0) * unidades
            cuota = fijo_valor * (unidades if fijo_por_unidad else 1)
            salidas = aplicar_regla(registrado_de(lista, cuota), regla, ctx_base)
            for u in sorted(set(range(1, n_unidades + 1)) | {pp["unidad"] for pp in lista}):
                grupos_unidad.append((u, [pp for pp in lista if pp["unidad"] == u], None))

        # Filas por unidad (si la regla no es por unidad, se reparte en proporción a lo registrado)
        total_reg = sum(registrado_de(pg, 0) for _, pg, _ in grupos_unidad) or 0
        for u, pg, res in grupos_unidad:
            if res is None:
                propio = registrado_de(pg, 0)
                proporcion = (propio / total_reg) if total_reg else (1 / len(grupos_unidad))
                res = {s: r(salidas[s] * proporcion) for s in SALIDAS}
                if modo == "PARTE":
                    res["registrado"] = r(propio)
            meds, n_partes, n_dias = resumen_partes(pg)
            recursos = sorted({pp["recurso"] for pp in pg}) or sorted({a["recurso_codigo"] for a in asignaciones[it["id"]] if a["unidad"] == u})
            variables = {**meds, **res, "cantidad": res["registrado"], "partes": n_partes, "dias": n_dias, "unidades": 1}
            unidades_out.append({
                "item_id": it["id"], "item_codigo": it["codigo"], "unidad": u, "recursos": recursos, "salidas": res,
                "medidas": meds, "indicadores": indicadores_de(variables), "partes": [pp["id"] for pp in pg],
                "n_partes": n_partes, "dias": n_dias,
            })

        # Filas por equipo (para reportes de avance/producción)
        for rec in sorted({pp["recurso"] for pp in lista}):
            pg = [pp for pp in lista if pp["recurso"] == rec]
            meds, n_partes, n_dias = resumen_partes(pg)
            reg = r(sum(pp["cantidad"] for pp in pg))
            proporcion = (reg / total_reg) if total_reg else 0
            res = {s: r(salidas[s] * proporcion) for s in SALIDAS}
            res["registrado"] = reg
            variables = {**meds, **res, "cantidad": reg, "partes": n_partes, "dias": n_dias, "unidades": 1}
            equipos_out.append({
                "item_id": it["id"], "item_codigo": it["codigo"], "recurso": rec, "salidas": res, "medidas": meds,
                "indicadores": indicadores_de(variables), "partes": [pp["id"] for pp in pg], "n_partes": n_partes, "dias": n_dias,
                "equipo": equipos_info.get(_norm(rec), {}),
            })

        meds, n_partes, n_dias = resumen_partes(lista)
        prev = acum_prev.get(it["id"], {"salidas": {s: 0.0 for s in SALIDAS}, "medidas": {}})
        saldo = num(it["saldo_inicial"]) or 0.0
        anterior = r(saldo + prev["salidas"]["certificar"])
        contratado = num(it["cantidad_contratada"])
        if contratado is None:
            contratado = (num(it["cantidad_mensual"]) or 0) * unidades * (num(contrato["duracion_meses"]) or 0)
        contratado = r(contratado)
        acumulado = r(anterior + salidas["certificar"])

        def pct(x):
            if not contratado:
                return None
            v = x / contratado
            return r(v if permite_100 else min(v, 1.0), 6)

        precios, importes = {}, {}
        for s in SALIDAS:
            oc = precios_oc.get((it["id"], s))
            pm = precio_manual(item_id=it["id"], salida=s)
            if oc:
                precios[s] = {"precio": num(oc["precio"]), "origen": "OC", "item_oc_cliente_id": oc["item_oc_cliente_id"],
                              "oc_item": f"{oc['numero']} - {oc['descripcion']}", "moneda": oc["moneda"]}
            elif pm:
                precios[s] = {"precio": pm["precio"], "origen": "MANUAL", "moneda": pm["moneda"]}
            if s in precios and precios[s]["precio"] is not None:
                importes[s] = r(salidas[s] * precios[s]["precio"], 2)
        facturar = icfg.get("facturar") or ["certificar"]
        importe = r(sum(importes.get(s, 0) for s in facturar), 2) if any(s in importes for s in facturar) else None
        variables = {**meds, **salidas, "cantidad": salidas["registrado"], "partes": n_partes, "dias": n_dias,
                     "unidades": unidades, "contratado": contratado}
        medidas_acum = {k: r((prev["medidas"].get(k) or 0) + (meds.get(k) or 0)) for k in codigos_medidas}
        meta = num(it["meta"])
        items_out.append({
            "item_id": it["id"], "codigo": it["codigo"], "descripcion": it["descripcion"], "unidad": it["unidad"],
            "seccion_id": it["seccion_id"], "seccion_codigo": (secciones.get(it["seccion_id"]) or {}).get("codigo"),
            "seccion_titulo": (secciones.get(it["seccion_id"]) or {}).get("titulo"),
            "unidades": unidades, "modo": modo, "medida": icfg.get("medida"), "regla": regla_item, "facturar": facturar,
            "salidas": salidas, "medidas": meds, "medidas_acumuladas": medidas_acum,
            "contratado": contratado, "saldo_inicial": saldo, "anterior": anterior, "acumulado": acumulado,
            "anterior_salidas": prev["salidas"], "anterior_pct": pct(anterior), "actual_pct": pct(salidas["certificar"]),
            "acumulado_pct": pct(acumulado), "saldo": r(contratado - acumulado) if contratado else None,
            "precios": precios, "importes": importes, "importe": importe, "indicadores": indicadores_de(variables),
            "meta": meta, "meta_medida": it["meta_medida"],
            "meta_pct": r(medidas_acum.get(it["meta_medida"], 0) / meta, 6) if meta and it["meta_medida"] else None,
            "partes": [pp["id"] for pp in lista], "n_partes": n_partes, "dias": n_dias,
            "asignaciones": [{"id": a["id"], "recurso": a["recurso_codigo"], "tipo": a["recurso_tipo"], "unidad": a["unidad"],
                              "turno": a["turno"], "desde": a["desde"], "hasta": a["hasta"]} for a in asignaciones[it["id"]]],
        })

    # Conceptos adicionales (descuentos/adicionales)
    conceptos_out = []
    for c in cfg.get("conceptos") or []:
        if c.get("activo", True) is False:
            continue
        aplica_s = {_texto(x) for x in c.get("aplica_secciones") or []}
        aplica_i = {_texto(x) for x in c.get("aplica_items") or []}
        sel = [it for it in items_out if (not aplica_s or it["seccion_codigo"] in aplica_s) and (not aplica_i or it["codigo"] in aplica_i)]
        variables = {k: r(sum((it["medidas"].get(k) or 0) for it in sel)) for k in codigos_medidas}
        for s in SALIDAS:
            variables[s] = r(sum(it["salidas"][s] for it in sel))
        base = r(evaluar(c.get("base") or "0", variables))
        pm = precio_manual(concepto=c.get("codigo"))
        precio = num(c.get("precio")) if c.get("precio") not in (None, "") else (pm["precio"] if pm else None)
        signo = -1 if (num(c.get("signo")) or 1) < 0 else 1
        conceptos_out.append({
            "codigo": c.get("codigo"), "titulo": c.get("titulo"), "unidad": c.get("unidad"), "base": base, "precio": precio,
            "signo": signo, "importe": r(base * precio * signo, 2) if precio is not None else None,
            "detalle": [{"item_codigo": it["codigo"], "base": r(evaluar(c.get("base") or "0", {**it["medidas"], **it["salidas"]}))} for it in sel],
        })

    total_importe_items = r(sum(it["importe"] or 0 for it in items_out), 2) if any(it["importe"] is not None for it in items_out) else None
    total_conceptos = r(sum(c["importe"] or 0 for c in conceptos_out), 2) if any(c["importe"] is not None for c in conceptos_out) else None
    validaciones = _validar(conn, cfg, items, asignaciones, en_periodo, partes_out, sin_item, multiples, fechas_invalidas,
                            historial, equipos_info, items_out, d_desde, d_hasta, c_eq, c_op, c_nro, turnos, c_turno, filtros, c_fecha)
    return {
        "version_motor": 1,
        "contrato": {k: contrato.get(k) for k in ("id", "nombre", "numero_contrato", "proyecto", "contratista", "cuit", "comitente",
                                                  "seguimiento_cc_id", "orden_compra_cliente_id", "duracion_meses", "fecha_inicio")},
        "plantilla": {"id": plantilla["id"], "nombre": plantilla["nombre"]},
        "periodo_desde": p_desde, "periodo_hasta": p_hasta, "dias_periodo": dias_periodo, "numero": numero,
        "items": items_out, "unidades": unidades_out, "equipos": equipos_out, "conceptos": conceptos_out,
        "partes": partes_out, "validaciones": validaciones,
        "totales": {"importe_items": total_importe_items, "importe_conceptos": total_conceptos,
                    "importe_neto": r((total_importe_items or 0) + (total_conceptos or 0), 2) if total_importe_items is not None else None,
                    "partes": len(partes_out)},
        "secciones": [{"id": s["id"], "codigo": s["codigo"], "titulo": s["titulo"]} for s in secciones.values()],
        "config": cfg,
    }


# ============================================================================
# Validaciones (todas activables y parametrizables desde la configuración)
# ============================================================================
TITULOS_VALIDACION = {
    "FINAL_MENOR_INICIO": "Valor final menor que el inicio",
    "INICIO_DISTINTO_ANTERIOR": "Inicio distinto del final del parte anterior del mismo equipo",
    "DUPLICADO_EQUIPO_FECHA_TURNO": "Más de un parte para el mismo equipo, fecha y turno",
    "DIAS_SIN_PARTE": "Días del período sin parte",
    "NRO_PARTE_REPETIDO": "N° de parte repetido",
    "CANTIDAD_MAXIMA": "Cantidad diaria por encima del máximo",
    "EQUIPO_SIN_ITEM": "Partes sin ítem asignado",
    "EQUIPO_INACTIVO": "Partes de equipos inactivos",
    "FECHA_INVALIDA": "Partes con fecha ilegible",
    "SUPERA_CONTRATO": "Acumulado por encima de lo contratado",
}


def _validar(conn, cfg, items, asignaciones, en_periodo, partes_out, sin_item, multiples, fechas_invalidas, historial,
             equipos_info, items_out, d_desde, d_hasta, c_eq, c_op, c_nro, turnos, c_turno, filtros, c_fecha):
    resultados = []
    asignados = [partes_out[p["id"]] for p in en_periodo if p["id"] in partes_out]
    for v in cfg.get("validaciones") or []:
        if not v.get("activa"):
            continue
        codigo = v.get("codigo")
        param = v.get("param") or {}
        mensajes = []
        if codigo == "FINAL_MENOR_INICIO":
            for pp in asignados:
                for ini, fin in param.get("pares") or []:
                    a, b = num(pp["datos"].get(ini)), num(pp["datos"].get(fin))
                    if a is not None and b is not None and b < a:
                        mensajes.append({"texto": f"{pp['recurso']} {_fecha_txt(date.fromisoformat(pp['fecha']))} parte {pp['datos'].get(c_nro)}: {fin} ({b:g}) < {ini} ({a:g})",
                                         "partes": [pp["id"]], "item": pp["item_codigo"]})
        elif codigo == "INICIO_DISTINTO_ANTERIOR":
            tol = num(param.get("tolerancia")) or 0
            for recurso, lista in historial.items():
                lista = sorted(lista, key=lambda p: (p["_fecha"], num(p.get("hs_inicio")) or 0, _texto(p.get(c_nro)), p["id"]))
                for prev, act in zip(lista, lista[1:]):
                    if act["id"] not in partes_out:
                        continue
                    for ini, fin in param.get("pares") or []:
                        a, b = num(prev.get(fin)), num(act.get(ini))
                        if a is None or b is None or (a == 0 and b == 0) or (num(prev.get(ini)) == 0 and a == 0):
                            continue
                        if abs(b - a) > tol:
                            mensajes.append({"texto": f"{recurso} {_fecha_txt(act['_fecha'])} parte {act.get(c_nro)}: {ini} {b:g} ≠ {fin} anterior {a:g} (parte {prev.get(c_nro)})",
                                             "partes": [prev["id"], act["id"]] if prev["id"] in partes_out else [act["id"]],
                                             "item": partes_out[act["id"]]["item_codigo"]})
        elif codigo == "DUPLICADO_EQUIPO_FECHA_TURNO":
            grupos = {}
            for pp in asignados:
                grupos.setdefault((pp["recurso"], pp["fecha"], pp["turno_norm"]), []).append(pp)
            for (rec, f, t), lista in grupos.items():
                if len(lista) > 1:
                    mensajes.append({"texto": f"{rec} {_fecha_txt(date.fromisoformat(f))} turno {t or '—'}: {len(lista)} partes",
                                     "partes": [pp["id"] for pp in lista], "item": lista[0]["item_codigo"]})
        elif codigo == "DIAS_SIN_PARTE":
            for it in items:
                if (_norm(it["cfg"].get("modo")) or "PARTE") == "FIJO":
                    continue
                for a in asignaciones[it["id"]]:
                    ini = max(d_desde, date.fromisoformat(a["desde"]))
                    fin = min(d_hasta, date.fromisoformat(a["hasta"])) if a["hasta"] else d_hasta
                    con_parte = {pp["fecha"] for pp in asignados if pp["asignacion_id"] == a["id"]}
                    faltan = [ini + timedelta(days=i) for i in range((fin - ini).days + 1) if (ini + timedelta(days=i)).isoformat() not in con_parte]
                    if faltan:
                        mensajes.append({"texto": f"{it['codigo']} {a['recurso_codigo']}{' turno ' + a['turno'] if a['turno'] else ''}: "
                                                  f"{len(faltan)} día(s) sin parte ({', '.join(_fecha_txt(d)[:5] for d in faltan[:12])}{'…' if len(faltan) > 12 else ''})",
                                         "partes": [], "item": it["codigo"]})
        elif codigo == "NRO_PARTE_REPETIDO":
            grupos = {}
            for pp in asignados:
                nro = _texto(pp["datos"].get(c_nro))
                if nro:
                    grupos.setdefault(nro, []).append(pp)
            for nro, lista in grupos.items():
                if len(lista) > 1:
                    mensajes.append({"texto": f"N° de parte {nro} usado {len(lista)} veces ({', '.join(sorted({pp['recurso'] for pp in lista}))})",
                                     "partes": [pp["id"] for pp in lista], "item": lista[0]["item_codigo"]})
        elif codigo == "CANTIDAD_MAXIMA":
            maximo = num(param.get("maximo"))
            if maximo is not None:
                for pp in asignados:
                    if pp["cantidad"] > maximo:
                        mensajes.append({"texto": f"{pp['recurso']} {_fecha_txt(date.fromisoformat(pp['fecha']))} parte {pp['datos'].get(c_nro)}: {pp['cantidad']:g} > {maximo:g}",
                                         "partes": [pp["id"]], "item": pp["item_codigo"]})
                    if pp["cantidad_nula"]:
                        mensajes.append({"texto": f"{pp['recurso']} {_fecha_txt(date.fromisoformat(pp['fecha']))} parte {pp['datos'].get(c_nro)}: "
                                                  f"no se pudo medir «{pp['medida']}» (faltan datos); se toma 0",
                                         "partes": [pp["id"]], "item": pp["item_codigo"]})
        elif codigo == "EQUIPO_SIN_ITEM":
            for p in sin_item:
                mensajes.append({"texto": f"{_texto(p.get(c_eq))} {_fecha_txt(p['_fecha'])} parte {p.get(c_nro)} turno {_texto(p.get(c_turno)) or '—'}: "
                                          "el equipo no tiene ítem vigente para esa fecha/turno", "partes": [], "parte_ids_fuera": [p["id"]]})
            for p, cods in multiples:
                mensajes.append({"texto": f"{_texto(p.get(c_eq))} {_fecha_txt(p['_fecha'])} parte {p.get(c_nro)}: asignado a varios ítems ({', '.join(cods)}); se toma {cods[0]}",
                                 "partes": [p["id"]], "item": cods[0]})
            if filtros:
                cand = _filas(conn, f"SELECT * FROM {TABLA_PARTES} WHERE id NOT IN ({','.join(str(i) for i in partes_out) or '0'})")
                asignados_eq = {_norm(a["recurso_codigo"]) for lista in asignaciones.values() for a in lista}
                for p in cand:
                    f = parse_fecha(p.get(c_fecha))
                    if f and d_desde <= f <= d_hasta and all(_cumple_filtro(p, flt) for flt in filtros) and _norm(p.get(c_eq)) not in asignados_eq:
                        mensajes.append({"texto": f"{_texto(p.get(c_eq))} {_fecha_txt(f)} parte {p.get(c_nro)}: cumple los filtros del contrato pero el equipo no está asignado a ningún ítem",
                                         "partes": [], "parte_ids_fuera": [p["id"]]})
        elif codigo == "EQUIPO_INACTIVO":
            campo = param.get("campo") or "operativo"
            activos = {_norm(x) for x in param.get("activos") or ["SI", ""]}
            for rec in sorted({pp["recurso"] for pp in asignados}):
                info = equipos_info.get(_norm(rec))
                if info is not None and _norm(info.get(campo)) not in activos:
                    ids = [pp["id"] for pp in asignados if pp["recurso"] == rec]
                    mensajes.append({"texto": f"{rec}: {campo} = «{info.get(campo)}» ({len(ids)} partes en el período)", "partes": ids})
        elif codigo == "FECHA_INVALIDA":
            for p in fechas_invalidas:
                mensajes.append({"texto": f"{_texto(p.get(c_eq))} parte {p.get(c_nro)}: fecha «{p.get(c_fecha)}» ilegible; no entra en ningún período",
                                 "partes": [], "parte_ids_fuera": [p["id"]]})
        elif codigo == "SUPERA_CONTRATO":
            for it in items_out:
                if it["contratado"] and it["acumulado"] > it["contratado"]:
                    mensajes.append({"texto": f"{it['codigo']}: acumulado {it['acumulado']:g} > contratado {it['contratado']:g}", "partes": [], "item": it["codigo"]})
        if mensajes:
            resultados.append({"codigo": codigo, "titulo": v.get("titulo") or TITULOS_VALIDACION.get(codigo, codigo),
                               "severidad": _norm(v.get("severidad")) or "AVISO", "mensajes": mensajes})
    return resultados


# ============================================================================
# Actas: alta, flujo, congelamiento, revisiones
# ============================================================================
def _estados(cfg):
    estados = ((cfg.get("flujo") or {}).get("estados")) or [{"codigo": "BORRADOR", "permiso": "generar"},
                                                             {"codigo": "EMITIDA", "permiso": "emitir", "congela": True}]
    return estados


def _cfg_contrato(conn, contrato_id):
    contrato = _contrato_base(conn, contrato_id)
    plantilla = obtener_plantilla(conn, contrato["plantilla_id"])
    return fusionar(plantilla["config"], contrato["config"])


def _acta(conn, acta_id):
    a = _fila(conn, "SELECT * FROM am_actas WHERE id = ?", (acta_id,))
    if not a:
        _err("NO_EXISTE", "acta inexistente")
    return a


def listar_actas(conn, contrato_id):
    filas = _filas(conn, """SELECT id, contrato_id, numero, revision, periodo_desde, periodo_hasta, fecha, estado, congelada, reemplaza_a_id,
                                   creado_por, creado_en, congelada_por, congelada_en, anulada_por, anulada_en, motivo_anulacion
                            FROM am_actas WHERE contrato_id = ? ORDER BY numero DESC, revision DESC""", (contrato_id,))
    for f in filas:
        f["certificados"] = _certificados_de(conn, f["id"])
    return filas


def _certificados_de(conn, acta_id):
    return _filas(conn, """SELECT c.id, c.numero, c.estado, c.seguimiento_cc_id FROM am_acta_certificados l
                           JOIN certificados_cliente c ON c.id = l.certificado_id WHERE l.acta_id = ? ORDER BY c.id""", (acta_id,))


def crear_acta(conn, contrato_id, datos, usuario):
    contrato = _contrato_base(conn, contrato_id)
    if not contrato["activo"]:
        _err("DATOS", "el contrato está inactivo")
    sug = sugerir_periodo(conn, contrato_id)
    numero = int(num(datos.get("numero")) or sug["numero"])
    if conn.execute("SELECT 1 FROM am_actas WHERE contrato_id = ? AND numero = ? AND estado <> 'ANULADA'", (contrato_id, numero)).fetchone():
        _err("DUPLICADO", f"ya existe el acta N° {numero}; para corregir una congelada emita una revisión")
    desde = _iso(datos.get("periodo_desde") or sug["periodo_desde"], "desde")
    hasta = _iso(datos.get("periodo_hasta") or sug["periodo_hasta"], "hasta")
    if hasta < desde:
        _err("DATOS", "el período termina antes de empezar")
    overrides = datos.get("overrides") or {}
    if isinstance(overrides, str):
        overrides = _json(overrides)
    estado = _estados(_cfg_contrato(conn, contrato_id))[0]["codigo"]
    revision = int((_fila(conn, "SELECT MAX(revision) AS r FROM am_actas WHERE contrato_id = ? AND numero = ?", (contrato_id, numero)) or {}).get("r") or -1) + 1
    acta_id = conn.execute(
        """INSERT INTO am_actas (contrato_id, numero, revision, periodo_desde, periodo_hasta, fecha, estado, overrides_json, observaciones,
                                 creado_por, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (contrato_id, numero, revision, desde, hasta, _iso(datos.get("fecha") or _hoy(), "fecha"), estado, _dump(overrides),
         _texto(datos.get("observaciones")) or None, usuario, _ahora())).lastrowid
    evento(conn, "ACTA", acta_id, "CREADA", usuario, {"numero": numero, "revision": revision})
    return obtener_acta(conn, acta_id)


def actualizar_acta(conn, acta_id, datos, usuario):
    a = _acta(conn, acta_id)
    if a["congelada"] or a["estado"] == "ANULADA":
        _err("INMUTABLE", "el acta está congelada; emita una revisión")
    desde = _iso(datos.get("periodo_desde") or a["periodo_desde"], "desde")
    hasta = _iso(datos.get("periodo_hasta") or a["periodo_hasta"], "hasta")
    if hasta < desde:
        _err("DATOS", "el período termina antes de empezar")
    overrides = datos.get("overrides", _json(a["overrides_json"]))
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides or "{}")
        except ValueError as exc:
            _err("DATOS", f"ajustes JSON inválidos: {exc}")
    _validar_config(fusionar(_cfg_contrato(conn, a["contrato_id"]), {k: v for k, v in overrides.items() if k != "items"}))
    conn.execute("""UPDATE am_actas SET periodo_desde = ?, periodo_hasta = ?, fecha = ?, overrides_json = ?, observaciones = ?,
                    actualizado_por = ?, actualizado_en = ? WHERE id = ?""",
                 (desde, hasta, _iso(datos.get("fecha") or a["fecha"], "fecha"), _dump(overrides),
                  _texto(datos.get("observaciones", a["observaciones"])) or None, usuario, _ahora(), acta_id))
    evento(conn, "ACTA", acta_id, "ACTUALIZADA", usuario)
    return obtener_acta(conn, acta_id)


def eliminar_acta(conn, acta_id, usuario):
    a = _acta(conn, acta_id)
    if a["congelada"]:
        _err("INMUTABLE", "un acta congelada no se elimina; anúlela")
    conn.execute("DELETE FROM am_actas WHERE id = ?", (acta_id,))
    evento(conn, "ACTA", acta_id, "ELIMINADA", usuario, {"numero": a["numero"], "revision": a["revision"]})
    return {"ok": True}


def _resultado(conn, a):
    if a["congelada"]:
        return _json(a["resultado_json"])
    return calcular(conn, a["contrato_id"], a["periodo_desde"], a["periodo_hasta"], a["numero"], _json(a["overrides_json"]), a["id"])


def obtener_acta(conn, acta_id, con_resultado=True):
    a = _acta(conn, acta_id)
    a["overrides"] = _json(a.pop("overrides_json"))
    salida = {k: v for k, v in a.items() if k not in ("resultado_json", "config_snapshot_json")}
    cfg = _json(a["config_snapshot_json"]) if a["congelada"] else _cfg_contrato(conn, a["contrato_id"])
    salida["estados"] = _estados(cfg)
    salida["certificados"] = _certificados_de(conn, acta_id)
    salida["contrato"] = _fila(conn, "SELECT id, nombre, numero_contrato, seguimiento_cc_id, orden_compra_cliente_id FROM am_contratos WHERE id = ?", (a["contrato_id"],))
    salida["historial"] = _filas(conn, "SELECT evento, usuario, fecha, detalle_json FROM am_eventos WHERE entidad = 'ACTA' AND entidad_id = ? ORDER BY id", (acta_id,))
    if con_resultado:
        a2 = _acta(conn, acta_id)
        salida["resultado"] = _resultado(conn, a2)
    return salida


def recalcular(conn, acta_id):
    a = _acta(conn, acta_id)
    return _resultado(conn, a)


def cambiar_estado(conn, acta_id, destino, usuario, puede):
    """Avanza o retrocede en el flujo configurado. `puede(accion)` valida el permiso del estado destino."""
    a = _acta(conn, acta_id)
    if a["estado"] == "ANULADA":
        _err("INMUTABLE", "el acta está anulada")
    cfg = _json(a["config_snapshot_json"]) if a["congelada"] else _cfg_contrato(conn, a["contrato_id"])
    estados = _estados(cfg)
    codigos = [e["codigo"] for e in estados]
    if destino not in codigos:
        _err("DATOS", f"estado desconocido {destino}")
    i_act = codigos.index(a["estado"]) if a["estado"] in codigos else 0
    i_dst = codigos.index(destino)
    if abs(i_dst - i_act) != 1:
        _err("DATOS", "solo se avanza o retrocede un estado por vez")
    est = estados[i_dst]
    if not puede(est.get("permiso") or "generar"):
        _err("PERMISO", f"su usuario no tiene la acción «{est.get('permiso')}» de Actas de Medición")
    if i_dst < i_act and a["congelada"]:
        _err("INMUTABLE", "un acta congelada no vuelve atrás; emita una revisión")
    if i_dst > i_act:
        resultado = _resultado(conn, a)
        bloqueos = [v for v in resultado["validaciones"] if v["severidad"] == "BLOQUEO"]
        if bloqueos:
            _err("BLOQUEO", "hay validaciones que bloquean el avance: " + "; ".join(f"{v['titulo']} ({len(v['mensajes'])})" for v in bloqueos))
        if est.get("congela") and not a["congelada"]:
            _congelar(conn, a, resultado, usuario)
    conn.execute("UPDATE am_actas SET estado = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?", (destino, usuario, _ahora(), acta_id))
    evento(conn, "ACTA", acta_id, f"ESTADO_{destino}", usuario, {"desde": a["estado"]})
    return obtener_acta(conn, acta_id)


def _congelar(conn, a, resultado, usuario):
    snapshot = resultado.get("config") or {}
    cuerpo = _dump(resultado)
    digest = hashlib.sha256((cuerpo + _dump(snapshot)).encode("utf-8")).hexdigest()
    conn.execute("""UPDATE am_actas SET congelada = 1, resultado_json = ?, config_snapshot_json = ?, hash = ?, congelada_por = ?, congelada_en = ?
                    WHERE id = ?""", (cuerpo, _dump(snapshot), digest, usuario, _ahora(), a["id"]))
    evento(conn, "ACTA", a["id"], "CONGELADA", usuario, {"hash": digest, "partes": len(resultado.get("partes") or {})})


def revisar_acta(conn, acta_id, usuario, motivo=""):
    """Nueva revisión de un acta congelada: mismo número, revisión+1, recalculada con la configuración y partes actuales."""
    a = _acta(conn, acta_id)
    if not a["congelada"]:
        _err("DATOS", "solo se revisan actas congeladas; un borrador se edita directamente")
    if conn.execute("SELECT 1 FROM am_actas WHERE contrato_id = ? AND numero = ? AND revision > ? AND estado <> 'ANULADA'",
                    (a["contrato_id"], a["numero"], a["revision"])).fetchone():
        _err("DUPLICADO", "ya hay una revisión posterior de esta acta")
    estado = _estados(_cfg_contrato(conn, a["contrato_id"]))[0]["codigo"]
    nueva = conn.execute(
        """INSERT INTO am_actas (contrato_id, numero, revision, periodo_desde, periodo_hasta, fecha, estado, overrides_json, observaciones,
                                 reemplaza_a_id, creado_por, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (a["contrato_id"], a["numero"], a["revision"] + 1, a["periodo_desde"], a["periodo_hasta"], _hoy(), estado, a["overrides_json"],
         _texto(motivo) or a["observaciones"], a["id"], usuario, _ahora())).lastrowid
    evento(conn, "ACTA", nueva, "REVISION_CREADA", usuario, {"reemplaza_a": a["id"], "motivo": motivo})
    return obtener_acta(conn, nueva)


def anular_acta(conn, acta_id, motivo, usuario):
    a = _acta(conn, acta_id)
    if a["estado"] == "ANULADA":
        _err("DATOS", "el acta ya está anulada")
    if len(_texto(motivo)) < 10:
        _err("DATOS", "indique el motivo de la anulación (mínimo 10 caracteres)")
    if any(c["estado"] != "ANULADO" for c in _certificados_de(conn, acta_id)):
        _err("PROTEGIDO", "el acta tiene un certificado vigente; anule primero el certificado")
    if not a["congelada"]:
        _err("DATOS", "un acta no congelada se elimina, no se anula")
    conn.execute("UPDATE am_actas SET estado = 'ANULADA', anulada_por = ?, anulada_en = ?, motivo_anulacion = ? WHERE id = ?",
                 (usuario, _ahora(), _texto(motivo), acta_id))
    evento(conn, "ACTA", acta_id, "ANULADA", usuario, {"motivo": motivo})
    return obtener_acta(conn, acta_id, con_resultado=False)


# ============================================================================
# Reportes (definidos por configuración)
# ============================================================================
def _plantilla_texto(texto, valores):
    def reemplazo(m):
        clave = m.group(1)
        v = valores.get(clave)
        return "" if v is None else str(v)
    return re.sub(r"\{([A-Za-z0-9_.]+)\}", reemplazo, texto or "")


def _filas_nivel(resultado, nivel, campos=None):
    partes = resultado.get("partes") or {}
    items = {it["item_id"]: it for it in resultado.get("items") or []}

    def base_item(it):
        return {"item_codigo": it["codigo"], "item_descripcion": it["descripcion"], "item_unidad": it["unidad"],
                "seccion_codigo": it["seccion_codigo"], "seccion_titulo": it["seccion_titulo"]}

    def metricas(fila, obj):
        fila.update(obj["salidas"])
        fila["actual"] = obj["salidas"]["certificar"]
        for k, v in (obj.get("medidas") or {}).items():
            fila[f"med_{k}"] = v
        for k, v in (obj.get("indicadores") or {}).items():
            fila[f"ind_{k}"] = v
        fila["n_partes"] = obj.get("n_partes")
        fila["dias"] = obj.get("dias")

    filas = []
    if nivel == "ITEM":
        for it in resultado.get("items") or []:
            fila = base_item(it)
            fila.update({k: it[k] for k in ("codigo", "descripcion", "unidad", "unidades", "contratado", "anterior", "acumulado",
                                            "anterior_pct", "actual_pct", "acumulado_pct", "saldo", "saldo_inicial", "meta", "meta_pct", "importe")})
            metricas(fila, it)
            for k, v in (it.get("medidas_acumuladas") or {}).items():
                fila[f"med_acum_{k}"] = v
            for s in SALIDAS:
                fila[f"anterior_{s}"] = it["anterior_salidas"].get(s)
                fila[f"acumulado_{s}"] = r((it["anterior_salidas"].get(s) or 0) + (it["salidas"].get(s) or 0))
                if s in it["precios"]:
                    fila[f"precio_{s}"] = it["precios"][s]["precio"]
                if s in it["importes"]:
                    fila[f"importe_{s}"] = it["importes"][s]
            filas.append((fila, it["partes"], it["seccion_titulo"] or ""))
    elif nivel == "UNIDAD":
        for u in resultado.get("unidades") or []:
            it = items[u["item_id"]]
            fila = base_item(it)
            fila.update({"n_unidad": u["unidad"], "equipos": " / ".join(u["recursos"])})
            metricas(fila, u)
            filas.append((fila, u["partes"], it["seccion_titulo"] or ""))
    elif nivel == "EQUIPO":
        for e in resultado.get("equipos") or []:
            it = items[e["item_id"]]
            fila = base_item(it)
            fila["equipo"] = e["recurso"]
            for k, v in (e.get("equipo") or {}).items():
                fila[f"equipo_{k}"] = v
            metricas(fila, e)
            filas.append((fila, e["partes"], it["seccion_titulo"] or ""))
    else:  # PARTE
        for pid, pp in sorted(partes.items(), key=lambda kv: (kv[1]["item_codigo"], kv[1]["unidad"], kv[1]["recurso"], kv[1]["fecha"],
                                                                _texto(kv[1]["datos"].get("nro_parte")))):
            it = items.get(pp["item_id"]) or {}
            fila = dict(pp["datos"])
            fila.update(base_item(it) if it else {})
            fila.update({"fecha": pp["fecha"], "cantidad": pp["cantidad"], "equipo": pp["recurso"], "n_unidad": pp["unidad"],
                         "turno_norm": pp["turno_norm"], "medida": pp["medida"]})
            u = next((x for x in resultado.get("unidades") or [] if x["item_id"] == pp["item_id"] and x["unidad"] == pp["unidad"]), None)
            fila["equipos"] = " / ".join(u["recursos"]) if u else pp["recurso"]
            for k, v in (pp.get("medidas") or {}).items():
                fila[f"med_{k}"] = v
            for k, v in (pp.get("factores") or {}).items():
                fila[f"f_{k}"] = v
            filas.append((fila, [int(pid) if str(pid).isdigit() else pid], ""))
    return filas


def catalogo_valores(conn, cfg):
    """Valores disponibles por nivel para armar columnas de reporte (incluye TODOS los campos del parte)."""
    medidas = cfg.get("medidas") or {}
    inds = cfg.get("indicadores") or []
    comunes_item = [("item_codigo", "Ítem"), ("item_descripcion", "Descripción del ítem"), ("item_unidad", "Unidad del ítem"),
                    ("seccion_titulo", "Sección")]
    salidas = [("registrado", "Registrado"), ("minimas", "Mínimas"), ("extras", "Extras"), ("certificar", "A certificar"),
               ("n_partes", "Cantidad de partes"), ("dias", "Días con parte")]
    meds = [(f"med_{k}", f"{m.get('titulo') or k} ({m.get('unidad') or ''})") for k, m in medidas.items()]
    indic = [(f"ind_{i['codigo']}", i.get("titulo") or i["codigo"]) for i in inds if i.get("codigo")]
    item = [("codigo", "Código"), ("descripcion", "Descripción"), ("unidad", "Unidad"), ("unidades", "Cantidad de unidades"),
            ("contratado", "Contratado"), ("anterior", "Acumulado anterior"), ("anterior_pct", "% anterior"),
            ("actual_pct", "% actual"), ("acumulado", "Acumulado a la fecha"), ("acumulado_pct", "% acumulado"),
            ("saldo", "Saldo contractual"), ("saldo_inicial", "Saldo inicial"), ("meta", "Meta"), ("meta_pct", "% de meta"),
            ("importe", "Importe")]
    item += [(f"med_acum_{k}", f"{m.get('titulo') or k} acumulado") for k, m in medidas.items()]
    for s, t in (("minimas", "mínimas"), ("extras", "extras"), ("certificar", "a certificar")):
        item += [(f"anterior_{s}", f"Anterior {t}"), (f"acumulado_{s}", f"Acumulado {t}"), (f"precio_{s}", f"Precio {t}"),
                 (f"importe_{s}", f"Importe {t}")]
    equipo_campos = [f[1] for f in conn.execute("PRAGMA table_info(vehiculos)").fetchall() if f[1] != "raw_json"]
    parte = [(c["campo"], c["etiqueta"]) for c in campos_parte(conn)]
    parte += [("fecha", "Fecha (normalizada)"), ("cantidad", "Cantidad medida"), ("equipo", "Equipo"), ("equipos", "Equipos de la unidad"),
              ("turno_norm", "Turno (normalizado)"), ("medida", "Medida usada")]
    parte += [(f"f_{f['codigo']}", f.get("titulo") or f["codigo"]) for f in cfg.get("factores") or []]
    return {
        "ITEM": [{"valor": v, "titulo": t} for v, t in item + salidas + meds + indic],
        "UNIDAD": [{"valor": v, "titulo": t} for v, t in comunes_item + [("n_unidad", "N° de unidad"), ("equipos", "Equipos")] + salidas + meds + indic],
        "EQUIPO": [{"valor": v, "titulo": t} for v, t in comunes_item + [("equipo", "Equipo (CI)")] +
                   [(f"equipo_{c}", f"Equipo: {c}") for c in equipo_campos if c != "codigo"] + salidas + meds + indic],
        "PARTE": [{"valor": v, "titulo": t} for v, t in comunes_item + parte + meds],
    }


def construir_reporte(resultado, reporte, acta_info=None):
    nivel = reporte.get("nivel") or "ITEM"
    filas = _filas_nivel(resultado, nivel)
    columnas = [c for c in reporte.get("columnas") or [] if c.get("valor")]
    agrupar = reporte.get("agrupar") or ""

    def valor_col(col, fila):
        v = col["valor"]
        if v.startswith("="):
            return r(evaluar(v, fila))
        return fila.get(v)

    def clave_grupo(fila):
        if agrupar == "seccion":
            return fila.get("seccion_titulo") or ""
        if agrupar == "item":
            return fila.get("item_codigo") or ""
        if agrupar == "unidad":
            return f"{fila.get('item_codigo')}#{fila.get('n_unidad')}"
        if agrupar == "equipo":
            return fila.get("equipo") or ""
        if agrupar:
            return _texto(fila.get(agrupar))
        return ""

    grupos, orden = {}, []
    for fila, partes, _ in filas:
        k = clave_grupo(fila)
        if k not in grupos:
            grupos[k] = {"titulo": _plantilla_texto(reporte.get("titulo_seccion") or "{" + (agrupar or "x") + "}", fila) if agrupar else "",
                         "filas": []}
            orden.append(k)
        grupos[k]["filas"].append({"celdas": [valor_col(c, fila) for c in columnas], "partes": partes})
    secciones = []
    for k in orden:
        g = grupos[k]
        totales = []
        for i, c in enumerate(columnas):
            if c.get("total"):
                totales.append(r(sum(num(f["celdas"][i]) or 0 for f in g["filas"])))
            else:
                totales.append(None)
        g["totales"] = totales if any(t is not None for t in totales) else None
        g["partes"] = [p for f in g["filas"] for p in f["partes"]]
        secciones.append(g)
    total_general = None
    if any(c.get("total") for c in columnas) and len(secciones) > 1 and reporte.get("una_tabla"):
        total_general = [r(sum(num(s["totales"][i]) or 0 for s in secciones if s["totales"])) if c.get("total") else None
                         for i, c in enumerate(columnas)]
    cfg = resultado.get("config") or {}
    valores = _valores_encabezado(resultado, acta_info)
    encabezado = None
    if reporte.get("encabezado", True):
        enc = cfg.get("encabezado") or {}
        encabezado = {"titulo": _plantilla_texto(enc.get("titulo"), valores), "logo": enc.get("logo"),
                      "campos": [{"etiqueta": c.get("etiqueta"), "valor": _plantilla_texto(c.get("valor"), valores)} for c in enc.get("campos") or []]}
    firmas = [{k: _plantilla_texto(f.get(k), valores) for k in ("titulo", "cargo", "empresa")} for f in cfg.get("firmas") or []] if reporte.get("firmas") else []
    return {
        "codigo": reporte.get("codigo"), "titulo": reporte.get("titulo"), "subtitulo": _plantilla_texto(reporte.get("subtitulo"), valores),
        "nivel": nivel, "una_tabla": bool(reporte.get("una_tabla")), "hoja_por_seccion": bool(reporte.get("hoja_por_seccion")),
        "columnas": [{"titulo": c.get("titulo") or c["valor"], "grupo": c.get("grupo") or "", "formato": c.get("formato") or "texto",
                      "total": bool(c.get("total")), "valor": c["valor"]} for c in columnas],
        "secciones": secciones, "total_general": total_general, "encabezado": encabezado, "firmas": firmas,
        "conceptos": resultado.get("conceptos") if reporte.get("mostrar_conceptos") else None,
        "emitir_importes": bool(cfg.get("emitir_importes")),
    }


def _valores_encabezado(resultado, acta_info):
    c = resultado.get("contrato") or {}
    acta_info = acta_info or {}
    valores = {f"contrato.{k}": v for k, v in c.items()}
    d, h = resultado.get("periodo_desde"), resultado.get("periodo_hasta")
    valores.update({
        "numero": acta_info.get("numero", resultado.get("numero")), "revision": acta_info.get("revision", 0),
        "fecha": _fecha_txt(parse_fecha(acta_info.get("fecha"))) if acta_info.get("fecha") else "",
        "periodo": f"{_fecha_txt(parse_fecha(d))} al {_fecha_txt(parse_fecha(h))}", "estado": acta_info.get("estado", ""),
    })
    return valores


def reportes_acta(conn, acta_id, solo_activos=True):
    a = obtener_acta(conn, acta_id)
    res = a["resultado"]
    cfg = res.get("config") or {}
    info = {k: a[k] for k in ("numero", "revision", "fecha", "estado")}
    return [construir_reporte(res, rep, info) for rep in cfg.get("reportes") or [] if rep.get("activo", True) or not solo_activos]


def partes_de(conn, acta_id, ids):
    a = obtener_acta(conn, acta_id)
    partes = a["resultado"].get("partes") or {}
    salida = []
    for i in ids:
        pp = partes.get(str(i)) or partes.get(i)
        if pp:
            salida.append(pp)
    return salida


def exportar_excel(conn, acta_id):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    a = obtener_acta(conn, acta_id, con_resultado=False)
    reportes = reportes_acta(conn, acta_id)
    wb = Workbook()
    wb.remove(wb.active)
    fino = Side(style="thin", color="9FB3C8")
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)
    cab = PatternFill("solid", fgColor="DCE8F7")
    tot = PatternFill("solid", fgColor="EEF4FC")
    formatos = {"entero": "#,##0", "decimal2": "#,##0.00", "decimal": "#,##0.00", "porcentaje": "0.00%", "moneda": "#,##0.00", "fecha": "DD/MM/YYYY"}
    usados = set()

    def hoja(nombre):
        base = re.sub(r"[\[\]\*\?/\\:]", "-", nombre)[:28] or "Hoja"
        n, i = base, 2
        while n in usados:
            n, i = f"{base[:25]} {i}", i + 1
        usados.add(n)
        return wb.create_sheet(n)

    def escribir(ws, rep, secciones):
        fila = 1
        if rep["encabezado"]:
            ws.cell(fila, 1, rep["encabezado"]["titulo"]).font = Font(bold=True, size=13)
            fila += 1
            for c in rep["encabezado"]["campos"]:
                ws.cell(fila, 1, f"{c['etiqueta']}:").font = Font(bold=True)
                ws.cell(fila, 2, c["valor"])
                fila += 1
            fila += 1
        if rep.get("subtitulo"):
            ws.cell(fila, 1, rep["subtitulo"]).font = Font(bold=True)
            fila += 1
        cols = rep["columnas"]
        if any(c["grupo"] for c in cols):
            for i, c in enumerate(cols, 1):
                cel = ws.cell(fila, i, c["grupo"])
                cel.font, cel.fill, cel.border = Font(bold=True), cab, borde
            fila += 1
        for i, c in enumerate(cols, 1):
            cel = ws.cell(fila, i, c["titulo"])
            cel.font, cel.fill, cel.border = Font(bold=True), cab, borde
            cel.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
            ws.column_dimensions[get_column_letter(i)].width = max(10, min(45, len(c["titulo"]) + 4))
        fila += 1
        for s in secciones:
            if s["titulo"]:
                ws.cell(fila, 1, s["titulo"]).font = Font(bold=True)
                fila += 1
            for f in s["filas"]:
                for i, (c, v) in enumerate(zip(cols, f["celdas"]), 1):
                    if c["formato"] == "fecha" and v:
                        v = parse_fecha(v) or v
                    cel = ws.cell(fila, i, v)
                    cel.border = borde
                    if c["formato"] in formatos:
                        cel.number_format = formatos[c["formato"]]
                fila += 1
            if s["totales"]:
                ws.cell(fila, 1, "TOTAL").font = Font(bold=True)
                for i, (c, v) in enumerate(zip(cols, s["totales"]), 1):
                    if v is not None:
                        cel = ws.cell(fila, i, v)
                        cel.font, cel.fill, cel.border = Font(bold=True), tot, borde
                        if c["formato"] in formatos:
                            cel.number_format = formatos[c["formato"]]
                fila += 1
            fila += 1
        if rep.get("total_general"):
            ws.cell(fila, 1, "TOTAL GENERAL").font = Font(bold=True)
            for i, (c, v) in enumerate(zip(cols, rep["total_general"]), 1):
                if v is not None:
                    ws.cell(fila, i, v).font = Font(bold=True)
            fila += 2
        if rep.get("conceptos"):
            for c in rep["conceptos"]:
                ws.cell(fila, 1, c["titulo"]).font = Font(bold=True)
                ws.cell(fila, 2, c["base"])
                ws.cell(fila, 3, c["unidad"])
                ws.cell(fila, 4, "Precio")
                ws.cell(fila, 5, c["precio"])
                ws.cell(fila, 6, "Importe")
                ws.cell(fila, 7, c["importe"])
                fila += 1
            fila += 1
        if rep["firmas"]:
            fila += 2
            for i, f in enumerate(rep["firmas"]):
                col = 1 + i * 4
                ws.cell(fila, col, f["titulo"]).font = Font(bold=True)
                ws.cell(fila + 1, col, f["cargo"])
                ws.cell(fila + 2, col, f["empresa"])

    for rep in reportes:
        if rep["hoja_por_seccion"] and len(rep["secciones"]) > 1:
            for s in rep["secciones"]:
                escribir(hoja(s["titulo"] or rep["titulo"]), rep, [s])
        else:
            escribir(hoja(rep["titulo"]), rep, rep["secciones"])
    if not wb.sheetnames:
        wb.create_sheet("Acta")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"Acta_medicion_{a['numero']}_rev{a['revision']}.xlsx"


# ============================================================================
# Conexión con Certificados (Seguimiento de Cliente)
# ============================================================================
def actas_para_certificado(conn, seguimiento_id, orden_compra_cliente_id=None):
    """Actas en estado de precarga, del seguimiento (y OC) del certificado, sin certificado vigente."""
    salida = []
    for c in _filas(conn, "SELECT id, nombre, orden_compra_cliente_id FROM am_contratos WHERE seguimiento_cc_id = ?", (seguimiento_id,)):
        cfg = _cfg_contrato(conn, c["id"])
        estados_ok = set((cfg.get("flujo") or {}).get("precarga_desde") or [e["codigo"] for e in _estados(cfg) if e.get("congela")])
        for a in _actas_vigentes(conn, c["id"], congeladas=False):
            if a["estado"] not in estados_ok:
                continue
            certs = [x for x in _certificados_de(conn, a["id"]) if x["estado"] != "ANULADO"]
            salida.append({"acta_id": a["id"], "contrato_id": c["id"], "contrato": c["nombre"], "numero": a["numero"], "revision": a["revision"],
                           "periodo_desde": a["periodo_desde"], "periodo_hasta": a["periodo_hasta"], "estado": a["estado"],
                           "orden_compra_cliente_id": c["orden_compra_cliente_id"],
                           "coincide_oc": not orden_compra_cliente_id or not c["orden_compra_cliente_id"] or c["orden_compra_cliente_id"] == orden_compra_cliente_id,
                           "certificado_vigente": certs[0] if certs else None})
    return salida


def precarga_certificado(conn, acta_id):
    """Cantidades del período por ítem de OC (según los vínculos ítem/salida -> ítem OC) y conceptos como descuentos."""
    a = obtener_acta(conn, acta_id)
    res = a["resultado"]
    cfg = res.get("config") or {}
    estados_ok = set((cfg.get("flujo") or {}).get("precarga_desde") or [e["codigo"] for e in a["estados"] if e.get("congela")])
    if a["estado"] not in estados_ok:
        _err("DATOS", f"el acta está en {a['estado']}; se precarga desde {', '.join(sorted(estados_ok))}")
    lineas, sin_vinculo = {}, []
    for it in res["items"]:
        vinculos = _filas(conn, "SELECT salida, item_oc_cliente_id FROM am_item_oc WHERE item_id = ?", (it["item_id"],))
        if not vinculos:
            if it["salidas"]["certificar"]:
                sin_vinculo.append(it["codigo"])
            continue
        for v in vinculos:
            cantidad = it["salidas"].get(v["salida"]) or 0
            linea = lineas.setdefault(v["item_oc_cliente_id"], {"item_oc_cliente_id": v["item_oc_cliente_id"], "cantidad_periodo": 0.0, "origen": []})
            linea["cantidad_periodo"] = r(linea["cantidad_periodo"] + cantidad)
            linea["origen"].append(f"{it['codigo']} {v['salida']}")
    ocs = {x["orden_compra_cliente_id"] for x in _filas(conn, f"""SELECT orden_compra_cliente_id FROM items_oc_cliente
                                                                   WHERE id IN ({','.join(str(i) for i in lineas) or '0'})""")}
    descuentos = []
    for c in res.get("conceptos") or []:
        if c["importe"] is None or not c["importe"]:
            continue
        if c["signo"] < 0:
            descuentos.append({"concepto": "OTRO", "descripcion": f"{c['titulo']}: {c['base']:g} {c.get('unidad') or ''} × {c['precio']:g}".strip()[:300],
                               "base": "MANUAL", "importe": abs(c["importe"])})
    conceptos_sin_precio = [c["titulo"] for c in res.get("conceptos") or [] if c["importe"] is None and c["base"]]
    rev = f" rev. {a['revision']}" if a["revision"] else ""
    texto = (f"Precargado desde Acta de Medición N° {a['numero']}{rev} "
             f"({_fecha_txt(parse_fecha(a['periodo_desde']))} al {_fecha_txt(parse_fecha(a['periodo_hasta']))})")
    return {
        "acta_id": acta_id, "numero": a["numero"], "revision": a["revision"], "periodo_desde": a["periodo_desde"], "periodo_hasta": a["periodo_hasta"],
        "seguimiento_cc_id": a["contrato"]["seguimiento_cc_id"], "orden_compra_cliente_id": a["contrato"]["orden_compra_cliente_id"],
        "ordenes": sorted(ocs), "lineas": [{**l, "observacion": f"Acta N° {a['numero']}: {', '.join(l['origen'])}"} for l in lineas.values()],
        "descuentos": descuentos, "items_sin_vinculo": sin_vinculo, "conceptos_sin_precio": conceptos_sin_precio, "observaciones": texto,
    }


def vincular_certificado(conn, acta_id, certificado_id, usuario):
    a = _acta(conn, acta_id)
    contrato = _contrato_base(conn, a["contrato_id"])
    cert = _fila(conn, "SELECT id, seguimiento_cc_id, estado FROM certificados_cliente WHERE id = ?", (certificado_id,))
    if not cert:
        _err("NO_EXISTE", "certificado inexistente")
    if contrato["seguimiento_cc_id"] and cert["seguimiento_cc_id"] != contrato["seguimiento_cc_id"]:
        _err("DATOS", "el certificado es de otro seguimiento")
    if cert["estado"] == "ANULADO":
        _err("DATOS", "el certificado está anulado")
    otros = [c for c in _certificados_de(conn, acta_id) if c["estado"] != "ANULADO" and c["id"] != certificado_id]
    if otros:
        _err("DUPLICADO", "el acta ya tiene otro certificado vigente")
    existente = _fila(conn, "SELECT acta_id FROM am_acta_certificados WHERE certificado_id = ?", (certificado_id,))
    if existente and existente["acta_id"] != acta_id:
        _err("DUPLICADO", "el certificado ya está vinculado a otra acta")
    if not existente:
        conn.execute("INSERT INTO am_acta_certificados (acta_id, certificado_id, creado_por, creado_en) VALUES (?, ?, ?, ?)",
                     (acta_id, certificado_id, usuario, _ahora()))
        evento(conn, "ACTA", acta_id, "CERTIFICADO_VINCULADO", usuario, {"certificado_id": certificado_id})
    return {"ok": True}


def acta_de_certificado(conn, certificado_id):
    return _fila(conn, """SELECT a.id AS acta_id, a.numero, a.revision, c.nombre AS contrato FROM am_acta_certificados l
                          JOIN am_actas a ON a.id = l.acta_id JOIN am_contratos c ON c.id = a.contrato_id WHERE l.certificado_id = ?""",
                 (certificado_id,))


# ============================================================================
# Datos auxiliares para las pantallas
# ============================================================================
def opciones(conn):
    return {
        "seguimientos": _filas(conn, """SELECT s.id, s.cliente_codigo, cl.razon_social AS cliente, s.centro_costo_id, cc.nombre AS centro_costo, s.cotizacion
                                        FROM seguimiento_cc s LEFT JOIN clientes cl ON cl.codigo = s.cliente_codigo
                                        LEFT JOIN centros_costos cc ON cc.id = s.centro_costo_id ORDER BY s.id"""),
        "ordenes": _filas(conn, "SELECT id, seguimiento_cc_id, numero, orden_compra, revision, moneda, monto_oc FROM Orden_de_compra_cliente ORDER BY seguimiento_cc_id, numero"),
        "items_oc": _filas(conn, """SELECT i.id, i.orden_compra_cliente_id, i.numero, i.descripcion, i.unidad, i.cantidad, i.precio,
                                           COALESCE(NULLIF(i.moneda, ''), o.moneda) AS moneda, o.seguimiento_cc_id, o.orden_compra
                                    FROM items_oc_cliente i JOIN Orden_de_compra_cliente o ON o.id = i.orden_compra_cliente_id ORDER BY o.id, i.numero"""),
        "equipos": _filas(conn, """SELECT TRIM(codigo) AS codigo, tipo, marca, modelo, dominio, operativo FROM vehiculos
                                   WHERE TRIM(COALESCE(codigo, '')) <> '' ORDER BY codigo"""),
        "operadores": _filas(conn, "SELECT TRIM(legajo) AS legajo, nombre FROM personal WHERE TRIM(COALESCE(legajo, '')) <> '' ORDER BY nombre"),
        "plantillas": _filas(conn, "SELECT id, nombre FROM am_plantillas WHERE activa = 1 ORDER BY nombre"),
        "salidas": list(SALIDAS),
        "funciones": sorted(FUNCIONES),
    }
